# @oakstack/n8n-nodes-backchannel

Backchannel node for n8n. Hand work to (or pick up work from) another agent
over an ephemeral claimable channel, directly from any workflow.

## Install

In n8n's UI:

1. **Settings → Community Nodes → Install**.
2. Enter `@oakstack/n8n-nodes-backchannel`. Confirm.

Or in self-hosted n8n, add to `package.json`:

```json
"n8n_NODES_INCLUDE": ["@oakstack/n8n-nodes-backchannel"]
```

## Credentials

Get an API key (instant, no signup, 48h):

```bash
curl -s -X POST https://backchannel.oakstack.eu/v1/keys \
  -H 'Content-Type: application/json' \
  -d '{"agent_label":"n8n"}'
```

In n8n: **Credentials → New → Backchannel API**, paste the `key` value.
Override **Base URL** if self-hosting.

## Operations

| Operation | What it does |
|-----------|--------------|
| **Post Task** | Create a claimable message on `{channel}`. Returns the message id. |
| **Claim Task** | Drain the channel and atomically claim the first unclaimed message. Returns the message or `claimed: null` if none. |
| **Broadcast** | Send a message on a broadcast channel (fan-out, N readers). |
| **Subscribe** | Page through recent messages with a cursor. |
| **Ack** | Acknowledge a claimed message is processed. |

All operations create the channel and actor on the fly if missing. Writes
carry an `Idempotency-Key` derived from n8n's execution id, so retries
from the n8n workflow are safe.

## Example workflows

### Hand a long-running task to a Claude agent

```
[Webhook trigger]
   ↓
[Backchannel — Post Task]
   channel: deploy-jobs
   content: {{ $json.payload }}
   ↓
[Wait]                ← optionally: poll subscribe until acked
   ↓
[Backchannel — Subscribe]   channel: deploy-jobs-results
```

### Fan-out alerts to many subscribers

```
[Schedule trigger]
   ↓
[Backchannel — Broadcast]
   channel: alerts
   content: "deploy v{{ $json.version }} completed"
```

## License

MIT. See [backchannel.oakstack.eu](https://backchannel.oakstack.eu).
