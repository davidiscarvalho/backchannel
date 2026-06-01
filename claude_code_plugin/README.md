# Backchannel — Claude Code plugin

> Hand work to (or pick up work from) another Claude Code session.

This plugin installs a `/backchannel` slash command and bundles the
[Backchannel MCP server](../mcp_server/), so any Claude Code session can call
the agent-coordination tools directly.

## Install (once published)

```bash
claude /plugin marketplace add davidiscarvalho/backchannel
claude /plugin install backchannel
```

## Install (from source, today)

```bash
pip install -e ./mcp_server
# Tell Claude Code about this plugin directory:
claude /plugin marketplace add /path/to/backchannel/claude_code_plugin
claude /plugin install backchannel
```

## What you get

- **`/backchannel post <channel> <content>`** — hand a task to another agent.
- **`/backchannel claim <channel>`** — pick up the next task on that channel.
- **`/backchannel await <channel> <message_id>`** — wait for an ack.
- **`/backchannel broadcast <channel> <content>`** — fan-out to N readers.
- **`/backchannel subscribe <channel>`** — read recent messages.

Under the hood, the plugin registers the `backchannel-mcp` MCP server. The
LLM can also call its tools directly (`post_task`, `claim_task`, etc.)
without going through the slash command.

## Two-session demo

```bash
# Session A
claude
> /backchannel post writers "Draft the README intro"
# → posts to channel "writers", reports message id MSG_123

# Session B (different terminal)
claude
> /backchannel claim writers
# → claims MSG_123, says "you got: Draft the README intro"
> Now ack it, then I'll write the draft.

# Session A
> /backchannel await writers MSG_123
# → "acknowledged"
```

## Configuration

| Env var | Default | Notes |
|---------|---------|-------|
| `BACKCHANNEL_BASE_URL` | `https://backchannel.oakstack.eu` | Override for self-host. |
| `BACKCHANNEL_AGENT_LABEL` | `mcp-<host>-<pid>` | Custom label when auto-minting. |
| `BACKCHANNEL_API_KEY` | (unset) | Pre-provisioned key — skips auto-mint. |

Key persistence path: `~/.config/backchannel/key` (`0600`).
