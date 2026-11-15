"""Shared FastAPI dependencies, rate limiter, and structured API error types."""

import asyncio
import math
import time
from collections import defaultdict, deque
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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, int]:
        """Record a request for `key` if under limit; return `(allowed, retry_after_s)`."""
        if self.limit <= 0:
            return True, 0

        now = self._clock()
        cutoff = now - self.window_seconds

        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.limit:
                oldest = bucket[0]
                retry_after = max(1, math.ceil(self.window_seconds - (now - oldest)))
                return False, retry_after

            bucket.append(now)
            return True, 0


def get_client_ip(request: Request) -> str:
    """Extract client IP, honoring the first X-Forwarded-For hop when present."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
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
    expected = get_settings().admin_token
    if not x_admin_token or x_admin_token != expected:
        raise APIError(
            401,
            "unauthorized",
            "Missing or invalid X-Admin-Token header.",
        )
