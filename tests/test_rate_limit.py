"""Rate limiter: thread-safe, bounded, with accurate per-limiter messages."""

import threading
import unittest
from datetime import datetime, timezone

from backchannel.rate_limit import SlidingWindowRateLimiter
from backchannel.store import APIError


def _now():
    return datetime.now(timezone.utc)


class RateLimitTests(unittest.TestCase):
    def test_check_uses_custom_message(self):
        lim = SlidingWindowRateLimiter(1, 60, _now, message="custom mint message")
        lim.check("a")
        with self.assertRaises(APIError) as ctx:
            lim.check("a")
        self.assertEqual(ctx.exception.message, "custom mint message")

    def test_thread_safe_no_lost_updates(self):
        # 200 threads each record once under a high limit; with the lock every
        # append lands, so the bucket holds exactly 200.
        lim = SlidingWindowRateLimiter(10_000, 60, _now)
        barrier = threading.Barrier(50)

        def worker():
            barrier.wait()
            for _ in range(4):
                lim.track("shared")

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(lim.events["shared"]), 200)

    def test_idle_buckets_are_swept(self):
        # Many one-shot subjects, then enough ops to trip a sweep: dead buckets
        # are evicted so the dict doesn't grow unbounded.
        lim = SlidingWindowRateLimiter(5, 1, _now)
        lim._SWEEP_EVERY = 10
        for i in range(50):
            lim.track(f"oneshot-{i}")
        import time

        time.sleep(1.1)  # let the 1s window expire for all of them
        # drive ops on a fresh subject to trigger sweeps
        for _ in range(10):
            lim.track("driver")
        # all the idle one-shot buckets should be gone; driver remains
        leftover = [k for k in lim.events if k.startswith("oneshot-")]
        self.assertEqual(leftover, [])


if __name__ == "__main__":
    unittest.main()
