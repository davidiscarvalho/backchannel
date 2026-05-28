# Backchannel — Launch Polish TODO

Derived from `RE_AUDIT_REPORT.md` (Round 2 re-audit, HEAD `148bc69`).
Framing assumed: **MIT self-host with `backchannel.oakstack.eu` as the
public showroom** (rate-limited demo, no GA SLA, self-host is the product).
Ordered by impact-per-hour. Each item is self-contained: file paths,
change, verification, effort.

The "Day 1" block (T1–T5) is what should land before announcing.
T6–T10 follow within the week. T11+ are this-month polish.

---

## Day 1 — land before announcing the public showroom

### T1. `server_tokens off` in nginx
**Severity:** low (info disclosure) • **Effort:** 5 min • **Category:** security
**File:** `ui/nginx.conf`
**Change.** Add `server_tokens off;` inside the `server { }` block.
**Verify.** `curl -sI https://backchannel.oakstack.eu/ | grep -i ^server:`
returns `server: nginx` with no version.

---

### T2. CSP + Referrer-Policy + Permissions-Policy in nginx
**Severity:** high (latent XSS → localStorage key exfil) • **Effort:** ~30 min
**Category:** security
**File:** `ui/nginx.conf`

**Background.** `backchannel/landing.py` ships inline `<script>` blocks
and the SPA stores the API key in `localStorage` (`ui/src/stores/auth.js`).
With no CSP, any future contributor adding `v-html` or an XSS in
`landing.py` exfiltrates keys with no boundary. HSTS and `X-Frame-Options`
are already emitted by the Python app, but only for app-served paths —
nginx-served SPA paths get no security headers.

**Change.** Add to every `location` block in `ui/nginx.conf`
(or once at `server` scope with `add_header ... always;`):
```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
add_header Referrer-Policy "no-referrer" always;
add_header Permissions-Policy "()" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Frame-Options "DENY" always;
```
Keep CSP permissive at `'unsafe-inline'` for now (the inline JS in
`landing.py` would break otherwise); tighten in T11.

**Verify.** `curl -sI https://backchannel.oakstack.eu/openapi.json`,
`/`, `/login`, and a non-existent SPA path all show all six headers.

---

### T3. Trusted-proxy plumbing (was H1+H2 from prior audit)
**Severity:** high (per-IP defenses collapse behind nginx) • **Effort:** ~2 h
**Category:** security / reliability

**Background.** `Request.remote_addr` is currently
`environ['REMOTE_ADDR']` (no XFF parsing — see `backchannel/http.py`
dispatch). Behind any proxy, every request appears from the proxy's IP,
so the per-IP `key_issuance_rate_limiter` (5/h) and
`invitation_rate_limiter` collapse into a single global bucket. This
affects the showroom AND every self-hoster using the bundled
`ui/nginx.conf`.

