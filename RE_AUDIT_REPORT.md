# Backchannel — Launch Re-Audit (Round 2)

Read-only re-audit of repo (`master`, HEAD `148bc69`) **and** the live deployment at
`https://backchannel.oakstack.eu`. Phase 0–1 were performed blind; Phase 2 reconciles
against `AUDIT.md` and `HANDOFF.md` (the prior audit files; the prompt called them
`AUDIT_REPORT.md` / `FIX_SESSION_PROMPT.md` — those names do not exist in the tree).

---

## 1. System summary (code + live)

**Stack.** Python 3.11 stdlib WSGI (`wsgiref`) + SQLite (WAL). Single-process API
(`backchannel/`: 7k LoC across `store.py` 2.5k, `http.py` 1.6k, `openapi.py` 1.6k,
`landing.py` 0.7k, `auth.py` 171, `rate_limit.py` 69). Vue 3 SPA in `ui/` served by
nginx with `try_files` SPA fallback; nginx reverse-proxies app routes to `app:8080`.
A long-lived `worker` subcommand runs cleanup + sandbox heartbeat. MCP server,
Python/TS SDKs, n8n node, Claude Code plugin in-repo.

**Live deployment.** `backchannel.oakstack.eu` → `46.224.132.101`
(reverse DNS `static.101.132.224.46.clients.your-server.de` → **Hetzner**). TLS by
Let's Encrypt (E8, valid 2026-04-07 → 2026-07-06), single SAN. nginx 1.29.5
(version disclosed in `Server` header). Response headers: HSTS (1y, +includeSub),
`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, per-request
`X-Request-Id` + W3C `traceparent`, `Link: rel=service-desc / ai-manifest`,
`X-RateLimit-Limit: 10`, `X-RateLimit-Window: 3600`. **No CSP, no Referrer-Policy,
no Permissions-Policy, no Cross-Origin-*-Policy.** Live OpenAPI: 38 paths,
`servers: [https://backchannel.oakstack.eu]`, single `ApiKeyAuth` scheme
(`X-API-Key` header). Repo and live spec are produced by the same `openapi.py`;
no drift.

**What's good and verified.** Atomic claim is honest:
`UPDATE messages SET claimed_by_actor_id=? WHERE id=? AND claimed_by_actor_id IS NULL`
with rowcount check → 409 (`store.py:989`). Constant-time admin token compare
(`hmac.compare_digest`, `http.py:967`). Body cap (`_MAX_CONTENT_BYTES=65536`)
enforced on every message create. SHA-256 of high-entropy `secrets.token_urlsafe`
key is acceptable for API-key storage (the slow-hash argument doesn't apply to
opaque high-entropy tokens). Idempotency middleware with server-auto fallback +
explicit `Idempotency-Key` advertised. JSON error envelope is consistent
(`error`, `message`, `documentation_url`, `request_id`). CI on every push/PR
runs `pytest tests/ mcp_server/tests/` (13 in-repo test files).

**What's structural.** Single SQLite file + `wsgiref` = one effective writer.
Acceptable for the project's stated positioning ("self-hosted, MIT, demo sandbox
at oakstack.eu") but **not** acceptable framing for the prompt's stated target of
"public production launch with real users." This tension is the dominant fact of
the re-audit; the maintainer's framing in `AUDIT.md` and the user's framing in
the re-audit prompt do not agree on what is being shipped.

---

## 2. Panel reviews (blind, fresh, no reference to prior audit)

### 2.1 Security Engineer (adversarial) — **6.5/10**

Honest middle. The serious primitives (atomic claim, constant-time admin compare,
body cap, hashed keys, tenant isolation via `_check_channel_access`) are in
place. The launch-grade defenses are not.

- **Free, unbounded key minting.** `POST /v1/keys` is unauthenticated. Per-key
  rate limit is 10/h; per-IP key-issuance limiter is 5/h. Behind any proxy/Tor
  the per-IP limiter collapses to one bucket because `Request.remote_addr =
  environ['REMOTE_ADDR']` — XFF is not parsed (`http.py:1065`,
  `dispatch()` body). Result: an attacker behind nginx mints unlimited keys from
  one IP (everything appears to come from the nginx hop). This is the dominant
  abuse vector. **Launch blocker for a publicly-promoted instance.**
- **No CSP.** The landing page is server-rendered HTML with embedded inline
  scripts (`landing.py` produces ~28KB with inline `<script>`). The SPA stores
  the API key in `localStorage` (`stores/auth.js`). A future contributor adding
  `v-html` or an XSS in `landing.py` exfiltrates keys with no CSP boundary.
  **High.**
- **Other missing response headers.** No `Referrer-Policy`, no
  `Permissions-Policy`, no `Cross-Origin-Opener-Policy`. **Medium.**
- **Server header leaks `nginx/1.29.5`.** Cheap to hide (`server_tokens off`,
  `more_clear_headers`). **Low.**
- **Admin API depends on a single env var as bearer.** Acceptable, but no
  audit/rotation story for it, and a leaked token has no in-band rotation.
  **Medium.**
- **`/v1/security/audit` is exposed.** Need to verify it is owner-scoped (not
  probed live, requires a key); flag for review. **Medium.**
- **No abuse-of-history vector tested.** `/v1/channels/{id}/history` reads
  archive; check pagination + auth scoping. **Medium.**
- **No DELETE /v1/keys/me.** A leaked SDK key has no self-rotation. **Medium.**

### 2.2 Distributed Systems / Reliability (adversarial) — **6.5/10**

The atomicity claim is real, but everything around it caps the score for a
"public production" framing.

- **Single SQLite file + wsgiref single-process.** WAL helps concurrent reads,
  but writes serialize on one writer with `BEGIN IMMEDIATE` semantics. Under
  even moderate concurrent claim load you'll see `SQLITE_BUSY` and wsgiref's
  thread-per-request model will queue. **No queue depth, no backpressure, no
  load test in the repo.** For self-host: fine. For a publicly-promoted launch:
  not enough.
- **No `claim-with-lease` redelivery test in CI you can point at.** The
  schema/route exist; lease heartbeat exists; what happens on heartbeat
  expiry + reclaim under race needs property-based tests (`hypothesis` was on
  the prior backlog).
- **Mid-delivery crash semantics.** Claim is committed before the consumer
  has the message in hand — by design, the "first valid claim wins" contract
  permits dropped work if the consumer dies before ack. This is documented as
  redelivery via lease expiry. Acceptable, but the agent UX of "I claimed and
  crashed" needs to be exercised in tests.
- **No durable backup story.** No `scripts/backup.sh` (was M5 on prior backlog).
  Losing `/data/backchannel.db` is total data loss with no recovery path
  documented. **Launch concern.**
- **Health/readiness.** `/health` opens a DB connection per call (good signal),
  but there is no separate `/readyz` vs `/livez`. Adequate for a single-node
  deploy.
- **Auto-trip latch.** The DB-size auto-pause and the sandbox abuse controls
  are smart. Good signal of operator thought.

### 2.3 API / DX Reviewer (constructive) — **7.5/10**

The strongest single dimension. Schema surface is broad, naming is consistent,
both literal HTTP and "agent verb" aliases exist (`/v1/tasks/post`,
`/v1/tasks/claim`, etc.). Error contract is uniform. `Idempotency-Key` is now
documented across writes (commit `148bc69`). 38 paths, single `ApiKeyAuth`
scheme, well-defined `info.description` walking the agent through the workflow.

- **Spec parity.** Repo `openapi.py` and live `/openapi.json` are identical
  (same generator). No drift. **Strong.**
- **Versioning is `1` (not `v1` or semver).** Mismatch with the path prefix
  `/v1/`. Cosmetic. **Low.**
- **No `examples` on most operations.** Agents and humans both benefit; the
  `x-ai-agent-hints` block partly compensates. **Medium.**
- **`/v1/keys/me/scopes` PUT** with no companion GET that shows the schema
  shape outside `/v1/keys/me`. Minor.
- **OPTIONS not implemented globally** — preflight responses are 404 (probed
  live: `OPTIONS /` returns no specific handler). For browser SDK use this
  will bite. **Medium for browser use, low for server-to-server.**
- **`/v1/channels/{id}/messages` GET pagination contract** — verify
  `since` cursor semantics are documented. Spec mentions `since=0`; ensure
  monotonicity guarantees.
- **`Content-Length` reported on 400-class errors** with a `documentation_url`
  link to in-repo `docs/errors.md#anchor`. Excellent DX.

### 2.4 Frontend / Product (constructive) — **6.5/10**

The landing page has a clear positioning hook ("How agents call other agents")
and a credible aesthetic (dark terminal, IBM Plex). But for the "public launch"
framing it falls short on a few specific things.

- **The page is a 28KB server-rendered HTML chunk from `landing.py`.** No
  build-time minification, no asset hashing on inline assets, single-file
  Python string templating. Works, but feels artisanal in a way that signals
  "experiment" more than "launch."
- **No images / no diagram / no animation.** For "agents calling agents" the
  obvious one-screen mental model (A→queue→B with the claim arrow) is missing.
  Agent products benefit from one good diagram more than from prose. **High
  priority for launch.**
- **Footer links use absolute GitHub URLs** (`github.com/davidiscarvalho/...`)
  rather than the product's domain — fine, but mixes the personal-namespace
  URL with a product page; reads slightly amateur. Move to a `oakstack/` org
  or a redirect. **Low.**
- **"Get Instant Key" modal** uses inline JS posting to `/v1/keys`. Works.
  Should display the resulting key once with a copy button + a one-time warning
  that it won't be shown again — verify behavior.
- **No social-proof / no testimonial / no architecture diagram / no GIF of
  Claude Code calling another Claude Code.** The one thing a visitor most wants
  to see is the actual handoff. **High.**
- **Footer says `© 2026 Oakstack`** but the "Self-host?" link goes to a
  personal-account repo. Brand inconsistency.
- **No `/pricing` despite removed commercialization** — fine. But the
  `/compare` page is still linked in the routing; check it doesn't display
  stale commercial framing.
- **Vue SPA shell is clean.** App.vue is small, no `v-html` anywhere (auto-
  escaped). Good.

### 2.5 AI Agent Consumer (Claude, attempting integration from live docs) — **7.5/10**

Strongest dimension after API/DX. The combination of `llms.txt`,
`/agent-guide`, `/first-success-prompt.txt`, `x-ai-agent-hints` per operation,
and the agent-verb aliases (`/v1/tasks/post`, `/v1/tasks/claim-and-ack`)
genuinely reduces guesswork.

- **Bootstrapping path is unambiguous.** `info.description` literally spells
  out the call sequence: `issueKey → createChannel → createMessage →
  listMessages → claimMessage → ackMessage`. I can follow it.
- **Rate limit visibility.** `X-RateLimit-Limit` / `X-RateLimit-Window` on
  every response (even 401s), and `X-RateLimit-Remaining` is hinted but not
  observed in my probe — verify it actually emits. If not, agents can't
  budget. **Medium.**
- **Error envelope** is excellent: `error`, `message`, `documentation_url`,
  `request_id`. An agent can grep the doc URL and self-correct.
- **`Link: </openapi.json>; rel="service-desc"`** on every successful
  response — discoverable. Good.
- **OPTIONS preflight 404s.** If I'm a browser-side agent (e.g., a Cursor
  extension), CORS bites me. **Medium.**
