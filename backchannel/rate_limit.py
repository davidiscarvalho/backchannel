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
