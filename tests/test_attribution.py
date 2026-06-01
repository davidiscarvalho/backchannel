"""Phase 2 — trustworthy attribution. A key may only act as actors its own
owner registered; resolving another owner's actor by id or alias is rejected
(403 actor_forbidden). Claims also record the server-verified key
(claimed_by_key_id) alongside the self-asserted claimed_by label."""
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


class AttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "attr.db"
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

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None, *, api_key: str = "key-a") -> tuple[int, dict]:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        raw_path, query = (path.split("?", 1) + [""])[:2]
        environ.update(
            REQUEST_METHOD=method,
            PATH_INFO=raw_path,
            QUERY_STRING=query,
            CONTENT_LENGTH=str(len(body)),
            CONTENT_TYPE="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_API_KEY=api_key,
        )
        environ["wsgi.input"] = BytesIO(body)
        holder: dict[str, str] = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response)).decode("utf-8")
        return int(holder["status"].split()[0]), json.loads(out or "{}")

    def _open_task(self) -> str:
        # Key A owns an open channel + a message; key B (different owner) can reach it.
        _, ch = self.request("POST", "/v1/channels", {"name": "jobs", "mode": "claimable"})
        _, m = self.request("POST", f"/v1/channels/{ch['id']}/messages", {"content": "work"})
        return m["message"]["id"]

    def test_cannot_claim_as_another_owners_actor_by_id(self) -> None:
        _, actor = self.request("POST", "/v1/actors", {"name": "agent-a"})  # owned by key A
        mid = self._open_task()
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": actor["id"]}, api_key="key-b")
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "actor_forbidden")

    def test_cannot_claim_as_another_owners_actor_by_alias(self) -> None:
        _, actor = self.request("POST", "/v1/actors", {"name": "agent-a"})
        self.request("POST", f"/v1/actors/{actor['id']}/aliases", {"alias": "the-a-agent"})
        mid = self._open_task()
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "the-a-agent"}, api_key="key-b")
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "actor_forbidden")

    def test_claim_records_verified_key_id(self) -> None:
        mid = self._open_task()
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "worker"}, api_key="key-b")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["message"]["claimed_by_key_id"], "key_owner_b")

    def test_plain_name_still_auto_creates_under_caller(self) -> None:
        mid = self._open_task()
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "brand-new-name"}, api_key="key-b")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["message"]["claimed_by"]["name"], "brand-new-name")


if __name__ == "__main__":
    unittest.main()
