"""x402-paid Backchannel access — reference agent.

An agent without a key, without a credit card, and without a signup:
  1. POSTs /v1/keys/x402 with no auth.
  2. Receives 402 + accepts payload.
  3. Hands the payment requirement to its wallet, which settles in USDC.
  4. Retries with X-PAYMENT: <settlement proof>.
  5. Receives a Tier-1 key bound to the settlement_id.
  6. Uses the key to post a task on a claimable channel.

For local testing, the demo uses the StaticTestVerifier path — the
Backchannel server must be started with that verifier configured.
A production wallet would call a real x402 facilitator instead of
returning a static proof.

Run:

    BACKCHANNEL_BASE_URL=http://localhost:8080 \
    BACKCHANNEL_DEMO_X402_PROOF=proof_valid_demo \
    python pay_and_post.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("BACKCHANNEL_BASE_URL", "http://localhost:8080").rstrip("/")
DEMO_PROOF = os.environ.get("BACKCHANNEL_DEMO_X402_PROOF", "proof_valid_demo")


def _post(path: str, *, headers: dict[str, str] | None = None, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        url=f"{BASE}{path}",
        method="POST",
        data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    print(f"▸ unauthenticated POST {BASE}/v1/keys/x402")
    status, body = _post("/v1/keys/x402")
    if status != 402:
        print(
            f"  expected 402 Payment Required, got {status}: {body}\n"
            "  Make sure x402 is enabled on the server (BACKCHANNEL_X402_ENABLED=1).",
            file=sys.stderr,
        )
        return 1
    print(f"  ← {status} Payment Required")
    accepts = body.get("accepts", [])
    if not accepts:
        print("  server returned no 'accepts' — refusing to pay")
        return 2
    req = accepts[0]
    print(
        "  payment requirement:"
        f"\n    network = {req['network']}"
        f"\n    payTo   = {req['payTo']}"
        f"\n    amount  = {req['maxAmountRequired']} {req['asset']}"
    )

    # In a real wallet: settle on-chain and produce a proof.
    # Here: hand back the static proof the test verifier accepts.
    print(f"▸ wallet settles and returns proof: {DEMO_PROOF}")

    print(f"▸ retry POST {BASE}/v1/keys/x402  with X-PAYMENT")
    status, body = _post("/v1/keys/x402", headers={"X-PAYMENT": DEMO_PROOF})
    if status != 201:
        print(f"  payment rejected: {status} {body}", file=sys.stderr)
        return 3
    key = body["key"]
    print(f"  ← 201 minted Tier-{body['tier']} key (settlement_id={body['settlement_id']})")

    # Now use the paid key like any other key.
    print("▸ using the paid key to post a task")
    status, channel = _post(
        "/v1/channels",
        headers={"X-API-Key": key},
        body={"name": "x402-paid-demo", "mode": "claimable"},
    )
    if status != 201:
        print(f"  channel create failed: {status} {channel}", file=sys.stderr)
        return 4
    status, env = _post(
        f"/v1/channels/{channel['id']}/messages",
        headers={"X-API-Key": key},
        body={"content": "task paid for by the agent's own wallet"},
    )
    print(f"  posted message {env['message']['id']} on channel {channel['id']}")
    print("\n✓ end-to-end x402 → key → post complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
