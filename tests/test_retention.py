from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from wsgiref.util import setup_testing_defaults

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.http import create_app
from backchannel.store import BackchannelStore


class FrozenClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self) -> datetime:
        return self.current

    def advance(self, *, days: int = 0, hours: int = 0, minutes: int = 0, seconds: int = 0) -> None:
        self.current = self.current + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.db"
        self.clock = FrozenClock(datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc))
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: {
                "key-1": AuthContext(raw_key, "key_owner_1", "owner_1", "free"),
                "key-2": AuthContext(raw_key, "key_owner_2", "owner_2", "free"),
            }[raw_key]
        )
        self.app = create_app(
            db_path=self.db_path,
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

        body_out = b"".join(self.app(environ, start_response))
        status = int(str(holder["status"]).split()[0])
        return status, json.loads(body_out.decode("utf-8")) if body_out else {}

    def _cleanup(self) -> None:
        BackchannelStore(self.db_path, now_provider=self.clock.now).archive_and_cleanup_expired_records()

    # --- retention_days field ------------------------------------------

    def test_channel_defaults_to_7_day_retention(self) -> None:
        status, channel = self.request("POST", "/v1/channels", {"name": "c", "mode": "broadcast"})
        self.assertEqual(status, 201)
        self.assertEqual(channel["retention_days"], 7)

    def test_create_channel_accepts_custom_retention_days(self) -> None:
        status, channel = self.request(
            "POST", "/v1/channels", {"name": "c", "mode": "broadcast", "retention_days": 30}
        )
        self.assertEqual(status, 201)
        self.assertEqual(channel["retention_days"], 30)

    def test_create_channel_rejects_out_of_range_retention_days(self) -> None:
        for bad in (0, 366):
            status, body = self.request(
                "POST", "/v1/channels", {"name": "c", "mode": "broadcast", "retention_days": bad}
            )
            self.assertEqual(status, 422, body)
            self.assertEqual(body["error"], "invalid_retention_days")

    def test_patch_channel_updates_retention_days(self) -> None:
        _, channel = self.request("POST", "/v1/channels", {"name": "c", "mode": "broadcast"})
        status, updated = self.request(
            "PATCH", f"/v1/channels/{channel['id']}", {"retention_days": 14}
        )
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["retention_days"], 14)

    # --- /history lifecycle --------------------------------------------

    def _make_channel(self, retention_days=7, ttl_seconds=300):
        _, channel = self.request(
            "POST",
            "/v1/channels",
            {"name": "c", "mode": "broadcast", "ttl_seconds": ttl_seconds, "retention_days": retention_days},
        )
        return channel["id"]

    def test_history_is_empty_while_message_is_live(self) -> None:
        channel_id = self._make_channel()
        self.request("POST", f"/v1/channels/{channel_id}/messages", {"content": "live"})
        status, body = self.request("GET", f"/v1/channels/{channel_id}/history")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"], [])

    def test_history_returns_message_after_expiry_and_archival(self) -> None:
        channel_id = self._make_channel(retention_days=7, ttl_seconds=300)
        self.request("POST", f"/v1/channels/{channel_id}/messages", {"content": "archive me"})
        # Expire the message, then run the cleanup worker to archive it.
        self.clock.advance(minutes=6)
        self._cleanup()
        # Live listing no longer shows it...
        _, live = self.request("GET", f"/v1/channels/{channel_id}/messages?since=0")
        self.assertEqual(live["data"], [])
        # ...but /history does, within the retention window.
        status, body = self.request("GET", f"/v1/channels/{channel_id}/history")
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["content"], "archive me")

    def test_history_message_is_purged_after_retention_window(self) -> None:
        channel_id = self._make_channel(retention_days=1, ttl_seconds=300)
        self.request("POST", f"/v1/channels/{channel_id}/messages", {"content": "transient"})
        self.clock.advance(minutes=6)
        self._cleanup()
        # Visible within retention.
        _, body = self.request("GET", f"/v1/channels/{channel_id}/history")
        self.assertEqual(len(body["data"]), 1)
        # Past the 1-day window, the next cleanup purges the archived row.
        self.clock.advance(days=2)
        self._cleanup()
        _, body = self.request("GET", f"/v1/channels/{channel_id}/history")
        self.assertEqual(body["data"], [])

    def test_history_returns_newest_first_and_paginates(self) -> None:
        channel_id = self._make_channel(retention_days=7, ttl_seconds=300)
        for i in range(3):
            self.request("POST", f"/v1/channels/{channel_id}/messages", {"content": f"m{i}"})
            self.clock.advance(seconds=10)
        self.clock.advance(minutes=6)
        self._cleanup()
        status, page1 = self.request("GET", f"/v1/channels/{channel_id}/history?limit=2")
        self.assertEqual(status, 200, page1)
        self.assertEqual([m["content"] for m in page1["data"]], ["m2", "m1"])
        _, page2 = self.request(
            "GET",
            f"/v1/channels/{channel_id}/history?limit=2&cursor={quote(page1['next_cursor'])}",
        )
        self.assertEqual([m["content"] for m in page2["data"]], ["m0"])

    def test_history_enforces_channel_access(self) -> None:
        _, channel = self.request(
            "POST", "/v1/channels", {"name": "c", "mode": "broadcast", "access": "restricted"}
        )
        status, body = self.request(
            "GET", f"/v1/channels/{channel['id']}/history", api_key="key-2"
        )
        self.assertEqual(status, 403, body)


if __name__ == "__main__":
    unittest.main()
