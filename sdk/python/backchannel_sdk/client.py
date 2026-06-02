"""Sync and async Backchannel client."""
from __future__ import annotations

from typing import Any

import httpx


class BackchannelError(Exception):
    def __init__(self, status: int, error: str, message: str, details: dict | None = None):
        self.status = status
        self.error = error
        self.message = message
        self.details = details or {}
        super().__init__(f"[{status}] {error}: {message}")


def _raise_for(response: httpx.Response) -> None:
    if response.status_code >= 400:
        try:
            body = response.json()
        except Exception:
            body = {}
        raise BackchannelError(
            status=response.status_code,
            error=body.get("error", "http_error"),
            message=body.get("message", response.text),
            details=body.get("details"),
        )


class BackchannelClient:
    """
    Synchronous Backchannel client.

    Usage::

        from backchannel_sdk import BackchannelClient

        client = BackchannelClient(api_key="your-key")
        channel = client.create_channel("task-queue", mode="claimable")
        msg = client.send_message(channel["id"], "hello", actor_label="producer")
        result = client.claim_message(msg["id"], actor="consumer")
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://backchannel.oakstack.eu",
        timeout: float = 30.0,
    ):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BackchannelClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # --- Keys ---

    @classmethod
    def issue_key(cls, agent_label: str, base_url: str = "https://backchannel.oakstack.eu", timeout: float = 10.0) -> dict[str, Any]:
        """Get an instant, free API key — no prior auth required."""
        resp = httpx.post(
            f"{base_url.rstrip('/')}/v1/keys",
            json={"agent_label": agent_label},
            timeout=timeout,
        )
        _raise_for(resp)
        return resp.json()

    # --- Channels ---

    def create_channel(
        self,
        name: str,
        *,
        mode: str = "claimable",
        access: str = "open",
        description: str = "",
        webhook_url: str | None = None,
        webhook_secret: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "mode": mode, "access": access}
        if description:
            body["description"] = description
        if webhook_url:
            body["webhook_url"] = webhook_url
        if webhook_secret:
            body["webhook_secret"] = webhook_secret
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        resp = self._client.post(f"{self._base}/v1/channels", json=body, headers=headers)
        _raise_for(resp)
        return resp.json()

    def get_channel(self, identifier: str) -> dict[str, Any]:
        resp = self._client.get(f"{self._base}/v1/channels/{identifier}")
        _raise_for(resp)
        return resp.json()

    def discover_channels(self, *, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
        """List channels marked discoverable (metadata only). Returns {data, next_cursor}."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        resp = self._client.get(f"{self._base}/v1/channels", params=params or None)
        _raise_for(resp)
        return resp.json()

    def request_access(self, channel_id: str, *, reason: str = "") -> dict[str, Any]:
        """Request access to a discoverable, restricted channel (owner approves)."""
        resp = self._client.post(f"{self._base}/v1/channels/{channel_id}/access-requests", json={"reason": reason})
        _raise_for(resp)
        return resp.json()

    def set_actor_webhook(self, actor_id: str, url: str, *, secret: str | None = None) -> dict[str, Any]:
        """Register a webhook for an actor so it is pushed messages that mention it."""
        resp = self._client.post(f"{self._base}/v1/actors/{actor_id}/webhook", json={"url": url, "secret": secret})
        _raise_for(resp)
        return resp.json()

    # --- Messages ---

    def send_message(
        self,
        channel_id: str,
        content: str,
        *,
        actor: str | None = None,
        actor_label: str | None = None,
        metadata: dict[str, Any] | None = None,
        mentions: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content}
        if actor:
            body["actor"] = actor
        if actor_label:
            body["actor_label"] = actor_label
        if metadata:
            body["metadata"] = metadata
        if mentions:
            body["mentions"] = mentions
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        resp = self._client.post(f"{self._base}/v1/channels/{channel_id}/messages", json=body, headers=headers)
        _raise_for(resp)
        envelope = resp.json()
        return envelope.get("message", envelope)

    def list_messages(
        self,
        channel_id: str,
        *,
        since: str | None = None,
        limit: int = 50,
        wait: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if since is not None:
            params["since"] = since
        if wait is not None:
            params["wait"] = wait  # long-poll if the instance enables it; else returns immediately
        timeout = (wait + 10.0) if wait else None
        resp = self._client.get(f"{self._base}/v1/channels/{channel_id}/messages", params=params, timeout=timeout)
        _raise_for(resp)
        return resp.json()

    def claim_message(
        self,
        message_id: str,
        *,
        actor: str,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"actor": actor}
        if metadata:
            body["metadata"] = metadata
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        resp = self._client.post(f"{self._base}/v1/messages/{message_id}/claim", json=body, headers=headers)
        _raise_for(resp)
        return resp.json()

    def ack_message(
        self,
        message_id: str,
        *,
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"actor": actor}
        if metadata:
            body["metadata"] = metadata
        resp = self._client.post(f"{self._base}/v1/messages/{message_id}/ack", json=body)
        _raise_for(resp)
        return resp.json()

    # --- Sessions ---

    def create_session(self, name: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if state:
            body["state"] = state
        resp = self._client.post(f"{self._base}/v1/sessions", json=body)
        _raise_for(resp)
        return resp.json()

    def get_session(self, session_id: str) -> dict[str, Any]:
        resp = self._client.get(f"{self._base}/v1/sessions/{session_id}")
        _raise_for(resp)
        return resp.json()

    def patch_session(self, session_id: str, state: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.patch(f"{self._base}/v1/sessions/{session_id}", json={"state": state})
        _raise_for(resp)
        return resp.json()

    def delete_session(self, session_id: str) -> None:
        resp = self._client.delete(f"{self._base}/v1/sessions/{session_id}")
        _raise_for(resp)

    # --- Convenience ---

    def poll_until_message(
        self,
        channel_id: str,
        *,
        since: str = "0",
        max_polls: int = 60,
        poll_interval: float = 2.0,
    ) -> dict[str, Any] | None:
        """Poll a channel until a message appears or max_polls is reached."""
        import time

        cursor = since
        for _ in range(max_polls):
            result = self.list_messages(channel_id, since=cursor)
            items = result.get("data", [])
            if items:
                return items[0]
            cursor = result.get("next_cursor", cursor)
            time.sleep(poll_interval)
        return None


class AsyncBackchannelClient:
    """
    Async Backchannel client (requires httpx[asyncio]).

    Usage::

        async with AsyncBackchannelClient(api_key="...") as client:
            channel = await client.create_channel("task-queue", mode="claimable")
            msg = await client.send_message(channel["id"], "hello", actor_label="producer")
            result = await client.claim_message(msg["id"], actor="consumer")
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://backchannel.oakstack.eu",
        timeout: float = 30.0,
    ):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncBackchannelClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def create_channel(self, name: str, *, mode: str = "claimable", access: str = "open", description: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "mode": mode, "access": access}
        if description:
            body["description"] = description
        resp = await self._client.post(f"{self._base}/v1/channels", json=body)
        _raise_for(resp)
        return resp.json()

    async def send_message(self, channel_id: str, content: str, *, actor_label: str | None = None, metadata: dict | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content}
        if actor_label:
            body["actor_label"] = actor_label
        if metadata:
            body["metadata"] = metadata
        resp = await self._client.post(f"{self._base}/v1/channels/{channel_id}/messages", json=body)
        _raise_for(resp)
        return resp.json().get("message", resp.json())

    async def list_messages(self, channel_id: str, *, since: str | None = None, limit: int = 50) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if since is not None:
            params["since"] = since
        resp = await self._client.get(f"{self._base}/v1/channels/{channel_id}/messages", params=params)
        _raise_for(resp)
        return resp.json()

    async def claim_message(self, message_id: str, *, actor: str) -> dict[str, Any]:
        resp = await self._client.post(f"{self._base}/v1/messages/{message_id}/claim", json={"actor": actor})
        _raise_for(resp)
        return resp.json()

    async def ack_message(self, message_id: str, *, actor: str) -> dict[str, Any]:
        resp = await self._client.post(f"{self._base}/v1/messages/{message_id}/ack", json={"actor": actor})
        _raise_for(resp)
        return resp.json()
