"""
Custom middleware for request tracing and rate limiting.
"""

import logging
import time
import uuid
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Attach a unique ``X-Request-ID`` header to every request/response.

    Makes it easy to correlate logs with specific API calls.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory token-bucket rate limiter per client IP.

    Only applied to POST requests (telemetry ingestion).

    .. note::
        This is a per-process rate limiter. With multiple Uvicorn
        workers, the effective limit is ``max_per_minute × workers``.
        For precise cross-process limiting, use Redis-based rate
        limiting instead.
    """

    def __init__(self, app, max_per_minute: int = 600) -> None:
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._buckets: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "POST":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = 60.0

        # Purge expired timestamps
        timestamps = self._buckets[client_ip]
        self._buckets[client_ip] = [t for t in timestamps if now - t < window]

        if len(self._buckets[client_ip]) >= self.max_per_minute:
            logger.warning(
                "Rate limit exceeded for %s (%d/%d per minute)",
                client_ip,
                len(self._buckets[client_ip]),
                self.max_per_minute,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        self._buckets[client_ip].append(now)
        return await call_next(request)
