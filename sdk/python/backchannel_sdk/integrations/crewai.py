"""CrewAI tool wrappers for Backchannel."""
from __future__ import annotations

from typing import Any

try:
    from crewai_tools import BaseTool
except ImportError as exc:
    raise ImportError("Install crewai: pip install backchannel-sdk[crewai]") from exc

from backchannel_sdk.client import BackchannelClient


class BackchannelSendTool(BaseTool):
    name: str = "Backchannel Send"
    description: str = (
        "Send a message to a Backchannel channel for agent coordination. "
        "Input: JSON with channel_id, content, and optional actor_label."
    )

    def __init__(self, client: BackchannelClient):
        self._bc = client
        super().__init__()

    def _run(self, channel_id: str, content: str, actor_label: str = "crewai-agent") -> str:
        import json
        msg = self._bc.send_message(channel_id, content, actor_label=actor_label)
        return json.dumps(msg)


class BackchannelClaimTool(BaseTool):
    name: str = "Backchannel Claim"
    description: str = (
        "Claim exclusive ownership of a message in a Backchannel claimable channel. "
        "First caller wins. Input: JSON with message_id and actor."
    )

    def __init__(self, client: BackchannelClient):
        self._bc = client
        super().__init__()

    def _run(self, message_id: str, actor: str) -> str:
        import json
        result = self._bc.claim_message(message_id, actor=actor)
        return json.dumps(result)


def get_tools(client: BackchannelClient) -> list[Any]:
    return [BackchannelSendTool(client), BackchannelClaimTool(client)]
