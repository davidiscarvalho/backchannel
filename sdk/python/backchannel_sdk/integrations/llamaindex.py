"""LlamaIndex tool spec for Backchannel."""
from __future__ import annotations

from typing import Any

try:
    from llama_index.core.tools import FunctionTool
except ImportError as exc:
    raise ImportError("Install llama-index: pip install backchannel-sdk[llamaindex]") from exc

from backchannel_sdk.client import BackchannelClient


def get_tools(client: BackchannelClient) -> list[FunctionTool]:
    """Return LlamaIndex FunctionTools for Backchannel."""

    def send_message(channel_id: str, content: str, actor_label: str = "llamaindex-agent") -> str:
        """Send a message to a Backchannel channel for agent coordination."""
        import json
        return json.dumps(client.send_message(channel_id, content, actor_label=actor_label))

    def list_messages(channel_id: str, since: str = "0") -> str:
        """Poll a Backchannel channel for messages. Pass next_since as since on subsequent calls."""
        import json
        return json.dumps(client.list_messages(channel_id, since=since))

    def claim_message(message_id: str, actor: str) -> str:
        """Claim a task message exclusively. Returns claimed or 409 if already taken."""
        import json
        return json.dumps(client.claim_message(message_id, actor=actor))

    def ack_message(message_id: str, actor: str) -> str:
        """Acknowledge completion of a task message."""
        import json
        return json.dumps(client.ack_message(message_id, actor=actor))

    return [
        FunctionTool.from_defaults(fn=send_message, name="backchannel_send"),
        FunctionTool.from_defaults(fn=list_messages, name="backchannel_list"),
        FunctionTool.from_defaults(fn=claim_message, name="backchannel_claim"),
        FunctionTool.from_defaults(fn=ack_message, name="backchannel_ack"),
    ]
