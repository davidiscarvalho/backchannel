# Server Hardening Spec — Backchannel Public Instance

**Audience:** a Claude Code instance (or operator) running on the server that
hosts the public Backchannel instance. This is a work order. Everything here
is infrastructure / deployment work — it does **not** require changes to the
Backchannel application code, with one optional exception called out in Task 4.

**Goal:** make the public instance survive an adversarial internet — in
particular a flood of requests from an agent (or many agents) that mints
unlimited API keys. The application alone cannot guarantee this; the edge must.

---

## What the application already handles (do NOT re-implement)

The app (commits `item70`/`item71`) already ships, in-process:

- **Per-key request rate limit** — `BACKCHANNEL_RATE_LIMIT` per
  `BACKCHANNEL_RATE_LIMIT_WINDOW` (default 10 / 3600s).
- **Per-IP key-issuance limit** — `POST /v1/keys` is capped at 5/hour per IP.
- **Generic per-channel abuse controls** — every channel supports
  `max_messages` (ring-buffer cap), `max_writes_per_minute` (keyless write
  throttle), and `paused` (per-channel kill switch).
- **Sandbox channel hardening** — the public `sandbox` channel ships with a
  short TTL and aggressive `max_messages` / `max_writes_per_minute`.
- **Admin kill switch** — `POST /v1/admin/channels/{id}/pause` and `/resume`,
  guarded by `X-Admin-Token` == `BACKCHANNEL_ADMIN_TOKEN`.
- **DB-size auto-trip** — the worker auto-pauses the sandbox channel if the
  SQLite file exceeds `BACKCHANNEL_DB_SIZE_LIMIT_BYTES`. It trips **once per
  worker lifetime**: after the operator resumes the channel, auto-protection
  does not re-arm until the worker restarts. Treat a trip as an incident, not
  a self-healing event.

**The gap this spec closes:** the per-key limit is useless against an attacker
who mints unlimited keys, and the app's HTTP server is `wsgiref.simple_server`
— **single-threaded**. One slow or flooded endpoint serializes *every* request.
Pausing the sandbox channel does not help if the flood targets `/v1/keys` or
any other route. The edge must absorb volume before it reaches the app.

---

## Task 1 — Put a reverse proxy in front of the app

The app currently binds `0.0.0.0:8080` (see `docker-compose.server.yml`). It
must **not** be the public listener.

1. Bind the app to the internal network only; expose **only** the proxy (443).
2. Use **Caddy** (recommended — automatic TLS, tiny config) or nginx.
3. Terminate TLS at the proxy. Force HTTPS.

Minimal Caddyfile:

```
backchannel.oakstack.eu {
    encode zstd gzip
    request_body {
        max_size 128KB
    }
    reverse_proxy backchannel_app:8080 {
        # see Task 4 for multi-process upstreams
    }
}
```

`128KB` body cap: the app accepts 64KB message content; 128KB leaves headroom
for JSON envelope overhead and rejects oversized bodies at the edge.

---

## Task 2 — Edge rate limiting (the primary defense)

Rate-limit by **client IP**, at the proxy, before requests reach the app.

1. **Global per-IP request limit** — e.g. 60 requests / minute / IP, burst 20.
2. **Strict limit on `POST /v1/keys`** — this endpoint is the root of the
   key-minting abuse vector. Cap it hard: e.g. **5 requests / hour / IP**
   (matching the app's own internal limit, enforced again at the edge so the
   request never even reaches the single-threaded app).
3. Return `429` with `Retry-After` when exceeded.

Caddy (with the `caddy-ratelimit` plugin) or nginx `limit_req_zone` both do
this. nginx example:

```nginx
limit_req_zone $binary_remote_addr zone=global:10m rate=60r/m;
limit_req_zone $binary_remote_addr zone=keys:10m   rate=5r/h;

location /v1/keys   { limit_req zone=keys  burst=2 nodelay; proxy_pass http://backchannel; }
location /          { limit_req zone=global burst=20 nodelay; proxy_pass http://backchannel; }
```

**Note on IPs:** a determined attacker rotates IPs. Edge IP limiting raises the
cost and stops casual / single-source floods; it is not absolute. Combine with
Task 5 (kill switch) and Task 6 (monitoring) for the rest.

---

## Task 3 — Connection caps and timeouts

1. **Max concurrent connections per IP** — e.g. 20 (nginx `limit_conn`).
2. **Total connection cap** — protect the single-threaded app; e.g. 256.
3. **Timeouts** — proxy read/send timeout ~30s; drop slow-loris connections
   (slow request-body / header attacks). Caddy handles this by default;
   nginx: set `client_body_timeout`, `client_header_timeout`, `send_timeout`.

---

## Task 4 — The single-threaded server (concurrency ceiling)

`wsgiref.simple_server` serves **one request at a time**. Even with a perfect
edge, a handful of concurrent slow requests will queue everything.

**Recommended fix: run N app processes behind the proxy.** This needs **zero
application code changes** — SQLite WAL mode (already enabled) supports
multiple processes sharing `/data/backchannel.db`. The single `worker` process
stays as-is.

- Run e.g. 4 `serve` processes (4 containers, or one container with a process
  manager) on 4 ports.
