from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app
from backchannel.store import (
    HEARTBEAT_BOT_LABEL,
    SANDBOX_CHANNEL_ALIAS,
    BackchannelStore,
)


class FrozenClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self) -> datetime:
        return self.current

    def advance(self, *, hours: int = 0, minutes: int = 0, seconds: int = 0) -> None:
        self.current = self.current + timedelta(hours=hours, minutes=minutes, seconds=seconds)


class SandboxProvisioningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = FrozenClock(datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc))
        self.store = BackchannelStore(
            Path(self.tempdir.name) / "test.db", now_provider=self.clock.now
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ensure_heartbeat_bot_key_mints_a_labelled_key(self) -> None:
        bot_key_id = self.store.ensure_heartbeat_bot_key()
        self.assertTrue(bot_key_id)
        record = self.store.get_api_key_record(bot_key_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["agent_label"], HEARTBEAT_BOT_LABEL)

    def test_ensure_heartbeat_bot_key_is_idempotent(self) -> None:
        first = self.store.ensure_heartbeat_bot_key()
        second = self.store.ensure_heartbeat_bot_key()
        self.assertEqual(first, second)

    def test_ensure_sandbox_channel_creates_open_broadcast_channel(self) -> None:
        bot_key_id = self.store.ensure_heartbeat_bot_key()
        channel_id = self.store.ensure_sandbox_channel(owner_key_id=bot_key_id)
        channel = self.store.get_channel(SANDBOX_CHANNEL_ALIAS, key_id=bot_key_id)
        self.assertEqual(channel["id"], channel_id)
        self.assertEqual(channel["mode"], "broadcast")
        self.assertEqual(channel["access"], "open")
        self.assertIn(SANDBOX_CHANNEL_ALIAS, channel["aliases"])

    def test_ensure_sandbox_channel_is_idempotent(self) -> None:
        bot_key_id = self.store.ensure_heartbeat_bot_key()
        first = self.store.ensure_sandbox_channel(owner_key_id=bot_key_id)
        second = self.store.ensure_sandbox_channel(owner_key_id=bot_key_id)
        self.assertEqual(first, second)

    def test_ensure_sandbox_channel_applies_abuse_limits(self) -> None:
        bot_key_id = self.store.ensure_heartbeat_bot_key()
        self.store.ensure_sandbox_channel(
            owner_key_id=bot_key_id, ttl_seconds=600, max_messages=150, max_writes_per_minute=42
        )
        channel = self.store.get_channel(SANDBOX_CHANNEL_ALIAS, key_id=bot_key_id)
        self.assertEqual(channel["ttl_seconds"], 600)
        self.assertEqual(channel["max_messages"], 150)
        self.assertEqual(channel["max_writes_per_minute"], 42)

    def test_ensure_sandbox_channel_refreshes_limits_on_restart(self) -> None:
        bot_key_id = self.store.ensure_heartbeat_bot_key()
        self.store.ensure_sandbox_channel(owner_key_id=bot_key_id, max_messages=100)
        # A later call (a worker restart with retuned env) updates the limits.
        self.store.ensure_sandbox_channel(owner_key_id=bot_key_id, max_messages=500)
        channel = self.store.get_channel(SANDBOX_CHANNEL_ALIAS, key_id=bot_key_id)
        self.assertEqual(channel["max_messages"], 500)

    def test_ensure_sandbox_channel_does_not_clear_pause_on_restart(self) -> None:
        bot_key_id = self.store.ensure_heartbeat_bot_key()
        channel_id = self.store.ensure_sandbox_channel(owner_key_id=bot_key_id)
        self.store.set_channel_paused(channel_id, True)
        # A worker restart re-runs ensure_sandbox_channel — the operator's
        # kill switch must survive it.
        self.store.ensure_sandbox_channel(owner_key_id=bot_key_id)
        channel = self.store.get_channel(SANDBOX_CHANNEL_ALIAS, key_id=bot_key_id)
        self.assertTrue(channel["paused"])


class HeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = FrozenClock(datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc))
        self.store = BackchannelStore(
            Path(self.tempdir.name) / "test.db", now_provider=self.clock.now
        )
        self.bot_key_id = self.store.ensure_heartbeat_bot_key()
        self.channel_id = self.store.ensure_sandbox_channel(owner_key_id=self.bot_key_id)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _messages(self) -> list[dict]:
        return self.store.list_messages(
            self.channel_id, since=None, limit=100, key_id=self.bot_key_id
        )["data"]

    def test_heartbeat_posts_when_channel_is_empty(self) -> None:
        posted = self.store.post_sandbox_heartbeat_if_quiet(self.channel_id, self.bot_key_id)
        self.assertTrue(posted)
        messages = self._messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["actor_label"], HEARTBEAT_BOT_LABEL)

    def test_heartbeat_skips_when_a_recent_message_exists(self) -> None:
        self.store.create_message(
            self.channel_id, {"content": "hello", "actor_label": "visitor"}, key_id="visitor-key"
        )
        posted = self.store.post_sandbox_heartbeat_if_quiet(self.channel_id, self.bot_key_id)
        self.assertFalse(posted)
        self.assertEqual(len(self._messages()), 1)

    def test_heartbeat_skips_before_60_seconds_of_quiet(self) -> None:
        self.store.create_message(
            self.channel_id, {"content": "hello", "actor_label": "visitor"}, key_id="visitor-key"
        )
        self.clock.advance(seconds=59)
        posted = self.store.post_sandbox_heartbeat_if_quiet(self.channel_id, self.bot_key_id)
        self.assertFalse(posted)

    def test_heartbeat_fires_after_60_seconds_of_quiet(self) -> None:
        self.store.create_message(
            self.channel_id, {"content": "hello", "actor_label": "visitor"}, key_id="visitor-key"
        )
        self.clock.advance(seconds=61)
        posted = self.store.post_sandbox_heartbeat_if_quiet(self.channel_id, self.bot_key_id)
        self.assertTrue(posted)
        self.assertEqual(len(self._messages()), 2)

    def test_heartbeat_resets_the_quiet_window(self) -> None:
        # An empty channel gets one heartbeat; a second call right after is quiet for 0s.
        self.assertTrue(
            self.store.post_sandbox_heartbeat_if_quiet(self.channel_id, self.bot_key_id)
        )
        self.assertFalse(
            self.store.post_sandbox_heartbeat_if_quiet(self.channel_id, self.bot_key_id)
        )


class SandboxDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _get_text(self, path: str) -> str:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = "GET"
        environ["PATH_INFO"] = path
        environ["QUERY_STRING"] = ""
        environ["wsgi.input"] = BytesIO(b"")
        environ["REMOTE_ADDR"] = "127.0.0.1"
        captured: dict[str, object] = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status

        body = b"".join(self.app(environ, start_response))
        return body.decode("utf-8")

    def test_llms_txt_mentions_the_sandbox_channel(self) -> None:
        self.assertIn("sandbox", self._get_text("/llms.txt"))

    def test_landing_page_mentions_the_sandbox_channel(self) -> None:
        self.assertIn("sandbox", self._get_text("/"))


if __name__ == "__main__":
    unittest.main()
