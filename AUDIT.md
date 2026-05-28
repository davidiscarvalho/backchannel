# Backchannel — Launch Readiness Audit

Read-only audit of `backchannel` (branch `master`, HEAD `cac6598`),
written after two earlier passes corrected each other and the product
framing was clarified with the maintainer.

## What backchannel is (maintainer's framing)

A **layer that agents use to communicate** — same primitive, multiple
shapes of use:

- **One person, multiple agent contexts.** My laptop's Claude Code talks
  to my server's Claude Code without me copy-pasting prompts between
  them. This is the everyday case.
- **Small communities sharing channels.** I send you an invitation
  token; your agent joins my channel; we coordinate without a shared
  cloud account.
- **Agent-driven setup.** The instance should be simple enough that an
  agent can stand it up unattended, mint its own key, share an
  invitation, and start coordinating — no human in the loop for the
  integration path.

It is **MIT, free, self-hosted**. Not a SaaS, not a tier ladder, not a
commercial product. The public instance at `https://backchannel.oakstack.eu`
is a deliberately throttled demo/sandbox to let people try the protocol
without breaking the server. The self-host stack is the product.

## What's already real and good

- **Atomic claim.** `UPDATE messages SET claimed_by_actor_id = ? WHERE
  id = ? AND claimed_by_actor_id IS NULL` with `cursor.rowcount == 0` →
  `409 already_claimed`. WAL on. "First valid claim wins" is honest.
  (`backchannel/store.py:claim_message`, `claim_with_lease`)
- **Schema maturity.** 2500-line store with `channels`, `messages`,
  `message_events`, `actors`, `actor_aliases`, `channel_members`,
  `channel_invitations`, `channel_aliases`, `channel_links`,
  `channel_events`, `sessions`, `key_scopes`, `idempotency_cache`,
  `pending_webhooks`, plus six `audit_*` tables for the mutation
  trail. Hot paths are indexed.
- **Tenant isolation.** `_check_channel_access` covers open / owner /
  team / membership → 403. Called from every channel-scoped read and
  write site (verified at 7+ call sites).
- **Body-size cap at the app.** `_MAX_CONTENT_BYTES = 65536` enforced
  on every message create, not edge-only.
- **Admin token comparison is constant-time** (`hmac.compare_digest`).
- **OpenAPI is at parity for the application surface.** 38 paths
  declared, covering every agent verb, `/v1/keys/me`,
  `/v1/messages/{id}/ack`, claim-with-lease, leases, security audit,
  observability, admin pause/resume, actors, members, invitations.
  A `tests/test_openapi_completeness.py` regression guard is already
  in the suite.
- **No XSS surface in default render.** The SPA uses Vue mustache
  (`{{ msg.content }}`) everywhere — no `v-html`, no `innerHTML`.
  Vue auto-escapes.
- **Idempotency middleware** for mutating operations, with a
  server-auto fallback that hashes the body.
- **Tests.** 14 files, ~2600 lines, ~94 functions, including
  `test_openapi_completeness`, `test_channel_protection`,
  `test_local_auth`, `test_sandbox`, `test_idempotency`.
- **Discovery surface.** `/llms.txt`, `/.well-known/ai-manifest.json`,
  `/.well-known/ai-plugin.json`, `/first-success-prompt.txt`,
  `x-ai-agent-hints` on operations. This is what makes the
  agent-driven-setup story credible.

## What needs to land

### Blockers (do today; both trivial)

**B1. `LoginView.vue` ships legacy "API Depot" copy.** Confirmed by
the maintainer as a bug — a leftover from a previous framing the
project moved away from in commits `item73`/`item74`. The frontend
was missed in that scrub.

- `ui/src/views/LoginView.vue` line 5: `"Enter your API Depot key to continue."`
- Placeholder: `"depot_key_…"`
- Footer: link to `https://the-api-depot.example`

Replace with copy that matches the self-host framing. Suggested:
- Sub: `"Paste your Backchannel API key to continue."`
- Placeholder: `"bc_…"` (or whatever prefix `mint_raw_key` actually uses — verify)
- Footer: an inline `curl -X POST /v1/keys -d '{"agent_label":"me"}'`
  snippet against a relative URL, so it works on any self-host instance.

Effort: ~15 minutes.

**B2. `docker-compose.self-host.yml` does not pass `.env` to services.**
`SELF-HOST.md` tells operators to `cp .env.template .env`. With the
compose as written, that step is silently no-op — `app` and `worker`
both lack `env_file: .env`. Operators cannot set
`BACKCHANNEL_ADMIN_TOKEN`, sandbox knobs, rate-limit values, or
DB-size limits without editing the compose file.

Fix: add `env_file: .env` to both services (matching what
`docker-compose.server.yml` already does).

Effort: ~10 minutes plus a smoke test.

### High (within ~2 weeks, before publicizing the demo URL widely)

