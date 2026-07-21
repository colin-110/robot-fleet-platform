"""
Custom middleware for request tracing and rate limiting.
"""

import logging
import time
import uuid
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from app.redis_pool import get_redis

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Attach a unique ``X-Request-ID`` header to every request/response.
    Makes it easy to correlate logs with specific API calls.
    """
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        
        request.state.request_id = request_id
        
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-backed rate limiter.
    Limits requests to max_requests per window_seconds per IP.
    """
    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Only rate-limit telemetry ingestion
        if request.method != "POST" or not (request.url.path.endswith("/telemetry") or request.url.path.endswith("/telemetry/batch")):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        redis = get_redis()
        
        if redis:
            key = f"rate_limit:{client_ip}"
            current_time = time.time()
            
            # Simple token bucket using Redis sorted sets
            pipeline = redis.pipeline()
            pipeline.zremrangebyscore(key, 0, current_time - self.window_seconds)
            pipeline.zcard(key)
            pipeline.zadd(key, {str(current_time): current_time})
            pipeline.expire(key, self.window_seconds)
            
            results = await pipeline.execute()
            request_count = results[1]
            
            if request_count >= self.max_requests:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please try again later."},
                    headers={"Retry-After": str(self.window_seconds)}
                )
            
        return await call_next(request)
