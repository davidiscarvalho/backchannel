"""Thin async HTTP client around the Backchannel REST API.

Kept deliberately small — the source of truth is the OpenAPI document at
``{base_url}/openapi.json``. This client only covers what the MCP tools
actually call.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://backchannel.oakstack.eu"
DEFAULT_TIMEOUT = 10.0


class BackchannelError(RuntimeError):
    """Raised on any non-2xx response. Carries the parsed error payload."""

    def __init__(self, status: int, payload: dict[str, Any]):
        self.status = status
        self.payload = payload
        message = payload.get("message") or payload.get("error") or "Backchannel API error"
        super().__init__(f"[{status}] {message}")


@dataclass
class BackchannelKey:
    key: str
    key_id: str
    tier: int
    expires_at: str | None
    agent_label: str | None = None


class BackchannelClient:
    """Async HTTP client. Use as ``async with BackchannelClient(...) as c``."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout, base_url=self.base_url)

    async def __aenter__(self) -> "BackchannelClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._http.aclose()

    # --- low-level ----------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        require_auth: bool = True,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if require_auth:
            if not self.api_key:
                raise BackchannelError(
                    401,
                    {
                        "error": "unauthorized",
                        "message": (
                            "No Backchannel API key configured. Set BACKCHANNEL_API_KEY "
                            "or call issue_key() first."
                        ),
                    },
                )
            headers["X-API-Key"] = self.api_key
        if idempotency_key is not None and method.upper() in {"POST", "PATCH", "DELETE"}:
            headers["Idempotency-Key"] = idempotency_key
        resp = await self._http.request(method, path, json=json, params=params, headers=headers)
        try:
            payload: dict[str, Any] = resp.json() if resp.content else {}
        except ValueError:
            payload = {"error": "invalid_response", "message": resp.text[:300]}
        if resp.status_code >= 400:
            raise BackchannelError(resp.status_code, payload)
        return payload

    # --- key lifecycle ------------------------------------------------

    async def issue_key(self, agent_label: str) -> BackchannelKey:
        data = await self._request(
            "POST",
            "/v1/keys",
            json={"agent_label": agent_label},
            require_auth=False,
        )
        return BackchannelKey(
            key=data["key"],
            key_id=data.get("key_id", ""),
            tier=int(data.get("tier", 0)),
            expires_at=data.get("expires_at"),
            agent_label=data.get("agent_label"),
        )

    async def keys_me(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/keys/me")

    async def discover_channels(self, limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        return await self._request("GET", "/v1/channels", params=params or None)

    async def request_access(self, channel_id: str, reason: str = "") -> dict[str, Any]:
        return await self._request(
            "POST", f"/v1/channels/{channel_id}/access-requests", json={"reason": reason}
        )

    # --- channels -----------------------------------------------------

    async def create_channel(
        self,
        name: str,
        mode: str = "claimable",
        *,
        description: str | None = None,
        access: str = "open",
        metadata_schema: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "mode": mode, "access": access}
        if description:
            body["description"] = description
        if metadata_schema:
            body["metadata_schema"] = metadata_schema
        if ttl_seconds:
            body["ttl_seconds"] = ttl_seconds
        return await self._request(
            "POST",
            "/v1/channels",
            json=body,
            idempotency_key=f"create-channel-{uuid.uuid4()}",
        )

    async def list_messages(
        self,
        channel: str,
        *,
        since: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = since
        return await self._request(
            "GET", f"/v1/channels/{channel}/messages", params=params
        )

    # --- messages -----------------------------------------------------

    async def post_message(
        self,
        channel: str,
        content: str,
        *,
        actor_label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content}
        if actor_label is not None:
            body["actor_label"] = actor_label
        if metadata is not None:
            body["metadata"] = metadata
        return await self._request(
            "POST",
            f"/v1/channels/{channel}/messages",
            json=body,
            idempotency_key=f"post-{uuid.uuid4()}",
        )

    async def ensure_actor(self, name: str) -> str:
        """Create the actor if missing; return the actor id. Idempotent."""
        try:
            record = await self._request(
                "POST",
                "/v1/actors",
                json={"name": name},
                idempotency_key=f"actor-{name}",
            )
            return record["id"]
        except BackchannelError as exc:
            if exc.status == 409:
                # Actor exists — fetch by name (the alias mechanism lets us
                # use the name as identifier on GET).
                got = await self._request("GET", f"/v1/actors/{name}")
                return got["id"]
            raise

    async def claim_message(self, message_id: str, actor: str) -> dict[str, Any]:
        actor_id = await self.ensure_actor(actor)
        return await self._request(
            "POST",
            f"/v1/messages/{message_id}/claim",
            json={"actor": actor_id},
            idempotency_key=f"claim-{message_id}-{actor}",
        )

    async def ack_message(self, message_id: str, actor: str) -> dict[str, Any]:
        actor_id = await self.ensure_actor(actor)
        return await self._request(
            "POST",
            f"/v1/messages/{message_id}/ack",
            json={"actor": actor_id},
            idempotency_key=f"ack-{message_id}-{actor}",
        )

    async def claim_and_ack(self, message_id: str, actor: str) -> dict[str, Any]:
        claimed = await self.claim_message(message_id, actor)
        acked = await self.ack_message(message_id, actor)
        return {"claim": claimed, "ack": acked}

    # --- broadcast (convenience) --------------------------------------

    async def broadcast(
        self,
        channel: str,
        content: str,
        *,
        actor_label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Same as post_message; the channel mode determines fan-out semantics.
        return await self.post_message(
            channel, content, actor_label=actor_label, metadata=metadata
        )


# --- key persistence ------------------------------------------------------

_DEFAULT_KEY_PATH = Path.home() / ".config" / "backchannel" / "key"


def load_persisted_key(path: Path | None = None) -> str | None:
    """Return a persisted key (from disk) or None."""
    p = path or _DEFAULT_KEY_PATH
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def persist_key(raw_key: str, path: Path | None = None) -> Path:
    """Write the key to disk with 0600 perms. Returns the path."""
    p = path or _DEFAULT_KEY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw_key, encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def resolve_api_key(env_var: str = "BACKCHANNEL_API_KEY") -> str | None:
    """Pick a key from env or persisted file. Returns None if neither."""
    return os.environ.get(env_var) or load_persisted_key()
