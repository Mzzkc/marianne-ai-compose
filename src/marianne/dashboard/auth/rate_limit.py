"""Rate limiting middleware for Marianne Dashboard.

Provides configurable rate limiting to protect API endpoints from abuse.
Uses a sliding window algorithm with in-memory storage.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from marianne.core.constants import (
    RATE_LIMIT_BURST_LIMIT,
    RATE_LIMIT_REQUESTS_PER_HOUR,
    RATE_LIMIT_REQUESTS_PER_MINUTE,
)


@dataclass
class RateLimitConfig:
    """Rate limiting configuration.

    Attributes:
        enabled: Whether rate limiting is active
        requests_per_minute: Max requests per minute per client
        requests_per_hour: Max requests per hour per client
        burst_limit: Max burst requests in 1 second
        excluded_paths: Paths exempt from rate limiting
        by_api_key: Use API key for rate limit tracking (vs IP)
    """

    enabled: bool = True
    requests_per_minute: int = RATE_LIMIT_REQUESTS_PER_MINUTE
    requests_per_hour: int = RATE_LIMIT_REQUESTS_PER_HOUR
    burst_limit: int = RATE_LIMIT_BURST_LIMIT
    excluded_paths: list[str] = field(
        default_factory=lambda: [
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/static",
            "/",           # page routes
            "/jobs",
            "/monitor",
            "/templates",
            "/editor",
            "/api/dashboard",   # htmx partials (stats, recent, system)
            "/api/templates",   # template grid partial
            "/api/monitor",     # monitor snapshot polling
        ]
    )
    by_api_key: bool = False


class SlidingWindowCounter:
    """Sliding window rate limiter implementation.

    Uses a time-bucketed approach for efficient memory usage while
    maintaining accuracy of the sliding window algorithm.
    """

    def __init__(self, window_seconds: int, max_requests: int):
        """Initialize counter.

        Args:
            window_seconds: Time window in seconds
            max_requests: Maximum requests allowed in window
        """
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _cleanup_bucket(self, key: str, window_start: float) -> None:
        """Remove expired entries from a bucket. Must be called under self._lock.

        O(k) where k = expired entries (vs O(n) for full list rebuild),
        since timestamps are appended chronologically.
        """
        bucket = self._buckets[key]
        while bucket and bucket[0] <= window_start:
            bucket.popleft()

    def is_allowed(self, key: str) -> tuple[bool, int, int]:
        """Check if request is allowed and record it.

        Args:
            key: Client identifier (IP or API key)

        Returns:
            Tuple of (allowed, remaining_requests, reset_time_seconds)
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            self._cleanup_bucket(key, window_start)

            current_count = len(self._buckets[key])

            if current_count >= self.max_requests:
                # Timestamps are appended chronologically, so [0] is the oldest
                oldest = self._buckets[key][0] if self._buckets[key] else now
                reset_time = int(oldest + self.window_seconds - now) + 1
                return False, 0, reset_time

            # Record this request
            self._buckets[key].append(now)
            remaining = self.max_requests - current_count - 1

            return True, remaining, self.window_seconds

    def check_only(self, key: str) -> tuple[bool, int, int]:
        """Check if request would be allowed without recording it.

        Args:
            key: Client identifier (IP or API key)

        Returns:
            Tuple of (allowed, remaining_requests, reset_time_seconds)
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            self._cleanup_bucket(key, window_start)

            current_count = len(self._buckets[key])

            if current_count >= self.max_requests:
                oldest = self._buckets[key][0] if self._buckets[key] else now
                reset_time = int(oldest + self.window_seconds - now) + 1
                return False, 0, reset_time

            remaining = self.max_requests - current_count - 1
            return True, remaining, self.window_seconds

    def record(self, key: str) -> None:
        """Record a request (call only after check_only passes)."""
        with self._lock:
            self._buckets[key].append(time.time())

    def get_count(self, key: str) -> int:
        """Get current request count for key.

        Args:
            key: Client identifier

        Returns:
            Number of requests in current window
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            self._cleanup_bucket(key, window_start)
            return len(self._buckets[key])

    def reset(self, key: str | None = None) -> None:
        """Reset counter for a key or all keys.

        Args:
            key: Client identifier or None to reset all
        """
        with self._lock:
            if key is None:
                self._buckets.clear()
            elif key in self._buckets:
                del self._buckets[key]


