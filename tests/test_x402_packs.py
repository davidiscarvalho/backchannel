"""Tests for x402 pack pricing + per-op credit debits."""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app
from backchannel.x402 import StaticTestVerifier, X402Config


class X402PackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        # Configure x402 with the default pack pricing + the static verifier.
        self.app.x402.config = X402Config(
            enabled=True,
            pay_to_address="0xPAYTO000000000000000000000000000000000",
            network="base-mainnet",
            price_per_request_usdc="0.01",
            verifier=StaticTestVerifier(accepted_proof="proof_pack_ok"),
            pack_usdc="5.00",
            pack_credits=6000,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _req(self, method: str, path: str, *, body: dict | None = None, headers: dict | None = None):
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

        def start_response(status, hs, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        return int(str(holder["status"]).split()[0]), json.loads(out.decode("utf-8"))

    def test_402_challenge_advertises_pack_price_and_credits(self) -> None:
        status, body = self._req("POST", "/v1/keys/x402")
        self.assertEqual(status, 402)
        req = body["accepts"][0]
        self.assertEqual(req["maxAmountRequired"], "5.00")
        self.assertEqual(req["extra"]["pack_credits"], 6000)
        # per_op_micros = 5_000_000 // 6000 = 833
        self.assertEqual(req["extra"]["per_op_usdc_micros"], 833)
        self.assertIn("6000 metered-op credits", req["description"])

    def test_payment_mints_key_with_full_pack_balance(self) -> None:
        status, body = self._req("POST", "/v1/keys/x402", headers={"X-PAYMENT": "proof_pack_ok"})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["plan"], "x402")
        # 5 USDC = 5_000_000 micros credited
        self.assertEqual(body["credit_micros_applied"], 5_000_000)
        self.assertEqual(body["pack_credits"], 6000)
        self.assertEqual(body["per_op_micros"], 833)

    def test_metered_op_debits_one_credit_worth_of_micros(self) -> None:
        _, paid = self._req("POST", "/v1/keys/x402", headers={"X-PAYMENT": "proof_pack_ok"})
        key = paid["key"]
        # Create a channel (not metered — it's a config call)
        status, ch = self._req(
            "POST", "/v1/channels",
            body={"name": "x402-debit", "mode": "claimable"},
            headers={"X-API-Key": key},
        )
        self.assertEqual(status, 201)
        # Post a message — metered.
        status, _ = self._req(
            "POST", f"/v1/channels/{ch['id']}/messages",
            body={"content": "x"},
            headers={"X-API-Key": key},
        )
        self.assertEqual(status, 201)
        # Balance should now be 5_000_000 - 833 = 4_999_167
        status, me = self._req("GET", "/v1/keys/me", headers={"X-API-Key": key})
        self.assertEqual(me["credit"]["balance_usdc_micros"], 5_000_000 - 833)

    def test_insufficient_credit_blocks_metered_op(self) -> None:
        _, paid = self._req("POST", "/v1/keys/x402", headers={"X-PAYMENT": "proof_pack_ok"})
        key = paid["key"]
        # Drain the credit by directly hammering the store down to almost-zero.
        self.app.store.debit_credit_micros(paid["key_id"], 5_000_000 - 500)  # leave 500 micros (<833)
        status, ch = self._req(
            "POST", "/v1/channels",
            body={"name": "no-credit", "mode": "claimable"},
            headers={"X-API-Key": key},
        )
        self.assertEqual(status, 201)
        status, body = self._req(
            "POST", f"/v1/channels/{ch['id']}/messages",
            body={"content": "denied"},
            headers={"X-API-Key": key},
        )
        self.assertEqual(status, 402, body)
        self.assertEqual(body["error"], "insufficient_credit")

    def test_reads_and_acks_are_free_for_x402(self) -> None:
        _, paid = self._req("POST", "/v1/keys/x402", headers={"X-PAYMENT": "proof_pack_ok"})
        key = paid["key"]
        # Drain to a balance that allows exactly one post + one claim (1666 micros).
        self.app.store.debit_credit_micros(paid["key_id"], 5_000_000 - 1666)
        status, ch = self._req(
            "POST", "/v1/channels",
            body={"name": "reads-free", "mode": "claimable"},
            headers={"X-API-Key": key},
        )
        self.assertEqual(status, 201)
        # Read the channel a few times — should not debit
        for _ in range(5):
            status, _ = self._req("GET", f"/v1/channels/{ch['id']}", headers={"X-API-Key": key})
            self.assertEqual(status, 200)
        # Balance still 1666 after reads
        status, me = self._req("GET", "/v1/keys/me", headers={"X-API-Key": key})
        self.assertEqual(me["credit"]["balance_usdc_micros"], 1666)

    def test_pricing_estimate_surfaces_pack(self) -> None:
        status, body = self._req("GET", "/v1/pricing/estimate")
        self.assertEqual(status, 200)
        x402_tier = next((t for t in body["tiers"] if t["name"] == "x402 Pack"), None)
        self.assertIsNotNone(x402_tier)
        self.assertEqual(x402_tier["pack_usdc"], "5.00")
        self.assertEqual(x402_tier["pack_credits"], 6000)
        self.assertEqual(x402_tier["per_op_micros"], 833)
        self.assertEqual(body["launch_pricing_ends"], "2026-08-15")


if __name__ == "__main__":
    unittest.main()