**Change.**
1. `ui/nginx.conf`: add to every `location` block:
   `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
   `proxy_set_header X-Real-IP $remote_addr;`
2. `backchannel/http.py`:
   - Read `BACKCHANNEL_TRUSTED_PROXIES` (comma-separated CIDR list,
     e.g. `127.0.0.1/32,172.16.0.0/12`). Use `ipaddress` from stdlib.
   - When deriving `Request.remote_addr`, if `REMOTE_ADDR` is in a
     trusted CIDR, walk `X-Forwarded-For` right-to-left, skipping
     trusted-proxy hops, and use the first untrusted address. Otherwise
     keep `REMOTE_ADDR`.
3. Apply the new `remote_addr` in: `key_issuance_rate_limiter.check`
   (`http.py:1065`-ish), `invitation_rate_limiter.check` (its call
   site), and `record_security_event` (in `store.py`).
4. `.env.template`: add `BACKCHANNEL_TRUSTED_PROXIES=` with a comment
   pointing at the bundled `nginx` (default `127.0.0.1/32` is fine for
   the bundled stack since `app:8080` only sees nginx).

**Verify.**
- Add a test in `tests/test_local_auth.py` (or new `test_xff.py`):
  mock `REMOTE_ADDR` = `127.0.0.1`, `X-Forwarded-For` =
  `1.2.3.4, 127.0.0.1`, expect `Request.remote_addr == "1.2.3.4"`.
- Spoof XFF from an untrusted client; expect it to be ignored.
- On live: `for i in {1..6}; do curl -X POST
  https://backchannel.oakstack.eu/v1/keys -d
  '{"agent_label":"abuse-'"$i"'"}' -H 'Content-Type: application/json';
  done` — the 6th should 429. Today it does not.

---

### T4. OPTIONS preflight + permissive CORS
**Severity:** medium (kills browser SDKs) • **Effort:** ~30 min
**Category:** API / DX
**File:** `backchannel/http.py`

**Background.** Live `OPTIONS /` and `OPTIONS /v1/...` return 404
(`No route for OPTIONS /...`). Any browser-side consumer (a Cursor
extension, a web playground, a JS SDK in someone's docs page) hits
this on preflight and never reaches the real call.

**Change.** Add a generic OPTIONS handler in the dispatch loop:
- For any matched route, on `OPTIONS`: return 204 with
  `Access-Control-Allow-Origin: *` (since the API uses header-based
  `X-API-Key` and no cookies, `*` is safe),
  `Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS`,
  `Access-Control-Allow-Headers: Content-Type, X-API-Key, Idempotency-Key, X-Admin-Token`,
  `Access-Control-Max-Age: 86400`.
- Also emit `Access-Control-Allow-Origin: *` on every non-OPTIONS
  response (CORS is request-driven; browsers need it on the actual
  response too).

**Verify.**
- `curl -X OPTIONS -i https://backchannel.oakstack.eu/v1/channels/sandbox/messages`
  → 204 with the three `Access-Control-*` headers.
- `curl -i https://backchannel.oakstack.eu/health | grep -i access-control`
  → `Access-Control-Allow-Origin: *`.
- Add `tests/test_cors.py`: assert OPTIONS on a real route returns 204.

---

### T5. The one diagram on the landing page
**Severity:** high (positioning) • **Effort:** ~2 h (one focused pass)
**Category:** frontend / product
**File:** `backchannel/landing.py`

**Background.** "How agents call other agents" is the right hook, but
the page has zero images. Agent products live on a one-screen mental
model. Right now the model has to be assembled from prose.

**Change.** Add a single inline SVG above the curl quickstart:
- Box "Agent A" on the left → arrow labeled `POST /v1/channels/x/messages`
  → cylinder labeled "claimable channel" → two arrows out:
  one labeled `claim` → "Agent B (wins)", one labeled `409 already_claimed`
  → "Agent C (loses)". Ack arrow back from B to the channel.
- Inline SVG (no external assets, no CSP impact, no build step).
- Match the existing dark / green-on-black `--accent: #58ff7d` palette
  from `ui/src/App.vue`.

**Verify.** Open `https://backchannel.oakstack.eu/` — the diagram is
above the fold on a 1366×768 viewport; renders without external
requests; the SVG validates.

---

## This week — within 5 working days

### T6. Loom / GIF of a real handoff above the fold
**Severity:** high (persuasion) • **Effort:** ~3 h (record + edit + host)
**Category:** frontend / marketing
**File:** `backchannel/landing.py` (embed), plus a `.gif` / `.webm`
asset committed under `ui/public/` or hosted on the showroom.

