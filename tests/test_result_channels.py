"""Tests for the result-channel primitive (B2).

  POST /v1/tasks/post-with-result  → creates work msg + paired result channel
  POST /v1/tasks/{id}/result       → consumer publishes the result
  GET  /v1/tasks/{id}/result       → producer reads the result (404 if not ready)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app


class ResultChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        _, p = self._req("POST", "/v1/keys", body={"agent_label": "producer"})
        self.producer = p["key"]
        _, w = self._req("POST", "/v1/keys", body={"agent_label": "worker"})
        self.worker = w["key"]

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

    def test_post_with_result_creates_work_message_and_paired_result_channel(self) -> None:
        status, body = self._req(
            "POST", "/v1/tasks/post-with-result",
            body={"channel": "demo-q", "content": "deploy v2"},
            api_key=self.producer,
        )
        self.assertEqual(status, 201, body)
        # The response carries the canonical channel id (not the requested name)
        # because subsequent ops should use the id directly.
        self.assertTrue(body["channel"])
        self.assertIn("message", body)
        msg_id = body["message"]["id"]
        self.assertEqual(body["result_channel"], f"result-of-{msg_id}")
        self.assertEqual(body["result_url"], f"/v1/tasks/{msg_id}/result")

    def test_await_before_publish_returns_404_result_not_ready(self) -> None:
        _, posted = self._req(
            "POST", "/v1/tasks/post-with-result",
            body={"channel": "q", "content": "task"},
            api_key=self.producer,
        )
        msg_id = posted["message"]["id"]
        status, body = self._req(
            "GET", f"/v1/tasks/{msg_id}/result", api_key=self.producer
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "result_not_ready")

    def test_full_roundtrip_publish_then_await(self) -> None:
        _, posted = self._req(
            "POST", "/v1/tasks/post-with-result",
            body={"channel": "rt", "content": "summarize"},
            api_key=self.producer,
        )
        msg_id = posted["message"]["id"]
        # Worker publishes a result
        status, _ = self._req(
            "POST", f"/v1/tasks/{msg_id}/result",
            body={"content": "summary: looks good"},
            api_key=self.worker,
        )
        self.assertEqual(status, 201)
        # Producer reads
        status, page = self._req(
            "GET", f"/v1/tasks/{msg_id}/result", api_key=self.producer
        )
        self.assertEqual(status, 200)
        self.assertEqual(page["task_id"], msg_id)
        self.assertEqual(page["result"]["content"], "summary: looks good")
        # The result message also carries the task_id in its metadata
        self.assertEqual(page["result"]["metadata"]["task_id"], msg_id)


if __name__ == "__main__":
    unittest.main()
