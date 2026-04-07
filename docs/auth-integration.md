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

- `owner_id` is an audit field recording who created a resource — it is not an access gate.
- `key_id` is the caller's identity used for access control in restricted channels.
- By default, any authenticated key can read and write any channel (`access: open`).
- Restricted channels (`access: restricted`) require explicit membership. The channel
  creator is auto-added; others gain access by resolving an invitation, or by being
  added manually by the channel owner.
- Cross-owner access is intentional — Backchannel is single-tenant by design.
- Invitation resolution is always cross-key: any authenticated key can resolve any
  active invitation regardless of who created it.

## Upstream Work Still Needed In `the-api-depot`

- Add the introspection endpoint described above.
- Authenticate Backchannel itself with a service token or another explicit server-to-server trust mechanism.
- Ensure revoked or inactive keys immediately return `active: false` or an auth failure.

---

© 2026 Oakstack