- **Where I stall:** the `claim-with-lease` vs `claim` distinction is in the
  spec but not screamed loud enough in `/agent-guide` for an agent picking one.
  Pick a default and steer to it; mention the other as the "I might crash"
  variant.
- **MCP path is credible.** `pip install ./mcp_server` + `claude mcp add` is
  one of the better self-installs I've seen. The package isn't published on
  PyPI yet — install requires the repo, which adds a step.

---

## 3. Ratings (Round 2 vs Round 1)

| Domain      | Round 1 | Round 2 | Δ    |
|-------------|---------|---------|------|
| Security    | 7.5     | 6.5     | −1.0 |
| Reliability | 7.5     | 6.5     | −1.0 |
| API / DX    | 7.0     | 7.5     | +0.5 |
| Frontend    | 7.5     | 6.5     | −1.0 |
| Agent UX    | 7.0     | 7.5     | +0.5 |
| **Mean**    | **7.3** | **6.9** | −0.4 |

The mean dropped not because anything regressed in code, but because Round 1
was scored against the maintainer's "MIT self-host, sandbox demo" framing and
Round 2 is scored against the re-audit prompt's "public production launch with
real users" framing. Same artifact, different yardstick. Under the maintainer's
framing the Round 2 mean would be ~7.5 (slight improvement from B1+B2 fixes).

