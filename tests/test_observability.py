"""Tests for /metrics endpoint and JSON logging (E1)."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app
from backchannel.observability import (
    JsonLogFormatter,
    StatRegistry,
    configure_json_logging,
    record_request,
)


class MetricsRegistryTests(unittest.TestCase):
    def test_counter_increments(self) -> None:
        r = StatRegistry()
        r.inc("requests_total", labels={"method": "GET", "status": "200"})
        r.inc("requests_total", labels={"method": "GET", "status": "200"})
        r.inc("requests_total", labels={"method": "POST", "status": "201"})
        out = r.render_prometheus()
        self.assertIn('requests_total{method="GET",status="200"} 2', out)
        self.assertIn('requests_total{method="POST",status="201"} 1', out)
        self.assertIn("# TYPE requests_total counter", out)

    def test_histogram_observe_renders_buckets(self) -> None:
        r = StatRegistry()
        r.observe("dur", 0.005, labels={"path": "/x"})
        r.observe("dur", 0.5, labels={"path": "/x"})
        r.observe("dur", 1.5, labels={"path": "/x"})
        out = r.render_prometheus()
        self.assertIn("# TYPE dur histogram", out)
        self.assertIn('dur_bucket{le="+Inf",path="/x"} 3', out)
        self.assertIn('dur_count{path="/x"} 3', out)
        # 0.5 falls under le=0.5 (one obs), 1.5 doesn't reach le=1 but reaches le=2.5
        self.assertIn('dur_bucket{le="0.5",path="/x"} 2', out)


class MetricsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _request(self, method: str, path: str, *, api_key: str | None = None) -> tuple[int, bytes]:
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = method
        environ["PATH_INFO"] = path
        environ["QUERY_STRING"] = ""
        environ["CONTENT_LENGTH"] = "0"
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(b"")
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key:
            environ["HTTP_X_API_KEY"] = api_key
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status
            holder["headers"] = headers

        body = b"".join(self.app(environ, start_response))
        return int(str(holder["status"]).split()[0]), body

    def test_metrics_endpoint_is_public_and_exposes_prometheus_text(self) -> None:
        # Hit /health a couple of times to generate counters
        for _ in range(3):
            self._request("GET", "/health")
        status, body = self._request("GET", "/metrics")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("# TYPE backchannel_requests_total counter", text)
        self.assertIn("backchannel_requests_total", text)
        # /health requests should be counted
        self.assertIn('path="/health"', text)

    def test_template_path_collapses_ids(self) -> None:
        # Mint a key + create a channel so we get a long id segment
        status, body = self._request("POST", "/v1/keys")
        # Pre-empt the missing field validation by sending a body:
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = "POST"
        environ["PATH_INFO"] = "/v1/keys"
        environ["QUERY_STRING"] = ""
        environ["CONTENT_LENGTH"] = "30"
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(b'{"agent_label":"obs-test"}')
        environ["REMOTE_ADDR"] = "127.0.0.1"
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        key = json.loads(out.decode("utf-8"))["key"]

        # Create channel
        environ["REQUEST_METHOD"] = "POST"
        environ["PATH_INFO"] = "/v1/channels"
        body = json.dumps({"name": "obs-ch", "mode": "broadcast"}).encode("utf-8")
        environ["CONTENT_LENGTH"] = str(len(body))
        environ["wsgi.input"] = BytesIO(body)
        environ["HTTP_X_API_KEY"] = key
        b2 = b"".join(self.app(environ, start_response))
        channel = json.loads(b2.decode("utf-8"))
        # GET the channel by id — id is long
        environ["REQUEST_METHOD"] = "GET"
        environ["PATH_INFO"] = f"/v1/channels/{channel['id']}"
        environ["CONTENT_LENGTH"] = "0"
        environ["wsgi.input"] = BytesIO(b"")
        b3 = b"".join(self.app(environ, start_response))

        # Now /metrics should show /v1/channels/{id} not the literal id
        status, body = self._request("GET", "/metrics")
        text = body.decode("utf-8")
        self.assertIn('path="/v1/channels/{id}"', text)
        # And the literal id should NOT appear in the metrics output
        self.assertNotIn(channel["id"], text)


class JsonLoggingTests(unittest.TestCase):
    def test_formatter_emits_json(self) -> None:
        rec = logging.LogRecord("t", logging.INFO, "f", 1, "hi %s", ("you",), None)
        out = JsonLogFormatter().format(rec)
        parsed = json.loads(out)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["msg"], "hi you")
        self.assertIn("ts", parsed)

    def test_configure_json_logging_replaces_handlers(self) -> None:
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            configure_json_logging("WARNING")
            self.assertEqual(len(root.handlers), 1)
            self.assertIsInstance(root.handlers[0].formatter, JsonLogFormatter)
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in before:
                root.addHandler(h)


if __name__ == "__main__":
    unittest.main()
