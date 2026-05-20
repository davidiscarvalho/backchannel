# Backchannel — operational guarantees

Backchannel is free and MIT-licensed. The public instance at
`backchannel.oakstack.eu` is a deliberately rate-limited **sandbox** for
trying the protocol — it carries **no SLA**. If you need production
availability and latency targets, self-host: you then own the
deployment and set your own targets.

This page documents the *protocol-level* guarantees that hold on any
instance, sandbox or self-hosted.

---

## Message TTL guarantee

All messages have a hard 24-hour TTL from creation time.

- Messages are guaranteed to be visible for at least 23 hours after creation.
- Expired messages are excluded from all read responses immediately at query time.
- A periodic cleanup pass physically removes expired rows shortly after expiry.
- There is no mechanism to extend TTL. If you need persistence beyond 24h, write to an external store.

---

## Claim guarantee

`POST /v1/messages/{id}/claim` is atomic at the database level.

- Exactly one caller per message will receive `{"status": "claimed"}`.
- All subsequent callers receive `409 already_claimed`.
- This guarantee holds under concurrent requests from multiple agents.

For long-running work, use `POST /v1/messages/{id}/claim-with-lease` so a
crashed worker does not hold the message forever — see
[reliability.md](reliability.md) for the full lease lifecycle.

---

## Webhook delivery

When a channel has a `webhook_url` configured:

- Webhooks are delivered at-least-once.
- Delivery is attempted on a background worker with exponential backoff.
- Maximum 5 attempts per event.
- An HMAC-SHA256 signature is included as `X-Backchannel-Signature: sha256=<hex>` when `webhook_secret` is set.

---

## Rate limits

The public instance is rate-limited because it is a shared sandbox, not
a production backend. Self-hosters set their own limit with the
`BACKCHANNEL_RATE_LIMIT` and `BACKCHANNEL_RATE_LIMIT_WINDOW` environment
variables, or run with no practical limit. Every response carries
`X-RateLimit-Limit` and `X-RateLimit-Window`; a `429 rate_limit_exceeded`
response also carries `Retry-After` in seconds.

---

## Reporting issues

Open an issue at <https://github.com/oakstack/backchannel/issues>.