class RateLimiter:
    """Combined rate limiter with multiple windows.

    Enforces limits at second (burst), minute, and hour granularity.
    """

    def __init__(self, config: RateLimitConfig | None = None):
        """Initialize rate limiter.

        Args:
            config: Rate limiting configuration
        """
        self.config = config or RateLimitConfig()

        # Create counters for different time windows
        self.burst_counter = SlidingWindowCounter(1, self.config.burst_limit)
        self.minute_counter = SlidingWindowCounter(
            60, self.config.requests_per_minute
        )
        self.hour_counter = SlidingWindowCounter(
            3600, self.config.requests_per_hour
        )

    def check(self, key: str) -> tuple[bool, dict[str, Any]]:
        """Check if request is allowed.

        Args:
            key: Client identifier

        Returns:
            Tuple of (allowed, rate_limit_info dict)
        """
        if not self.config.enabled:
            return True, {"enabled": False}

        # Check all tiers first (read-only) before recording
        burst_ok, burst_remaining, burst_reset = (
            self.burst_counter.check_only(key)
        )
        if not burst_ok:
            return False, {
                "limit": "burst",
                "remaining": 0,
                "reset": burst_reset,
                "retry_after": burst_reset,
            }

        minute_ok, minute_remaining, minute_reset = (
            self.minute_counter.check_only(key)
        )
        if not minute_ok:
            return False, {
                "limit": "minute",
                "remaining": 0,
                "reset": minute_reset,
                "retry_after": minute_reset,
            }

        hour_ok, hour_remaining, hour_reset = (
            self.hour_counter.check_only(key)
        )
        if not hour_ok:
            return False, {
                "limit": "hour",
                "remaining": 0,
                "reset": hour_reset,
                "retry_after": hour_reset,
            }

        # All checks passed — record the request in all tiers
        self.burst_counter.record(key)
        self.minute_counter.record(key)
        self.hour_counter.record(key)

        return True, {
            "limit": "none",
            "burst_remaining": burst_remaining,
            "minute_remaining": minute_remaining,
            "hour_remaining": hour_remaining,
        }

    def reset(self, key: str | None = None) -> None:
        """Reset rate limits for key or all.

        Args:
            key: Client identifier or None for all
        """
        self.burst_counter.reset(key)
        self.minute_counter.reset(key)
        self.hour_counter.reset(key)


def get_client_identifier(request: Request, by_api_key: bool = False) -> str:
    """Get client identifier for rate limiting.

    Args:
        request: FastAPI request
        by_api_key: Use API key if present

    Returns:
        Client identifier string
    """
    if by_api_key:
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"key:{api_key[:8]}..."  # Truncate for privacy

    # Fall back to IP address
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware for FastAPI.

    Applies configurable rate limits and returns appropriate headers.
    """

    def __init__(self, app: ASGIApp, config: RateLimitConfig | None = None):
        """Initialize middleware.

        Args:
            app: ASGI application (FastAPI or Starlette app)
            config: Rate limit configuration
        """
        super().__init__(app)
        self.config = config or RateLimitConfig()
        self.limiter = RateLimiter(self.config)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process rate limiting for each request.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response with rate limit headers
        """
        # Check if rate limiting is enabled
        if not self.config.enabled:
            return await call_next(request)

        # Check if path is excluded
        path = request.url.path
        if self._is_excluded_path(path):
            return await call_next(request)

        # Get client identifier
        client_id = get_client_identifier(request, self.config.by_api_key)

        # Check rate limit
        allowed, info = self.limiter.check(client_id)

        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Rate limit exceeded",
                    "limit": info["limit"],
                    "retry_after": info["retry_after"],
                },
                headers={
                    "Retry-After": str(info["retry_after"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(info["reset"]),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        if "minute_remaining" in info:
            response.headers["X-RateLimit-Limit"] = str(
                self.config.requests_per_minute
            )
            response.headers["X-RateLimit-Remaining"] = str(
                info["minute_remaining"]
            )
            response.headers["X-RateLimit-Reset"] = "60"

        return response

    def _is_excluded_path(self, path: str) -> bool:
        """Check if path is excluded from rate limiting.

        Args:
            path: Request path

        Returns:
            True if excluded
        """
        for excluded in self.config.excluded_paths:
            if path == excluded or path.startswith(excluded + "/"):
                return True
        return False


# Public API
__all__ = [
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimiter",
    "SlidingWindowCounter",
    "get_client_identifier",
]
