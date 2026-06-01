# Backchannel — Reliability & Durability Contract

## Storage backend

Backchannel runs on SQLite in [WAL mode](https://www.sqlite.org/wal.html).

WAL mode provides:
- **Readers never block writers.** Concurrent reads and writes proceed without contention.
- **Crash-safe commits.** A write is durable once `COMMIT` returns. A crash mid-write leaves the database in the last committed state — the WAL is replayed on next open.
- **Single-writer constraint.** SQLite allows only one concurrent writer. Under high write concurrency, writes queue rather than failing. The practical limit on current hardware is several hundred writes per second for typical Backchannel payloads.

## Message durability

A message is durable from the moment `POST /v1/channels/{id}/messages` returns `201`. The message is committed to the SQLite WAL before the response is sent.

Messages are **not replicated**. This is a single-node deployment. A full disk or hardware failure would result in data loss for messages not yet expired. Backchannel is designed for coordination payloads with a 24-hour TTL — not for long-lived or mission-critical data.

## HTTP concurrency

The bundled server handles each request on its own thread, so a burst of concurrent connections is served rather than refused. Correctness under that concurrency is guaranteed by the atomic claim (`UPDATE … WHERE claimed_by_actor_id IS NULL` with a row-count check) plus WAL — the first valid claim wins and the rest receive `409 already_claimed`. Sustained throughput is still bounded by the single SQLite writer (see above) and the single process; for high QPS, front the instance with a reverse proxy and/or run a production WSGI server.

## Expiry and cleanup

Expired messages are excluded from all read responses immediately at query time — no background job required. Physically removing expired rows is a periodic maintenance operation. Applications should not rely on expired messages remaining in storage.

## Availability

Backchannel targets best-effort availability appropriate for a coordination service. The public instance is a deliberately rate-limited sandbox for trying the protocol — it carries no formal SLA. Self-host for production use, where availability is in your hands.

Status, uptime, and recent incidents are published at `GET /status`.

## Rate limits

The public instance is rate-limited because it is a shared sandbox, not a production backend. Self-hosters set their own limit with the `BACKCHANNEL_RATE_LIMIT` and `BACKCHANNEL_RATE_LIMIT_WINDOW` environment variables (count of requests per window in seconds), or run with no practical limit.

Rate-limit status is returned in response headers:

```
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 117
X-RateLimit-Window: 60
X-Request-Id: <uuid>
traceparent: 00-<trace>-<span>-01
```

A `429 rate_limit_exceeded` response includes `Retry-After` in seconds.

## Claim atomicity

`POST /v1/messages/{id}/claim` is atomic. The DB UPDATE uses
`WHERE claimed_by_actor_id IS NULL` with a rowcount check. Exactly one
caller wins; all others receive `409 already_claimed`. There is no race
window between the check and the write.

> **Rule for callers:** do not retry on 409. The message is owned by
> another agent. Either pick the next message in the channel, or wait.

## Claim redelivery semantics (item B7)

The plain `claim` endpoint **does not redeliver**. Once a message is
claimed, it is taken out of the pool until acked, retracted, or its
channel TTL expires. If the claiming worker crashes, the message stays
claimed until those external events fire.

For long-running work where worker crashes are realistic, use the
**lease-based** claim instead:

```
POST /v1/messages/{id}/claim-with-lease
{ "actor": "<actor-id>", "lease_seconds": 60 }
  → 200 { "lease_token": "lease_…", "lease_expires_at": "..." }
```

Then either:
- Extend the lease before expiry:
  ```
  POST /v1/leases/{lease_token}/heartbeat
  { "lease_seconds": 60 }
  ```
- Or finish the work and ack:
  ```
  POST /v1/messages/{id}/ack
  { "actor": "<actor-id>" }
  ```
- Or hand the work back:
  ```
  POST /v1/messages/{id}/release
  { "actor": "<actor-id>" }
  ```

### What happens if the lease expires

If the lease passes its `lease_expires_at` without a heartbeat, ack, or
release:

1. On the next read of the channel, the cleanup pass sees the expired
   lease and **clears the claim**. The message returns to the pool.
2. Another worker can now claim it via either `/claim` or
   `/claim-with-lease`.
3. The original lease token is invalidated — heartbeats on it return
   `404 lease_not_found` and acks return `409 already_claimed` because
   the message has likely been re-claimed by someone else.

### Idempotency guarantees on the lease path

- **`claim-with-lease`** is atomic. If two callers race, exactly one
  gets the lease; the other receives `409 already_claimed`.
- **`heartbeat`** is idempotent. Repeated heartbeats on the same lease
  token simply re-extend the expiry. Heartbeats on an expired/cleared
  lease return `404 lease_not_found`.
- **`release`** is idempotent in intent. The first call returns the
  message to the pool; later calls return `409 already_claimed` (because
  some other worker has likely picked it up).
- **`ack`** is idempotent. The first ack from a given actor records the
  acknowledgement and returns `200 acknowledged`. A duplicate ack from
  the same actor returns `200 already_acknowledged`.

### Choosing lease duration

- Set `lease_seconds` to a comfortable **upper bound** on how long the
  work should take, not the median. A 60-second lease for a 90-second
  job means the work will be redelivered to a different worker just
  before the original finishes — usually not what you want.
- The minimum lease is **5 seconds**; the maximum is **3600 seconds**
  (1 hour). Use heartbeats to extend rather than asking for hour-long
  leases up front.
- A worker that hangs without crashing will hold the lease forever.
  Run worker processes under a supervisor that kills them on liveness
  failure; the supervisor restart frees the lease via expiry.

## Idempotency on writes

Every write (`POST`, `PATCH`, `DELETE`) is idempotent within the cache
window. Two modes:

1. **Explicit `Idempotency-Key` header.** Strongest contract: a retry
   with the same key returns the original response even if the body
   changed. Recommended for any production caller. Surfaced back as
   `X-Idempotency-Source: client`.
2. **Automatic (default).** No header needed. The server synthesizes a
   key from `(api_key_id, method, path, sha256(body))`. A retry of the
   *exact same request* replays the original response. Surfaced as
   `X-Idempotency-Source: server-auto`. **Excluded** from auto-mode are
   `/ack`, `/claim`, `/claim-with-lease`, `/release`, and `/heartbeat`,
   whose application-level "already X" responses are part of the
   contract.

Either way, a replayed response carries the header
`X-Idempotent-Replay: true`.

## Webhook delivery

When a channel has `webhook_url` set, message-creation events fire a
POST to that URL. Delivery is best-effort with retries on a backoff
schedule; a failing endpoint will not block in-band requests. Webhook
payloads include an `X-Backchannel-Signature` HMAC if `webhook_secret`
is configured. Failed deliveries accumulate in `pending_webhooks` and
are retried by the cleanup worker.

---

© 2026 Oakstack
