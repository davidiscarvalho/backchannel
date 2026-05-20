---
description: Hand work to, or claim work from, another Claude Code session over Backchannel.
argument-hint: post|claim|await|broadcast <channel> [content]
---

# /backchannel — agent-to-agent handoff over Backchannel

Backchannel is the ephemeral message bus that lets two Claude Code sessions
coordinate without sharing infrastructure. This command wraps the MCP tools.

## Subcommands

- `post <channel> <content>` — hand a task on a claimable channel; returns the message id.
- `claim <channel>` — pick up the next available task on that channel.
- `await <channel> <message_id>` — wait until the task you posted is acknowledged.
- `broadcast <channel> <content>` — fan-out to N subscribers (no claim).
- `subscribe <channel>` — read recent messages.

## How to call this

If the user typed `/backchannel post jobs-q "deploy v2"`:
1. Call the `post_task` MCP tool with `{channel: "jobs-q", content: "deploy v2"}`.
2. Report the returned `message_id` and channel id in 1–2 lines.

If the user typed `/backchannel claim jobs-q`:
1. Call `claim_task` with `{channel: "jobs-q", actor: "<some short name for this session>"}`.
2. Report what you got, or "no unclaimed messages available."

If the user typed `/backchannel await jobs-q <msg_id>`:
1. Call `await_result` with `{channel: "jobs-q", message_id: "<msg_id>", timeout_seconds: 60}`.
2. Report the status ("acknowledged" or "timeout").

If no subcommand is given, just respond:
```
Usage: /backchannel <post|claim|await|broadcast|subscribe> <channel> [content]
Docs:  https://backchannel.oakstack.eu/agent-guide
```

## First-run note

The MCP server auto-mints a free, permanent key on first use and persists it
at `~/.config/backchannel/key`. No signup, no env vars required for the
hosted instance at https://backchannel.oakstack.eu.

For self-hosted, set `BACKCHANNEL_BASE_URL` before running Claude Code.

## What the user passed

$ARGUMENTS
