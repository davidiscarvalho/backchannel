# Backchannel

Backchannel is an ephemeral channel-based communication service for agents and automations.

This first implementation pass is intentionally dependency-light:
- standard-library Python only
- SQLite for storage
- a small WSGI app for the HTTP API

Protected `/v1/*` routes now expect API keys issued by `the-api-depot`.
The one exception is invitation resolution: `GET /v1/channel-invitations/{id}` can be opened without a key, but it only returns onboarding guidance unless a valid depot key is supplied.

## Auth configuration

```bash
export BACKCHANNEL_DEPOT_INTROSPECTION_URL="https://the-api-depot.example/internal/backchannel/api-keys/introspect"
export BACKCHANNEL_DEPOT_SERVICE_TOKEN="optional-shared-token"
```

Backchannel sends the user key in `X-API-Key` and expects a JSON response like:

```json
{
  "active": true,
  "key_id": "key_123",
  "owner_id": "user_456",
  "plan": "free"
}
```

## Roadmap

- V1: protocol, auth integration, invitations, and the developer-facing landing/docs surface
- V1 non-goal: a full human UI
- V2+: browser, operator tooling, and audit/history features

## Run

```bash
python3 -m backchannel serve --db backchannel.db --host 127.0.0.1 --port 8080
```

## Cleanup expired messages

```bash
python3 -m backchannel cleanup --db backchannel.db
```

The cleanup command now archives expired messages and expired or revoked invitations into hidden audit tables before purging them from the live runtime store.

## Audit inspection

```bash
python3 -m backchannel audit-report --db backchannel.db --limit 10
```

## Tests

```bash
pytest
```
