# API Depot Integration

Backchannel trusts `the-api-depot` as the API key control plane.

## Protected Paths

All protected `/v1/*` Backchannel routes require:

```http
X-API-Key: <depot-issued-key>
```

Public paths remain:

- `GET /`
- `GET /health`
- `GET /v1/channel-invitations/{id}` with no key returns onboarding guidance instead of channel details

## Introspection Contract

Backchannel expects an upstream endpoint configured in `BACKCHANNEL_DEPOT_INTROSPECTION_URL`.

Example request from Backchannel to the depot:

```http
GET /internal/backchannel/api-keys/introspect HTTP/1.1
Host: the-api-depot
X-API-Key: depot_key_abc123
Authorization: Bearer <BACKCHANNEL_DEPOT_SERVICE_TOKEN>
```

Expected JSON response:

```json
{
  "active": true,
  "key_id": "key_123",
  "owner_id": "user_456",
  "plan": "free"
}
```

## Ownership Model

- `owner_id` is the tenant boundary inside Backchannel.
- `key_id` is recorded as the key that created a channel or actor.
- Channels and actors are only available to requests authenticated as the same `owner_id`.
- Invitation ids are safe discovery handles for channels, but they still resolve only for the matching `owner_id`.
- This avoids creating a second key-management system inside Backchannel.

## Upstream Work Still Needed In `the-api-depot`

- Add the introspection endpoint described above.
- Authenticate Backchannel itself with a service token or another explicit server-to-server trust mechanism.
- Ensure revoked or inactive keys immediately return `active: false` or an auth failure.
