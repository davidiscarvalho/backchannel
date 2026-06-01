"""Phase 1 — crash safety. A leased claim whose holder crashes (never
heartbeats, never acks) must become claimable again, both when a new claimer
races in (in-request takeover) and proactively via the worker sweep
(reclaim_expired_leases). These make the OpenAPI 'auto-released on crash'
guarantee true."""
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
from backchannel.store import BackchannelStore


class FrozenClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self) -> datetime:
        return self.current

    def advance(self, *, minutes: int = 0, seconds: int = 0) -> None:
        self.current = self.current + timedelta(minutes=minutes, seconds=seconds)


class LeaseReclaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "lease.db"
        self.clock = FrozenClock(datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc))
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: {
                "key-a": AuthContext(raw_key, "key_owner_a", "owner_a", "free"),
                "key-b": AuthContext(raw_key, "key_owner_b", "owner_b", "free"),
            }[raw_key]
        )
        self.app = create_app(
            db_path=self.db_path,
            now_provider=self.clock.now,
            authenticator=self.authenticator,
        )
        # A second store handle on the same DB + clock to drive the worker sweep.
        self.store = BackchannelStore(self.db_path, now_provider=self.clock.now)

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

    def _post_task(self) -> tuple[str, str]:
        _, ch = self.request("POST", "/v1/channels", {"name": "jobs", "mode": "claimable"})
        _, m = self.request("POST", f"/v1/channels/{ch['id']}/messages", {"content": "do work"})
        return ch["id"], m["message"]["id"]

    def test_unexpired_lease_blocks_takeover(self) -> None:
        _cid, mid = self._post_task()
        status, _ = self.request("POST", f"/v1/messages/{mid}/claim-with-lease", {"lease_seconds": 60})
        self.assertEqual(status, 200)
        # Another agent tries immediately — lease is live, must be refused.
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "b"}, api_key="key-b")
        self.assertEqual(status, 409, body)

    def test_expired_lease_is_taken_over_by_another_agent(self) -> None:
        _cid, mid = self._post_task()
        status, _ = self.request("POST", f"/v1/messages/{mid}/claim-with-lease", {"lease_seconds": 60})
        self.assertEqual(status, 200)
        self.clock.advance(minutes=2)  # blow past the 60s lease
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "rescuer"}, api_key="key-b")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "claimed")
        self.assertEqual(body["message"]["claimed_by"]["name"], "rescuer")
        # Exclusivity must re-close after takeover: a third agent is refused.
        # This proves the takeover UPDATE cleared lease_expires_at — without it
        # C could steal work B is actively doing, and every other test still passes.
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "latecomer"}, api_key="key-a")
        self.assertEqual(status, 409, body)

    def test_worker_sweep_returns_expired_lease_to_unclaimed(self) -> None:
        cid, mid = self._post_task()
        self.request("POST", f"/v1/messages/{mid}/claim-with-lease", {"lease_seconds": 60})
        # Before expiry: not in the unclaimed set.
        _, listing = self.request("GET", f"/v1/channels/{cid}/messages?since=0&status=unclaimed")
        self.assertFalse(any(m["id"] == mid for m in listing["data"]))

        self.clock.advance(minutes=2)
        reclaimed = self.store.reclaim_expired_leases()
        self.assertEqual(reclaimed, 1)
        _, listing = self.request("GET", f"/v1/channels/{cid}/messages?since=0&status=unclaimed")
        self.assertTrue(any(m["id"] == mid for m in listing["data"]), "reclaimed message should be unclaimed again")

    def test_acked_message_is_never_reclaimed(self) -> None:
        _cid, mid = self._post_task()
        self.request("POST", f"/v1/messages/{mid}/claim-with-lease", {"lease_seconds": 60})
        status, _ = self.request("POST", f"/v1/messages/{mid}/ack", {})
        self.assertEqual(status, 200)
        self.clock.advance(minutes=2)
        # Sweep must skip acked work, and a takeover claim must be refused.
        self.assertEqual(self.store.reclaim_expired_leases(), 0)
        status, body = self.request("POST", f"/v1/messages/{mid}/claim", {"actor": "b"}, api_key="key-b")
        self.assertEqual(status, 409, body)


if __name__ == "__main__":
    unittest.main()
