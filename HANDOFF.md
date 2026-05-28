# Backchannel — Fix Session Handoff

> Meta note: this handoff itself is an instance of the use case the
> product is built for — passing work between agent sessions without
> a human re-explaining the context. The instructions below are
> self-contained; you will not see the conversation that produced
> them.

## Product framing (read first — this constrains every decision below)

Backchannel is a **layer that agents use to communicate**. The
primitive is the same across use cases, but the *audience* shapes
priorities:

- **Primary case: one person, multiple agent contexts.** A user runs
  Claude Code on their laptop and on a remote server. Instead of
  copy-pasting prompts between them, the two instances talk via
  backchannel.
- **Secondary case: small communities sharing channels** via
  invitation tokens. Not multi-tenant SaaS — just "I sent you an
  invite, your agent joined my channel".
- **Constraint: agent-driven setup.** The instance should be simple
  enough that an agent can stand it up unattended (mint its own key,
  share an invitation, start coordinating) — no human in the loop
  for the integration path.

**MIT, free, self-hosted.** Not a SaaS, not a commercial product.
The public instance at `https://backchannel.oakstack.eu` is a
deliberately throttled demo sandbox to let people try the protocol
without breaking the server. The self-host stack
(`docker-compose.self-host.yml`) is the product.

Full context lives in `AUDIT.md` at the repo root. Read it if you
need the why behind any item below; the *what* is here.

## Working rules

1. **Self-host install path is the product.** Whatever you change
   must not regress `docker compose -f docker-compose.self-host.yml
   up -d --build` for a fresh clone.
2. **Atomic commits, one fix per commit.** Format:
   `fix(<area>): <one-line>` plus a short body. No `git add .` —
   stage files individually.
3. **Verify after each fix.** Don't move on until the current fix
   has the verification step below returning the expected result.
4. **No framework dependencies.** Stay on `wsgiref` + SQLite. The
   `waitress` upgrade is post-launch.
5. **Don't bundle post-launch items into the blocker fixes.** Keep
   the launch diff small and reviewable.

## Blockers — land both, in order

### B1. Remove legacy "API Depot" copy from the SPA login

**Severity:** BLOCKER • **Effort:** ~15 min • **Category:** frontend

**File:** `ui/src/views/LoginView.vue`

**Background.** Commits `item73` and `item74` scrubbed "API Depot",
"tier", and "x402" language from prose and Python code after the
project moved away from a commercial framing. The SPA was missed.
The maintainer has confirmed this is a leftover bug — there is no
"API Depot", just Backchannel.

**Current strings** (all in `LoginView.vue`):
- Line 5 (sub-line under the logo): `"Enter your API Depot key to continue."`
- Input placeholder: `"depot_key_…"`
- Footer link: `<a href="https://the-api-depot.example" target="_blank">Get one at the API Depot →</a>`

**Change.**
1. Sub-line → `"Paste your Backchannel API key to continue."`.
2. Placeholder → verify the actual key prefix in
   `backchannel/auth.py` (look at `mint_raw_key`); use that. If the
   prefix is `bc_`, use `"bc_…"`. If there is no prefix convention
   yet, use a generic `"Your API key"`.
3. Footer → replace the external link with an inline `curl` snippet
   in a `<pre>` block, so a self-hoster sees how to mint a key
   against their own instance. Use a relative path; the SPA is
   co-served with the API behind the same proxy:

   ```
   Don't have a key yet? Mint one against this instance:

   curl -X POST /v1/keys -H 'Content-Type: application/json' \
     -d '{"agent_label":"my-agent"}'
   ```

   A copy-to-clipboard button is a nice-to-have, not required for
   this blocker.

**Verify.**
- `cd ui && npm run build` succeeds.
- `git grep -i 'api depot\|depot_key\|the-api-depot' -- ':!AUDIT.md'
  ':!HANDOFF.md'` returns no hits.
- Open the built page in a browser (or visually inspect the diff).
  No reference to a domain you don't control.

**Done when:** the build is green, the grep is clean, the page reads
correctly for a brand-new self-hoster.

---

### B2. Self-host compose must read `.env`

**Severity:** BLOCKER • **Effort:** ~10 min plus a smoke test •
**Category:** reliability / infra

**File:** `docker-compose.self-host.yml`

