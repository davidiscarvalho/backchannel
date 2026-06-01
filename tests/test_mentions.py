"""Mentions → per-agent webhook push. A message can mention actors; a mentioned
agent that can read the channel and has registered a webhook is pushed a
'mention' event, rate-limited to 1/min per (channel, actor). Members-only:
non-readers are dropped (no leak, no push)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.http import create_app
from backchannel.store import BackchannelStore


class MentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "mention.db"
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: {
                "key-a": AuthContext(raw_key, "key_owner_a", "owner_a", "free"),
                "key-b": AuthContext(raw_key, "key_owner_b", "owner_b", "free"),
            }[raw_key]
        )
        self.app = create_app(
            db_path=self.db_path,
            now_provider=lambda: datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc),
            authenticator=self.authenticator,
        )
        self.store = BackchannelStore(self.db_path, now_provider=lambda: datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None, *, api_key: str = "key-a") -> tuple[int, dict]:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        raw_path, query = (path.split("?", 1) + [""])[:2]
        environ.update(REQUEST_METHOD=method, PATH_INFO=raw_path, QUERY_STRING=query,
                       CONTENT_LENGTH=str(len(body)), CONTENT_TYPE="application/json",
                       REMOTE_ADDR="127.0.0.1", HTTP_X_API_KEY=api_key)
        environ["wsgi.input"] = BytesIO(body)
        holder: dict[str, str] = {}
        def start_response(status, headers, exc_info=None): holder["status"] = status
        out = b"".join(self.app(environ, start_response)).decode("utf-8")
        return int(holder["status"].split()[0]), json.loads(out or "{}")

    def _mention_webhooks(self) -> int:
        with self.store.connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM pending_webhooks WHERE event_type = 'mention'").fetchone()["n"]

    def _setup_b_actor_with_webhook(self) -> str:
        _, actor = self.request("POST", "/v1/actors", {"name": "agent-b"}, api_key="key-b")
        status, _ = self.request("POST", f"/v1/actors/{actor['id']}/webhook", {"url": "https://hook.test/b"}, api_key="key-b")
        self.assertEqual(status, 200)
        return actor["id"]

    def test_webhook_registration_is_owner_only_and_masks_secret(self) -> None:
        _, actor = self.request("POST", "/v1/actors", {"name": "agent-b"}, api_key="key-b")
        # A cannot register on B's actor.
        status, body = self.request("POST", f"/v1/actors/{actor['id']}/webhook", {"url": "https://evil/x"}, api_key="key-a")
        self.assertEqual(status, 403, body)
        # B can; GET masks the secret.
        self.request("POST", f"/v1/actors/{actor['id']}/webhook", {"url": "https://hook.test/b", "secret": "s3cr3t"}, api_key="key-b")
        status, got = self.request("GET", f"/v1/actors/{actor['id']}/webhook", api_key="key-b")
        self.assertEqual(status, 200)
        self.assertTrue(got["has_secret"])
        self.assertNotIn("secret", got)

    def test_mention_of_member_pushes_webhook_and_records_mention(self) -> None:
        actor_b = self._setup_b_actor_with_webhook()
        _, ch = self.request("POST", "/v1/channels", {"name": "war-room", "mode": "claimable", "access": "restricted"})
        cid = ch["id"]
        self.request("POST", f"/v1/channels/{cid}/members", {"key_id": "key_owner_b"})  # B is a member
        status, posted = self.request("POST", f"/v1/channels/{cid}/messages", {"content": "@b take this", "mentions": [actor_b]})
        self.assertEqual(status, 201, posted)
        self.assertEqual([m["id"] for m in posted["message"]["mentions"]], [actor_b])
        self.assertEqual(self._mention_webhooks(), 1)

    def test_mention_of_non_member_is_dropped(self) -> None:
        actor_b = self._setup_b_actor_with_webhook()
        # Restricted channel where B was NOT added.
        _, ch = self.request("POST", "/v1/channels", {"name": "private", "mode": "claimable", "access": "restricted"})
        status, posted = self.request("POST", f"/v1/channels/{ch['id']}/messages", {"content": "x", "mentions": [actor_b]})
        self.assertEqual(status, 201, posted)
        self.assertEqual(posted["message"]["mentions"], [])
        self.assertEqual(self._mention_webhooks(), 0)

    def test_mention_push_is_rate_limited_per_channel_member(self) -> None:
        actor_b = self._setup_b_actor_with_webhook()
        _, ch = self.request("POST", "/v1/channels", {"name": "noisy", "mode": "broadcast", "access": "open"})
        cid = ch["id"]
        # Open channel → B can read, so mention is eligible. Two posts at the same
        # frozen instant → only one push (1/min per channel-member).
        self.request("POST", f"/v1/channels/{cid}/messages", {"content": "one", "mentions": [actor_b]})
        self.request("POST", f"/v1/channels/{cid}/messages", {"content": "two", "mentions": [actor_b]})
        self.assertEqual(self._mention_webhooks(), 1)


if __name__ == "__main__":
    unittest.main()