**H1. `Request.remote_addr` trusts `REMOTE_ADDR` blindly.** Behind any
proxy, every request appears from the proxy. Affects the per-IP key
issuance limiter, the invitation lookup limiter, and the security
audit log. This matters for the *self-host default stack too*, not
just the demo: `ui/nginx.conf` proxies to `app:8080`, so even a local
self-hoster's per-IP defenses are blind.

Fix: add `BACKCHANNEL_TRUSTED_PROXIES` (CIDR list). Derive
`Request.remote_addr` by walking `X-Forwarded-For` right-to-left
through trusted hops; ignore `XFF` when the immediate hop is not
trusted. Apply in `issue_key`, the invitation rate limiter, and
`record_security_event`.

**H2. `ui/nginx.conf` does not set `X-Forwarded-For` or security
headers.** Two-fold:
1. Add `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
   to every `location` block (so H1 has the data it needs).
2. Add `Content-Security-Policy`, `Strict-Transport-Security`,
   `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
   The CSP closes the latent risk of a future contributor adding a
   `v-html` and turning the in-`localStorage` key into an
   exfiltration target.

**H3. `DELETE /v1/keys/me`.** Self-service key rotation. The
self-host operator can delete a row, but an SDK consumer who leaks
a key has no in-protocol recovery. Add the endpoint; mark the key
inactive; update the spec and `llms.txt`.

**H4. Invitations as a first-class surface for agents.** The
invitation system exists in the schema and in the API, but it isn't
treated as a first-class flow. Under the maintainer's framing
("share addresses/codes/keys/invitations") it is one of the top-three
features. What to do:
- Make sure every invitation operation has an `x-ai-agent-hints`
  block with a clear `when_to_use` and `agent_prompt_snippet`.
- Surface invitation creation prominently in the SPA's
  `InvitationsView` with a copy-token button.
- Add a worked example to `docs/` of the full flow: agent A creates
  a channel, mints an invitation, hands the token to agent B (on a
  different instance, with a different key), agent B redeems and
  joins.

### Medium (this month)

- **M1.** CI hardening: add `cd ui && npm run build`, a
  `docker compose up + curl /health + env-var visibility` smoke job,
  `ruff` and `mypy` on `backchannel/`, coverage gate at 80%. The
  npm-build job would have caught B1; the env-var job would have
  caught B2.
- **M2.** Swap `wsgiref` → `waitress` behind an env flag for
  self-hosters with non-trivial QPS. Keep `wsgiref` as the dev
  default. Add a 50-concurrent-claim test.
- **M3.** Property-based atomicity tests with `hypothesis`.
- **M4.** Auto-trip metric (`backchannel_db_autotrip_armed` gauge on
  `/metrics`).
- **M5.** `scripts/backup.sh` + `scripts/restore.sh` + a restore-drill
  pytest.
- **M6.** Audit `pending_webhooks` — either ship the feature with an
  admin API or fence it.
- **M7.** `Idempotency-Key` and `X-Request-Id` documented in OpenAPI
  and `llms.txt`.

### Low (backlog)

Empty/error states across detail views; mobile/responsive pass;
deprecation-policy doc; OpenAPI lint in CI; `x-codeSamples` per
operation; structured-log schema doc; `backchannel.db` in
`.dockerignore`.

## Verdict

**SHIP-WITH-FIXES.** Both blockers are trivial; the high-priority work
fits in one focused fortnight.

Per-domain rating (after B1+B2 land):

| Domain | Score |
|---|---|
| Security | 7.5 |
| Reliability | 7.5 |
| API / DX | 7 |
| Frontend | 7.5 |
| Agent UX | 7 |
| **Mean** | **7.3** |

Structural ceiling for this product (without changing what it is):
**9.2**. The binding constraint is reliability (caps at 8.5 because
"one process, one SQLite file, no broker" is a product axiom). Three
months of focused work closes the gap to ~9.0; the last 0.2 requires
third-party security review and an agent-integration benchmark.

## Where the polish-vs-engineering split sits

The engineering is competent and roughly at 7/10 of what it can be.
The product clarity (under the maintainer's framing) is at 8/10 — the
framing is sharper than most "AI infrastructure" launches. The
**presentation** (login screen, env-file plumbing, first-run flow) is
at 5/10 and is what most people will judge first.

A developer who lands on this forms a verdict in 90 seconds. The
technical bet — agent-discoverable, atomic, self-host, MIT, no
signup — deserves a presentation that matches the taste of the
architecture. That is the entire focus of the blockers and the
two-week H-tier work.

## A small structural suggestion

Under the maintainer's framing — "share addresses/codes/keys/invitations"
between personal instances on different networks — there is one feature
that fits the product but isn't there yet:

**Peer discovery on a trusted network.** mDNS or Tailscale-aware
auto-discovery so "my laptop should just see my server's instance" works
without manually copying a URL. Not for v1. Worth keeping on the
roadmap because it makes the per-person-multi-instance use case
near-frictionless and is a small, focused feature that fits the
existing architecture.
