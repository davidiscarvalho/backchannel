# Operator's guide — turning Backchannel into "free if self-hosted, pay for hosted"

> The code is done. This is the **operational** guide: every account
> you need to create, every config to set, every page to update, every
> test to run, in the order to do them. Allow ~4 hours over 1–2 days.

The model you're shipping:

- **Self-hosters get the entire product for free.** MIT license, one
  container, no asterisks.
- **The hosted instance at `backchannel.oakstack.eu` is the convenience
  tier.** Free 48h test key → €9.99/mo Pro → €39.99/mo Scale → USDC via
  x402 (agent-pay-per-call, no human checkout).

This guide covers your half of that. The code half is already done and
on `master`.

---

## Section 0 — sanity check before you start

```
cd /Users/carvalho/_wrk/backchannel
git log --oneline | head -3
# newest commit must be: item67: open-core framing — instance_kind + /why-hosted ...

.venv/bin/python -m pytest tests/ mcp_server/tests/ -q | tail -2
# → 75 passed
```

If those don't match, **stop** and tell me what differs.

---

## Section 1 — choose your domains and addresses (15 min)

Write these down in a note app. They're referenced everywhere downstream.
Anything you don't have yet — register now. **Stripe takes 1–3 business
days to verify, start it first.**

| Thing | Why | Recommendation |
|--|--|--|
| Primary public host | Default URL agents call | backchannel.oakstack.eu |
| Docs site host | mkdocs deploy target | docs.backchannel.oakstack.eu |
| Stripe account | Pro/Scale billing | dashboard.stripe.com |
| x402 receiving wallet | USDC arrives here. NEW wallet, not personal. | Coinbase Wallet on Base |
| x402 facilitator | Verifies proofs | Coinbase's hosted, x402.org |
| Public email | Pricing/security pages | hello@oakstack.eu |
| Security contact | docs/security.md | security@oakstack.eu |

---

## Section 2 — push to GitHub (2 min)

```
cd /Users/carvalho/_wrk/backchannel
git push origin master
```

Browse https://github.com/davidiscarvalho/backchannel/commits/master.
Top commit should be `item67: open-core framing`.

---

## Section 3 — deploy and flip the instance to "hosted" (15 min)

The ONLY difference between the hosted box and a self-host at the wire
level is `BACKCHANNEL_INSTANCE_KIND=hosted`. Set it.

```
ssh <user>@<hetzner-host>
cd /opt/backchannel
git pull origin master
sudo nano .env
```

Add or update:
```
BACKCHANNEL_BASE_URL=https://backchannel.oakstack.eu
BACKCHANNEL_INSTANCE_KIND=hosted
BACKCHANNEL_INVITATION_ONBOARDING_URL=
BACKCHANNEL_DEMO_KEY=
```

Save (Ctrl-O, Enter, Ctrl-X).

```
docker compose down
docker compose up -d --build
docker compose logs -f app | head -10
# → Backchannel listening on http://0.0.0.0:8080
```

Ctrl-C. Verify from your laptop:

```
HOST=https://backchannel.oakstack.eu
curl -s $HOST/health | jq            # "instance_kind": "hosted"
curl -s $HOST/status | jq            # "instance_kind": "hosted"
curl -sI $HOST/why-hosted | head -3  # HTTP/2 200, text/html
curl -s $HOST/ | grep -E 'MIT|Self-host' | head -3
```

If `instance_kind` still shows `self-hosted`: `docker compose restart app`.

---

## Section 4 — Stripe (60–90 min)

The longest section. 90 min focused, plus 1–3 business days waiting
for Stripe verification.

### 4.1. Register

dashboard.stripe.com/register → business email + password.
Settings → Business → Activate your account → legal name, tax ID,
business description ("API service for AI agent coordination"), payout
bank account. Submit. WAIT 1–3 days. Do everything below in test mode
meanwhile.

### 4.2. Test mode

Top-right toggle. Secret keys start `sk_test_…`.

### 4.3. Two products

dashboard.stripe.com/test/products → + Add product.

PRODUCT 1: Backchannel Pro
- Description: Permanent API key, 300 req/min.
- Standard pricing, 9.99 EUR, monthly recurring.
- Save. Copy the Price ID (`price_XXXXXXX`).

PRODUCT 2: Backchannel Scale
- Description: 1000 req/min, team quotas, 24h support SLA.
- 39.99 EUR / month. Save. Copy Price ID.

