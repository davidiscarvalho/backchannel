"""Tests for the agent-verb endpoint aliases (B1):
  POST /v1/tasks/post
  POST /v1/tasks/claim
  POST /v1/tasks/subscribe
"""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app


class AgentVerbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        _, p = self._req("POST", "/v1/keys", body={"agent_label": "producer-v"})
        self.producer = p["key"]
        _, w = self._req("POST", "/v1/keys", body={"agent_label": "worker-v"})
        self.worker = w["key"]
        _, actor = self._req("POST", "/v1/actors", body={"name": "verb-worker"}, api_key=self.worker)
        self.actor_id = actor["id"]

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
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key:
            environ["HTTP_X_API_KEY"] = api_key
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        return int(str(holder["status"]).split()[0]), json.loads(out.decode("utf-8"))

    def test_post_creates_channel_and_returns_message(self) -> None:
        status, body = self._req(
            "POST", "/v1/tasks/post",
            body={"channel": "verbs-q", "content": "do work"},
            api_key=self.producer,
        )
        self.assertEqual(status, 201, body)
        self.assertIn("message", body)
        self.assertIn("channel", body)
        self.assertIn("next_cursor", body)

    def test_claim_returns_message_when_available(self) -> None:
        _, posted = self._req(
            "POST", "/v1/tasks/post",
            body={"channel": "claim-q", "content": "task"},
            api_key=self.producer,
        )
        channel_id = posted["channel"]
        status, body = self._req(
            "POST", "/v1/tasks/claim",
            body={"channel": channel_id, "actor": self.actor_id},
            api_key=self.worker,
        )
        self.assertEqual(status, 200, body)
        self.assertIsNotNone(body["claimed"])
        self.assertEqual(body["claimed"]["id"], posted["message"]["id"])

    def test_claim_returns_null_when_empty(self) -> None:
        # Channel without any messages on it
        _, posted = self._req(
            "POST", "/v1/tasks/post",
            body={"channel": "empty-q", "content": "x"},
            api_key=self.producer,
        )
        channel_id = posted["channel"]
        # First claim drains it
        self._req(
            "POST", "/v1/tasks/claim",
            body={"channel": channel_id, "actor": self.actor_id},
            api_key=self.worker,
        )
        # Second claim has nothing
        status, body = self._req(
            "POST", "/v1/tasks/claim",
            body={"channel": channel_id, "actor": self.actor_id},
            api_key=self.worker,
        )
        self.assertEqual(status, 200)
        self.assertIsNone(body["claimed"])

    def test_subscribe_returns_message_page(self) -> None:
        _, posted = self._req(
            "POST", "/v1/tasks/post",
            body={"channel": "sub-q", "content": "hello"},
            api_key=self.producer,
        )
        status, body = self._req(
            "POST", "/v1/tasks/subscribe",
            body={"channel": posted["channel"], "limit": 10},
            api_key=self.producer,
        )
        self.assertEqual(status, 200)
        self.assertIn("data", body)
        self.assertGreaterEqual(len(body["data"]), 1)


if __name__ == "__main__":
    unittest.main()
