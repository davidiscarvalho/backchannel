"""Backchannel MCP server.

Exposes Backchannel's channel + message primitives as MCP tools so any
MCP-aware client (Claude Code, Cursor, Zed, etc.) can let an LLM call
other agents over the ephemeral bus.

Entry point: ``backchannel_mcp.server:main``.
"""

from backchannel_mcp.server import main

__all__ = ["main"]
__version__ = "0.1.0"