### 4.4. Keys + webhook

- dashboard.stripe.com/test/apikeys → copy the secret key (sk_test_…).
- dashboard.stripe.com/test/webhooks → Add endpoint:
  - URL: https://backchannel.oakstack.eu/v1/stripe/webhook
  - Events: checkout.session.completed, customer.subscription.updated,
    customer.subscription.deleted, invoice.payment_succeeded,
    invoice.payment_failed
  - Save. Reveal signing secret (whsec_…). Copy.

### 4.5. Put secrets on Hetzner (never in git)

```
sudo nano /opt/backchannel/.env
```

Add:
```
STRIPE_SECRET_KEY=sk_test_xxxxxxxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxx
STRIPE_PRICE_PRO=price_xxxxxxxxxx
STRIPE_PRICE_SCALE=price_xxxxxxxxxx
```

### 4.6. Ask me to implement F4

The Checkout + portal + webhook handler code is not in the repo yet.
Reply:

> "Implement F4 against the Stripe price IDs in .env. Routes:
> POST /v1/billing/checkout, POST /v1/billing/portal,
> POST /v1/stripe/webhook (signature-verified, promotes the key on
> subscription.created/updated, downgrades on cancellation). Tests."

Budget 2–4 h of my time.

### 4.7. Test in test mode

After F4 lands:
```
KEY=$(curl -s -X POST https://backchannel.oakstack.eu/v1/keys \
   -H 'Content-Type: application/json' -d '{"agent_label":"stripe-test"}' | jq -r .key)
CHECKOUT=$(curl -s -X POST https://backchannel.oakstack.eu/v1/billing/checkout \
   -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
   -d '{"tier":"pro"}' | jq -r .checkout_url)
open "$CHECKOUT"
```

Use test card 4242 4242 4242 4242. Complete. Then:

```
curl -s https://backchannel.oakstack.eu/v1/keys/me -H "X-API-Key: $KEY" | jq
# → tier: 1, plan: "pro"
```

### 4.8. Live mode

Once Stripe activates:
1. Switch to live mode in dashboard.
2. Recreate the two products in live mode (data is separate).
3. Get live sk_live_… and whsec_…
4. Update .env on Hetzner. `docker compose up -d`.

---

## Section 5 — x402 facilitator + receiving wallet (60–90 min)

Skip if not ready for USDC yet — 402 challenge works regardless; the
default NullVerifier refuses any proof.

### 5.1. Base wallet

Coinbase Wallet → new wallet → Base mainnet. Write down the seed
phrase OFFLINE. Copy receiving address (0x…).

### 5.2. Fund with ~5 USDC

Operational float for testing. Sent from any source.

### 5.3. Facilitator

A) Coinbase hosted — URL on x402.org, get API key.
B) Self-host github.com/coinbase/x402 — out of scope here.

### 5.4. Ask me to wire it

Reply:
> "Wire the Coinbase facilitator verifier into __main__.py. Env vars
> BACKCHANNEL_X402_FACILITATOR_URL / KEY. Add tests."

### 5.5. Env vars on Hetzner

```
BACKCHANNEL_X402_ENABLED=1
BACKCHANNEL_X402_RECEIVING_ADDRESS=0xYourBaseAddress
BACKCHANNEL_X402_NETWORK=base-mainnet
BACKCHANNEL_X402_PRICE_USDC=0.01
BACKCHANNEL_X402_FACILITATOR_URL=https://facilitator.x402.org
BACKCHANNEL_X402_FACILITATOR_KEY=<if needed>
```

`docker compose up -d`. Verify:
```
curl -i -X POST https://backchannel.oakstack.eu/v1/keys/x402
# → HTTP/2 402 ; accepts payload references YOUR receiving address
```

### 5.6. End-to-end test with real wallet

Use `x402-fetch` (github.com/coinbase/x402) against
https://backchannel.oakstack.eu/v1/keys/x402. Pay 0.01 USDC. Confirm
the USDC ARRIVES IN YOUR WALLET. A facilitator returning valid:true
without an on-chain transfer is wrong.

### 5.7. Flip the landing badge

Edit backchannel/landing.py: change "agent-native · coming soon" to
"agent-native · live". Commit, redeploy.

---

## Section 6 — publish the artifacts (60 min)

See GUIDE.md sections 5–10 for full step-by-step. Quick reference:

