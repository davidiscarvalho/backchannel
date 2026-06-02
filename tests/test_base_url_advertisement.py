"""A self-host with BACKCHANNEL_BASE_URL unset must advertise ITS OWN host in
agent docs (OpenAPI/ai-manifest/llms.txt), not the public showroom (review #2).
An explicit BASE_URL still wins, and an untrusted X-Forwarded-Host can't hijack
the advertised URL."""

import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app

PUBLIC = "backchannel.oakstack.eu"


def _get(app, path, extra_env=None):
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD="GET", PATH_INFO=path, REMOTE_ADDR="1.2.3.4")
    environ["wsgi.input"] = BytesIO(b"")
    if extra_env:
        environ.update(extra_env)
    holder = {}

    def start_response(status, headers, exc_info=None):
        holder["status"] = status

    body = b"".join(app(environ, start_response)).decode("utf-8")
    return body


class BaseUrlAdvertisementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "b.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def _app(self):
        # ensure BASE_URL is unset for the self-host scenario
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BACKCHANNEL_BASE_URL", None)
            return create_app(db_path=self.db)

    def test_self_host_advertises_its_own_host(self):
        app = self._app()
        for path in ("/llms.txt", "/ai-manifest.json", "/agent-guide"):
            body = _get(app, path, {"HTTP_HOST": "bus.example.com"})
            self.assertIn("bus.example.com", body, f"{path} should advertise the request host")
            self.assertNotIn(PUBLIC, body, f"{path} must not advertise the public showroom on a self-host")

    def test_explicit_base_url_wins(self):
        with mock.patch.dict(os.environ, {"BACKCHANNEL_BASE_URL": "https://canonical.example"}):
            app = create_app(db_path=self.db)
        body = _get(app, "/ai-manifest.json", {"HTTP_HOST": "bus.example.com"})
        self.assertIn("canonical.example", body)
        self.assertNotIn("bus.example.com", body)

    def test_untrusted_forwarded_host_is_ignored(self):
        # No trusted proxies configured => X-Forwarded-Host must not override Host.
        app = self._app()
        body = _get(
            app, "/ai-manifest.json",
            {"HTTP_HOST": "real.example.com", "HTTP_X_FORWARDED_HOST": "attacker.example"},
        )
        self.assertIn("real.example.com", body)
        self.assertNotIn("attacker.example", body)

    def test_allowed_hosts_rejects_spoofed_host(self):
        # N1: with ALLOWED_HOSTS set and BASE_URL unset, a Host not on the list
        # must not be echoed into discovery URLs — the canonical first allowed
        # host is advertised instead.
        with mock.patch.dict(os.environ, {"BACKCHANNEL_ALLOWED_HOSTS": "bus.example.com, alt.example.com"}):
            os.environ.pop("BACKCHANNEL_BASE_URL", None)
            app = create_app(db_path=self.db)
        body = _get(app, "/ai-manifest.json", {"HTTP_HOST": "evil.example.com"})
        self.assertNotIn("evil.example.com", body, "spoofed Host must not be advertised")
        self.assertIn("bus.example.com", body, "canonical first allowed host should be used")

    def test_allowed_hosts_permits_listed_host_with_port(self):
        with mock.patch.dict(os.environ, {"BACKCHANNEL_ALLOWED_HOSTS": "bus.example.com"}):
            os.environ.pop("BACKCHANNEL_BASE_URL", None)
            app = create_app(db_path=self.db)
        body = _get(app, "/ai-manifest.json", {"HTTP_HOST": "bus.example.com:8443"})
        # hostname matches the allowlist => the full host (with port) is advertised
        self.assertIn("bus.example.com:8443", body)


if __name__ == "__main__":
    unittest.main()