**Change.** Record a ~12-second clip: Claude Code on laptop calls
`post_task` via MCP → Claude Code on server claims and acks. Embed
above the fold (alongside or below T5's diagram). `<video autoplay
muted loop playsinline>` or `<img>` with a GIF.

**Verify.** Page weight stays under ~500 KB. Plays on mobile Safari
(check `playsinline`).

---

### T7. Three numbered curl cards
**Severity:** medium (DX) • **Effort:** ~1 h • **Category:** frontend
**File:** `backchannel/landing.py`

Replace the current wall of `<code>` blocks in the "Try it" section
with three numbered cards: **1. mint key** / **2. post task** /
**3. claim task**. Each card has one curl command, a copy button
(inline JS, no deps), and the expected JSON shape collapsed by default.

**Verify.** Click each copy button; clipboard contains the curl line.

---

### T8. Fix footer GitHub link namespace
**Severity:** low (brand) • **Effort:** 15 min
**Files:** `backchannel/landing.py`, `README.md`, anywhere
`github.com/davidiscarvalho/backchannel` appears.

**Change.** Either (a) move the repo under `github.com/oakstack/` and
update links, or (b) set up a redirect `github.com/oakstack/backchannel`
→ the personal repo (GitHub transfer + redirect is automatic) and use
the org-namespaced URL everywhere.

**Verify.** `git grep -E 'github\\.com/davidiscarvalho/backchannel'`
returns no hits in user-facing files.

---

### T9. Strip HTML entities `&middot;` / `&rarr;` from copy
**Severity:** low (taste) • **Effort:** 10 min
**File:** `backchannel/landing.py`
Use Unicode `·` and `→` directly. Smaller HTML, cleaner reading.

---

### T10. Verify the "Get Instant Key" reveal flow
**Severity:** medium (UX correctness) • **Effort:** 15 min check + fix
if needed
**File:** `backchannel/landing.py` (modal JS).

After `POST /v1/keys` succeeds, the returned key must be shown ONCE
with a copy-to-clipboard button and a visible "you won't see this
again" warning. Today's modal behavior is not verified in the audit.

**Verify.** Manually run the flow on the live site; the key is
displayed, copyable, and the warning is impossible to miss.

---

## This month — polish

### T11. Externalize inline `<script>` from `landing.py`
**Severity:** medium (so CSP can drop `'unsafe-inline'`) • **Effort:** ~1 h
Move inline JS into a hashed `static/landing.js`. Tighten CSP in T2
to remove `'unsafe-inline'` from `script-src`.

---

### T12. Publish `backchannel-mcp` to PyPI
**Severity:** medium (agent install path) • **Effort:** ~1 h
**File:** `mcp_server/pyproject.toml` (if absent, add).
Current docs say `pip install ./mcp_server` — requires `git clone`.
A real PyPI package makes `claude mcp add backchannel -- backchannel-mcp`
a one-liner.

**Verify.** Fresh venv: `pip install backchannel-mcp && claude mcp add
backchannel -- backchannel-mcp` works without the repo present.

---

### T13. `DELETE /v1/keys/me` for self-rotation (was H3)
**Severity:** medium (leaked-key recovery) • **Effort:** ~45 min
**Files:** `backchannel/http.py` (route), `backchannel/store.py`
(`revoke_api_key` already exists, line ~2313), `backchannel/openapi.py`
(spec).

Authenticated DELETE that calls `store.revoke_api_key(auth.key_id)`.
Add to `/llms.txt` and `x-ai-agent-hints`.

**Verify.** Add `tests/test_local_auth.py::test_delete_keys_me_revokes`.
After DELETE, subsequent requests with the same key return 401.

---

### T14. CI hardening (was M1)
**Severity:** medium • **Effort:** ~1 h
**File:** `.github/workflows/ci.yml`

Add three jobs (parallel):
1. `pytest tests/ mcp_server/tests/` (exists).
2. `cd ui && npm ci && npm run build` (would have caught B1).
3. `pip install ruff mypy && ruff check backchannel/ && mypy backchannel/`
   (start with `--ignore-missing-imports`).

Optional: coverage gate at 70% via `pytest --cov=backchannel
--cov-fail-under=70`.

**Verify.** Push a no-op commit; all jobs green.

---

### T15. Backup / restore docs + script
**Severity:** medium (operator confidence) • **Effort:** ~1 h
**Files:** `scripts/backup.sh`, `scripts/restore.sh`, `SELF-HOST.md`
section.

`backup.sh` uses `sqlite3 backchannel.db ".backup /backup/bc-$(date
+%Y%m%dT%H%M%S).db"`. `restore.sh` reverses it with a downtime
warning. `SELF-HOST.md` documents both, plus a `cron` example.