| Artifact | Where | Command |
|--|--|--|
| Python SDK | PyPI | `cd sdk/python && python -m build && twine upload dist/*` |
| MCP server | PyPI | `cd mcp_server && python -m build && twine upload dist/*` |
| TS SDK | npm | `cd sdk/typescript && npm publish --access public` |
| n8n node | npm | `cd n8n_node && npm publish --access public` |
| MCP registry | GitHub PR | GUIDE.md §9 |
| Claude Code plugin | marketplace | GUIDE.md §10 |

Prereqs: `~/.pypirc` set, `npm login`, `@backchannel` scope owned.

---

## Section 7 — UX audit (10 min)

Fresh browser, open each. Tick if it looks right:

- [ ] https://backchannel.oakstack.eu/ — eyebrow has `MIT` link; lede
      mentions "Free, MIT-licensed, self-hostable"; "Self-host (free)"
      button next to "Get a Test key".
- [ ] https://backchannel.oakstack.eu/why-hosted — two columns + 9-row table.
- [ ] https://backchannel.oakstack.eu/pricing — Test/Pro/Scale/x402 columns.
- [ ] https://backchannel.oakstack.eu/status.html — Operational pill, DB latency, last cleanup run timestamp.
- [ ] `curl /health` → `"instance_kind": "hosted"`.
- [ ] `/.well-known/ai-manifest.json | jq .tagline` → `"How agents call other agents."`

Any wrong → paste screenshot to me.

---

## Section 8 — launch post (45 min)

Headline options:
- "Backchannel is free. Hosted is for people who'd rather not run a container."
- "Open-source agent coordination — and a hosted box for when you don't want to run it."
- "How agents call other agents. MIT licensed. No signup."

Body skeleton:
1. One sentence what it does.
2. Protocol in four bullets → link /llms.txt.
3. Self-host in one paragraph + one command → link SELF-HOST.md.
4. Hosted in one paragraph → what it adds (default address, x402
   wallet, registry listing, SLA).
5. Pricing: Test free, Pro €9.99/mo, Scale €39.99/mo, x402 USDC →
   link /pricing and /why-hosted.
6. Try now: pip install backchannel-mcp && claude mcp add backchannel -- backchannel-mcp.

DO NOT lead with features. DO NOT name competitors. DO NOT oversell
hosted — wedge is "free, MIT, MCP-native".

Post: Oakstack blog → LinkedIn → Hacker News (Show HN, honest about
pre-launch state).

---

## Section 9 — first-week watching

| Signal | Where | Healthy |
|--|--|--|
| Request volume by route | Grafana → Backchannel dashboard | growing, /v1/keys not flat |
| Error rate | same | <0.5% |
| New keys | a Tier-1 key calling GET /v1/security/audit | outreach spikes are good |
| x402 settlements | facilitator dashboard | matches key.issue.x402 audit rows |
| Stripe events | dashboard.stripe.com/events | one checkout.session.completed per signup |
| Hetzner CPU/disk | existing monitoring | SQLite + WAL ok to few hundred writes/s |
| Public chatter | HN, Twitter, LinkedIn | reply within a day |

Investigation order: /v1/security/audit → /metrics → logs.

---

## Section 10 — what I can do next

After sections 1–7, reply with the task you want:
- "Implement F4 (Stripe Checkout + webhook + portal)" — §4.7.
- "Wire the Coinbase facilitator verifier" — §5.4.
- "D2: human dashboard with email magic-links" — needs SMTP service
  (Postmark/Resend/SES) chosen first. 6–8 h.
- "D3: polish the playground" — 2 h.
- "Draft the launch blog post" — I draft; you edit.
- "Audit-fix the whole repo" — I run gsd-audit-fix.

What I cannot do without you:
- git push (your credentials).
- SSH to Hetzner (your key).
- Stripe / x402 / facilitator accounts.
- Marketplace submissions.
- Editorial calls.

---

## Appendix — quick reference

Repo: /Users/carvalho/_wrk/backchannel
Test command: `.venv/bin/python -m pytest tests/ mcp_server/tests/`
Master commits since session start: 28
Tests: 75 pass.
Plan tasks done: 35 of 39.

FIRST thing to do: `git push origin master`.
SECOND thing: set `BACKCHANNEL_INSTANCE_KIND=hosted` in Hetzner .env.

---

*Generated 2026-05-13. Pair with GUIDE.md for full publishing detail.
If a step's expected output differs from yours, paste actual output to
me at the step boundary.*
