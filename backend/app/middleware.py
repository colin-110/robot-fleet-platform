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

from app.redis_pool import get_redis

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"

# Only telemetry ingestion is rate limited; dashboard reads are cheap and cached.
_RATE_LIMITED_SUFFIXES = ("/telemetry", "/telemetry/batch")


def _is_rate_limited(method: str, path: str) -> bool:
    return method == "POST" and path.endswith(_RATE_LIMITED_SUFFIXES)


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
    """Attach a unique ``X-Request-ID`` to every request and response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class RateLimitMiddleware:
    """Redis-backed sliding-window rate limiter on the ingest endpoints."""

    def __init__(
        self,
        app: ASGIApp,
        max_requests: int = 100,
        window_seconds: int = 60,
        trusted_proxy_count: int = 0,
    ) -> None:
        self.app = app
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.trusted_proxy_count = trusted_proxy_count

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_rate_limited(
            scope.get("method", ""), scope.get("path", "")
        ):
            await self.app(scope, receive, send)
            return

        client_ip = client_ip_from_scope(scope, self.trusted_proxy_count)
        allowed = await _check_rate_limit(client_ip, self.max_requests, self.window_seconds)
        if not allowed:
            response = _rate_limited_response(self.window_seconds)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# ── Shared limiter logic ────────────────────────────────────────────


async def _check_rate_limit(client_ip: str, max_requests: int, window_seconds: int) -> bool:
    """Return ``True`` if this request is within the limit.

    Sliding window over a Redis sorted set: drop entries older than the window,
    count what's left, and record this request. The count is read *before* the
    insert lands so a client gets exactly ``max_requests`` per window rather
    than one fewer.

    Fails open — if Redis is unavailable we serve the request rather than
    hard-failing ingestion on a cache outage.
    """
    redis = get_redis()
    if redis is None:
        return True

    key = f"rate_limit:{client_ip}"
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
    """Original BaseHTTPMiddleware request-ID implementation."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class LegacyRateLimitMiddleware(BaseHTTPMiddleware):
    """Original BaseHTTPMiddleware rate limiter."""

    def __init__(
        self,
        app,
        max_requests: int = 100,
        window_seconds: int = 60,
        trusted_proxy_count: int = 0,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.trusted_proxy_count = trusted_proxy_count

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not _is_rate_limited(request.method, request.url.path):
            return await call_next(request)

        client_ip = client_ip_from_scope(request.scope, self.trusted_proxy_count)
        allowed = await _check_rate_limit(client_ip, self.max_requests, self.window_seconds)
        if not allowed:
            return _rate_limited_response(self.window_seconds)

        return await call_next(request)