**Verify.** Run backup → delete live DB → restore → app comes back
with same channels/messages.

---

### T16. Worked invitation example end-to-end
**Severity:** medium (the killer story) • **Effort:** ~1 h
**File:** `docs/invitations-flow.md` (new).

Walk: agent A on instance 1 mints invitation; agent B on instance 2
redeems and joins; both post and claim in the shared channel. Include
the actual curl commands. Link from `landing.py` and `agent-guide`.

---

### T17. `x-ai-agent-hints` on every invitation operation (was H4)
**Severity:** medium • **Effort:** ~45 min
**File:** `backchannel/openapi.py`
Audit each `/v1/channel-invitations/*` and `/v1/channels/{id}/invitations`
operation; add `x-ai-agent-hints` with `when_to_use` and
`agent_prompt_snippet`. Mirror the quality of the hints on
`/v1/keys/me` and the agent verbs.

---

### T18. Surface invitation copy-token affordance in SPA
**Severity:** medium (UX) • **Effort:** ~1 h
**File:** `ui/src/views/InvitationsView.vue`
Each row gets a copy-to-clipboard button for the invitation token.
A "create invitation" action visible without diving into a sub-view.

---

## Backlog — keep on the roadmap, not for launch

- **B1.** `waitress` upgrade behind `BACKCHANNEL_WSGI=waitress` env flag,
  with `wsgiref` as dev default (was M2).
- **B2.** `hypothesis`-based property tests for atomicity under
  concurrent claim (was M3). Add a 50-concurrent-claim test in CI.
- **B3.** `backchannel_db_autotrip_armed` Prometheus gauge (was M4).
- **B4.** `pending_webhooks` decision — either ship with an admin API
  + retry policy doc, or fence the table behind a feature flag (was M6).
- **B5.** Mobile / responsive pass on `landing.py`. Hand-rolled Python
  HTML makes this painful; consider moving the landing into the Vue
  build output so the rest of the UI's toolchain applies.
- **B6.** Public status widget on the landing — "X messages handed off
  in the last hour" pulled from `/v1/observability/metrics` (gated to
  a single counter).
- **B7.** In-browser demo agent — click "post task" → see the claim
  happen → see the result. The protocol is small enough that this is
  achievable and would dominate the page's persuasion budget.
- **B8.** Peer discovery on a trusted network (mDNS or Tailscale-aware)
  so personal-multi-instance "my laptop sees my server" works without
  copying a URL.
- **B9.** `x-codeSamples` per OpenAPI operation (curl + Python + TS).
- **B10.** Structured-log schema doc; `backchannel.db` in `.dockerignore`;
  deprecation-policy doc; OpenAPI lint in CI; empty/error states across
  detail views.

---

## Execution discipline (please honor these)

1. **Atomic commits, one task per commit.** Format:
   `<type>(<area>): <one-line>` (e.g. `feat(http): xff parsing with
   trusted-proxy CIDRs`, `fix(nginx): add CSP and referrer-policy`).
2. **Self-host path must not regress.** After each change, `docker
   compose -f docker-compose.self-host.yml up -d --build` and
   `curl localhost:8080/health` must succeed from a fresh clone.
3. **Run the tests after each task.** `pytest tests/ mcp_server/tests/`
   must stay green. Add tests for behavioral changes (T3, T4, T13).
4. **No framework dependencies.** Stay on stdlib (`wsgiref`, `sqlite3`,
   `hashlib`, `secrets`, `hmac`, `ipaddress`). `waitress` is B1, not
   day-1.
5. **No `git add .`.** Stage files individually so commits review cleanly.
6. **Verify with `curl` against the running container** before claiming
   a task done. Don't ship a fix you haven't seen work end-to-end.
7. **If a task surfaces something out of scope, write it under
   "Backlog" in this file and keep moving.** Don't bundle.
