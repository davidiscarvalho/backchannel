"""Bounded long-poll: GET messages can block until a new message arrives or the
(capped) wait elapses, via an in-process condition signalled by createMessage.
Opt-in + waiter-capped. Tests use real wall-clock blocking, kept sub-second."""
from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

from backchannel.store import BackchannelStore

OWNER = "o1"
KEY = "k1"


class LongPollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = BackchannelStore(Path(self.tempdir.name) / "lp.db")
        # Enable long-poll deterministically (don't depend on env).
        self.store._longpoll_enabled = True
        self.store._longpoll_max_wait = 5.0
        self.store._longpoll_sem = threading.BoundedSemaphore(8)
        ch = self.store.create_channel({"name": "lp", "mode": "broadcast"}, owner_id=OWNER, key_id=KEY)
        self.cid = ch["id"]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _post(self, content: str) -> None:
        self.store.create_message(self.cid, {"content": content, "actor_label": "p"}, key_id=KEY, owner_id=OWNER)

    def _list(self, wait=None, since="0"):
        return self.store.list_messages(self.cid, since, 50, key_id=KEY, owner_id=OWNER, wait=wait)

    def test_disabled_flag_returns_immediately(self) -> None:
        self.store._longpoll_enabled = False
        t0 = time.monotonic()
        res = self._list(wait=5)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(res["data"], [])

    def test_waiter_wakes_on_concurrent_post(self) -> None:
        def delayed_post():
            time.sleep(0.2)
            self._post("hello")
        threading.Thread(target=delayed_post, daemon=True).start()
        t0 = time.monotonic()
        res = self._list(wait=3)
        elapsed = time.monotonic() - t0
        self.assertEqual(len(res["data"]), 1, res)
        self.assertEqual(res["data"][0]["content"], "hello")
        self.assertLess(elapsed, 2.0, "should wake on the post, not wait the full cap")

    def test_timeout_returns_empty(self) -> None:
        t0 = time.monotonic()
        res = self._list(wait=0.4)
        elapsed = time.monotonic() - t0
        self.assertEqual(res["data"], [])
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertLess(elapsed, 1.5)

    def test_existing_message_returns_without_waiting(self) -> None:
        self._post("already here")
        t0 = time.monotonic()
        res = self._list(wait=5)
        self.assertLess(time.monotonic() - t0, 0.5, "must not block when data already present")
        self.assertEqual(len(res["data"]), 1)

    def test_cursor_gap_is_not_missed(self) -> None:
        # Post, capture its cursor=0 (before), then long-poll with the pre-post
        # cursor — check-then-wait must return it immediately, never block.
        self._post("gap")
        t0 = time.monotonic()
        res = self._list(wait=5, since="0")
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(len(res["data"]), 1)

    def test_at_capacity_degrades_to_immediate(self) -> None:
        self.store._longpoll_sem = threading.BoundedSemaphore(1)
        # First waiter holds the only slot for a bit.
        holder_done = threading.Event()

        def holder():
            self._list(wait=1.0)  # empty channel → holds the slot ~1s
            holder_done.set()
        threading.Thread(target=holder, daemon=True).start()
        time.sleep(0.25)  # ensure the holder acquired the slot
        t0 = time.monotonic()
        res = self._list(wait=3)  # no slot available → must return immediately
        self.assertLess(time.monotonic() - t0, 0.6, "second waiter should not block at capacity")
        self.assertEqual(res["data"], [])
        holder_done.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