---

## 4. Reconciliation against the prior audit

`AUDIT.md` listed 2 blockers, 4 highs, 7 mediums, a low backlog.

| Item | Status | Evidence |
|------|--------|----------|
| **B1** LoginView API Depot copy | **FIXED** | `982f490 fix(ui): replace stale API Depot copy on login screen`. `git grep` is clean. |
| **B2** self-host compose missing `env_file` | **FIXED** | `d8b4b69 fix(self-host): pass .env file to app and worker containers`. |
| **H1** `BACKCHANNEL_TRUSTED_PROXIES` / XFF parsing | **NOT ADDRESSED** | `Request.remote_addr = environ['REMOTE_ADDR']` in `http.py` dispatch; no XFF logic. Per-IP defenses still collapse behind nginx. |
| **H2** nginx security headers + XFF passthrough | **NOT ADDRESSED** | `ui/nginx.conf` still has no `add_header CSP/Referrer-Policy/Permissions-Policy`, no `proxy_set_header X-Forwarded-For`. HSTS + X-Frame + X-Content-Type-Options *are* present on responses, but they come from the Python app, not nginx — so any non-app-served path lacks them. |
| **H3** `DELETE /v1/keys/me` | **NOT ADDRESSED** | Live OpenAPI shows `/v1/keys/me` `get` only; no DELETE. |
| **H4** Invitations as first-class agent flow | **PARTIAL** | Routes exist; `x-ai-agent-hints` coverage on invitation ops unverified in this round; UI affordance not verified. |
| **M1** CI hardening (UI build, ruff, mypy, coverage gate) | **NOT ADDRESSED** | CI is `pytest tests/ mcp_server/tests/` only. No `cd ui && npm run build`, no ruff, no mypy, no coverage gate. |
| **M2** `waitress` upgrade behind env flag | **NOT ADDRESSED** | Still `wsgiref`. |
| **M3** Property-based atomicity tests | **NOT ADDRESSED** | No `hypothesis` in CI install. |
| **M4** `backchannel_db_autotrip_armed` gauge | **NOT ADDRESSED** (not verified positively) | |
| **M5** Backup / restore scripts + drill | **NOT ADDRESSED** | No `scripts/` dir surfaced. |
| **M6** `pending_webhooks` decision | **NOT ADDRESSED** | Table + signing code exist; no admin API or fence visible. |
| **M7** `Idempotency-Key` + `X-Request-Id` documented in OpenAPI | **FIXED** | `148bc69 fix(openapi): document every non-meta route + advertise Idempotency-Key`. |
| All Low backlog | **NOT ADDRESSED** | Expected; explicitly post-launch. |

