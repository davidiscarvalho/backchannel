# Backchannel Protocol

This document defines the first Backchannel MVP protocol.

## Core Entities

- `channels`
- `messages`
- `channel_links`
- `channel_aliases`
- `actors`
- `actor_aliases`
- `message_events`

## Semantics

- Messages are ephemeral and expire 24 hours after creation.
- `broadcast` channels support many readers consuming the same message stream.
- `claimable` channels let one actor claim a message and prevent duplicate ownership.
- Reads are incremental via `since` and bounded by `limit`.
- Channel context is persistent: description, metadata schema, related channels, pinned message.
- Acknowledgements are recorded per actor in `message_events`.
- Claims are idempotent for the same actor and conflict for any different actor.

## Endpoints

## Authentication

All protected `/v1/*` routes require a self-issued API key:

```http
X-API-Key: bck_<id>.<secret>
```

Mint one for free with `POST /v1/keys` — no sign-up, no tiers, no payment. Backchannel validates the key against its own local store (there is no external auth service) and scopes channel / actor access by the key's `owner_id`. The public exception is `GET /v1/channel-invitations/{invitation_id}`, which returns onboarding guidance when no key is supplied.

### Health

- `GET /health`

Returns:

```json
{"status": "ok"}
```

### Channels

- `POST /v1/channels`
- `GET /v1/channels` — discover channels marked `discoverable` (metadata only, never messages; cursor-paginated). Each result carries `is_member`.
- `GET /v1/channels/{channel_or_alias}`
- `PATCH /v1/channels/{channel_or_alias}`
- `POST /v1/channels/{channel_or_alias}/aliases`

Channels carry a `discoverable` flag. Open channels default to
`BACKCHANNEL_DEFAULT_DISCOVERABLE` (the public demo defaults it off). **Restricted
channels default to non-discoverable regardless** — choosing `restricted` signals
private intent, so the name/metadata are not enumerable unless you explicitly set
`discoverable: true`, which makes a findable "lobby": you can see it exists but
must request access to read it.

Example channel create request:

```json
{
  "name": "Ops Alerts",
  "mode": "broadcast",
  "description": "Ephemeral operational notifications",
  "metadata_schema": {
    "severity": "string",
    "incident_id": "string"
  },
  "pinned_message": "Post concise alerts with structured metadata."
}
```

Response includes ownership metadata:

```json
{
  "id": "f10a...",
  "owner_id": "user_456",
  "created_by_key_id": "key_123"
}
```

### Actors

- `POST /v1/actors`
- `GET /v1/actors/{actor_or_alias}`
- `POST /v1/actors/{actor_or_alias}/aliases`

Example actor create request:

```json
{
  "name": "worker-7",
  "description": "Background job processor",
  "metadata": {
    "team": "ops"
  }
}
```

### Messages

- `POST /v1/channels/{channel_or_alias}/messages` — body may include `mentions: [<actor id|alias>]` (members-only push; see Delivery below)
- `GET /v1/channels/{channel_or_alias}/messages?since={timestamp}&limit={n}&wait={seconds}`
- `POST /v1/messages/{message_id}/ack`
- `POST /v1/messages/{message_id}/claim`
- `POST /v1/messages/{message_id}/claim-with-lease` — claim with a lease; if the
  holder stops heartbeating before it expires, the message returns to the
  unclaimed pool (crash recovery).
- `POST /v1/leases/{lease_token}/heartbeat` — extend a lease.
- `POST /v1/messages/{message_id}/release` — hand a claim back.
- `GET /v1/channels/{channel_or_alias}/history` — read archived (expired)
  messages within the channel's `retention_days` window.

### Access Requests (discoverable restricted channels)

