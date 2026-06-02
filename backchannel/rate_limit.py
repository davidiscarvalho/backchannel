from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable

from backchannel.store import APIError


class SlidingWindowRateLimiter:
    """In-memory sliding-window limiter.

    Thread-safe (the server is threaded — ThreadingWSGIServer), and bounded:
    idle buckets are swept periodically so a stream of distinct subjects
    (per-IP / per-key) can't grow ``events`` without limit. Note this is
    per-process state: multi-replica deployments need a shared store or each
    replica enforces independently (documented in docs/reliability.md).
    """

    _SWEEP_EVERY = 1024

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        now_provider: Callable[[], datetime],
        message: str | None = None,
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self.now_provider = now_provider
        # 429 message used by check(); enforce() has its own sandbox-aware text.
        self.message = message or f"Rate limit exceeded: {limit} per {window_seconds}s"
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._ops = 0

    def _now_ts(self) -> float:
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.timestamp()

    def _prune(self, subject: str, now_ts: float) -> deque[float]:
        """Drop expired timestamps for *subject*. Caller must hold the lock."""
        history = self.events[subject]
        cutoff = now_ts - self.window_seconds
        while history and history[0] <= cutoff:
            history.popleft()
        return history

    def _maybe_sweep(self, now_ts: float) -> None:
        """Evict buckets whose newest event has expired. Caller holds the lock."""
        self._ops += 1
        if self._ops % self._SWEEP_EVERY != 0:
            return
        cutoff = now_ts - self.window_seconds
        dead = [s for s, h in self.events.items() if not h or h[-1] <= cutoff]
        for s in dead:
            del self.events[s]

    def check(self, subject: str) -> None:
        now_ts = self._now_ts()
        with self._lock:
            history = self._prune(subject, now_ts)
            if len(history) >= self.limit:
                raise APIError(
                    429,
                    "rate_limit_exceeded",
                    self.message,
                    {"retry_after": self.window_seconds},
                )
            history.append(now_ts)
            self._maybe_sweep(now_ts)

    def track(self, subject: str, limit: int | None = None) -> int:
        """Record a request without enforcing the limit. Returns remaining count."""
        effective_limit = limit if limit is not None else self.limit
        now_ts = self._now_ts()
        with self._lock:
            history = self._prune(subject, now_ts)
            history.append(now_ts)
            self._maybe_sweep(now_ts)
            return max(0, effective_limit - len(history))

    def enforce(self, subject: str) -> int:
        """Record a request AND enforce the limit. Raises 429 when exceeded.
        Returns the remaining count for the X-RateLimit-Remaining header."""
        now_ts = self._now_ts()
        with self._lock:
            history = self._prune(subject, now_ts)
            if len(history) >= self.limit:
                raise APIError(
                    429,
                    "rate_limit_exceeded",
                    f"Rate limit exceeded: {self.limit} requests per {self.window_seconds}s. "
                    "The public instance is a sandbox — self-host for higher limits.",
                    {"retry_after": self.window_seconds},
                )
            history.append(now_ts)
            self._maybe_sweep(now_ts)
            return max(0, self.limit - len(history))