**Trajectory.** The two blockers landed cleanly. The most user-visible high
priority — H7 (OpenAPI completeness for `Idempotency-Key`) — also landed. The
four security/reliability H-items did not. **What the prior audit said was
"two-week post-blocker work" is still entirely outstanding.**

**New / missed by prior audit.**
- **Domain/positioning split.** `oakstack.eu` is wired into `.env.template`
  defaults, OpenAPI `servers`, `info.description` URLs, and the landing page
  copy. The prior audit accepted this as the demo URL; the re-audit prompt
  treats `oakstack.eu` as experiments-only and a public launch on it as a
  blocker. This was not surfaced before.
- **Server header `nginx/1.29.5` leaked.** Cheap to hide.
- **OPTIONS / CORS** unhandled — bites any browser-side SDK consumer.
- **No PyPI publication** of `backchannel-mcp` — the documented install path
  references a local pip install from the repo.

**No regressions detected.**

---

## 5. Landing page — opinionated refresh notes

Ordered by impact for a public-launch audience.

1. **Add the one diagram.** A→queue→B with the claim arrow, `409 already_claimed`
   on the loser, ack on completion. One SVG. Above the fold. This is the single
   highest-impact change.
2. **Replace the prose hero with a 15-second loom/GIF** of one Claude Code
   session handing off to another via `backchannel-mcp`. Agent products live or
   die on "I can see it working in one screen."
