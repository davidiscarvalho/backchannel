"""x402 payment middleware tests.

Validates:
  - When x402 is disabled, /v1/keys/x402 returns 503 x402_unavailable.
  - When enabled, an unauthenticated request returns 402 with an `accepts`
    payload shaped per the x402 spec.
  - Submitting a valid X-PAYMENT proof mints a Tier-1 key.
  - Submitting an invalid proof returns 402 again with error_detail.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app
from backchannel.x402 import StaticTestVerifier, X402Config


class X402Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app_obj = create_app(db_path=Path(self.tempdir.name) / "test.db")
        # Reconfigure the x402 middleware in-place (avoids env-var leakage).
        self.app_obj.x402.config = X402Config(
            enabled=True,
            pay_to_address="0xPAYTO000000000000000000000000000000000",
            network="base-mainnet",
            price_per_request_usdc="0.01",
            verifier=StaticTestVerifier(accepted_proof="proof_valid_demo"),
            pack_usdc="5.00",
            pack_credits=6000,
        )
        self.app = self.app_obj

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _request(self, method: str, path: str, *, payment_header: str | None = None, body: dict | None = None):
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
        if payment_header is not None:
            environ["HTTP_X_PAYMENT"] = payment_header
        status_holder: dict = {}

        def start_response(status, headers, exc_info=None):
            status_holder["status"] = status
            status_holder["headers"] = headers

        body_bytes = b"".join(self.app(environ, start_response))
        status_code = int(str(status_holder["status"]).split()[0])
        return status_code, json.loads(body_bytes.decode("utf-8"))

    def test_x402_disabled_returns_503(self) -> None:
        self.app_obj.x402.config.enabled = False
        status, body = self._request("POST", "/v1/keys/x402")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "x402_unavailable")

    def test_first_call_returns_402_with_accepts(self) -> None:
        status, body = self._request("POST", "/v1/keys/x402")
        self.assertEqual(status, 402)
        self.assertEqual(body["error"], "payment_required")
        self.assertEqual(body["x402Version"], 1)
        self.assertEqual(len(body["accepts"]), 1)
        req = body["accepts"][0]
        self.assertEqual(req["scheme"], "exact")
        self.assertEqual(req["network"], "base-mainnet")
        # /v1/keys/x402 advertises the credit-pack price, not per-request.
        self.assertEqual(req["maxAmountRequired"], "5.00")
        self.assertEqual(req["asset"], "USDC")
        self.assertEqual(req["payTo"], "0xPAYTO000000000000000000000000000000000")
        self.assertIn("documentation_url", body)

    def test_valid_payment_mints_tier1_key(self) -> None:
        status, body = self._request("POST", "/v1/keys/x402", payment_header="proof_valid_demo")
        self.assertEqual(status, 201, body)
        self.assertTrue(body["key"].startswith("bck_"))
        self.assertEqual(body["tier"], 1)
        self.assertEqual(body["plan"], "x402")
        self.assertIsNotNone(body["settlement_id"])

    def test_invalid_payment_returns_402_with_error_detail(self) -> None:
        status, body = self._request("POST", "/v1/keys/x402", payment_header="proof_wrong")
        self.assertEqual(status, 402)
        self.assertEqual(body["error"], "payment_required")
        self.assertEqual(body.get("error_detail"), "payment_verification_failed")

    def test_minted_key_authenticates(self) -> None:
        status, paid = self._request("POST", "/v1/keys/x402", payment_header="proof_valid_demo")
        self.assertEqual(status, 201)
        # Use the issued key to call a protected route
        raw_key = paid["key"]
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = "GET"
        environ["PATH_INFO"] = "/v1/keys/me"
        environ["QUERY_STRING"] = ""
        environ["CONTENT_LENGTH"] = "0"
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(b"")
        environ["REMOTE_ADDR"] = "127.0.0.1"
        environ["HTTP_X_API_KEY"] = raw_key
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        body = b"".join(self.app(environ, start_response))
        self.assertEqual(int(str(holder["status"]).split()[0]), 200)
        me = json.loads(body.decode("utf-8"))
        self.assertEqual(me["tier"], 1)


if __name__ == "__main__":
    unittest.main()
