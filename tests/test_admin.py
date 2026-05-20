from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.http import create_app

ADMIN_TOKEN = "test-admin-secret-123"


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current


class _Harness(unittest.TestCase):
    admin_token_env: str | None = ADMIN_TOKEN

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        if self.admin_token_env is not None:
            os.environ["BACKCHANNEL_ADMIN_TOKEN"] = self.admin_token_env
            self.addCleanup(os.environ.pop, "BACKCHANNEL_ADMIN_TOKEN", None)
        else:
            os.environ.pop("BACKCHANNEL_ADMIN_TOKEN", None)
        self.clock = _Clock()
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: {
                "key-1": AuthContext(raw_key, "key_owner_1", "owner_1", "free"),
            }[raw_key]
        )
        self.app = create_app(
            db_path=Path(self.tempdir.name) / "test.db",
            now_provider=self.clock.now,
            authenticator=self.authenticator,
        )
        self.addCleanup(self.tempdir.cleanup)

    def request(self, method, path, payload=None, *, api_key=None, admin_token=None):
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
        if admin_token is not None:
            environ["HTTP_X_ADMIN_TOKEN"] = admin_token
        holder: dict[str, object] = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        status = int(str(holder["status"]).split()[0])
        return status, (json.loads(out.decode("utf-8")) if out else {})


class AdminKillSwitchTests(_Harness):
    def _channel(self, **extra):
        payload = {"name": "c", "mode": "broadcast"}
        payload.update(extra)
        status, channel = self.request("POST", "/v1/channels", payload, api_key="key-1")
        self.assertEqual(status, 201, channel)
        return channel

    def test_admin_pause_blocks_writes_resume_restores(self) -> None:
        channel = self._channel()
        status, paused = self.request(
            "POST", f"/v1/admin/channels/{channel['id']}/pause", admin_token=ADMIN_TOKEN
        )
        self.assertEqual(status, 200, paused)
        self.assertTrue(paused["paused"])

        status, body = self.request(
            "POST", f"/v1/channels/{channel['id']}/messages", {"content": "x"}, api_key="key-1"
        )
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"], "channel_paused")

        status, resumed = self.request(
            "POST", f"/v1/admin/channels/{channel['id']}/resume", admin_token=ADMIN_TOKEN
        )
        self.assertEqual(status, 200, resumed)
        self.assertFalse(resumed["paused"])
        status, _ = self.request(
            "POST", f"/v1/channels/{channel['id']}/messages", {"content": "x"}, api_key="key-1"
        )
        self.assertEqual(status, 201)

    def test_admin_can_pause_by_alias(self) -> None:
        channel = self._channel()
        self.request(
            "POST", f"/v1/channels/{channel['id']}/aliases", {"alias": "my-alias"}, api_key="key-1"
        )
        status, paused = self.request(
            "POST", "/v1/admin/channels/my-alias/pause", admin_token=ADMIN_TOKEN
        )
        self.assertEqual(status, 200, paused)
        self.assertTrue(paused["paused"])

    def test_admin_pauses_restricted_channel_without_ownership(self) -> None:
        # A restricted channel owned by key-1; the admin holds no API key
        # at all yet can still pause it — the kill switch bypasses ownership.
        channel = self._channel(access="restricted")
        status, paused = self.request(
            "POST", f"/v1/admin/channels/{channel['id']}/pause", admin_token=ADMIN_TOKEN
        )
        self.assertEqual(status, 200, paused)
        self.assertTrue(paused["paused"])

    def test_wrong_token_is_rejected(self) -> None:
        channel = self._channel()
        status, body = self.request(
            "POST", f"/v1/admin/channels/{channel['id']}/pause", admin_token="wrong"
        )
        self.assertEqual(status, 401, body)
        self.assertEqual(body["error"], "admin_unauthorized")

    def test_missing_token_is_rejected(self) -> None:
        channel = self._channel()
        status, body = self.request("POST", f"/v1/admin/channels/{channel['id']}/pause")
        self.assertEqual(status, 401, body)
        self.assertEqual(body["error"], "admin_unauthorized")


class AdminDisabledTests(_Harness):
    admin_token_env = None  # BACKCHANNEL_ADMIN_TOKEN unset

    def test_admin_api_is_disabled_without_a_token(self) -> None:
        status, channel = self.request(
            "POST", "/v1/channels", {"name": "c", "mode": "broadcast"}, api_key="key-1"
        )
        self.assertEqual(status, 201)
        status, body = self.request(
            "POST", f"/v1/admin/channels/{channel['id']}/pause", admin_token="anything"
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "admin_disabled")


if __name__ == "__main__":
    unittest.main()
