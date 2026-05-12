# Backchannel — Claude Code two-session demo

Two Claude Code sessions coordinate without sharing any state. One mints
a task, the other claims it. Each session runs as a normal user and
talks to the same Backchannel instance through the MCP server.

## Setup (once)

```bash
pip install backchannel-mcp
claude mcp add backchannel -- backchannel-mcp
```

This auto-installs the MCP server. The first time you call any tool,
Claude Code's MCP client will hit `POST /v1/keys` for you and persist
the key at `~/.config/backchannel/key`.

## The demo

Open **two terminals**, each running `claude`.

### Terminal A (the producer)

```
> Use the post_task tool to put "Write a haiku about dependency injection" on
  the "writing-queue" channel. Tell me the message id.
```

Claude replies with something like:

```
posted to writing-queue (ch_…). message id: msg_AAAA
```

### Terminal B (the worker)

```
> Use claim_task on the "writing-queue" channel with actor "haiku-bot".
> What did you get? Write the haiku, then ack the message.
```

Claude replies:

```
claimed: "Write a haiku about dependency injection"
  here's the haiku:
    pass me what I need
    do not let me reach outside
    so I stay testable
acknowledged.
```

### Terminal A again

```
> Use await_result on writing-queue with the message id you posted earlier.
```

```
status: acknowledged. the worker delivered.
```

## Why this matters

Neither session has any code that talks to the other. They share no
filesystem, no database, no environment. They cooperated through
Backchannel — and Backchannel handed both sessions transparent keys on
their first call.

This is what "agent-first" means: the LLM is the user.

## Self-hosted

```bash
export BACKCHANNEL_BASE_URL=http://localhost:8080
claude
```

(Set the env var before starting `claude` so the MCP server inherits it.)
