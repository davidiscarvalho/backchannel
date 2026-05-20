"""x402 payment middleware for Backchannel.

x402 (RFC-style spec from Coinbase, May 2025) is an HTTP-native protocol
for agent micropayments. A server replies with ``HTTP 402 Payment Required``
plus an ``accepts`` payload describing how to pay; the client (or its
wallet) settles in USDC, retries with an ``X-PAYMENT`` header carrying
the settlement proof; the server verifies and serves the response, often
issuing a credit or a scoped API key in return.

This module is the protocol-level scaffolding. Real on-chain settlement
verification is delegated to a pluggable ``PaymentVerifier``:

    - ``NullVerifier`` (default — production-disabled): refuses all proofs.
      Use until you've configured a facilitator. The 402 response still
      goes out (which is the agent-facing UX); only the *retry* fails.
    - ``StaticTestVerifier``: accepts a single pre-shared proof. Useful
      for end-to-end tests and demos without an on-chain dependency.
    - A real verifier (TODO): an httpx call to a Coinbase / Wallet x402
      facilitator endpoint that returns whether a payment is valid.

The middleware is intentionally additive. When ``x402_enabled`` is False
(the default), Backchannel behaves identically to today's app.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

DEFAULT_PRICE_USDC = "0.01"  # legacy: per-request when packs disabled
DEFAULT_NETWORK = "base-mainnet"
DEFAULT_ASSET = "USDC"

# Credit-pack defaults. One pack purchase via /v1/keys/x402 mints a key
# with `pack_credits` metered ops in its credit_balance. One metered op
# (message creation, claim) debits `pack_usdc * 1_000_000 / pack_credits`
# micros from the balance. Pack pricing makes the per-call settlement
# economics work (single on-chain transfer per pack, not per call) and
# gives buyers volume discount room.
DEFAULT_PACK_USDC = "5.00"
DEFAULT_PACK_CREDITS = 6000


# --- Verifier protocol ----------------------------------------------------


class PaymentVerifier(Protocol):
    """Returns True if the proof is a valid settlement for the requirement."""

    def verify(self, payment_proof: str, requirement: "PaymentRequirement") -> bool:
        ...


@dataclass
class NullVerifier:
    def verify(self, payment_proof: str, requirement: "PaymentRequirement") -> bool:
        return False


@dataclass
class StaticTestVerifier:
    """Accept a single pre-shared proof string. For tests + local demos only."""

    accepted_proof: str

    def verify(self, payment_proof: str, requirement: "PaymentRequirement") -> bool:
        return payment_proof == self.accepted_proof


# --- Payment requirement (the body of a 402 response) --------------------


@dataclass
class PaymentRequirement:
    """The shape that goes into the ``accepts`` array of a 402 body.

    Mirrors the x402 spec at <https://www.x402.org/> — keep field names
    aligned so off-the-shelf wallets and ``x402-fetch`` clients work
    without translation.
    """

    scheme: str = "exact"
    network: str = DEFAULT_NETWORK
    max_amount_required: str = DEFAULT_PRICE_USDC
    asset: str = DEFAULT_ASSET
    pay_to: str = ""
    resource: str = ""
    description: str = "Backchannel API access"
    mime_type: str = "application/json"
    output_schema: dict[str, Any] | None = None
    max_timeout_seconds: int = 60
    extra: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scheme": self.scheme,
            "network": self.network,
            "maxAmountRequired": self.max_amount_required,
            "asset": self.asset,
            "payTo": self.pay_to,
            "resource": self.resource,
            "description": self.description,
            "mimeType": self.mime_type,
            "maxTimeoutSeconds": self.max_timeout_seconds,
        }
        if self.output_schema is not None:
            payload["outputSchema"] = self.output_schema
        if self.extra:
            payload["extra"] = self.extra
        return payload


# --- Middleware ----------------------------------------------------------


@dataclass
class X402Config:
    enabled: bool = False
    pay_to_address: str = ""
    network: str = DEFAULT_NETWORK
    price_per_request_usdc: str = DEFAULT_PRICE_USDC
    verifier: PaymentVerifier = field(default_factory=NullVerifier)
    # Map of resource path → price override (string USDC amount).
    price_overrides: dict[str, str] = field(default_factory=dict)
    # Credit pack: one settlement mints N metered-op credits.
    pack_usdc: str = DEFAULT_PACK_USDC
    pack_credits: int = DEFAULT_PACK_CREDITS

    @classmethod
    def from_env(cls) -> "X402Config":
        try:
            pack_credits = int(os.environ.get("BACKCHANNEL_X402_PACK_CREDITS", str(DEFAULT_PACK_CREDITS)))
        except ValueError:
            pack_credits = DEFAULT_PACK_CREDITS
        return cls(
            enabled=os.environ.get("BACKCHANNEL_X402_ENABLED", "").lower() in {"1", "true", "yes"},
            pay_to_address=os.environ.get("BACKCHANNEL_X402_RECEIVING_ADDRESS", ""),
            network=os.environ.get("BACKCHANNEL_X402_NETWORK", DEFAULT_NETWORK),
            price_per_request_usdc=os.environ.get(
                "BACKCHANNEL_X402_PRICE_USDC", DEFAULT_PRICE_USDC
            ),
            pack_usdc=os.environ.get("BACKCHANNEL_X402_PACK_USDC", DEFAULT_PACK_USDC),
            pack_credits=pack_credits,
        )

    def per_op_micros(self) -> int:
        """USDC micros debited per metered op on a plan='x402' key."""
        try:
            pack_micros = int(round(float(self.pack_usdc) * 1_000_000))
        except (TypeError, ValueError):
            pack_micros = int(round(float(DEFAULT_PACK_USDC) * 1_000_000))
        credits = self.pack_credits if self.pack_credits > 0 else DEFAULT_PACK_CREDITS
        return max(1, pack_micros // credits)

    def requirement_for(self, resource: str) -> PaymentRequirement:
        price = self.price_overrides.get(resource, self.price_per_request_usdc)
        return PaymentRequirement(
            network=self.network,
            max_amount_required=price,
            pay_to=self.pay_to_address,
            resource=resource,
            description=f"Backchannel — {resource}",
        )


@dataclass
class X402Decision:
    """What the middleware decided about a request."""

    status: int  # 0 = pass-through; 200 = paid; 402 = require payment
    requirement: PaymentRequirement | None = None
    settlement_id: str | None = None
    error: str | None = None


class X402Middleware:
    """Decides whether a request should be 402-challenged or passed through.

    Used in the request pipeline *only* for routes that opt in (typically
    routes that don't already have a valid X-API-Key). When a payment
    succeeds, ``mint_credit_callback`` is invoked to attach a credit to
    the requester — typically a freshly-issued key.
    """

    def __init__(
        self,
        config: X402Config,
        mint_credit_callback: Callable[[PaymentRequirement, str], dict[str, Any]] | None = None,
    ):
        self.config = config
        self.mint_credit_callback = mint_credit_callback

    def is_active(self) -> bool:
        return self.config.enabled and bool(self.config.pay_to_address)

    def evaluate(self, *, resource: str, payment_header: str | None) -> X402Decision:
        if not self.is_active():
            return X402Decision(status=0)
        requirement = self.config.requirement_for(resource)
        if not payment_header:
            return X402Decision(status=402, requirement=requirement)
        if not self.config.verifier.verify(payment_header, requirement):
            return X402Decision(
                status=402,
                requirement=requirement,
                error="payment_verification_failed",
            )
        settlement_id = f"x402-{uuid.uuid4().hex[:16]}"
        return X402Decision(status=200, requirement=requirement, settlement_id=settlement_id)

    def build_402_body(self, requirement: PaymentRequirement) -> dict[str, Any]:
        """The body shape an x402-aware client expects on a 402."""
        return {
            "x402Version": 1,
            "error": "payment_required",
            "accepts": [requirement.to_payload()],
            "documentation_url": "https://www.x402.org/",
            "issued_at": int(time.time()),
        }
