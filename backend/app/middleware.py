"""
Request tracing and rate limiting middleware.

Two implementations of each are kept deliberately:

``RequestIDMiddleware`` / ``RateLimitMiddleware``
    Pure ASGI. They operate on the raw ``(scope, receive, send)`` triple.

``LegacyRequestIDMiddleware`` / ``LegacyRateLimitMiddleware``
    The original Starlette ``BaseHTTPMiddleware`` versions.

``BaseHTTPMiddleware`` is convenient but costly: it runs the downstream app in
a separate anyio task and pipes the response through a memory object stream, so
every request pays task-spawn plus stream overhead — per middleware. The pure
ASGI versions just wrap ``send``.

Both are retained so ``scripts/benchmark_matrix.py`` can measure the difference
on this workload rather than citing someone else's benchmark. Select with the
``OPT_ASGI_MIDDLEWARE`` setting.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import reset_request_id, set_request_id
from app.redis_pool import get_redis

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"

# Two budgets, because the two classes of write have very different shapes.
#
# "ingest" is the fleet reporting telemetry: high volume by design, so the
# budget is generous. "console" is what a browser can reach without holding the
# master key — minting tickets and dispatching commands. Those are public on
# the hosted demo, so an unlimited budget would let anyone mint tickets forever
# and drive the fleet as fast as they can send requests.
#
# Dashboard reads stay unlimited: cheap, cached, and read-only.
_INGEST_SUFFIXES = ("/telemetry", "/telemetry/batch")
_CONSOLE_SUFFIXES = ("/auth/ticket",)
_COMMANDS_SEGMENT = "/commands/"

INGEST_BUCKET = "ingest"
CONSOLE_BUCKET = "console"


def _rate_limit_bucket(method: str, path: str) -> str | None:
    """Which budget applies, or None when the path is not limited."""
    if method != "POST":
        return None
    if path.endswith(_INGEST_SUFFIXES):
        return INGEST_BUCKET
    if path.endswith(_CONSOLE_SUFFIXES):
        return CONSOLE_BUCKET
    # Operator dispatch: POST /api/v1/commands/{robot_id}. The robot-facing
    # /claim sub-path is excluded deliberately — robots poll it continuously and
    # a console-sized budget would throttle the fleet itself.
    if _COMMANDS_SEGMENT in path and not path.endswith("/claim"):
        return CONSOLE_BUCKET
    return None


def client_ip_from_scope(scope: Scope, trusted_proxy_count: int) -> str:
    """Resolve the real client IP, accounting for reverse proxies.

    ``scope["client"]`` is the socket peer. Behind nginx/CloudFront/an ALB that
    is the *proxy*, so keying a rate limiter on it collapses the entire fleet
    into a single bucket.

    ``X-Forwarded-For`` is client-controlled and trivially spoofed, so we don't
    simply take the leftmost entry. Each trusted proxy appends the address it
    saw, so with N trusted proxies the Nth entry from the right is the last one
    a proxy actually observed and the leftmost value a client cannot forge.
    """
    peer = scope.get("client")
    fallback = peer[0] if peer else "unknown"

    if trusted_proxy_count <= 0:
        return fallback

    forwarded = Headers(scope=scope).get("x-forwarded-for")
    if not forwarded:
        return fallback

    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return fallback

    index = len(hops) - trusted_proxy_count
    return hops[max(index, 0)]


# ── Pure ASGI implementations ───────────────────────────────────────


class RequestIDMiddleware:
    """Attach an ``X-Request-ID`` to every request, response, and log line.

    An inbound id is honoured rather than replaced, so a trace started by nginx
    or a caller survives into this service instead of being renamed at the door.

    Also emits one structured access line per request. Production runs uvicorn
    with ``--no-access-log``, so without this a deployed request leaves no trace
    at all — which is exactly how a dispatched command became unverifiable after
    the fact.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500  # if the app raises, the request did fail

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _log_access(scope, status_code, time.perf_counter() - started)
            # Reset even on failure: under uvicorn the same task can serve the
            # next request, and a stale id is worse than none.
            reset_request_id(token)


class RateLimitMiddleware:
    """Redis-backed sliding-window rate limiter on the ingest endpoints."""

    def __init__(
        self,
        app: ASGIApp,
        max_requests: int = 100,
        console_max_requests: int = 60,
        window_seconds: int = 60,
        trusted_proxy_count: int = 0,
    ) -> None:
        self.app = app
        self.max_requests = max_requests
        self.console_max_requests = console_max_requests
        self.window_seconds = window_seconds
        self.trusted_proxy_count = trusted_proxy_count

    def _budget(self, bucket: str) -> int:
        return self.console_max_requests if bucket == CONSOLE_BUCKET else self.max_requests

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        bucket = (
            _rate_limit_bucket(scope.get("method", ""), scope.get("path", ""))
            if scope["type"] == "http"
            else None
        )
        if bucket is None:
            await self.app(scope, receive, send)
            return

        client_ip = client_ip_from_scope(scope, self.trusted_proxy_count)
        allowed = await _check_rate_limit(
            client_ip, self._budget(bucket), self.window_seconds, bucket
        )
        if not allowed:
            response = _rate_limited_response(self.window_seconds)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _log_access(scope: Scope, status_code: int, elapsed_seconds: float) -> None:
    """One structured line per request.

    Fields go through ``extra=`` rather than into the message, so the JSON
    formatter emits them as queryable keys instead of something a dashboard has
    to regex back out.
    """
    from app.config import get_settings

    if not get_settings().access_log:
        return

    path = scope.get("path", "")
    # Health and metrics are scraped every few seconds; logging them buries
    # real traffic without adding anything a monitor does not already know.
    if path in ("/health", "/metrics"):
        return

    method = scope.get("method", "-")
    duration_ms = round(elapsed_seconds * 1000, 2)
    logger.info(
        "%s %s %s %sms",
        method,
        path,
        status_code,
        duration_ms,
        extra={
            "http_method": method,
            "http_path": path,
            "http_status": status_code,
            "duration_ms": duration_ms,
        },
    )


