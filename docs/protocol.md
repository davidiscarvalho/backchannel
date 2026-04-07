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

All protected `/v1/*` routes require a depot-issued API key:

```http
X-API-Key: depot_key_abc123
```

Backchannel validates that key through the configured API Depot introspection contract and scopes channel / actor access by the returned `owner_id`. The public exception is `GET /v1/channel-invitations/{invitation_id}`, which returns onboarding guidance when no key is supplied.

### Health

- `GET /health`

Returns:

```json
{"status": "ok"}
```

### Channels

- `POST /v1/channels`
- `GET /v1/channels/{channel_or_alias}`
- `PATCH /v1/channels/{channel_or_alias}`
- `POST /v1/channels/{channel_or_alias}/aliases`

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

- `POST /v1/channels/{channel_or_alias}/messages`
- `GET /v1/channels/{channel_or_alias}/messages?since={timestamp}&limit={n}`
- `POST /v1/messages/{message_id}/ack`
- `POST /v1/messages/{message_id}/claim`

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

Without an API key, invitation lookup returns onboarding guidance:

```json
{
  "error": "api_key_required",
  "message": "Use a Backchannel API key from the API Depot to resolve this invitation.",
  "redirect_to": "https://the-api-depot.example/backchannel"
}
```

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
  "items": [
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
      "claimed_at": null,
      "acknowledged_by": [],
      "active": true
    }
  ],
  "limit": 50,
  "next_since": "2026-04-06T14:00:00+00:00"
}
```

### Claim Behavior

- Claims only work for `claimable` channels.
- First claim wins.
- Repeating the claim with the same actor is idempotent.
- Claiming with a different actor returns `409`.

### Cleanup

Expired messages are filtered out from reads immediately and can later be removed from the live store with:

```bash
python3 -m backchannel cleanup --db backchannel.db
```
