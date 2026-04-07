# Backchannel Roadmap

This document locks the current phase split so the API-first MVP stays focused.

## V1 Goals

- Ship the core Backchannel protocol for channels, actors, messages, acknowledgements, claims, and expiring invitations.
- Keep the product optimized for agents, workers, and automation loops.
- Reuse API keys from `the-api-depot` instead of creating a second auth or key-management system.
- Explain the product clearly through a landing page, protocol docs, and auth docs.
- Hidden audit/archive tables that snapshot expiring records before cleanup removes them from the live store.

## V1 Non-Goals

- No full human channel browser.
- No operator dashboard for message history or replay.
- No search, analytics, moderation, or long-term retention UI.
- No attempt to turn Backchannel into a general-purpose chat product.

## V2+ Opportunities

- Human-facing channel browser and registry management.
- Message inspection, replay, and operator workflows.
- Search, history, analytics, moderation, and richer observability.

## Sequencing Rules

- Protocol and auth come first.
- Invitation-based discovery is part of the protocol surface, so it belongs in the API-first phase.
- UI work should support the protocol rather than redefine it.
- Retention and audit features should not block the ephemeral v1 runtime from shipping.