# ── Shared limiter logic ────────────────────────────────────────────

# In-memory backend's state. Module-level, not per-request: the whole point
# is one process's counters, shared across requests within that process.
# Unbounded by client IP, deliberately — see rate_limit_backend's docstring
# in config.py for why that's fine at this app's actual keyspace size.
_memory_windows: dict[str, list[float]] = {}


def _check_rate_limit_memory(
    client_ip: str, max_requests: int, window_seconds: int, bucket: str
) -> bool:
    """In-process equivalent of the Redis sliding window, for a single worker.

    Same semantics: drop entries older than the window, count what's left,
    record this request, return whether the count *before* recording was
    under budget. No pipeline, no network call, no metered command.
    """
    key = f"{bucket}:{client_ip}"
    now = time.time()
    cutoff = now - window_seconds

    timestamps = _memory_windows.setdefault(key, [])
    fresh = [t for t in timestamps if t > cutoff]

    allowed = len(fresh) < max_requests
    fresh.append(now)
    _memory_windows[key] = fresh
    return allowed


async def _check_rate_limit(
    client_ip: str, max_requests: int, window_seconds: int, bucket: str = INGEST_BUCKET
) -> bool:
    """Return ``True`` if this request is within the limit.

    Sliding window: drop entries older than the window, count what's left,
    and record this request. The count is read *before* the insert lands so a
    client gets exactly ``max_requests`` per window rather than one fewer.
    Backed by Redis (default, correct across multiple instances) or an
    in-process dict (``RATE_LIMIT_BACKEND=memory``, correct for exactly one
    instance and free) — see config.py.

    The Redis path fails open — if Redis is unavailable we serve the request
    rather than hard-failing ingestion on a cache outage.
    """
    from app.config import get_settings

    if get_settings().rate_limit_backend == "memory":
        return _check_rate_limit_memory(client_ip, max_requests, window_seconds, bucket)

    redis = get_redis()
    if redis is None:
        return True

    # Bucketed so a burst of console requests cannot exhaust the fleet's
    # ingest budget, or vice versa.
    key = f"rate_limit:{bucket}:{client_ip}"
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex[:8]}"  # unique: identical timestamps collide

    try:
        pipeline = redis.pipeline()
        pipeline.zremrangebyscore(key, 0, now - window_seconds)
        pipeline.zcard(key)
        pipeline.zadd(key, {member: now})
        pipeline.expire(key, window_seconds)
        results = await pipeline.execute()
    except Exception:
        logger.warning("Rate limiter unavailable, failing open", exc_info=True)
        return True

    count_before_insert = results[1]
    return count_before_insert < max_requests


def _rate_limited_response(window_seconds: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
        headers={"Retry-After": str(window_seconds)},
    )


# ── Legacy BaseHTTPMiddleware implementations (benchmark baseline) ──


class LegacyRequestIDMiddleware(BaseHTTPMiddleware):
    """Original BaseHTTPMiddleware request-ID implementation.

    Kept at parity with the ASGI version — including log correlation and the
    access line — because the benchmark compares the two implementations and a
    difference in what they *do* would make that comparison meaningless.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _log_access(request.scope, status_code, time.perf_counter() - started)
            reset_request_id(token)


class LegacyRateLimitMiddleware(BaseHTTPMiddleware):
    """Original BaseHTTPMiddleware rate limiter."""

    def __init__(
        self,
        app,
        max_requests: int = 100,
        console_max_requests: int = 60,
        window_seconds: int = 60,
        trusted_proxy_count: int = 0,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.console_max_requests = console_max_requests
        self.window_seconds = window_seconds
        self.trusted_proxy_count = trusted_proxy_count

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        bucket = _rate_limit_bucket(request.method, request.url.path)
        if bucket is None:
            return await call_next(request)

        budget = self.console_max_requests if bucket == CONSOLE_BUCKET else self.max_requests
        client_ip = client_ip_from_scope(request.scope, self.trusted_proxy_count)
        allowed = await _check_rate_limit(client_ip, budget, self.window_seconds, bucket)
        if not allowed:
            return _rate_limited_response(self.window_seconds)

        return await call_next(request)
