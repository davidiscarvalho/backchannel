"""Phase 3 — discovery + request-to-join. A discoverable+restricted channel is
a findable lobby: any key can see it exists via GET /v1/channels, but must
request access (owner approves) before it can read. This resolves the
discovery-vs-secrecy paradox without handing out a free write capability."""
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


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "disco.db"
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

    def test_discoverable_restricted_lobby_request_approve_flow(self) -> None:
        _, ch = self.request("POST", "/v1/channels", {"name": "secret-lane", "mode": "claimable", "access": "restricted", "discoverable": True})
        cid = ch["id"]
        self.assertTrue(ch["discoverable"])
        self.request("POST", f"/v1/channels/{cid}/messages", {"content": "classified"})

        # B can discover the lobby but is not a member and cannot read it.
        status, page = self.request("GET", "/v1/channels", api_key="key-b")
        self.assertEqual(status, 200)
        found = [c for c in page["data"] if c["id"] == cid]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["is_member"])
        status, _ = self.request("GET", f"/v1/channels/{cid}/messages?since=0", api_key="key-b")
        self.assertEqual(status, 403)

        # B requests access; A sees it pending and approves.
        status, req = self.request("POST", f"/v1/channels/{cid}/access-requests", {"reason": "need to help"}, api_key="key-b")
        self.assertEqual(status, 202, req)
        self.assertEqual(req["status"], "pending")
        status, pending = self.request("GET", f"/v1/channels/{cid}/access-requests")
        self.assertEqual(status, 200)
        self.assertEqual(len(pending["data"]), 1)
        status, _ = self.request("POST", f"/v1/channels/{cid}/access-requests/{req['request_id']}/approve")
        self.assertEqual(status, 200)

        # B can now read.
        status, listing = self.request("GET", f"/v1/channels/{cid}/messages?since=0", api_key="key-b")
        self.assertEqual(status, 200, listing)
        self.assertEqual(len(listing["data"]), 1)

    def test_non_discoverable_channel_is_not_listed(self) -> None:
        _, ch = self.request("POST", "/v1/channels", {"name": "hidden", "mode": "claimable", "discoverable": False})
        status, page = self.request("GET", "/v1/channels", api_key="key-b")
        self.assertEqual(status, 200)
        self.assertFalse(any(c["id"] == ch["id"] for c in page["data"]))

    def test_restricted_channel_is_private_by_default(self) -> None:
        # No explicit discoverable + access=restricted => NOT enumerable by a
        # non-member, even on an instance whose default is discoverable=true.
        _, ch = self.request(
            "POST", "/v1/channels",
            {"name": "secret-room", "mode": "claimable", "access": "restricted"},
        )
        self.assertFalse(ch["discoverable"])
        status, page = self.request("GET", "/v1/channels", api_key="key-b")
        self.assertEqual(status, 200)
        self.assertFalse(any(c["id"] == ch["id"] for c in page["data"]))

    def test_restricted_channel_can_opt_into_discovery(self) -> None:
        # Explicit discoverable=true still makes a findable request-to-join lobby.
        _, ch = self.request(
            "POST", "/v1/channels",
            {"name": "open-lobby", "mode": "claimable", "access": "restricted", "discoverable": True},
        )
        self.assertTrue(ch["discoverable"])
        status, page = self.request("GET", "/v1/channels", api_key="key-b")
        self.assertTrue(any(c["id"] == ch["id"] for c in page["data"]))

    def test_access_request_on_open_channel_is_noop(self) -> None:
        _, ch = self.request("POST", "/v1/channels", {"name": "open-lane", "mode": "broadcast", "access": "open", "discoverable": True})
        status, result = self.request("POST", f"/v1/channels/{ch['id']}/access-requests", {}, api_key="key-b")
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "open")

    def test_preexisting_channel_backfills_as_not_discoverable(self) -> None:
        # A channel row that predates the discoverable column (migration
        # backfill) must NOT become listable — never retroactively expose
        # channels whose only protection was id-secrecy.
        store = BackchannelStore(self.db_path, now_provider=lambda: datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc))
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO channels (id, owner_key_id, owner_id, name, mode, access, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("legacy-1", "key_owner_a", "owner_a", "legacy", "broadcast", "open",
                 "2026-04-05T13:00:00+00:00", "2026-04-05T13:00:00+00:00"),
            )
            conn.commit()
        page = store.list_discoverable_channels(key_id="key_owner_a")
        self.assertFalse(any(c["id"] == "legacy-1" for c in page["data"]))

    def test_non_owner_cannot_approve_or_list(self) -> None:
        _, ch = self.request("POST", "/v1/channels", {"name": "lane", "mode": "claimable", "access": "restricted", "discoverable": True})
        cid = ch["id"]
        status, req = self.request("POST", f"/v1/channels/{cid}/access-requests", {}, api_key="key-b")
        self.assertEqual(status, 202)
        # B (not owner) cannot list or approve.
        status, _ = self.request("GET", f"/v1/channels/{cid}/access-requests", api_key="key-b")
        self.assertEqual(status, 403)
        status, _ = self.request("POST", f"/v1/channels/{cid}/access-requests/{req['request_id']}/approve", api_key="key-b")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
