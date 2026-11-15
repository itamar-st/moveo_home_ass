"""Shared FastAPI dependencies, rate limiter, and structured API error types."""

import asyncio
import math
import secrets
import time
from collections import deque
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Header, Request

from app.config import get_settings
from app.db.session import get_db

__all__ = [
    "APIError",
    "SlidingWindowRateLimiter",
    "enforce_chat_rate_limit",
    "get_client_ip",
    "get_db",
    "require_admin_token",
]


class APIError(Exception):
    """Domain/HTTP error rendered in the §8 `{"error": ...}` envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class SlidingWindowRateLimiter:
    """In-memory per-IP sliding-window rate limiter for chat endpoints."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        *,
        max_buckets: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_buckets = max(1, max_buckets)
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    def _evict_if_needed(self, cutoff: float) -> None:
        """Remove expired buckets first, then oldest-inserted keys if still at capacity."""
        if len(self._buckets) < self.max_buckets:
            return
        stale_keys = [
            k for k, dq in self._buckets.items() if not dq or dq[-1] <= cutoff
        ]
        for k in stale_keys:
            del self._buckets[k]
        while len(self._buckets) >= self.max_buckets:
            oldest_key = next(iter(self._buckets))
            del self._buckets[oldest_key]

    async def check(self, key: str) -> tuple[bool, int]:
        """Record a request for `key` if under limit; return `(allowed, retry_after_s)`."""
        if self.limit <= 0:
            return True, 0

        now = self._clock()
        cutoff = now - self.window_seconds

        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is not None:
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if not bucket:
                    del self._buckets[key]
                    bucket = None

            if bucket is not None and len(bucket) >= self.limit:
                oldest = bucket[0]
                retry_after = max(1, math.ceil(self.window_seconds - (now - oldest)))
                return False, retry_after

            if bucket is None:
                self._evict_if_needed(cutoff)
                bucket = deque()
                self._buckets[key] = bucket

            bucket.append(now)
            return True, 0


def get_client_ip(request: Request) -> str:
    """Extract client IP, honoring X-Forwarded-For only when trusted proxy headers are enabled."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Use the rightmost hop appended by the immediate trusted reverse proxy
            # so a client cannot spoof the leftmost value.
            hops = [part.strip() for part in forwarded.split(",") if part.strip()]
            if hops:
                return hops[-1]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def enforce_chat_rate_limit(request: Request) -> None:
    """FastAPI dependency enforcing `CHAT_RATE_LIMIT_PER_MIN` per client IP."""
    limiter: SlidingWindowRateLimiter | None = getattr(
        request.app.state, "chat_rate_limiter", None
    )
    if limiter is None:
        limiter = SlidingWindowRateLimiter(get_settings().chat_rate_limit_per_min)
        request.app.state.chat_rate_limiter = limiter

    client_ip = get_client_ip(request)
    allowed, retry_after = await limiter.check(client_ip)
    if not allowed:
        raise APIError(
            429,
            "rate_limited",
            f"Rate limit exceeded ({limiter.limit} requests/min). Please retry in {retry_after}s.",
            details={"retry_after_seconds": retry_after},
        )


async def require_admin_token(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """FastAPI dependency validating the `X-Admin-Token` header for admin routes."""
    expected = get_settings().admin_token.strip()
    if not expected:
        raise APIError(
            503,
            "admin_disabled",
            "Admin endpoints are disabled because ADMIN_TOKEN is not configured.",
        )
    if not x_admin_token or not secrets.compare_digest(
        x_admin_token.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise APIError(
            401,
            "unauthorized",
            "Missing or invalid X-Admin-Token header.",
        )