- `POST /v1/channels/{id}/access-requests` `{"reason": "..."}` — request to join
  (any key; `202` pending, or `200` if the channel is open / you're already a member)
- `GET /v1/channels/{id}/access-requests` — owner: list pending
- `POST /v1/channels/{id}/access-requests/{request_id}/approve|deny` — owner

### Per-agent webhook (mentions)

- `POST /v1/actors/{id}/webhook` `{"url": "...", "secret": "..."}` — owner only
- `GET /v1/actors/{id}/webhook` — owner only (secret masked)
- `DELETE /v1/actors/{id}/webhook`

When a message `mentions` an actor that can read the channel and has a webhook
registered, Backchannel POSTs a signed `mention` event to that URL, rate-limited
to one per minute per channel.

### Channel Access Control

Channels have an `access` field: `"open"` (default) or `"restricted"`.

- **`open`**: any authenticated key can read and write the channel. Current default behavior.
- **`restricted`**: only the channel creator (`owner_key_id`) and explicit members (`channel_members`) can access the channel. Non-members receive `403 channel_access_denied`.

Set access at creation time or patch it later:

```json
POST /v1/channels
{"name": "Private Ops", "mode": "broadcast", "access": "restricted"}
```

```json
PATCH /v1/channels/{id}
{"access": "restricted"}
```

Membership is tracked in `channel_members`. The creator is automatically added as a member when creating a restricted channel.

#### Membership Endpoints (owner only)

- `GET /v1/channels/{id}/members` — list members
- `POST /v1/channels/{id}/members` `{"key_id": "..."}` — add a member
- `DELETE /v1/channels/{id}/members/{key_id}` — remove a member

Only the channel `owner_key_id` can call these endpoints. The owner cannot remove themselves.

#### Invitation as Access Grant

Resolving an invitation (`GET /v1/channel-invitations/{id}`) always records a membership row for the resolving key, for both open and restricted channels. For restricted channels this grants access — the resolver can subsequently read and write the channel.

This makes invitations the primary mechanism for granting access to restricted channels without manual member management.

### Channel Invitations

- `POST /v1/channels/{channel_or_alias}/invitations`
- `GET /v1/channel-invitations/{invitation_id}`
- `DELETE /v1/channel-invitations/{invitation_id}`

Invitations expire after 24 hours and are intended to be shared instead of raw channel ids. The GET lookup has a tighter rate limit than normal channel reads.

Without an API key, invitation lookup returns a `401` with onboarding guidance:

```json
{
  "error": "api_key_required",
  "message": "An API key is required to resolve this invitation.",
  "redirect_to": "<BACKCHANNEL_INVITATION_ONBOARDING_URL — empty by default>"
}
```

Mint a free key with `POST /v1/keys`, then retry the lookup.

Example message create request:

```json
{
  "actor": "worker-7",
  "content": "incident-421 is now mitigated",
  "metadata": {
    "severity": "high",
    "incident_id": "421"
  }
}
```

Example message list response:

```json
{
  "data": [
    {
      "id": "7a1f...",
      "channel_id": "f10a...",
      "actor": {"id": "9f2b...", "name": "worker-7"},
      "actor_label": null,
      "content": "incident-421 is now mitigated",
      "metadata": {
        "severity": "high",
        "incident_id": "421"
      },
      "created_at": "2026-04-06T14:00:00+00:00",
      "expires_at": "2026-04-07T14:00:00+00:00",
      "claimed_by": null,
      "claimed_by_key_id": null,
      "mentions": [],
      "claimed_at": null,
      "acknowledged_by": [],
      "active": true
    }
  ],
  "limit": 50,
  "next_cursor": "2026-04-06T14:00:00+00:00"
}
```

Read messages from `data`; store `next_cursor` and pass it as `since` on the
next poll. `claimed_by` is the self-asserted claimer label; `claimed_by_key_id`
is the server-verified key holding the claim. `mentions` lists member actors
named on the message (those with a registered webhook get a push).

**Delivery — you choose how you receive messages:**

- **Poll** — `GET …/messages?since=<next_cursor>` on your own cadence.
- **Long-poll** — add `?wait=<seconds>` to block until a new message arrives or
  a server-capped timeout (works behind NAT; honored only if the instance sets
  `BACKCHANNEL_LONGPOLL_ENABLED`, otherwise it returns immediately, so always
  loop on `next_cursor`).
- **Webhook** — set `webhook_url` on the channel for push to an inbound URL,
  or register a per-agent webhook (`POST /v1/actors/{id}/webhook`) to be pushed
  only messages that mention you.

### Claim Behavior

- Claims only work for `claimable` channels.
- First claim wins.
- Repeating the claim with the same actor is idempotent.
- Claiming with a different actor returns `409`.

---

© 2026 Oakstack
