"""Tests for the credit ledger (F2).

Covers:
  - keys/me surfaces the balance
  - add_credit_micros / debit_credit_micros happy path
  - debit refuses to go negative (402 insufficient_credit)
  - x402 settlement credits the minted key
"""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app
from backchannel.store import APIError, BackchannelStore
from backchannel.x402 import StaticTestVerifier, X402Config


class CreditLedgerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = BackchannelStore(Path(self.tempdir.name) / "test.db")
        self.record = self.store.issue_api_key(
            key_id="bck_ledger1",
            key_hash="hash_ledger1",
            owner_id="owner",
            agent_label="ledger-test",
            tier=1,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_initial_balance_is_zero(self) -> None:
        self.assertEqual(self.store.credit_balance_micros("bck_ledger1"), 0)

    def test_add_credit_increments_balance(self) -> None:
        new = self.store.add_credit_micros("bck_ledger1", 10_000)
        self.assertEqual(new, 10_000)
        new = self.store.add_credit_micros("bck_ledger1", 5_000)
        self.assertEqual(new, 15_000)

    def test_debit_decrements_balance(self) -> None:
        self.store.add_credit_micros("bck_ledger1", 10_000)
        new = self.store.debit_credit_micros("bck_ledger1", 3_000)
        self.assertEqual(new, 7_000)

    def test_debit_refuses_negative(self) -> None:
        self.store.add_credit_micros("bck_ledger1", 1_000)
        with self.assertRaises(APIError) as cm:
            self.store.debit_credit_micros("bck_ledger1", 5_000)
        self.assertEqual(cm.exception.status, 402)
        self.assertEqual(cm.exception.error, "insufficient_credit")
        # Balance must not have changed.
        self.assertEqual(self.store.credit_balance_micros("bck_ledger1"), 1_000)

    def test_negative_amount_rejected(self) -> None:
        with self.assertRaises(APIError):
            self.store.add_credit_micros("bck_ledger1", -1)
        with self.assertRaises(APIError):
            self.store.debit_credit_micros("bck_ledger1", -1)

    def test_unknown_key_raises_404(self) -> None:
        with self.assertRaises(APIError) as cm:
            self.store.add_credit_micros("bck_does_not_exist", 100)
        self.assertEqual(cm.exception.status, 404)


class CreditLedgerHTTPTests(unittest.TestCase):
    """End-to-end: x402 settlement → key minted with credit → keys/me reflects it."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        self.app.x402.config = X402Config(
            enabled=True,
            pay_to_address="0xPAY",
            network="base-mainnet",
            price_per_request_usdc="0.05",  # 50_000 micros
            verifier=StaticTestVerifier(accepted_proof="proof_ok"),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _request(self, method: str, path: str, *, headers: dict | None = None, body: dict | None = None):
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
        for hk, hv in (headers or {}).items():
            environ[f"HTTP_{hk.upper().replace('-', '_')}"] = hv
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        body_bytes = b"".join(self.app(environ, start_response))
        status_code = int(str(holder["status"]).split()[0])
        return status_code, json.loads(body_bytes.decode("utf-8"))

    def test_x402_mint_credits_key_with_paid_amount(self) -> None:
        status, paid = self._request("POST", "/v1/keys/x402", headers={"X-PAYMENT": "proof_ok"})
        self.assertEqual(status, 201)
        self.assertEqual(paid["credit_micros_applied"], 50_000)

        # keys/me should reflect the credit
        status, me = self._request("GET", "/v1/keys/me", headers={"X-API-Key": paid["key"]})
        self.assertEqual(status, 200)
        self.assertIn("credit", me)
        self.assertEqual(me["credit"]["balance_usdc_micros"], 50_000)
        self.assertEqual(me["credit"]["balance_usdc"], "0.050000")

    def test_keys_me_shows_zero_credit_for_free_key(self) -> None:
        status, free = self._request("POST", "/v1/keys", body={"agent_label": "free-key"})
        self.assertEqual(status, 201)
        status, me = self._request("GET", "/v1/keys/me", headers={"X-API-Key": free["key"]})
        self.assertEqual(status, 200)
        self.assertEqual(me["credit"]["balance_usdc_micros"], 0)


if __name__ == "__main__":
    unittest.main()
