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
    hash_key,
    mint_raw_key,
    split_key,
)
from backchannel.http import create_app
from backchannel.store import BackchannelStore


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

    def test_issue_key_is_permanent(self) -> None:
        status, payload = self.request("POST", "/v1/keys", {"agent_label": "worker-alpha"})
        self.assertEqual(status, 201, payload)
        self.assertTrue(payload["key"].startswith("bck_"))
        self.assertIn(".", payload["key"])
        # Keys are permanent now — no expiry, no tiers.
        self.assertIsNone(payload["expires_at"])
        self.assertIn("rate_limit", payload)

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

    # --- no depot env required -----------------------------------------

    def test_no_depot_env_variables_required(self) -> None:
        # The app constructed in setUp uses no external auth service.
        status, _ = self.request("POST", "/v1/keys", {"agent_label": "ephemeral"})
        self.assertEqual(status, 201)

    def test_delete_keys_me_revokes(self) -> None:
        """DELETE /v1/keys/me revokes the calling key; subsequent use returns 401."""
        status, data = self.request("POST", "/v1/keys", {"agent_label": "to-revoke"})
        self.assertEqual(status, 201)
        key = data["key"]

        # Key works before revocation
        status, data = self.request("GET", "/v1/keys/me", api_key=key)
        self.assertEqual(status, 200)
        self.assertTrue(data["active"])

        # Revoke
        status, data = self.request("DELETE", "/v1/keys/me", api_key=key)
        self.assertEqual(status, 200)
        self.assertTrue(data["revoked"])

        # Key no longer works
        status, data = self.request("GET", "/v1/keys/me", api_key=key)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
