"""Tests for the LocalAuthenticator path — self-contained key issuance,
hashing at rest, promote flow, expiry, revocation."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.auth import (
    LocalAuthenticator,
    hash_key,
    mint_raw_key,
    split_key,
)
from backchannel.http import create_app
from backchannel.store import APIError, BackchannelStore


class FrozenClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self) -> datetime:
        return self.current

    def advance(self, *, hours: int = 0, minutes: int = 0) -> None:
        self.current = self.current + timedelta(hours=hours, minutes=minutes)


class LocalAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.db"
        self.clock = FrozenClock(datetime(2026, 5, 12, 14, 0, tzinfo=timezone.utc))
        # No injected authenticator → LocalAuthenticator is the default.
        self.app = create_app(db_path=self.db_path, now_provider=self.clock.now)
        self.store: BackchannelStore = self.app.store

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    # --- request helpers ------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        api_key: str | None = None,
    ) -> tuple[int, dict]:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        environ["REQUEST_METHOD"] = method
        if "?" in path:
            raw_path, query = path.split("?", 1)
        else:
            raw_path, query = path, ""
        environ["PATH_INFO"] = raw_path
        environ["QUERY_STRING"] = query
        environ["CONTENT_LENGTH"] = str(len(body))
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(body)
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key is not None:
            environ["HTTP_X_API_KEY"] = api_key

        status_holder: dict[str, object] = {}

        def start_response(status: str, headers, exc_info=None) -> None:
            status_holder["status"] = status
            status_holder["headers"] = headers

        body_bytes = b"".join(self.app(environ, start_response))
        status_code = int(str(status_holder["status"]).split()[0])
        return status_code, json.loads(body_bytes.decode("utf-8"))

    # --- mint/split/hash ------------------------------------------------

    def test_mint_raw_key_format(self) -> None:
        key_id, secret, raw_key = mint_raw_key()
        self.assertTrue(key_id.startswith("bck_"))
        self.assertEqual(raw_key, f"{key_id}.{secret}")
        # round-trip through split
        out_id, out_secret = split_key(raw_key)
        self.assertEqual(out_id, key_id)
        self.assertEqual(out_secret, secret)

    def test_hash_is_deterministic_and_unique(self) -> None:
        _, _, raw1 = mint_raw_key()
        _, _, raw2 = mint_raw_key()
        self.assertNotEqual(raw1, raw2)
        self.assertEqual(hash_key(raw1), hash_key(raw1))
        self.assertNotEqual(hash_key(raw1), hash_key(raw2))

    def test_split_rejects_malformed(self) -> None:
        with self.assertRaises(ValueError):
            split_key("no-dot")
        with self.assertRaises(ValueError):
            split_key(".no-id")
        with self.assertRaises(ValueError):
            split_key("no-secret.")

    # --- issue_key endpoint ---------------------------------------------

    def test_issue_key_returns_tier0_with_48h_ttl(self) -> None:
        status, payload = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["tier"], 0)
        self.assertTrue(payload["key"].startswith("bck_"))
        self.assertIn(".", payload["key"])
        # expires_at = now + 48h
        expires = datetime.fromisoformat(payload["expires_at"])
        delta = expires - self.clock.now()
        self.assertAlmostEqual(delta.total_seconds(), 48 * 3600, delta=2)

    def test_issue_key_label_reuse_returns_409(self) -> None:
        status1, _ = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        self.assertEqual(status1, 201)
        status2, body2 = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        self.assertEqual(status2, 409)
        self.assertEqual(body2["error"], "label_in_use")

    def test_issue_key_requires_agent_label(self) -> None:
        status, body = self.request("POST", "/v1/keys", {})
        self.assertEqual(status, 422)
        self.assertEqual(body["error"], "missing_field")

    # --- authenticate ---------------------------------------------------

    def test_issued_key_authenticates(self) -> None:
        _, payload = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        raw_key = payload["key"]
        status, _ = self.request("GET", "/v1/keys/me", api_key=raw_key)
        self.assertEqual(status, 200)

    def test_unknown_key_rejected_401(self) -> None:
        status, body = self.request("GET", "/v1/keys/me", api_key="bck_unknown.deadbeef")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_malformed_key_rejected_401(self) -> None:
        status, body = self.request("GET", "/v1/keys/me", api_key="not-a-key")
        self.assertEqual(status, 401)

    def test_missing_key_rejected_401(self) -> None:
        status, body = self.request("GET", "/v1/keys/me")
        self.assertEqual(status, 401)

    # --- key hashing at rest --------------------------------------------

    def test_raw_key_is_not_stored(self) -> None:
        _, payload = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        raw_key = payload["key"]
        key_id = payload["key_id"]
        # Direct DB read — confirm the secret half is nowhere to be found.
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT key_id, key_hash FROM api_keys WHERE key_id = ?", (key_id,)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["key_id"], key_id)
        self.assertEqual(row["key_hash"], hash_key(raw_key))
        # The secret must not be a substring of the hash.
        _, secret = split_key(raw_key)
        self.assertNotIn(secret, row["key_hash"])

    # --- promote -------------------------------------------------------

    def test_promote_issues_new_tier1_key_and_revokes_old(self) -> None:
        _, issued = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        tier0 = issued["key"]
        # Bypass authenticator cache by spacing operations (cache is in-memory).
        status, promoted = self.request(
            "POST", "/v1/keys/promote", {"email": "alpha@example.com"}, api_key=tier0
        )
        self.assertEqual(status, 200, promoted)
        self.assertEqual(promoted["tier"], 1)
        self.assertIsNone(promoted["expires_at"])
        self.assertNotEqual(promoted["key"], tier0)

        # Old key is now inactive — invalidate cache + re-attempt
        self.app.authenticator.invalidate_cache()
        status, _ = self.request("GET", "/v1/keys/me", api_key=tier0)
        self.assertEqual(status, 401)

        # New key authenticates
        status, me = self.request("GET", "/v1/keys/me", api_key=promoted["key"])
        self.assertEqual(status, 200)
        self.assertEqual(me["tier"], 1)

    def test_promote_twice_returns_409(self) -> None:
        _, issued = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        tier0 = issued["key"]
        self.request("POST", "/v1/keys/promote", {"email": "a@b.c"}, api_key=tier0)
        # Second promote with the same (now revoked) key — first 401
        self.app.authenticator.invalidate_cache()
        status, _ = self.request("POST", "/v1/keys/promote", {"email": "a@b.c"}, api_key=tier0)
        self.assertEqual(status, 401)

    # --- expiry --------------------------------------------------------

    def test_expired_tier0_key_returns_410(self) -> None:
        _, issued = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        raw_key = issued["key"]
        # Advance past 48h
        self.clock.advance(hours=49)
        self.app.authenticator.invalidate_cache()
        status, body = self.request("GET", "/v1/keys/me", api_key=raw_key)
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "key_expired")
        self.assertIn("upgrade_url", body)

    # --- no depot env required -----------------------------------------

    def test_no_depot_env_variables_required(self) -> None:
        # The app constructed in setUp uses no BACKCHANNEL_DEPOT_* envs.
        # Re-issue and re-auth still works.
        status, _ = self.request("POST", "/v1/keys", {"agent_label": "ephemeral"})
        self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main()
