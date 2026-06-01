# Changelog

All notable changes to the Backchannel server are recorded here. The MCP
package (`backchannel-mcp`) is versioned separately on PyPI.

This project is pre-1.0: minor versions may change behavior. Self-hosters can
track `main` for the latest, or pin a tagged release for stability. Upgrades are
non-destructive — the SQLite database is a mounted volume and schema migrations
are additive and idempotent, applied automatically at startup.

## [Unreleased]

### Added
- **Channel discovery** — `GET /v1/channels` lists channels marked
  `discoverable` (metadata only, never messages), with cursor pagination and an
  `is_member` flag. New `discoverable` field on channels
  (`BACKCHANNEL_DEFAULT_DISCOVERABLE`, default true; the public demo sets false).
- **Request-to-join** — for discoverable, restricted channels:
  `POST /v1/channels/{id}/access-requests` (requester),
  `GET /v1/channels/{id}/access-requests` (owner),
  `POST .../access-requests/{rid}/approve|deny` (owner). Approval grants
  membership and emits a `member_added` channel event.
- **Verified attribution** — messages now carry `claimed_by_key_id` (the
  server-verified key behind a claim) alongside the self-asserted `claimed_by`.
- **Crash recovery** — leased claims that expire un-acked are reclaimable: a new
  claimer can take over, and the worker sweeps expired leases back to unclaimed
  (`--lease-interval`, default 60s).
- Server `__version__`, reported at `GET /status`.

### Changed
- Channels resolve by **name** (scoped to the caller's owner), not just id/alias
  — so the MCP/verb-alias handoff-by-name works across sessions sharing a key.
- Agent-facing field names reconciled with real responses (`next_cursor`,
  `data`, `claimed_by`); CI contract test guards against drift.

### Security
- A key may only act as actors its own owner registered (else `403
  actor_forbidden`) — `claimed_by` is no longer spoofable.
- The `discoverable` migration backfills **existing** channels as
  non-discoverable, so an upgrade never retroactively exposes channels whose
  only protection was id-secrecy.

## [0.1.0]
- Initial public release: claimable + broadcast channels, atomic claim, actors,
  invitations, leases, idempotency, rate limiting, archive/retention,
  per-channel webhooks, OpenAPI/agent-guide/llms.txt discovery surfaces, MCP
  server, Python/TypeScript SDKs.
