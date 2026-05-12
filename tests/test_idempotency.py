"""Tests for idempotency-by-default (B6)."""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        # Mint a fresh key for these tests.
        status, body, _ = self._request("POST", "/v1/keys", body={"agent_label": "idem-tests"})
        self.assertEqual(status, 201)
        self.key = body["key"]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        api_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, dict, dict]:
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = method
        environ["PATH_INFO"] = path
        environ["QUERY_STRING"] = ""
        payload = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        environ["CONTENT_LENGTH"] = str(len(payload))
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(payload)
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key is not None:
            environ["HTTP_X_API_KEY"] = api_key
        if idempotency_key is not None:
            environ["HTTP_IDEMPOTENCY_KEY"] = idempotency_key
        holder: dict = {}

        def start_response(status: str, headers, exc_info=None):
            holder["status"] = status
            holder["headers"] = headers

        body_bytes = b"".join(self.app(environ, start_response))
        status_code = int(str(holder["status"]).split()[0])
        headers = {k: v for k, v in holder["headers"]}
        return status_code, json.loads(body_bytes.decode("utf-8")), headers

    # --- explicit Idempotency-Key (pre-existing behavior) -------------

    def test_explicit_key_replays_response(self) -> None:
        status, first, _ = self._request(
            "POST",
            "/v1/channels",
            body={"name": "explicit-idem", "mode": "broadcast"},
            api_key=self.key,
            idempotency_key="explicit-key-1",
        )
        self.assertEqual(status, 201)

        status, second, hdrs = self._request(
            "POST",
            "/v1/channels",
            body={"name": "DIFFERENT-NAME-IGNORED", "mode": "broadcast"},
            api_key=self.key,
            idempotency_key="explicit-key-1",
        )
        self.assertEqual(status, 201)
        # Same channel id is returned even though body differs — that's what
        # an explicit idempotency key promises.
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(hdrs.get("X-Idempotent-Replay"), "true")
        self.assertEqual(hdrs.get("X-Idempotency-Source"), "client")

    # --- auto idempotency (B6) ---------------------------------------

    def test_auto_idempotency_same_request_replays(self) -> None:
        body = {"name": "auto-1", "mode": "broadcast"}
        status, first, h1 = self._request("POST", "/v1/channels", body=body, api_key=self.key)
        self.assertEqual(status, 201)
        # Repeat the exact same request → should replay
        status, second, h2 = self._request("POST", "/v1/channels", body=body, api_key=self.key)
        self.assertEqual(status, 201)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(h2.get("X-Idempotent-Replay"), "true")
        self.assertEqual(h2.get("X-Idempotency-Source"), "server-auto")
        # The first response also gets X-Idempotency-Source on the cache write
        self.assertEqual(h1.get("X-Idempotency-Source"), "server-auto")

    def test_auto_idempotency_different_body_is_distinct(self) -> None:
        status, first, _ = self._request(
            "POST", "/v1/channels", body={"name": "alpha", "mode": "broadcast"}, api_key=self.key
        )
        self.assertEqual(status, 201)
        status, second, hdrs = self._request(
            "POST", "/v1/channels", body={"name": "beta", "mode": "broadcast"}, api_key=self.key
        )
        self.assertEqual(status, 201)
        self.assertNotEqual(first["id"], second["id"])
        # No replay header — they're independent requests
        self.assertNotEqual(hdrs.get("X-Idempotent-Replay"), "true")

    def test_auto_skipped_on_ack_claim_release(self) -> None:
        """ack/claim/release/heartbeat routes are excluded from auto-idempotency
        so their already_X application-level responses still surface."""
        # Setup: claimable channel + message + actor
        _, ch, _ = self._request(
            "POST", "/v1/channels", body={"name": "ack-test", "mode": "claimable"},
            api_key=self.key,
        )
        _, msg, _ = self._request(
            "POST", f"/v1/channels/{ch['id']}/messages", body={"content": "task"},
            api_key=self.key,
        )
        _, actor, _ = self._request(
            "POST", "/v1/actors", body={"name": "worker-x"}, api_key=self.key,
        )
        # First claim
        status, claimed, _ = self._request(
            "POST", f"/v1/messages/{msg['message']['id']}/claim",
            body={"actor": actor["id"]}, api_key=self.key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(claimed["status"], "claimed")
        # First ack
        status, acked, _ = self._request(
            "POST", f"/v1/messages/{msg['message']['id']}/ack",
            body={"actor": actor["id"]}, api_key=self.key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(acked["status"], "acknowledged")
        # Second ack with identical body — would replay if auto-idempotent.
        # We expect the application's already_acknowledged response instead.
        status, dup, hdrs = self._request(
            "POST", f"/v1/messages/{msg['message']['id']}/ack",
            body={"actor": actor["id"]}, api_key=self.key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(dup["status"], "already_acknowledged")
        self.assertNotEqual(hdrs.get("X-Idempotent-Replay"), "true")

    def test_auto_idempotency_not_applied_to_get(self) -> None:
        # GET is not a mutation; no idempotency machinery fires.
        status, _, hdrs = self._request("GET", "/v1/keys/me", api_key=self.key)
        self.assertEqual(status, 200)
        self.assertNotIn("X-Idempotency-Source", hdrs)


if __name__ == "__main__":
    unittest.main()
