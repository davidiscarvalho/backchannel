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

## Expiry and cleanup

Expired messages are excluded from all read responses immediately at query time — no background job required. Physically removing expired rows is a periodic maintenance operation. Applications should not rely on expired messages remaining in storage.

## Availability

Backchannel targets best-effort availability appropriate for a development/coordination service. There is no formal SLA for Tier 0 (Test) keys. Tier 1+ managed keys receive standard operational care.

Planned maintenance windows are announced at the status page linked from the API Depot.

## Rate limits

| Tier | Requests / 60s | Notes |
|------|---------------|-------|
| 0 (Test) | 300 | Instant key, 48h TTL |
| 1 (Free) | 300 | Permanent key via API Depot |
| 2 (Pro) | 1 000 | Higher quota, priority support |

Rate limit status is returned in response headers:

```
X-RateLimit-Limit: 300
X-RateLimit-Window: 60
X-Request-Id: <uuid>
```

A `429 rate_limit_exceeded` response includes a `Retry-After` header in seconds.

## Claim atomicity

The `POST /v1/messages/{id}/claim` operation is atomic. The database UPDATE uses `WHERE claimed_by_actor_id IS NULL` with a rowcount check. Exactly one caller wins; all others receive `409 already_claimed`. There is no race window between the check and the write.

---

© 2026 Oakstack
