"""The app emits baseline security headers on every response, so a bare
self-host (no nginx) is still hardened. HSTS is gated on an HTTPS scheme."""

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app


class SecurityHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "sec.db")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _headers(self, path: str, extra_env: dict | None = None) -> dict[str, str]:
        environ: dict = {}
        setup_testing_defaults(environ)
        environ.update(REQUEST_METHOD="GET", PATH_INFO=path, REMOTE_ADDR="127.0.0.1")
        environ["wsgi.input"] = BytesIO(b"")
        if extra_env:
            environ.update(extra_env)
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["headers"] = {k: v for k, v in headers}

        b"".join(self.app(environ, start_response))
        return holder["headers"]

    def test_baseline_headers_on_json(self):
        h = self._headers("/health")
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(h.get("X-Frame-Options"), "DENY")
        self.assertEqual(h.get("Referrer-Policy"), "no-referrer")
        self.assertIn("Content-Security-Policy", h)

    def test_baseline_headers_on_html_landing(self):
        h = self._headers("/")
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", h.get("Content-Security-Policy", ""))

    def test_csp_keeps_script_src_tight(self):
        # The XSS-relevant directive must not allow inline scripts.
        csp = self._headers("/")["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        # but inline styles are allowed (the landing uses them)
        self.assertIn("style-src 'self' 'unsafe-inline'", csp)

    def test_hsts_only_over_https(self):
        self.assertNotIn("Strict-Transport-Security", self._headers("/health"))
        h = self._headers("/health", {"HTTP_X_FORWARDED_PROTO": "https"})
        self.assertIn("Strict-Transport-Security", h)


if __name__ == "__main__":
    unittest.main()
