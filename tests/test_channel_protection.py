from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.http import create_app


class FrozenClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self) -> datetime:
        return self.current

    def advance(self, *, minutes: int = 0, seconds: int = 0) -> None:
        self.current = self.current + timedelta(minutes=minutes, seconds=seconds)


class ChannelProtectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.clock = FrozenClock(datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc))
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: {
                "key-1": AuthContext(raw_key, "key_owner_1", "owner_1", "free"),
                "key-2": AuthContext(raw_key, "key_owner_2", "owner_2", "free"),
            }[raw_key]
        )
        self.app = create_app(
            db_path=Path(self.tempdir.name) / "test.db",
            now_provider=self.clock.now,
            authenticator=self.authenticator,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, method, path, payload=None, *, api_key="key-1"):
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        environ["REQUEST_METHOD"] = method
        raw_path, _, query = path.partition("?")
        environ["PATH_INFO"] = raw_path
        environ["QUERY_STRING"] = query
        environ["CONTENT_LENGTH"] = str(len(body))
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(body)
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key is not None:
            environ["HTTP_X_API_KEY"] = api_key
        holder: dict[str, object] = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        status = int(str(holder["status"]).split()[0])
        return status, (json.loads(out.decode("utf-8")) if out else {})

    def _channel(self, **extra):
        payload = {"name": "c", "mode": "broadcast"}
        payload.update(extra)
        status, channel = self.request("POST", "/v1/channels", payload)
        self.assertEqual(status, 201, channel)
        return channel

    # --- defaults -------------------------------------------------------

    def test_new_channel_has_no_limits_and_is_not_paused(self) -> None:
        channel = self._channel()
        self.assertIsNone(channel["max_messages"])
        self.assertIsNone(channel["max_writes_per_minute"])
        self.assertFalse(channel["paused"])

    # --- max_messages (ring buffer) ------------------------------------

    def test_max_messages_caps_the_channel_to_the_newest_n(self) -> None:
        channel = self._channel(max_messages=3)
        for i in range(5):
            self.request("POST", f"/v1/channels/{channel['id']}/messages", {"content": f"m{i}"})
        _, listing = self.request("GET", f"/v1/channels/{channel['id']}/messages?since=0")
        contents = [m["content"] for m in listing["data"]]
        self.assertEqual(contents, ["m2", "m3", "m4"])

    def test_create_channel_rejects_invalid_max_messages(self) -> None:
        status, body = self.request(
            "POST", "/v1/channels", {"name": "c", "mode": "broadcast", "max_messages": 0}
        )
        self.assertEqual(status, 422, body)
        self.assertEqual(body["error"], "invalid_max_messages")

    # --- max_writes_per_minute -----------------------------------------

    def test_write_rate_limit_rejects_excess_writes(self) -> None:
        channel = self._channel(max_writes_per_minute=2)
        for i in range(2):
            status, _ = self.request(
                "POST", f"/v1/channels/{channel['id']}/messages", {"content": f"m{i}"}
            )
            self.assertEqual(status, 201)
        status, body = self.request(
            "POST", f"/v1/channels/{channel['id']}/messages", {"content": "overflow"}
        )
        self.assertEqual(status, 429, body)
        self.assertEqual(body["error"], "channel_write_rate_exceeded")

    def test_write_rate_limit_window_recovers(self) -> None:
        channel = self._channel(max_writes_per_minute=2)
        for i in range(2):
            self.request("POST", f"/v1/channels/{channel['id']}/messages", {"content": f"m{i}"})
        self.clock.advance(seconds=61)
        status, _ = self.request(
            "POST", f"/v1/channels/{channel['id']}/messages", {"content": "fresh window"}
        )
        self.assertEqual(status, 201)

    # --- paused ---------------------------------------------------------

    def test_paused_channel_rejects_writes_but_allows_reads(self) -> None:
        channel = self._channel()
        self.request("POST", f"/v1/channels/{channel['id']}/messages", {"content": "before"})
        status, updated = self.request(
            "PATCH", f"/v1/channels/{channel['id']}", {"paused": True}
        )
        self.assertEqual(status, 200, updated)
        self.assertTrue(updated["paused"])

        status, body = self.request(
            "POST", f"/v1/channels/{channel['id']}/messages", {"content": "blocked"}
        )
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"], "channel_paused")

        # Reads are unaffected.
        status, listing = self.request("GET", f"/v1/channels/{channel['id']}/messages?since=0")
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["data"]), 1)

    def test_resumed_channel_accepts_writes_again(self) -> None:
        channel = self._channel()
        self.request("PATCH", f"/v1/channels/{channel['id']}", {"paused": True})
        self.request("PATCH", f"/v1/channels/{channel['id']}", {"paused": False})
        status, _ = self.request(
            "POST", f"/v1/channels/{channel['id']}/messages", {"content": "ok now"}
        )
        self.assertEqual(status, 201)

    # --- patch ----------------------------------------------------------

    def test_patch_updates_limits(self) -> None:
        channel = self._channel()
        status, updated = self.request(
            "PATCH",
            f"/v1/channels/{channel['id']}",
            {"max_messages": 50, "max_writes_per_minute": 10},
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["max_messages"], 50)
        self.assertEqual(updated["max_writes_per_minute"], 10)


if __name__ == "__main__":
    unittest.main()
