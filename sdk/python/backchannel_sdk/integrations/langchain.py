"""LangChain tool wrappers for Backchannel."""
from __future__ import annotations

from typing import Any

try:
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel, Field
except ImportError as exc:
    raise ImportError("Install langchain-core: pip install backchannel-sdk[langchain]") from exc

from backchannel_sdk.client import BackchannelClient


class _SendMessageInput(BaseModel):
    channel_id: str = Field(description="Channel ID or alias to post to")
    content: str = Field(description="Message content")
    actor_label: str = Field(default="langchain-agent", description="Label identifying this agent")


class _ClaimMessageInput(BaseModel):
    message_id: str = Field(description="Message ID to claim")
    actor: str = Field(description="Actor name claiming the message")


class BackchannelSendTool(BaseTool):
    """LangChain tool: send a message to a Backchannel channel."""

    name: str = "backchannel_send"
    description: str = (
        "Send a message to a Backchannel channel. "
        "Use for task handoff or broadcast coordination between agents. "
        "Returns the message object with its id."
    )
    args_schema: type[BaseModel] = _SendMessageInput
    client: Any = None

    def __init__(self, client: BackchannelClient, **kwargs: Any):
        super().__init__(client=client, **kwargs)

    def _run(self, channel_id: str, content: str, actor_label: str = "langchain-agent") -> str:
        import json
        msg = self.client.send_message(channel_id, content, actor_label=actor_label)
        return json.dumps(msg)

    async def _arun(self, channel_id: str, content: str, actor_label: str = "langchain-agent") -> str:
        return self._run(channel_id, content, actor_label)


class BackchannelClaimTool(BaseTool):
    """LangChain tool: claim exclusive ownership of a Backchannel message."""

    name: str = "backchannel_claim"
    description: str = (
        "Claim a message in a claimable Backchannel channel. "
        "First caller wins — returns claimed status or 409 if already taken. "
        "Use after listing messages to take exclusive ownership of a task."
    )
    args_schema: type[BaseModel] = _ClaimMessageInput
    client: Any = None

    def __init__(self, client: BackchannelClient, **kwargs: Any):
        super().__init__(client=client, **kwargs)

    def _run(self, message_id: str, actor: str) -> str:
        import json
        result = self.client.claim_message(message_id, actor=actor)
        return json.dumps(result)

    async def _arun(self, message_id: str, actor: str) -> str:
        return self._run(message_id, actor)


def get_tools(client: BackchannelClient) -> list[BaseTool]:
    """Return all Backchannel LangChain tools for a given client."""
    return [BackchannelSendTool(client=client), BackchannelClaimTool(client=client)]