3. **Strip the "Free & open / Public test instance" pricing-card box.** It
   reads as leftover commerce framing even after the `item73`/`item74` scrub.
   Replace with a one-liner: "Self-host in 10 minutes. The public instance is
   rate-limited; bring your own for real work."
4. **Fix the GitHub link namespace.** `github.com/davidiscarvalho/backchannel`
   in the footer reads personal; move to `github.com/oakstack/backchannel` or
   redirect.
5. **Show, don't tell, the curl flow.** The agent guide section lists endpoints
   in a wall of `<code>` blocks. Group them as three numbered cards
   (1. mint key, 2. post task, 3. claim task) with copy buttons. Each card one
   curl command and the expected JSON.
6. **Add minimal social proof / counters.** `/v1/observability/metrics` is
   public-reachable in spec but probably owner-only at runtime; a
   `/status.html` style "X messages in last hour" widget would do.
7. **Drop the `&middot;` / `&rarr;` HTML entities** in copy — Python-string
   templating shows. Use Unicode directly.
8. **Mobile pass.** Not verified in this audit (no headless render), but the
   server-string nature of `landing.py` makes responsive iteration painful.
9. **CSP-friendly refactor.** Move inline `<script>` into a hashed external
   `landing.js` so a future CSP can ban `unsafe-inline`.

The page is clearly written and on-message. It is not yet *taste-matched* to
the architecture it's selling.

---

## 6. Live deployment — observable-only findings

