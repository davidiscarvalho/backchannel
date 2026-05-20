from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable

from backchannel.store import APIError


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int, now_provider: Callable[[], datetime]):
        self.limit = limit
        self.window_seconds = window_seconds
        self.now_provider = now_provider
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, subject: str) -> None:
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now_ts = now.timestamp()
        cutoff = now_ts - self.window_seconds
        history = self.events[subject]
        while history and history[0] <= cutoff:
            history.popleft()
        if len(history) >= self.limit:
            raise APIError(
                429,
                "rate_limit_exceeded",
                "Invitation lookup rate limit exceeded",
                {"retry_after": self.window_seconds},
            )
        history.append(now_ts)

    def track(self, subject: str, limit: int | None = None) -> int:
        """Record a request without enforcing the limit. Returns remaining count."""
        effective_limit = limit if limit is not None else self.limit
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now_ts = now.timestamp()
        cutoff = now_ts - self.window_seconds
        history = self.events[subject]
        while history and history[0] <= cutoff:
            history.popleft()
        history.append(now_ts)
        return max(0, effective_limit - len(history))

    def enforce(self, subject: str) -> int:
        """Record a request AND enforce the limit. Raises 429 when exceeded.
        Returns the remaining count for the X-RateLimit-Remaining header."""
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now_ts = now.timestamp()
        cutoff = now_ts - self.window_seconds
        history = self.events[subject]
        while history and history[0] <= cutoff:
            history.popleft()
        if len(history) >= self.limit:
            raise APIError(
                429,
                "rate_limit_exceeded",
                f"Rate limit exceeded: {self.limit} requests per {self.window_seconds}s. "
                "The public instance is a sandbox — self-host for higher limits.",
                {"retry_after": self.window_seconds},
            )
        history.append(now_ts)
        return max(0, self.limit - len(history))
