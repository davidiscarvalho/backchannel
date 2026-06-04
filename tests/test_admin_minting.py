"""Closeable / admin-gated key minting (Alt 1).

Public POST /v1/keys is open by default (the showroom relies on it), but a
private self-host can close it at runtime via POST /v1/admin/minting — then
public minting returns 403 and the operator issues keys via POST /v1/admin/keys.
The toggle is persisted (survives a store reopen), not an env/restart flag.
"""

import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app

ADMIN = "s3cret-admin-token"


def _req(app, method, path, body=None, headers=None):
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, REMOTE_ADDR="9.9.9.9")
    raw = json.dumps(body).encode() if body is not None else b""
    environ["wsgi.input"] = BytesIO(raw)
    environ["CONTENT_LENGTH"] = str(len(raw))
    environ["CONTENT_TYPE"] = "application/json"
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    holder = {}

    def start_response(status, hdrs, exc_info=None):
        holder["status"] = int(status.split()[0])

    chunks = b"".join(app(environ, start_response))
    parsed = json.loads(chunks.decode()) if chunks else {}
    return holder["status"], parsed


class AdminMintingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "m.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def _app(self, admin_token=ADMIN):
        with mock.patch.dict(os.environ, {"BACKCHANNEL_ADMIN_TOKEN": admin_token}, clear=False):
            os.environ.pop("BACKCHANNEL_BASE_URL", None)
            return create_app(db_path=self.db)

    def test_public_minting_open_by_default(self):
        app = self._app()
        status, body = _req(app, "POST", "/v1/keys", {"agent_label": "anyone"})
        self.assertEqual(status, 201)
        self.assertTrue(body["key"].startswith("bck_"))

    def test_closed_public_minting_returns_403(self):
        app = self._app()
        s, _ = _req(app, "POST", "/v1/admin/minting", {"enabled": False}, {"X-Admin-Token": ADMIN})
        self.assertEqual(s, 200)
        status, body = _req(app, "POST", "/v1/keys", {"agent_label": "stranger"})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "minting_closed")

    def test_admin_mint_works_even_when_closed(self):
        app = self._app()
        _req(app, "POST", "/v1/admin/minting", {"enabled": False}, {"X-Admin-Token": ADMIN})
        status, body = _req(app, "POST", "/v1/admin/keys", {"agent_label": "prod-worker"}, {"X-Admin-Token": ADMIN})
        self.assertEqual(status, 201)
        self.assertEqual(body["agent_label"], "prod-worker")
        self.assertTrue(body["key"].startswith("bck_"))

    def test_admin_endpoints_require_token(self):
        app = self._app()
        s1, _ = _req(app, "POST", "/v1/admin/keys", {"agent_label": "x"})  # no token
        self.assertEqual(s1, 401)
        s2, _ = _req(app, "POST", "/v1/admin/minting", {"enabled": True}, {"X-Admin-Token": "wrong"})
        self.assertEqual(s2, 401)

    def test_admin_disabled_when_no_token_configured(self):
        app = self._app(admin_token="")  # admin API off
        s, body = _req(app, "POST", "/v1/admin/keys", {"agent_label": "x"}, {"X-Admin-Token": "anything"})
        self.assertEqual(s, 403)
        self.assertEqual(body["error"], "admin_disabled")

    def test_toggle_persists_across_store_reopen(self):
        app = self._app()
        _req(app, "POST", "/v1/admin/minting", {"enabled": False}, {"X-Admin-Token": ADMIN})
        # New app on the same db file — setting must still be closed.
        app2 = self._app()
        status, body = _req(app2, "POST", "/v1/keys", {"agent_label": "later"})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "minting_closed")

    def test_health_reports_minting_state(self):
        app = self._app()
        _, h1 = _req(app, "GET", "/health")
        self.assertTrue(h1["public_minting_enabled"])
        _req(app, "POST", "/v1/admin/minting", {"enabled": False}, {"X-Admin-Token": ADMIN})
        _, h2 = _req(app, "GET", "/health")
        self.assertFalse(h2["public_minting_enabled"])


if __name__ == "__main__":
    unittest.main()
