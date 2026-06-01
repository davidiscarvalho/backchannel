"""Tests for OPTIONS preflight and CORS headers (T4)."""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app


def _make_app():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    return create_app(db_path=db_path)


def _raw_request(app, method: str, path: str):
    """Send a WSGI request and return (status_str, headers_dict, body_bytes)."""
    environ: dict = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    environ["CONTENT_LENGTH"] = "0"
    environ["wsgi.input"] = BytesIO(b"")
    environ["REMOTE_ADDR"] = "127.0.0.1"
    captured: dict = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(app(environ, start_response))
    return captured["status"], dict(captured["headers"]), body


def test_options_on_known_route_returns_204():
    app = _make_app()
    status, headers, _ = _raw_request(app, "OPTIONS", "/v1/channels/sandbox/messages")
    assert status.startswith("204")
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "GET" in headers["Access-Control-Allow-Methods"]
    assert "X-API-Key" in headers["Access-Control-Allow-Headers"]
    assert headers["Access-Control-Max-Age"] == "86400"


def test_options_on_unknown_route_returns_404():
    app = _make_app()
    status, _, _ = _raw_request(app, "OPTIONS", "/no-such-route")
    assert status.startswith("404")


def test_cors_header_on_regular_response():
    app = _make_app()
    status, headers, _ = _raw_request(app, "GET", "/health")
    assert status.startswith("200")
    assert headers["Access-Control-Allow-Origin"] == "*"