- Point the proxy upstream pool at all 4.
- **Use IP-hash / sticky upstreams.** The app's per-key rate limiter is
  in-memory and **per-process** — without sticky routing a client spreads
  across processes and gets ~N× its intended limit. `ip_hash` (nginx) or
  `lb_policy ip_hash` (Caddy) keeps a client pinned to one process so per-key
  limits stay meaningful.

Example nginx upstream:

```nginx
upstream backchannel {
    ip_hash;
    server backchannel_app_1:8080;
    server backchannel_app_2:8080;
    server backchannel_app_3:8080;
    server backchannel_app_4:8080;
}
```

**Alternative (needs app code change — do NOT do this without owner sign-off):**
make the server multithreaded by mixing `socketserver.ThreadingMixIn` into the
wsgiref server in `backchannel/__main__.py`. If you do this, the in-memory
`SlidingWindowRateLimiter` in `backchannel/rate_limit.py` becomes a data race —
its `deque`/`defaultdict` are mutated without locks. You would have to add a
`threading.Lock` around `check`/`track`/`enforce`. The multi-process approach
above avoids all of this and is preferred.

---

## Task 5 — Edge-level kill switch (defense in depth)

The app's admin kill switch (`/v1/admin/channels/{id}/pause`) is the primary
control. Add an **independent** edge switch that works even if the app is
wedged or unresponsive:

1. Keep a proxy config snippet that returns `503` for chosen paths
   (e.g. `POST /v1/keys`, `/v1/channels/sandbox/*`).
2. Document the one-command enable/disable (a commented `location` block, or
   a Caddy matcher toggled by reloading config).
3. This is for emergencies — when the app itself cannot be reached to call its
   admin API.

---

## Task 6 — Monitoring and alerting

1. **Disk usage alert** — alert before the `BACKCHANNEL_DB_SIZE_LIMIT_BYTES`
   auto-trip fires, so a human investigates first.
2. **Health** — poll `GET /health` (already used by the compose healthcheck).
3. **Metrics** — `GET /metrics` exposes Prometheus metrics; scrape them.
4. **Rate-limit signal** — watch proxy `429` rate; a spike is an attack.
5. **Security audit** — `GET /v1/security/audit` records key issuance, channel
   pause/resume, etc. Surface it.
6. Optionally add fail2ban (or the proxy's own IP-ban feature) to ban IPs that
   sustain `429`s.

---

## Task 7 — Process supervision and backups

1. **Restart policy** — `restart: unless-stopped` is already set in
   `docker-compose.server.yml`. Keep it.
2. **Wire the worker's environment.** The `worker` service in
   `docker-compose.server.yml` currently has **no `env_file`** — so
   `BACKCHANNEL_DB_SIZE_LIMIT_BYTES` and the `BACKCHANNEL_SANDBOX_*` knobs
   cannot be tuned for it. Add `env_file: .env` to the `worker` service.
   (This repo's `item71` commit already adds it; verify it is present on the
   server's checkout.)
3. **SQLite backups** — schedule `sqlite3 /data/backchannel.db ".backup ..."`
   (WAL-safe) off-box. The data is ephemeral by design, but the `api_keys`
   table is not — losing it invalidates every issued key.

---

## Configuration reference (env vars)

Set these in the server's `.env` (consumed by both `app` and `worker` —
see Task 7.2):

| Variable | Default | Purpose |
|---|---|---|
| `BACKCHANNEL_ADMIN_TOKEN` | _(unset)_ | Enables `/v1/admin/*`. Set to a long random secret. **Required** for the kill switch. |
| `BACKCHANNEL_DB_SIZE_LIMIT_BYTES` | `1073741824` | Worker auto-pauses the sandbox if the DB exceeds this. `0` disables. |
| `BACKCHANNEL_SANDBOX_TTL_SECONDS` | `600` | Sandbox message TTL (clamped 300–2592000). |
| `BACKCHANNEL_SANDBOX_MAX_MESSAGES` | `200` | Sandbox ring-buffer cap. |
| `BACKCHANNEL_SANDBOX_MAX_WRITES_PER_MINUTE` | `60` | Sandbox keyless write throttle. |
| `BACKCHANNEL_RATE_LIMIT` | `10` | Per-key request limit. |
| `BACKCHANNEL_RATE_LIMIT_WINDOW` | `3600` | Per-key limit window (seconds). |

---

## Acceptance criteria

- [ ] The app is not reachable except through the proxy on 443 (TLS).
- [ ] `POST /v1/keys` from one IP is throttled to ≤ 5/hour at the edge.
- [ ] A general request flood from one IP gets `429` at the edge, and other
      IPs / endpoints stay responsive during it.
- [ ] Request bodies over ~128KB are rejected at the edge.
- [ ] `BACKCHANNEL_ADMIN_TOKEN` is set; `POST /v1/admin/channels/sandbox/pause`
      with the token returns `200` and blocks sandbox writes.
- [ ] The `worker` service receives `.env` (auto-trip + sandbox knobs tunable).
- [ ] Multiple app processes run behind an ip-hash upstream (or the
      single-threaded ceiling is explicitly accepted and documented).
- [ ] Disk-usage and `429`-rate alerts are wired.
