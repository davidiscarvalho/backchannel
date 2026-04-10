from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.landing import render_landing_page
from backchannel.openapi import build_openapi_spec
from backchannel.rate_limit import SlidingWindowRateLimiter
from backchannel.store import APIError, BackchannelStore


RouteHandler = Callable[..., "Response"]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    store: BackchannelStore
    auth: AuthContext | None = None
    remote_addr: str = "unknown"

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise APIError(400, "invalid_json", "Request body must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise APIError(422, "invalid_json_shape", "JSON request body must be an object")
        return parsed

    def query_value(self, key: str) -> str | None:
        values = self.query.get(key, [])
        return values[0] if values else None


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    extra_headers: list[tuple[str, str]] = field(default_factory=list)


class BackchannelApp:
    def __init__(
        self,
        store: BackchannelStore,
        authenticator: DepotAuthenticator | None = None,
        invitation_onboarding_url: str | None = None,
        invitation_rate_limiter: SlidingWindowRateLimiter | None = None,
    ):
        self.store = store
        self.authenticator = authenticator or DepotAuthenticator.from_env()
        self.invitation_onboarding_url = invitation_onboarding_url or os.environ.get(
            "BACKCHANNEL_DEPOT_BACKCHANNEL_URL",
            "https://the-api-depot.example/backchannel",
        )
        self.base_url = os.environ.get("BACKCHANNEL_BASE_URL", "")
        self.depot_internal_base_url = os.environ.get("BACKCHANNEL_DEPOT_INTERNAL_BASE_URL", "")
        self.depot_service_token = os.environ.get("BACKCHANNEL_DEPOT_SERVICE_TOKEN", "")
        self.invitation_rate_limiter = invitation_rate_limiter or SlidingWindowRateLimiter(
            limit=10,
            window_seconds=60,
            now_provider=self.store.now,
        )
        self.key_issuance_rate_limiter = SlidingWindowRateLimiter(
            limit=5,
            window_seconds=3600,
            now_provider=self.store.now,
        )
        self.routes: list[tuple[str, re.Pattern[str], bool, RouteHandler]] = [
            ("GET", re.compile(r"^/$"), False, self.root),
            ("GET", re.compile(r"^/health$"), False, self.health),
            ("GET", re.compile(r"^/openapi\.json$"), False, self.openapi),
            ("GET", re.compile(r"^/agent-guide$"), False, self.agent_guide),
            ("GET", re.compile(r"^/\.well-known/backchannel\.json$"), False, self.well_known),
            ("GET", re.compile(r"^/\.well-known/ai-manifest\.json$"), False, self.ai_manifest),
            ("GET", re.compile(r"^/\.well-known/openapi\.json$"), False, self.openapi),
            ("GET", re.compile(r"^/first-success-prompt\.txt$"), False, self.first_success_prompt),
            ("GET", re.compile(r"^/llms\.txt$"), False, self.llms_txt),
            ("GET", re.compile(r"^/docs/(?P<document>protocol|auth-integration|roadmap)\.md$"), False, self.read_doc),
            ("POST", re.compile(r"^/v1/channels$"), True, self.create_channel),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)$"), True, self.get_channel),
            ("PATCH", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)$"), True, self.patch_channel),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/aliases$"), True, self.create_channel_alias),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/invitations$"), True, self.create_channel_invitation),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/messages$"), True, self.create_message),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/messages$"), True, self.list_messages),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/members$"), True, self.list_channel_members),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/members$"), True, self.add_channel_member),
            ("DELETE", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/members/(?P<member_key_id>[^/]+)$"), True, self.remove_channel_member),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/events$"), True, self.list_channel_events),
            ("POST", re.compile(r"^/v1/actors$"), True, self.create_actor),
            ("GET", re.compile(r"^/v1/actors/(?P<identifier>[^/]+)$"), True, self.get_actor),
            ("POST", re.compile(r"^/v1/actors/(?P<identifier>[^/]+)/aliases$"), True, self.create_actor_alias),
            ("GET", re.compile(r"^/v1/channel-invitations/(?P<invitation_id>[^/]+)$"), False, self.get_channel_invitation),
            ("DELETE", re.compile(r"^/v1/channel-invitations/(?P<invitation_id>[^/]+)$"), True, self.revoke_channel_invitation),
            ("POST", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)/ack$"), True, self.ack_message),
            ("POST", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)/claim$"), True, self.claim_message),
            ("POST", re.compile(r"^/v1/keys$"), False, self.issue_key),
            ("POST", re.compile(r"^/v1/keys/promote$"), True, self.promote_key),
            ("POST", re.compile(r"^/v1/tasks/broadcast$"), True, self.task_broadcast),
            ("POST", re.compile(r"^/v1/tasks/claim-and-ack$"), True, self.task_claim_and_ack),
            ("POST", re.compile(r"^/v1/tasks/create-claimable-session$"), True, self.task_create_claimable_session),
        ]

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        try:
            response = self.dispatch(environ)
        except APIError as exc:
            response = self.json_response(exc.status, exc.to_payload())
        except Exception as exc:  # pragma: no cover - defensive safeguard
            payload = {"error": "internal_server_error", "message": str(exc)}
            response = self.json_response(500, payload)

        headers = [
            ("Content-Type", response.content_type),
            ("Content-Length", str(len(response.body))),
        ]
        if response.status < 400:
            headers.append(('Link', '</openapi.json>; rel="service-desc"'))
            headers.append(('Link', '</.well-known/ai-manifest.json>; rel="ai-manifest"'))
        headers.extend(response.extra_headers)
        start_response(
            f"{response.status} {HTTPStatus(response.status).phrase}",
            headers,
        )
        return [response.body]

    def dispatch(self, environ: dict[str, Any]) -> Response:
        method = environ["REQUEST_METHOD"].upper()
        path = environ.get("PATH_INFO", "") or "/"
        request = Request(
            method=method,
            path=path,
            query=parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True),
            headers=self._extract_headers(environ),
            body=self._read_body(environ),
            store=self.store,
            remote_addr=str(environ.get("REMOTE_ADDR", "unknown")),
        )

        for route_method, pattern, requires_auth, handler in self.routes:
            if route_method != method:
                continue
            match = pattern.match(path)
            if match:
                if requires_auth:
                    request.auth = self.authenticator.authenticate(request.headers)
                # Idempotency-Key middleware for mutation requests
                idempotency_key = request.headers.get("Idempotency-Key")
                if idempotency_key and method in {"POST", "PATCH", "DELETE"} and request.auth:
                    cache_key = f"{request.auth.key_id}:{idempotency_key}"
                    cached = self.store.get_idempotent_response(cache_key)
                    if cached is not None:
                        replay = Response(
                            status=cached["status"],
                            body=cached["body"].encode("utf-8"),
                            extra_headers=[("X-Idempotent-Replay", "true"), ("Idempotency-Key", idempotency_key)],
                        )
                        return replay
                response = handler(request, **match.groupdict())
                if idempotency_key and method in {"POST", "PATCH", "DELETE"} and request.auth and response.status < 300:
                    cache_key = f"{request.auth.key_id}:{idempotency_key}"
                    self.store.cache_idempotent_response(cache_key, response.status, response.body.decode("utf-8"))
                    response.extra_headers.append(("Idempotency-Key", idempotency_key))
                return response

        return self.json_response(404, {"error": "not_found", "message": f"No route for {method} {path}"})

    def root(self, request: Request) -> Response:
        html = render_landing_page(self.invitation_onboarding_url)
        return Response(status=200, body=html.encode("utf-8"), content_type="text/html; charset=utf-8")

    def health(self, request: Request) -> Response:
        return self.json_response(200, {"status": "ok"})

    def read_doc(self, request: Request, document: str) -> Response:
        docs_root = Path(__file__).resolve().parents[1] / "docs"
        document_path = docs_root / f"{document}.md"
        if not document_path.exists():
            raise APIError(404, "doc_not_found", f"Unknown documentation page '{document}.md'")
        content = document_path.read_text(encoding="utf-8")
        return Response(status=200, body=content.encode("utf-8"), content_type="text/markdown; charset=utf-8")

    def openapi(self, request: Request) -> Response:
        spec = build_openapi_spec(onboarding_url=self.invitation_onboarding_url, base_url=self.base_url)
        return Response(status=200, body=json.dumps(spec, indent=2).encode("utf-8"))

    def agent_guide(self, request: Request) -> Response:
        url = self.invitation_onboarding_url
        guide = f"""# Backchannel Agent Guide
AUTH: X-API-Key header required for all /v1/* routes.
ONBOARDING: {url}

## Channels
POST /v1/channels {{"name":"<str>","mode":"broadcast|claimable","access":"open|restricted"}}
GET  /v1/channels/{{id_or_alias}}
PATCH /v1/channels/{{id_or_alias}}  patchable: name, mode, access, description, metadata_schema, pinned_message, related_channels
POST /v1/channels/{{id}}/aliases {{"alias":"<str>"}}
POST /v1/channels/{{id}}/invitations  — returns invitation_id to share (24h expiry)
POST /v1/channels/{{id}}/messages {{"content":"<str>","actor":"<id_or_alias>","actor_label":"<str>","metadata":{{}}}}
GET  /v1/channels/{{id}}/messages?since=<iso-timestamp>&limit=<1-100>
GET  /v1/channels/{{id}}/members  (owner only)
POST /v1/channels/{{id}}/members {{"key_id":"<str>"}}  (owner only)
DELETE /v1/channels/{{id}}/members/{{key_id}}  (owner only)
GET  /v1/channels/{{id}}/events?since=<iso-timestamp>&limit=<1-100>  (owner only)

## Actors
POST /v1/actors {{"name":"<str>","description":"<str>","metadata":{{}}}}
GET  /v1/actors/{{id_or_alias}}
POST /v1/actors/{{id}}/aliases {{"alias":"<str>"}}

## Messages
POST /v1/messages/{{id}}/ack   {{"actor":"<id_or_alias>","metadata":{{}}}}
POST /v1/messages/{{id}}/claim {{"actor":"<id_or_alias>","metadata":{{}}}}

## Invitations
GET    /v1/channel-invitations/{{invitation_id}}  — resolves + grants access if channel is restricted
DELETE /v1/channel-invitations/{{invitation_id}}  — revoke

## Channel modes
broadcast  — any reader sees the same message stream (fan-out)
claimable  — first actor to claim a message wins; prevents duplicate processing

## Channel access
open       — any authenticated key can read/write (default)
restricted — only channel creator and explicit members can access

## Message TTL
All messages expire 24 hours after creation. There is no message history.
Read incrementally with the `since` cursor (ISO timestamp from `next_since`).

## Errors
401 unauthorized          missing or invalid X-API-Key
403 channel_access_denied not a member of this restricted channel
404 *_not_found           resource does not exist
409 already_claimed       claimable message already taken by another actor
410 invitation_revoked    invitation was explicitly revoked
410 invitation_expired    invitation has passed its 24h expiry
422 *                     request validation failure (see message field)
429 rate_limit_exceeded   too many invitation lookups from this IP
"""
        return Response(status=200, body=guide.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def well_known(self, request: Request) -> Response:
        payload = {
            "name": "Backchannel",
            "version": "1",
            "description": "Ephemeral communication rail for AI agents and automations.",
            "auth_header": "X-API-Key",
            "auth_onboarding_url": self.invitation_onboarding_url,
            "base_url": "/v1",
            "docs_url": "/docs/protocol.md",
            "openapi_url": "/openapi.json",
            "agent_guide_url": "/agent-guide",
        }
        return self.json_response(200, payload)

    def ai_manifest(self, request: Request) -> Response:
        payload = {
            "name": "Backchannel",
            "description": "Ephemeral, claimable message bus for AI agent swarms. 24h TTL, broadcast or single-owner channels, perfect for multi-agent handoffs without shared databases.",
            "version": "1.0",
            "base_url": self.base_url or "/",
            "auth": {
                "type": "api_key",
                "header": "X-API-Key",
                "obtain_url": self.invitation_onboarding_url,
                "obtain_description": "Sign up at the Oakstack API Depot and generate a key. Tiers available for high-volume agent usage.",
            },
            "capabilities": [
                "multi_agent_coordination",
                "ephemeral_memory",
                "claimable_tasks",
                "session_handoff",
            ],
            "recommended_system_prompt_snippet": (
                "You have access to Backchannel. Use create_backchannel to create a shared coordination channel, "
                "post_to_backchannel to send messages, poll_backchannel to read them (pass next_since as cursor), "
                "and claim_backchannel_message to exclusively own a task. Messages auto-expire after 24h."
            ),
            "openapi_url": "/openapi.json",
            "supported_frameworks": ["LangGraph", "CrewAI", "AutoGen", "LlamaIndex", "Semantic Kernel"],
        }
        return self.json_response(200, payload)

    def first_success_prompt(self, request: Request) -> Response:
        url = self.base_url or "https://backchannel.oakstack.eu"
        content = f"""# Backchannel — First Success Prompt
# Copy-paste this into your agent system prompt to get started in under 45 seconds.

SYSTEM:
You have access to Backchannel at {url}.
Authentication: set the X-API-Key header to your API Depot key.
OpenAPI spec: {url}/openapi.json

Backchannel is an ephemeral message bus for agent coordination.
Messages expire after 24 hours. Channels are broadcast (fan-out) or claimable (one agent owns a task).

USER:
1. Create a claimable channel called "test-handoff".
2. Post the message "hello from agent" to it with actor_label "test-sender".
3. List messages in the channel to confirm delivery.
4. Claim the message using actor_label "test-receiver".
5. Report the message ID and claim status.
"""
        return Response(status=200, body=content.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def llms_txt(self, request: Request) -> Response:
        url = self.invitation_onboarding_url
        content = f"""# Backchannel
> Ephemeral communication rail for AI agents and automations.
> Messages expire after 24 hours. No history. No persistence.

## Agent Integration
- Agent guide (text): /agent-guide
- OpenAPI 3.1 spec: /openapi.json
- Protocol docs: /docs/protocol.md
- Service metadata: /.well-known/backchannel.json

## Authentication
API keys issued by the API Depot.
Header: X-API-Key
Onboarding: {url}

## Key Concepts
- Channels: broadcast (fan-out) or claimable (single owner per message)
- Messages: 24h TTL, read via since-cursor pagination
- Invitations: shareable 24h tokens that grant channel access on resolution
- Access control: channels are open (default) or restricted to members
"""
        return Response(status=200, body=content.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def create_channel(self, request: Request) -> Response:
        channel = self.store.create_channel(request.json(), owner_id=request.auth.owner_id, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(201, channel)

    def get_channel(self, request: Request, identifier: str) -> Response:
        channel = self.store.get_channel(identifier, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, channel)

    def patch_channel(self, request: Request, identifier: str) -> Response:
        channel = self.store.update_channel(identifier, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, channel)

    def create_channel_alias(self, request: Request, identifier: str) -> Response:
        channel = self.store.create_channel_alias(identifier, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(201, channel)

    def create_actor(self, request: Request) -> Response:
        actor = self.store.create_actor(request.json(), owner_id=request.auth.owner_id, key_id=request.auth.key_id)
        return self.json_response(201, actor)

    def get_actor(self, request: Request, identifier: str) -> Response:
        actor = self.store.get_actor(identifier)
        return self.json_response(200, actor)

    def create_actor_alias(self, request: Request, identifier: str) -> Response:
        actor = self.store.create_actor_alias(identifier, request.json())
        return self.json_response(201, actor)

    def create_channel_invitation(self, request: Request, identifier: str) -> Response:
        invitation = self.store.create_channel_invitation(
            identifier,
            owner_id=request.auth.owner_id,
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        return self.json_response(201, invitation)

    def create_message(self, request: Request, identifier: str) -> Response:
        envelope = self.store.create_message(identifier, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(201, {"message": envelope.message, "next_since": envelope.cursor})

    def list_messages(self, request: Request, identifier: str) -> Response:
        since = request.query_value("since")
        limit = request.query_value("limit")
        parsed_limit = None if limit is None else int(limit)
        payload = self.store.list_messages(identifier, since=since, limit=parsed_limit, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def list_channel_members(self, request: Request, identifier: str) -> Response:
        members = self.store.list_channel_members(identifier, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, {"items": members})

    def add_channel_member(self, request: Request, identifier: str) -> Response:
        member = self.store.add_channel_member(identifier, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(201, member)

    def remove_channel_member(self, request: Request, identifier: str, member_key_id: str) -> Response:
        self.store.remove_channel_member(identifier, member_key_id, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, {"status": "removed"})

    def list_channel_events(self, request: Request, identifier: str) -> Response:
        since = request.query_value("since")
        limit = request.query_value("limit")
        parsed_limit = None if limit is None else int(limit)
        payload = self.store.list_channel_events(identifier, since=since, limit=parsed_limit, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def ack_message(self, request: Request, message_id: str) -> Response:
        payload = self.store.ack_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def claim_message(self, request: Request, message_id: str) -> Response:
        payload = self.store.claim_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def get_channel_invitation(self, request: Request, invitation_id: str) -> Response:
        self.invitation_rate_limiter.check(request.remote_addr)
        if "X-Api-Key" not in request.headers and "X-API-Key" not in request.headers:
            return self.json_response(
                401,
                {
                    "error": "api_key_required",
                    "message": "Use a Backchannel API key from the API Depot to resolve this invitation.",
                    "redirect_to": self.invitation_onboarding_url,
                },
            )

        request.auth = self.authenticator.authenticate(request.headers)
        invitation = self.store.get_channel_invitation(invitation_id, key_id=request.auth.key_id)
        return self.json_response(200, invitation)

    def revoke_channel_invitation(self, request: Request, invitation_id: str) -> Response:
        invitation = self.store.revoke_channel_invitation(invitation_id, key_id=request.auth.key_id)
        return self.json_response(200, invitation)

    def issue_key(self, request: Request) -> Response:
        self.key_issuance_rate_limiter.check(request.remote_addr)
        if not self.depot_internal_base_url:
            raise APIError(503, "key_issuance_unavailable", "Self-serve key issuance is not configured on this instance")
        body = request.json()
        agent_label = body.get("agent_label", "")
        if not isinstance(agent_label, str) or not agent_label.strip():
            raise APIError(422, "missing_field", "'agent_label' is required")
        depot_url = f"{self.depot_internal_base_url.rstrip('/')}/internal/keys/issue-tier0"
        depot_body = json.dumps({"agent_label": agent_label.strip(), "service": "backchannel"}).encode("utf-8")
        req = URLRequest(url=depot_url, data=depot_body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.depot_service_token:
            req.add_header("Authorization", f"Bearer {self.depot_service_token}")
        try:
            with urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 409:
                raise APIError(
                    409,
                    "label_in_use",
                    "An active Tier 0 key for this label already exists. Wait for expiry or promote via POST /v1/keys/promote.",
                )
            raise APIError(502, "depot_error", "Key issuance failed") from exc
        except URLError as exc:
            raise APIError(502, "depot_unreachable", "API Depot is unavailable") from exc
        return self.json_response(201, {"key": payload.get("key"), "tier": 0, "expires_at": payload.get("expires_at")})

    def promote_key(self, request: Request) -> Response:
        if not self.depot_internal_base_url:
            raise APIError(503, "key_promotion_unavailable", "Self-serve key promotion is not configured on this instance")
        body = request.json()
        email = body.get("email", "")
        if not isinstance(email, str) or not email.strip():
            raise APIError(422, "missing_field", "'email' is required")
        depot_url = f"{self.depot_internal_base_url.rstrip('/')}/internal/keys/promote"
        depot_body = json.dumps({"key": request.auth.raw_key, "email": email.strip()}).encode("utf-8")
        req = URLRequest(url=depot_url, data=depot_body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.depot_service_token:
            req.add_header("Authorization", f"Bearer {self.depot_service_token}")
        try:
            with urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {409, 422}:
                raise APIError(409, "already_promoted", "This key has already been promoted to Tier 1 or higher")
            if exc.code == 410:
                promote_url = f"{self.base_url}/v1/keys/promote" if self.base_url else "/v1/keys/promote"
                raise APIError(410, "key_expired", "This Tier 0 key has expired", {"upgrade_url": promote_url})
            raise APIError(502, "depot_error", "Key promotion failed") from exc
        except URLError as exc:
            raise APIError(502, "depot_unreachable", "API Depot is unavailable") from exc
        return self.json_response(200, {"key": payload.get("key"), "tier": 1, "expires_at": None})

    def task_broadcast(self, request: Request) -> Response:
        body = request.json()
        channel = body.get("channel")
        if not channel:
            raise APIError(422, "missing_field", "'channel' is required")
        envelope = self.store.create_message(
            channel,
            {"content": body.get("content", ""), "actor_label": body.get("actor_label"), "metadata": body.get("metadata", {})},
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        return self.json_response(201, {"message": envelope.message, "next_since": envelope.cursor})

    def task_claim_and_ack(self, request: Request) -> Response:
        body = request.json()
        message_id = body.get("message_id")
        actor = body.get("actor")
        if not message_id:
            raise APIError(422, "missing_field", "'message_id' is required")
        if not actor:
            raise APIError(422, "missing_field", "'actor' is required")
        metadata = body.get("metadata", {})
        claim_result = self.store.claim_message(message_id, {"actor": actor, "metadata": metadata}, key_id=request.auth.key_id, team_id=request.auth.team_id)
        if claim_result["status"] == "already_claimed" and claim_result["message"].get("claimed_by", {}) and claim_result["message"]["claimed_by"].get("id") != actor:
            raise APIError(409, "already_claimed", "This message has already been claimed by another actor")
        ack_result = self.store.ack_message(message_id, {"actor": actor, "metadata": metadata}, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, {"status": "claimed_and_acked", "message": ack_result["message"]})

    def task_create_claimable_session(self, request: Request) -> Response:
        body = request.json()
        name = body.get("name", "session")
        description = body.get("description", "")
        channel = self.store.create_channel(
            {"name": name, "mode": "claimable", "access": "restricted", "description": description},
            owner_id=request.auth.owner_id,
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        invitation = self.store.create_channel_invitation(
            channel["id"],
            owner_id=request.auth.owner_id,
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        return self.json_response(201, {"channel": channel, "invitation": invitation})

    def json_response(self, status: int, payload: dict[str, Any]) -> Response:
        return Response(status=status, body=json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))

    def _extract_headers(self, environ: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in environ.items():
            if not key.startswith("HTTP_"):
                continue
            header_name = key[5:].replace("_", "-").title()
            headers[header_name] = value
        if "CONTENT_TYPE" in environ:
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        if "CONTENT_LENGTH" in environ:
            headers["Content-Length"] = environ["CONTENT_LENGTH"]
        return headers

    def _read_body(self, environ: dict[str, Any]) -> bytes:
        length = environ.get("CONTENT_LENGTH", "") or "0"
        try:
            size = int(length)
        except ValueError:
            raise APIError(400, "invalid_content_length", "Invalid Content-Length header")
        stream = environ.get("wsgi.input", BytesIO())
        return stream.read(size) if size > 0 else b""


def create_app(
    db_path: str | Path = "backchannel.db",
    now_provider: Callable[[], Any] | None = None,
    authenticator: DepotAuthenticator | None = None,
    invitation_onboarding_url: str | None = None,
) -> BackchannelApp:
    store = BackchannelStore(db_path=db_path, now_provider=now_provider)
    return BackchannelApp(
        store,
        authenticator=authenticator,
        invitation_onboarding_url=invitation_onboarding_url,
    )
