"""Tests for the security audit log (E6)."""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app
from backchannel.store import BackchannelStore


class SecurityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        self.store: BackchannelStore = self.app.store

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _req(self, method: str, path: str, *, body: dict | None = None, api_key: str | None = None):
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = method
        environ["PATH_INFO"] = path
        environ["QUERY_STRING"] = ""
        payload = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        environ["CONTENT_LENGTH"] = str(len(payload))
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(payload)
        environ["REMOTE_ADDR"] = "203.0.113.7"
        if api_key:
            environ["HTTP_X_API_KEY"] = api_key
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        return int(str(holder["status"]).split()[0]), json.loads(out.decode("utf-8"))

    def test_issue_key_records_audit_event(self) -> None:
        status, body = self._req("POST", "/v1/keys", body={"agent_label": "audit-1"})
        self.assertEqual(status, 201)
        events = self.store.list_security_events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["event_type"], "key.issue.tier0")
        self.assertEqual(ev["subject_key_id"], body["key_id"])
        self.assertEqual(ev["remote_addr"], "203.0.113.7")
        self.assertEqual(ev["detail"]["agent_label"], "audit-1")

    def test_promote_key_records_audit_event(self) -> None:
        _, issued = self._req("POST", "/v1/keys", body={"agent_label": "audit-promote"})
        _, promoted = self._req(
            "POST", "/v1/keys/promote",
            body={"email": "a@b.c"}, api_key=issued["key"],
        )
        events = self.store.list_security_events()
        event_types = [e["event_type"] for e in events]
        self.assertIn("key.promote", event_types)
        promote_ev = next(e for e in events if e["event_type"] == "key.promote")
        self.assertEqual(promote_ev["actor_key_id"], issued["key_id"])
        self.assertEqual(promote_ev["subject_key_id"], promoted["key_id"])
        self.assertEqual(promote_ev["detail"]["email"], "a@b.c")

    def test_security_audit_endpoint_scopes_to_caller(self) -> None:
        # Two separate keys; each should only see its own events.
        _, k1 = self._req("POST", "/v1/keys", body={"agent_label": "audit-a"})
        _, k2 = self._req("POST", "/v1/keys", body={"agent_label": "audit-b"})
        status, page = self._req("GET", "/v1/security/audit", api_key=k1["key"])
        self.assertEqual(status, 200)
        # k1 sees the event where it is the subject (its own issuance)
        self.assertTrue(any(e["subject_key_id"] == k1["key_id"] for e in page["data"]))
        # k1 must NOT see k2's issuance event
        self.assertFalse(any(e["subject_key_id"] == k2["key_id"] for e in page["data"]))


if __name__ == "__main__":
    unittest.main()