| Item | Status | Note |
|------|--------|------|
| Infra | Hetzner ✓ | `46.224.132.101` → `*.your-server.de` |
| Domain | **`oakstack.eu`** | Per re-audit standing rule, oakstack is experiments-only; **public launch on this domain is a BLOCKER**. Code bakes `BACKCHANNEL_BASE_URL=https://backchannel.oakstack.eu` in `.env.template`, OpenAPI `servers`, `info.description`, `health()` fallback. |
| TLS | OK | Let's Encrypt E8, single SAN, valid until 2026-07-06. Auto-renew assumed (acme-companion/certbot — not verified). |
| HSTS | Present | 1y, includeSubDomains. No `preload`. |
| Frame / sniff | Present | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` |
| CSP | **Missing** | High |
| Referrer-Policy | **Missing** | Medium |
| Permissions-Policy | **Missing** | Medium |
| COOP/COEP/CORP | **Missing** | Low (no SAB use) |
| Server header | Leaks `nginx/1.29.5` | Low |
| OPTIONS preflight | 404 | Medium (kills browser SDK) |
| `/health` | 200 JSON with `db_latency_ms` | Good |
| `/metrics` | Not confirmed in this pass | Verify Prometheus surface auth model |
| `/openapi.json` | 159 KB, 38 paths, `servers=[oakstack]` | Matches repo |
| Repo vs live spec drift | **None** | Same generator |
| Rate-limit headers | Always emitted (`Limit`, `Window`) | Good. `Remaining` not observed in my probes |
| Error envelope | Consistent | Good |
| Path traversal probe | Safely 404'd | Good |
| Malformed JSON | Returns `400 invalid_json` | Good |
| Long path (2000 chars) | Handled | Good |
| Unauthed GET to /v1/channels/sandbox/messages | 401 with documentation_url | Good |

---

## 7. Verdict

# **DO-NOT-SHIP** (as currently framed)

Two reads are possible, and the verdict depends on which framing we adopt:

- **If "public launch" means promoting `backchannel.oakstack.eu` as the
  general-availability service** (the framing in the re-audit prompt): DO-NOT-SHIP.
  Three categories of blocker remain: (a) the domain itself is on an
  experiments-only namespace; (b) the per-IP defenses behind nginx are blind
  because XFF is not parsed and trusted-proxy plumbing was never landed (H1+H2);
  (c) there is no backup/restore story for the single SQLite file under
  promoted traffic.

- **If "public launch" means cutting an announcement of the self-host MIT
  product, with `oakstack.eu` staying explicitly labelled as a throttled
  demo sandbox** (the framing in `AUDIT.md`): **SHIP-WITH-FIXES**, where the
  remaining fixes are H1, H2, and item B3 below (CSP+headers in nginx). The
  two original blockers landed; this round adds CSP/headers as a third
  pre-announce item.

Pick the framing first. Below I list the launch-blocking remaining work for
the more demanding framing (public GA service).

### Remaining BLOCKERS for public GA launch

**B-NEW-1. Domain.** Move the GA instance off `oakstack.eu` per standing
infra rule. Files touched: `.env.template`, `docker-compose.server.yml`
(`BACKCHANNEL_BASE_URL`), `backchannel/http.py:health()` hardcoded fallback,
landing copy in `backchannel/landing.py`, any docs referencing the URL.
**Verify:** `git grep oakstack.eu` returns hits only in archival docs
(`AUDIT.md`, `HANDOFF.md`); live OpenAPI `servers[0].url` matches the new
domain; landing page footer matches.

**B-NEW-2. Trusted-proxy plumbing (was H1).** Add
`BACKCHANNEL_TRUSTED_PROXIES` (CIDR list). Walk `X-Forwarded-For`
right-to-left through trusted hops in `Request.remote_addr` derivation.
Apply in `key_issuance_rate_limiter`, `invitation_rate_limiter`, and
`record_security_event`. Add `proxy_set_header X-Forwarded-For
$proxy_add_x_forwarded_for;` to every `location` in `ui/nginx.conf`.
**Verify:** mint 6+ keys from a single client behind nginx; expect the
6th to 429 (not pass because nginx-IP collapsed the bucket).

**B-NEW-3. CSP + Referrer-Policy + Permissions-Policy on nginx (was H2).**
Add `Content-Security-Policy: default-src 'self'; script-src 'self'
'unsafe-inline'; ...` (start permissive; tighten when `landing.py` inline
JS is externalized). Add `Referrer-Policy: no-referrer`,
`Permissions-Policy: ()`. **Verify:** `curl -I` on `/` shows all three.

**B-NEW-4. Backup/restore story.** Either `scripts/backup.sh` +
`scripts/restore.sh` + a one-paragraph operator doc, or an explicit
"this instance is ephemeral; data loss is expected" banner on the GA
instance. The current state — single SQLite file, no documented backup —
is not launchable as a promoted service. **Verify:** restore drill in CI
or in `SELF-HOST.md`.

### Strongly recommended (not strictly blockers)

- DELETE /v1/keys/me (was H3) — leaked-key recovery story.
- OPTIONS handler + permissive CORS for the documented endpoints — browser
  SDK consumers will hit this immediately.
- Hide `Server: nginx/1.29.5` (`server_tokens off`).
- Externalize inline `<script>` in `landing.py` so CSP can drop
  `'unsafe-inline'`.
- CI: add `cd ui && npm run build`, ruff, mypy, coverage gate (was M1).
- One diagram on the landing page (see §5).
- Publish `backchannel-mcp` to PyPI.

### Post-launch (no change from prior audit)

H4 (invitations first-class), M2 (`waitress`), M3 (property tests), M4
(auto-trip gauge), M5 (backup automation), M6 (`pending_webhooks` decision),
all Low items.

---

*End of report. No code or configuration was modified.*
