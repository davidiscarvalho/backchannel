"""AutoGen function wrappers for Backchannel."""
from __future__ import annotations

from typing import Any

from backchannel_sdk.client import BackchannelClient


def make_backchannel_functions(client: BackchannelClient) -> list[dict[str, Any]]:
    """
    Return AutoGen-compatible function definitions for Backchannel operations.

    Usage::

        from autogen import AssistantAgent
        from backchannel_sdk import BackchannelClient
        from backchannel_sdk.integrations.autogen import make_backchannel_functions

        client = BackchannelClient(api_key="...")
        functions = make_backchannel_functions(client)

        assistant = AssistantAgent(
            name="coordinator",
            function_map={fn["name"]: fn["callable"] for fn in functions},
        )
    """

    def send_message(channel_id: str, content: str, actor_label: str = "autogen-agent") -> dict[str, Any]:
        """Send a message to a Backchannel channel."""
        return client.send_message(channel_id, content, actor_label=actor_label)

    def list_messages(channel_id: str, since: str = "0") -> dict[str, Any]:
        """List messages in a Backchannel channel since a cursor."""
        return client.list_messages(channel_id, since=since)

    def claim_message(message_id: str, actor: str) -> dict[str, Any]:
        """Claim exclusive ownership of a message. First caller wins."""
        return client.claim_message(message_id, actor=actor)

    def ack_message(message_id: str, actor: str) -> dict[str, Any]:
        """Acknowledge completion of a message."""
        return client.ack_message(message_id, actor=actor)

    return [
        {
            "name": "backchannel_send_message",
            "callable": send_message,
            "description": "Send a coordination message to a Backchannel channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string", "description": "Channel ID or alias"},
                    "content": {"type": "string", "description": "Message content"},
                    "actor_label": {"type": "string", "description": "Label for this agent"},
                },
                "required": ["channel_id", "content"],
            },
        },
        {
            "name": "backchannel_list_messages",
            "callable": list_messages,
            "description": "List messages in a Backchannel channel. Pass next_since from the previous response as 'since'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                    "since": {"type": "string", "description": "Cursor from previous list_messages response. Use '0' for all messages."},
                },
                "required": ["channel_id"],
            },
        },
        {
            "name": "backchannel_claim_message",
            "callable": claim_message,
            "description": "Claim a task message. First caller wins; 409 means another agent claimed it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "actor": {"type": "string"},
                },
                "required": ["message_id", "actor"],
            },
        },
        {
            "name": "backchannel_ack_message",
            "callable": ack_message,
            "description": "Acknowledge completion of a message after processing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "actor": {"type": "string"},
                },
                "required": ["message_id", "actor"],
            },
        },
    ]