**Background.** `SELF-HOST.md` instructs the operator to
`cp .env.template .env` and fill in values. The self-host compose
does **not** pass `.env` to either the `app` or `worker` service.
Result: the values are silently ignored.
`docker-compose.server.yml` (the demo's compose) does this
correctly — copy the pattern across.

**Without this fix, self-hosters cannot:**
- Set `BACKCHANNEL_ADMIN_TOKEN` (admin pause/resume disabled).
- Tune `BACKCHANNEL_RATE_LIMIT` / `BACKCHANNEL_RATE_LIMIT_WINDOW`.
- Tune `BACKCHANNEL_SANDBOX_*` knobs.
- Set `BACKCHANNEL_DB_SIZE_LIMIT_BYTES`.
- Set `BACKCHANNEL_INSTANCE_KIND` or `BACKCHANNEL_BASE_URL` from
  `.env` (the inline `environment:` defaults always win as written).

**Change.** Add `env_file: .env` to both `app` and `worker` services
in `docker-compose.self-host.yml`. Keep the existing inline
`environment:` block on `app` for the public defaults
(`BACKCHANNEL_BASE_URL`, `BACKCHANNEL_INVITATION_ONBOARDING_URL`,
`BACKCHANNEL_DEMO_KEY`). Docker Compose's precedence is `env_file`
first, then `environment` — so the inline block will override `.env`
for those three keys. If you want `.env` to take precedence for
those too, remove the `${VAR:-}` indirection from the inline block
and rely on the `.env` value. Pick one approach and explain in the
commit message.

**Verify.** From a fresh clone (or after `git stash`):

```bash
cp .env.template .env
# Edit .env: add BACKCHANNEL_ADMIN_TOKEN=test-token-please-change
docker compose -f docker-compose.self-host.yml up -d --build
sleep 5

# 1. Env var visible inside the container?
docker compose -f docker-compose.self-host.yml exec app \
  python3 -c "import os; print(os.environ.get('BACKCHANNEL_ADMIN_TOKEN'))"
# Expect: test-token-please-change

# 2. Worker also gets it?
docker compose -f docker-compose.self-host.yml exec worker \
  python3 -c "import os; print(os.environ.get('BACKCHANNEL_ADMIN_TOKEN'))"
# Expect: test-token-please-change

# 3. Functional: admin endpoint accepts the token from .env?
curl -s -X POST http://localhost:8080/v1/admin/channels/sandbox/pause \
  -H "X-Admin-Token: test-token-please-change" \
  -w '\nstatus: %{http_code}\n'
# Expect: status: 200 (or 204)
```

Tear down:
```bash
docker compose -f docker-compose.self-host.yml down -v
rm .env
```

**Done when:** all three checks return the expected output on a
fresh clone.

---

## Definition of done (both blockers)

- [ ] B1 committed; `git grep -i 'api depot\|depot_key\|the-api-depot'`
      returns nothing outside `AUDIT.md` and `HANDOFF.md`; the SPA
      builds; LoginView reads cleanly for a Backchannel self-hoster.
- [ ] B2 committed; on a fresh clone, `.env` values are observable in
      both `app` and `worker` containers; admin pause endpoint works
      with a token set in `.env`.
- [ ] `pytest tests/ mcp_server/tests/` is green (no regression).
- [ ] One commit per blocker. Commit messages explain the *why*
      (cite the `item73`/`item74` scrub for B1; cite
      `SELF-HOST.md`'s `cp .env.template .env` instruction for B2).

## Final pre-announce checklist

Run from a fresh clone after the two blockers land:

1. `pytest tests/ mcp_server/tests/` — green.
2. `cd ui && npm ci && npm run build` — green.
3. `docker compose -f docker-compose.self-host.yml up -d --build` —
   all three services healthy within 30s; `curl localhost:8080/health`
   returns `{"status":"ok",...}`.
4. Mint a key, log into the SPA at `http://localhost:3000`. The
   login screen contains no reference to "API Depot", `depot_key_`,
   or `the-api-depot`. The inline `curl` snippet for minting works
   against the local instance.
5. With `BACKCHANNEL_ADMIN_TOKEN` set in `.env`, restart, hit
   `POST /v1/admin/channels/sandbox/pause` with the header — 200.
6. `git grep -i 'api depot\|depot_key\|the-api-depot' -- ':!AUDIT.md'
   ':!HANDOFF.md'` — empty.

If all six pass: ship the self-host announcement.

If any fail: do not announce. Fix, re-run the checklist.

## Out of scope for this session

The audit (`AUDIT.md`) identifies four high-priority items, several
mediums, and a small backlog. These are **not** for this session:

- **H1** — `BACKCHANNEL_TRUSTED_PROXIES` / `X-Forwarded-For` parsing
  in `Request.remote_addr`. Affects per-IP defenses behind the
  bundled `ui/nginx.conf`. Two-week horizon.
- **H2** — `X-Forwarded-For` + security headers (CSP, HSTS,
  `X-Content-Type-Options`, `Referrer-Policy`) in `ui/nginx.conf`.
  Pairs with H1.
- **H3** — `DELETE /v1/keys/me` self-service key rotation.
- **H4** — Invitations as a first-class agent flow:
  `x-ai-agent-hints` on every invitation operation, a copy-token
  affordance in `InvitationsView`, a worked example in `docs/` of
  the full cross-instance invite flow.
- All **M-tier** items: CI hardening, `waitress` upgrade, property
  tests, backup scripts, webhooks audit, `pending_webhooks` decision.

Don't bundle these into the blocker PRs. They go on separate
branches, separate PRs, after the launch.

## Why this matters

The maintainer's framing is "a tool for everyone that handles
multiple instances and agents". The two blockers are both
*presentation* failures — a stale string on the login screen and a
silently-ignored `.env`. They don't break the queue. They break the
first 90 seconds of trust a new self-hoster forms. Fixing them is
fast; not fixing them poisons everything downstream.

Go.
