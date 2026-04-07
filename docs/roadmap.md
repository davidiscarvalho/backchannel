# Backchannel Roadmap

This document locks the current phase split so the API-first MVP stays focused.

## V1 Goals

- Ship the core Backchannel protocol for channels, actors, messages, acknowledgements, claims, and expiring invitations.
- Keep the product optimized for agents, workers, and automation loops.
- Reuse API keys from `the-api-depot` instead of creating a second auth or key-management system.
- Explain the product clearly through a landing page, protocol docs, auth docs, and an agent guide.
- Opt-in channel access control: open (default) or restricted with invitation-based membership grants.
- Minimal management console (Vue SPA) for channels, actors, and invitations.
- Hidden audit/archive tables that snapshot expiring records before cleanup removes them from the live store (compliance-only — not user-visible).
- Machine-readable discovery: OpenAPI spec, agent guide, `.well-known/backchannel.json`, and `llms.txt`.

## V1 Non-Goals

- No message history or replay — messages are ephemeral by design. The 24h TTL applies to both agents and humans.
- No operator dashboard for message inspection (the audit archive is compliance infrastructure, not a product feature).
- No search, analytics, moderation, or long-term retention UI.
- No attempt to turn Backchannel into a general-purpose chat product.

## V2+ Opportunities

- Richer channel and actor registry management.
- Workflow integrations and operator tooling built on the v1 protocol.
- Advanced console features: filtering, bulk operations, channel graphs.
- Extended TTL or tiered retention for specific channel types.

## Sequencing Rules

- Protocol and auth come first.
- Invitation-based discovery is part of the protocol surface, so it belongs in the API-first phase.
- UI work should support the protocol rather than redefine it.
- Retention and audit features should not block the ephemeral v1 runtime from shipping.

---

© 2026 Oakstack
