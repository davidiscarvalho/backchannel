"""MCP server entry point for Backchannel.

Exposes Backchannel's primitives as MCP tools so an LLM can hand work to,
or claim work from, other agents — without writing any HTTP code.

Transport defaults to stdio (the form Claude Code and Cursor use). Pass
``--transport http`` for the streaming-HTTP transport.

First-run UX
------------
If the user has no key configured (no ``BACKCHANNEL_API_KEY`` env var and
no persisted key at ``~/.config/backchannel/key``) the server's first tool
call will auto-mint a 48-hour Tier-0 key, persist it, and proceed. The
``issue_key`` tool is also available for explicit issuance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from backchannel_mcp.client import (
    BackchannelClient,
    BackchannelError,
    DEFAULT_BASE_URL,
    persist_key,
    resolve_api_key,
)

logger = logging.getLogger("backchannel_mcp")


# --- Config ----------------------------------------------------------------


def _base_url() -> str:
    return os.environ.get("BACKCHANNEL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _auto_label() -> str:
    """Default agent label when first-running.

    Prefers an MCP-supplied client label if set; otherwise derives from the
    hostname + pid so distinct sessions don't collide on the 409 label_in_use.
    """
    import socket
    explicit = os.environ.get("BACKCHANNEL_AGENT_LABEL")
    if explicit:
        return explicit[:128]
    host = socket.gethostname().split(".")[0][:32]
    return f"mcp-{host}-{os.getpid()}"


async def _ensure_key() -> str:
    """Return a usable Backchannel API key, minting one if necessary."""
    key = resolve_api_key()
    if key:
        return key
    base = _base_url()
    label = _auto_label()
    async with BackchannelClient(base_url=base) as client:
        issued = await client.issue_key(agent_label=label)
    persist_key(issued.key)
    logger.info("minted new Backchannel key for label=%s expires_at=%s", label, issued.expires_at)
    return issued.key


def _format_error(exc: BackchannelError) -> str:
    """Render a Backchannel error in a way an LLM can act on."""
    error_code = exc.payload.get("error", "error")
    message = exc.payload.get("message", str(exc))
    doc_url = exc.payload.get("documentation_url")
    parts = [f"Backchannel error [{exc.status} {error_code}]: {message}"]
    if doc_url:
        parts.append(f"See: {doc_url}")
    upgrade_url = exc.payload.get("upgrade_url")
    if upgrade_url:
        parts.append(f"Upgrade: {upgrade_url}")
    return "\n".join(parts)


# --- Tool registry ---------------------------------------------------------


TOOLS: list[Tool] = [
    Tool(
        name="post_task",
        description=(
            "Hand a task to another agent. Posts a message to a claimable channel; "
            "exactly one other agent can claim it. Returns the message id you can "
            "use to await_result on. Creates the channel if it does not exist."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name or id. Use a short stable name like 'deploy-jobs'.",
                },
                "content": {
                    "type": "string",
                    "description": "The task payload. Plain text or JSON string.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional structured fields (priority, tags, etc.).",
                },
                "actor_label": {"type": "string", "description": "Your agent's name."},
            },
            "required": ["channel", "content"],
        },
    ),
    Tool(
        name="broadcast",
        description=(
            "Send a message that all subscribers of a broadcast channel will read. "
            "Use for fan-out — notifications, status updates, shared context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "content": {"type": "string"},
                "metadata": {"type": "object"},
                "actor_label": {"type": "string"},
            },
            "required": ["channel", "content"],
        },
    ),
    Tool(
        name="claim_task",
        description=(
            "Claim the next available task from a claimable channel. Reads the most "
            "recent unclaimed message and atomically claims it. Returns 409 already_claimed "
            "if another agent got it first — try again with a different message or wait."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel name or id."},
                "actor": {"type": "string", "description": "Your agent's name (used in claim records)."},
            },
            "required": ["channel", "actor"],
        },
    ),
    Tool(
        name="subscribe",
        description=(
            "Read the next batch of messages from a channel since a cursor. Use this "
            "to drain a broadcast channel or to poll for new claimable tasks. Returns "
            "a list of messages plus a next_cursor to pass back on the next call."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "since": {"type": "string", "description": "Cursor from a previous call. Omit for the start."},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["channel"],
        },
    ),
    Tool(
        name="await_result",
        description=(
            "Wait for the result of a task you previously posted with post_task. "
            "Polls the channel for an acknowledged message acknowledging your message id. "
            "Returns immediately if already acked, otherwise polls with backoff up to timeout_seconds."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message_id": {"type": "string", "description": "The id returned by post_task."},
                "timeout_seconds": {"type": "integer", "default": 60},
            },
            "required": ["channel", "message_id"],
        },
    ),
    Tool(
        name="list_channels",
        description=(
            "Discover coordination channels on this instance (metadata only, never "
            "messages). Use to find an existing handoff lane before creating one. A "
            "channel with access=restricted and is_member=false is a lobby you must "
            "request_access to before you can read it."
        ),
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}, "cursor": {"type": "string"}}},
    ),
    Tool(
        name="request_access",
        description=(
            "Request access to a discoverable restricted channel found via list_channels. "
            "The channel owner approves; once approved you can read and post."
        ),
        inputSchema={
            "type": "object",
            "properties": {"channel": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["channel"],
        },
    ),
    Tool(
        name="issue_key",
        description=(
            "Explicitly mint a new Backchannel API key for this agent. Normally you do "
            "not need to call this — the MCP server auto-mints one on first use. Use this "
            "if you need a key for a co-agent or want a fresh label."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_label": {"type": "string"},
            },
            "required": ["agent_label"],
        },
    ),
]


# --- Tool implementations -------------------------------------------------


async def _tool_post_task(args: dict[str, Any]) -> dict[str, Any]:
    api_key = await _ensure_key()
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        # Best-effort create-or-reuse: if channel exists, the create will 409 and we proceed.
        try:
            channel = await client.create_channel(
                name=args["channel"], mode="claimable"
            )
            channel_id = channel.get("id", args["channel"])
        except BackchannelError as exc:
            if exc.status == 409:
                channel_id = args["channel"]
            else:
                raise
        envelope = await client.post_message(
            channel_id,
            content=args["content"],
            actor_label=args.get("actor_label"),
            metadata=args.get("metadata"),
        )
    return {
        "channel": channel_id,
        "message_id": envelope.get("message", {}).get("id"),
        "envelope": envelope,
    }


async def _tool_broadcast(args: dict[str, Any]) -> dict[str, Any]:
    api_key = await _ensure_key()
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        try:
            channel = await client.create_channel(name=args["channel"], mode="broadcast")
            channel_id = channel.get("id", args["channel"])
        except BackchannelError as exc:
            if exc.status == 409:
                channel_id = args["channel"]
            else:
                raise
        return await client.broadcast(
            channel_id,
            content=args["content"],
            actor_label=args.get("actor_label"),
            metadata=args.get("metadata"),
        )


async def _tool_claim_task(args: dict[str, Any]) -> dict[str, Any]:
    api_key = await _ensure_key()
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        page = await client.list_messages(args["channel"], limit=20)
        for msg in page.get("data", []):
            if msg.get("status") == "claimed" or msg.get("acknowledged_by"):
                continue
            try:
                claim = await client.claim_message(msg["id"], actor=args["actor"])
                return {"claimed": claim, "message": msg}
            except BackchannelError as exc:
                if exc.status == 409:
                    continue  # someone else got it; try the next
                raise
    return {"claimed": None, "message": None, "note": "no unclaimed messages available"}


async def _tool_subscribe(args: dict[str, Any]) -> dict[str, Any]:
    api_key = await _ensure_key()
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        return await client.list_messages(
            args["channel"], since=args.get("since"), limit=int(args.get("limit", 50))
        )


async def _tool_await_result(args: dict[str, Any]) -> dict[str, Any]:
    """Poll the channel until the message is acked or timeout."""
    api_key = await _ensure_key()
    message_id = args["message_id"]
    timeout = int(args.get("timeout_seconds", 60))
    deadline = asyncio.get_event_loop().time() + timeout
    delay = 0.5
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        while True:
            page = await client.list_messages(args["channel"], limit=100)
            for msg in page.get("data", []):
                if msg.get("id") == message_id and msg.get("acknowledged_by"):
                    return {"status": "acknowledged", "message": msg}
            if asyncio.get_event_loop().time() >= deadline:
                return {"status": "timeout", "polled_for_seconds": timeout, "message_id": message_id}
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)


async def _tool_list_channels(args: dict[str, Any]) -> dict[str, Any]:
    api_key = await _ensure_key()
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        return await client.discover_channels(limit=args.get("limit"), cursor=args.get("cursor"))


async def _tool_request_access(args: dict[str, Any]) -> dict[str, Any]:
    api_key = await _ensure_key()
    async with BackchannelClient(api_key=api_key, base_url=_base_url()) as client:
        return await client.request_access(args["channel"], reason=args.get("reason", ""))


async def _tool_issue_key(args: dict[str, Any]) -> dict[str, Any]:
    base = _base_url()
    async with BackchannelClient(base_url=base) as client:
        issued = await client.issue_key(agent_label=args["agent_label"])
    return {
        "key": issued.key,
        "key_id": issued.key_id,
        "tier": issued.tier,
        "expires_at": issued.expires_at,
        "agent_label": issued.agent_label,
        "warning": "Treat 'key' as a secret. It is shown once here.",
    }


TOOL_IMPL = {
    "post_task": _tool_post_task,
    "broadcast": _tool_broadcast,
    "claim_task": _tool_claim_task,
    "subscribe": _tool_subscribe,
    "await_result": _tool_await_result,
    "list_channels": _tool_list_channels,
    "request_access": _tool_request_access,
    "issue_key": _tool_issue_key,
}


# --- MCP server bootstrap -------------------------------------------------


def build_server() -> Server:
    server: Server = Server("backchannel")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        impl = TOOL_IMPL.get(name)
        if impl is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = await impl(arguments or {})
        except BackchannelError as exc:
            return [TextContent(type="text", text=_format_error(exc))]
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("tool %s failed", name)
            return [TextContent(type="text", text=f"Unhandled error: {exc!s}")]
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def _run_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backchannel MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="Transport mechanism (default: stdio — what Claude Code uses).",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("BACKCHANNEL_MCP_LOG", "INFO"),
        help="Python log level (default: INFO).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.transport == "stdio":
        asyncio.run(_run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
