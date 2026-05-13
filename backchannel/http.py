from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request as URLRequest
from urllib.request import urlopen

from backchannel.auth import (
    AuthContext,
    DepotAuthenticator,
    LocalAuthenticator,
    hash_key,
    mint_raw_key,
    split_key,
)
from backchannel.x402 import (
    PaymentRequirement,
    X402Config,
    X402Decision,
    X402Middleware,
)
from backchannel.observability import record_request, registry as metrics_registry
from backchannel.landing import render_landing_page
from backchannel.openapi import build_openapi_spec
from backchannel.rate_limit import SlidingWindowRateLimiter
from backchannel.store import APIError, BackchannelStore


RouteHandler = Callable[..., "Response"]


def _template_path(path: str) -> str:
    """Collapse path-id segments to {id} so metrics cardinality stays bounded.

    Heuristic: replace any segment that looks like an opaque id (long, mostly
    alphanumeric, contains a digit) with '{id}'. Keeps short literal segments
    like 'channels', 'messages', 'keys', 'health' intact.
    """
    parts: list[str] = []
    for seg in path.split("/"):
        if not seg:
            parts.append(seg)
            continue
        if len(seg) >= 8 and any(c.isdigit() for c in seg) and all(c.isalnum() or c in "-_." for c in seg):
            parts.append("{id}")
        else:
            parts.append(seg)
    return "/".join(parts)


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
        authenticator: DepotAuthenticator | LocalAuthenticator | None = None,
        invitation_onboarding_url: str | None = None,
        invitation_rate_limiter: SlidingWindowRateLimiter | None = None,
    ):
        self.store = store
        self.authenticator = authenticator or LocalAuthenticator(store=store)
        self.invitation_onboarding_url = invitation_onboarding_url or os.environ.get(
            "BACKCHANNEL_INVITATION_ONBOARDING_URL",
            "",
        )
        self.base_url = os.environ.get("BACKCHANNEL_BASE_URL", "")
        # Legacy depot env vars are intentionally ignored — auth is self-contained now.
        self.demo_key = os.environ.get("BACKCHANNEL_DEMO_KEY", "")
        self.x402 = X402Middleware(X402Config.from_env())
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
        # Non-enforcing tracker for X-RateLimit-Remaining header (keyed by key_id)
        self.api_rate_tracker = SlidingWindowRateLimiter(
            limit=300,
            window_seconds=60,
            now_provider=self.store.now,
        )
        self.routes: list[tuple[str, re.Pattern[str], bool, RouteHandler]] = [
            ("GET", re.compile(r"^/$"), False, self.root),
            ("GET", re.compile(r"^/health$"), False, self.health),
            ("GET", re.compile(r"^/openapi\.json$"), False, self.openapi),
            ("GET", re.compile(r"^/agent-guide$"), False, self.agent_guide),
            ("GET", re.compile(r"^/ai-manifest\.json$"), False, self.ai_manifest),
            ("GET", re.compile(r"^/\.well-known/backchannel\.json$"), False, self.well_known),
            ("GET", re.compile(r"^/\.well-known/ai-manifest\.json$"), False, self.ai_manifest),
            ("GET", re.compile(r"^/\.well-known/openapi\.json$"), False, self.openapi),
            ("GET", re.compile(r"^/first-success-prompt\.txt$"), False, self.first_success_prompt),
            ("GET", re.compile(r"^/llms\.txt$"), False, self.llms_txt),
            ("GET", re.compile(r"^/docs/(?P<document>protocol|auth-integration|roadmap|sla|reliability|errors)\.md$"), False, self.read_doc),
            ("GET", re.compile(r"^/docs/playground$"), False, self.playground),
            ("GET", re.compile(r"^/compare$"), False, self.compare),
            ("GET", re.compile(r"^/pricing$"), False, self.pricing_page),
            ("GET", re.compile(r"^/metrics$"), False, self.prometheus_metrics),
            ("GET", re.compile(r"^/robots\.txt$"), False, self.robots_txt),
            ("GET", re.compile(r"^/\.well-known/ai-plugin\.json$"), False, self.ai_plugin),
            ("GET", re.compile(r"^/\.well-known/agent-policy\.json$"), False, self.agent_policy),
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
            ("POST", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)/release$"), True, self.release_message),
            ("POST", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)/claim-with-lease$"), True, self.claim_message_with_lease),
            ("POST", re.compile(r"^/v1/leases/(?P<lease_token>[^/]+)/heartbeat$"), True, self.heartbeat_lease),
            ("DELETE", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)$"), True, self.delete_message),
            ("DELETE", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)$"), True, self.delete_channel),
            ("POST", re.compile(r"^/v1/keys$"), False, self.issue_key),
            ("POST", re.compile(r"^/v1/keys/x402$"), False, self.issue_key_x402),
            ("POST", re.compile(r"^/v1/keys/promote$"), True, self.promote_key),
            ("POST", re.compile(r"^/v1/tasks/broadcast$"), True, self.task_broadcast),
            ("POST", re.compile(r"^/v1/tasks/post$"), True, self.task_post),
            ("POST", re.compile(r"^/v1/tasks/claim$"), True, self.task_claim_verb),
            ("POST", re.compile(r"^/v1/tasks/subscribe$"), True, self.task_subscribe),
            ("POST", re.compile(r"^/v1/tasks/claim-and-ack$"), True, self.task_claim_and_ack),
            ("POST", re.compile(r"^/v1/tasks/create-claimable-session$"), True, self.task_create_claimable_session),
            ("POST", re.compile(r"^/v1/tasks/post-with-result$"), True, self.task_post_with_result),
            ("POST", re.compile(r"^/v1/tasks/(?P<message_id>[^/]+)/result$"), True, self.task_publish_result),
            ("GET", re.compile(r"^/v1/tasks/(?P<message_id>[^/]+)/result$"), True, self.task_await_result),
            ("GET", re.compile(r"^/v1/pricing/estimate$"), False, self.pricing_estimate),
            ("GET", re.compile(r"^/v1/sessions$"), True, self.list_sessions),
            ("POST", re.compile(r"^/v1/sessions$"), True, self.create_session),
            ("GET", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.get_session),
            ("PATCH", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.patch_session),
            ("DELETE", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.delete_session),
            ("GET", re.compile(r"^/v1/observability/metrics$"), True, self.observability_metrics),
            ("GET", re.compile(r"^/v1/keys/me$"), True, self.keys_me),
            ("PUT", re.compile(r"^/v1/keys/me/scopes$"), True, self.set_key_scopes),
            ("GET", re.compile(r"^/account/usage$"), True, self.account_usage),
            ("GET", re.compile(r"^/status$"), False, self.status),
            ("GET", re.compile(r"^/status\.html$"), False, self.status_page),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/metrics$"), True, self.channel_metrics),
            ("GET", re.compile(r"^/v1/security/audit$"), True, self.security_audit),
        ]

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        request_id = str(uuid.uuid4())
        docs_base = self.base_url or "https://backchannel.oakstack.eu"
        _t0 = __import__("time").monotonic()
        method_for_metrics = environ.get("REQUEST_METHOD", "GET")
        path_for_metrics = environ.get("PATH_INFO", "/")
        try:
            response = self.dispatch(environ)
        except APIError as exc:
            payload = exc.to_payload()
            payload["request_id"] = request_id
            payload["documentation_url"] = f"{docs_base}/docs/errors.md#{exc.error.replace('_', '-')}"
            response = self.json_response(exc.status, payload)
        except Exception as exc:  # pragma: no cover - defensive safeguard
            payload = {
                "error": "internal_server_error",
                "message": str(exc),
                "request_id": request_id,
                "documentation_url": f"{docs_base}/docs/errors.md#internal-server-error",
            }
            response = self.json_response(500, payload)
        try:
            record_request(
                method_for_metrics,
                _template_path(path_for_metrics),
                response.status,
                (__import__("time").monotonic() - _t0) * 1000.0,
            )
        except Exception:  # pragma: no cover - metrics must never break a request
            pass

        # W3C traceparent: echo incoming or generate from request_id
        incoming_traceparent = environ.get("HTTP_TRACEPARENT", "")
        if incoming_traceparent:
            traceparent = incoming_traceparent
        else:
            trace_id = request_id.replace("-", "")
            span_id = trace_id[:16]
            traceparent = f"00-{trace_id}-{span_id}-01"

        headers = [
            ("Content-Type", response.content_type),
            ("Content-Length", str(len(response.body))),
            ("X-Request-Id", request_id),
            ("traceparent", traceparent),
            ("X-RateLimit-Limit", "300"),
            ("X-RateLimit-Window", "60"),
        ]
        if response.status < 400:
            headers.append(('Link', '</openapi.json>; rel="service-desc"'))
            headers.append(('Link', '</.well-known/ai-manifest.json>; rel="ai-manifest"'))
        if response.status == 429:
            headers.append(("Retry-After", "60"))
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
                    request.auth.scopes = self.store.get_key_scopes(request.auth.key_id)
                    rate_limits_by_tier = {0: 300, 1: 300, 2: 1000}
                    tier_limit = rate_limits_by_tier.get(request.auth.tier or 0, 300)
                    remaining = self.api_rate_tracker.track(request.auth.key_id, limit=tier_limit)
                    # Will be appended to response headers below
                    request.rate_limit_remaining = remaining
                # Idempotency middleware for mutation requests.
                #
                # Behavior:
                #   - Client-supplied Idempotency-Key: trusted as-is. Replays
                #     return the original response with X-Idempotent-Replay: true.
                #   - No Idempotency-Key: server synthesizes one from
                #     (key_id, method, path, sha256(body)). Retries of the *exact
                #     same request* within the cache window return the original
                #     response. The synthetic key is surfaced as
                #     Idempotency-Key + X-Idempotency-Source: server-auto so
                #     clients can opt into stronger semantics with an explicit key.
                idempotency_key = request.headers.get("Idempotency-Key")
                idempotency_source = "client" if idempotency_key else "server-auto"
                is_mutation = method in {"POST", "PATCH", "DELETE"}
                # Auto-idempotency is skipped on handlers that intentionally
                # return distinct application-level responses for repeated
                # calls (ack/claim/release/heartbeat — their already_X status
                # codes are part of the contract). Explicit Idempotency-Key
                # still works on those routes.
                _auto_skip_suffixes = (
                    "/ack", "/claim", "/claim-with-lease", "/release", "/heartbeat",
                )
                if (
                    is_mutation
                    and request.auth
                    and not idempotency_key
                    and not any(path.endswith(suffix) for suffix in _auto_skip_suffixes)
                ):
                    body_digest = hashlib.sha256(request.body or b"").hexdigest()[:32]
                    idempotency_key = f"auto-{method}-{body_digest}-{path}"
                if idempotency_key and is_mutation and request.auth:
                    cache_key = f"{request.auth.key_id}:{idempotency_key}"
                    cached = self.store.get_idempotent_response(cache_key)
                    if cached is not None:
                        replay = Response(
                            status=cached["status"],
                            body=cached["body"].encode("utf-8"),
                            extra_headers=[
                                ("X-Idempotent-Replay", "true"),
                                ("X-Idempotency-Source", idempotency_source),
                                ("Idempotency-Key", idempotency_key),
                            ],
                        )
                        return replay
                response = handler(request, **match.groupdict())
                if hasattr(request, "rate_limit_remaining"):
                    response.extra_headers.append(("X-RateLimit-Remaining", str(request.rate_limit_remaining)))
                if idempotency_key and is_mutation and request.auth and response.status < 300:
                    cache_key = f"{request.auth.key_id}:{idempotency_key}"
                    self.store.cache_idempotent_response(cache_key, response.status, response.body.decode("utf-8"))
                    response.extra_headers.append(("Idempotency-Key", idempotency_key))
                    response.extra_headers.append(("X-Idempotency-Source", idempotency_source))
                return response

        return self.json_response(404, {"error": "not_found", "message": f"No route for {method} {path}"})

    def root(self, request: Request) -> Response:
        html = render_landing_page(self.invitation_onboarding_url)
        return Response(status=200, body=html.encode("utf-8"), content_type="text/html; charset=utf-8")

    def health(self, request: Request) -> Response:
        import time
        t0 = time.monotonic()
        with self.store.connect() as conn:
            conn.execute("SELECT 1")
        db_latency_ms = round((time.monotonic() - t0) * 1000, 1)
        base = self.base_url or "https://backchannel.oakstack.eu"
        return self.json_response(200, {
            "status": "ok",
            "db_latency_ms": db_latency_ms,
            "version": "1.0",
            "status_url": f"{base}/docs/reliability.md",
        })

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
        base = self.base_url or "https://backchannel.oakstack.eu"
        guide = f"""# Backchannel — Agent System Prompt

## What it is
Backchannel is an ephemeral message bus for agent coordination.
Messages expire after 24 hours. No persistence. No history beyond the TTL.
Use it for: multi-agent handoffs, broadcast fan-out, temporary shared state.
Do NOT use it for: persistent storage, human chat, synchronous RPC.

## Authentication
Header: X-API-Key
Get an instant free key (no sign-up):
  POST {base}/v1/keys
  Body: {{"agent_label": "your-agent-name"}}
  Returns: {{"key": "...", "tier": 0, "expires_at": "..."}}

## Idempotency
All write operations accept an optional Idempotency-Key header.
Repeat the same key within 24h to get the same response without side effects.
  Idempotency-Key: <uuid-or-any-unique-string>
  Response includes X-Idempotent-Replay: true if served from cache.

---

## Pattern 1: Claimable task handoff (one producer → one consumer)

# Step 1 — producer creates a channel
POST {base}/v1/channels
X-API-Key: <key>
{{"name": "task-queue", "mode": "claimable"}}
→ returns channel.id

# Step 2 — producer posts a task message
POST {base}/v1/channels/<channel_id>/messages
X-API-Key: <key>
{{"content": "process invoice #123", "actor_label": "producer-agent"}}
→ returns message.id

# Step 3 — consumer polls for messages
GET {base}/v1/channels/<channel_id>/messages?since=0
X-API-Key: <key>
→ returns messages[], next_cursor cursor

# Step 4 — consumer claims the message (exclusive ownership)
POST {base}/v1/messages/<message_id>/claim
X-API-Key: <key>
{{"actor": "consumer-agent"}}
→ 200 {{"status": "claimed", ...}} or 409 already_claimed (another consumer won)

# Step 5 — consumer acks completion
POST {base}/v1/messages/<message_id>/ack
X-API-Key: <key>
{{"actor": "consumer-agent"}}

---

## Pattern 2: Broadcast fan-out (one producer → N consumers)

POST {base}/v1/channels
{{"name": "results-bus", "mode": "broadcast"}}

POST {base}/v1/channels/<channel_id>/messages
{{"content": "analysis complete", "actor_label": "orchestrator"}}

# All consumers poll independently — no claiming needed
GET {base}/v1/channels/<channel_id>/messages?since=<last_next_cursor>

---

## Reliability
Messages are durable from the moment the 201 is returned (SQLite WAL).
Single-node deployment — no replication. 24h TTL is hard.
Claim is atomic: WHERE claimed_by_actor_id IS NULL + rowcount check.
See: {base}/docs/reliability.md

## Full API reference

### Channels
POST /v1/channels                {{"name":"<str>","mode":"broadcast|claimable","access":"open|restricted"}}
GET /v1/channels/<id_or_alias>
PATCH /v1/channels/<id_or_alias>   patchable: name, mode, access, description, pinned_message
POST /v1/channels/<id>/aliases   {{"alias":"<str>"}}
POST /v1/channels/<id>/invitations → returns invitation_id (24h expiry, grants restricted access)
GET /v1/channels/<id>/members    owner only
POST /v1/channels/<id>/members   {{"key_id":"<str>"}}  owner only
DELETE /v1/channels/<id>/members/<key_id>  owner only
GET /v1/channels/<id>/events     owner only; ?since=<cursor>&limit=<1-100>

### Messages
POST   /v1/channels/<id>/messages   {{"content":"<str>","actor":"<id_or_alias>","actor_label":"<str>","metadata":{{}}}}
GET    /v1/channels/<id>/messages   ?since=<iso_or_0>&limit=<1-100>&status=unclaimed|claimed&expiring_before=<iso>
POST   /v1/messages/<id>/claim      {{"actor":"<id_or_alias>"}}
POST   /v1/messages/<id>/release    {{"actor":"<id_or_alias>"}}  (un-claim; crash recovery)
POST   /v1/messages/<id>/ack        {{"actor":"<id_or_alias>"}}
DELETE /v1/messages/<id>            retract before claim (409 if already claimed)
DELETE /v1/channels/<id>            owner only; cascades messages + members

### Actors
POST /v1/actors                   {{"name":"<str>","description":"<str>"}}
GET  /v1/actors/<id_or_alias>
POST /v1/actors/<id>/aliases      {{"alias":"<str>"}}

### Keys (self-serve)
POST /v1/keys                     {{"agent_label":"<str>"}}  → instant Tier 0 key, no auth required
POST /v1/keys/promote             {{"email":"<str>"}}  → promotes to managed Tier 1 key

### Invitations
GET    /v1/channel-invitations/<id>   resolves token; grants restricted channel access on first call
DELETE /v1/channel-invitations/<id>   revoke

### Observability / account
GET /v1/keys/me         → current key's tier, owner_id, plan
GET /account/usage      → tier and rate limit info

---

## Crash recovery pattern
If a consumer agent crashes after claiming a message, another agent cannot claim it
until the original claimer releases it or the message TTL expires.
Recovery path:
  1. Watchdog polls: GET /v1/channels/<id>/messages?status=unclaimed&expiring_before=<+1h>
  2. If empty (all claimed), check if the claiming agent is still alive
  3. Original claimer calls: POST /v1/messages/<msg_id>/release {{"actor":"<claimer>"}}
  4. Another consumer can now claim it

---

## Error codes
401 unauthorized          missing or invalid X-API-Key
403 channel_access_denied not a member of this restricted channel
404 *_not_found           resource does not exist
409 already_claimed       claimable message already taken — try the next unclaimed message
410 message_expired       message TTL has passed
410 invitation_expired    invitation has passed its 24h expiry
422 *                     request validation failure (see message field)
429 rate_limit_exceeded   back off and retry after Retry-After seconds
"""
        return Response(status=200, body=guide.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def playground(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        auth_config = ""
        if self.demo_key:
            auth_config = (
                f',"authentication":{{"preferredSecurityScheme":"ApiKeyAuth",'
                f'"apiKey":{{"token":"{self.demo_key}"}}}}'
            )
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Backchannel API Playground</title>
  </head>
  <body>
    <script
      id="api-reference"
      data-url="{base}/openapi.json"
      data-configuration='{{
        "theme": "purple",
        "defaultHttpClient": {{"targetKey": "shell", "clientKey": "curl"}},
        "hiddenClients": false,
        "metaData": {{"title": "Backchannel API Playground"}}{auth_config}
      }}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>"""
        return Response(status=200, body=html.encode("utf-8"), content_type="text/html; charset=utf-8")

    def prometheus_metrics(self, request: Request) -> Response:
        body = metrics_registry.render_prometheus().encode("utf-8")
        return Response(status=200, body=body, content_type="text/plain; version=0.0.4")

    def pricing_page(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Backchannel — pricing</title>
    <meta name="description" content="Backchannel pricing. Free 48h test key. €9.99/mo Pro. €39.99/mo Scale. Pay-per-call in USDC via x402 for agent-native billing.">
    <style>
      :root {{
        --bg: #020402; --panel: rgba(7,20,8,0.84); --line: rgba(84,255,138,0.28);
        --text: #d6ffd8; --muted: #8bcf90; --accent: #58ff7d;
      }}
      body {{ margin: 0; padding: 32px 20px; background: var(--bg); color: var(--text);
              font-family: 'IBM Plex Mono', 'Menlo', monospace; line-height: 1.5; }}
      .wrap {{ max-width: 1100px; margin: 0 auto; }}
      a {{ color: var(--accent); text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      h1 {{ font-size: 2.4rem; letter-spacing: -0.03em; margin: 0 0 0.4em; }}
      h1 .accent {{ color: var(--accent); }}
      p.lede {{ color: var(--muted); margin: 0 0 32px; max-width: 700px; }}
      table.cmp {{ width: 100%; border-collapse: collapse; margin: 32px 0;
                   border: 1px solid var(--line); background: var(--panel); }}
      table.cmp th, table.cmp td {{
        padding: 14px 16px; text-align: left; border-bottom: 1px solid var(--line);
        vertical-align: top; font-size: 0.9rem;
      }}
      table.cmp thead th {{ background: rgba(88,255,125,0.08); color: var(--accent); }}
      table.cmp tbody tr:last-child td {{ border-bottom: none; }}
      table.cmp tbody tr.row-feature td:first-child {{ color: var(--muted); }}
      table.cmp td.tier-price {{ font-size: 1.2rem; font-weight: 700; color: var(--accent); }}
      table.cmp td.tier-x402 {{ font-style: italic; opacity: 0.85; }}
      table.cmp td.yes {{ color: var(--accent); }}
      table.cmp td.no {{ color: rgba(255,120,120,0.85); }}
      .cta-row {{ display: flex; gap: 14px; flex-wrap: wrap; margin-top: 32px; }}
      .cta-row a.btn {{ padding: 12px 20px; border: 1px solid var(--accent); border-radius: 8px;
                        background: rgba(88,255,125,0.06); }}
      .cta-row a.btn.primary {{ background: var(--accent); color: var(--bg); border-color: var(--accent); }}
      .small {{ color: var(--muted); font-size: 0.85rem; }}
      .footnote {{ color: var(--muted); font-size: 0.8rem; margin-top: 24px; }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <p class="small"><a href="/">← Backchannel</a></p>
      <h1>Pricing.<br><span class="accent">Pick the lane that matches the agent.</span></h1>
      <p class="lede">
        Backchannel charges for production access, not for trying it.
        Spin up a 48-hour test key without signing up. When the agent is
        ready, upgrade — by email (Pro, Scale) or by USDC (x402).
      </p>

      <table class="cmp">
        <thead>
          <tr>
            <th></th>
            <th>Test</th>
            <th>Pro</th>
            <th>Scale</th>
            <th>x402</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Price</strong></td>
            <td class="tier-price">Free</td>
            <td class="tier-price">€9.99<span class="small">/mo</span></td>
            <td class="tier-price">€39.99<span class="small">/mo</span></td>
            <td class="tier-price tier-x402">USDC<span class="small">/call</span></td>
          </tr>
          <tr><td><strong>Audience</strong></td>
            <td>Evaluation</td><td>Single agent / small team</td>
            <td>Agent swarms</td><td>Wallet-equipped agents</td></tr>
          <tr><td><strong>Key lifetime</strong></td>
            <td>48 hours</td><td>Permanent</td>
            <td>Permanent</td><td>Permanent (per settlement)</td></tr>
          <tr><td><strong>Rate limit</strong></td>
            <td>300 req/min</td><td>300 req/min</td>
            <td>1000 req/min</td><td>per-call billed</td></tr>
          <tr><td><strong>Channels</strong></td>
            <td class="yes">unlimited</td><td class="yes">unlimited</td>
            <td class="yes">unlimited</td><td class="yes">unlimited</td></tr>
          <tr><td><strong>Restricted channels + invitations</strong></td>
            <td class="yes">yes</td><td class="yes">yes</td>
            <td class="yes">yes</td><td class="yes">yes</td></tr>
          <tr><td><strong>Claim-with-lease + heartbeat</strong></td>
            <td class="yes">yes</td><td class="yes">yes</td>
            <td class="yes">yes</td><td class="yes">yes</td></tr>
          <tr><td><strong>Webhooks</strong></td>
            <td class="yes">yes</td><td class="yes">yes</td>
            <td class="yes">yes</td><td class="yes">yes</td></tr>
          <tr><td><strong>Team quotas</strong></td>
            <td class="no">no</td><td class="yes">yes</td>
            <td class="yes">yes</td><td class="no">no</td></tr>
          <tr><td><strong>Priority support</strong></td>
            <td class="no">community</td><td>email (best-effort)</td>
            <td class="yes">email (24h SLA)</td><td>community</td></tr>
          <tr><td><strong>Signup</strong></td>
            <td>none</td><td>email magic-link</td>
            <td>email magic-link</td><td>wallet (no email)</td></tr>
          <tr><td><strong>Settlement</strong></td>
            <td>—</td><td>Stripe (monthly)</td>
            <td>Stripe (monthly + metered overage)</td>
            <td>USDC on Base via <a href="https://www.x402.org/">x402</a></td></tr>
          <tr><td><strong>Get started</strong></td>
            <td><code>POST /v1/keys</code></td>
            <td><code>POST /v1/keys/promote</code></td>
            <td><code>POST /v1/keys/promote</code></td>
            <td><code>POST /v1/keys/x402</code></td></tr>
        </tbody>
      </table>

      <p class="small">
        Launch pricing: Pro and Scale are free during the launch window.
        x402 is opt-in per instance — see
        <a href="/docs/x402.md">docs/x402.md</a> for facilitator setup.
      </p>

      <div class="cta-row">
        <a class="btn primary" href="/">Get a Test key (60 seconds)</a>
        <a class="btn" href="/docs/x402.md">Pay-per-call docs</a>
        <a class="btn" href="/llms.txt">llms.txt</a>
        <a class="btn" href="/openapi.json">OpenAPI</a>
      </div>

      <p class="footnote">
        All tiers run the same engine, the same protocol, and the same
        agent-first surface. The difference is volume, support, and how
        you settle the bill.
      </p>
    </div>
  </body>
</html>
"""
        return Response(status=200, body=html.encode("utf-8"), content_type="text/html; charset=utf-8")

    def compare(self, request: Request) -> Response:
        html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Backchannel vs. Alternatives</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
      h1 { font-size: 1.5rem; }
      table { border-collapse: collapse; width: 100%; margin-top: 1.5rem; }
      th, td { border: 1px solid #ddd; padding: 0.6rem 0.8rem; text-align: left; }
      th { background: #f5f5f5; }
      .win { color: #2a7a2a; font-weight: bold; }
      .lose { color: #999; }
      .partial { color: #888; }
      p.note { color: #666; font-size: 0.9rem; }
    </style>
  </head>
  <body>
    <h1>Backchannel vs. Alternatives</h1>
    <p>An honest feature matrix. Where a competitor wins, we say so.</p>

    <table>
      <thead>
        <tr>
          <th>Feature</th>
          <th>Backchannel</th>
          <th>Redis pub/sub</th>
          <th>AWS SQS</th>
          <th>Raw DB queue</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Zero setup for consumers</td>
          <td class="win">✓ HTTP, no client lib</td>
          <td class="lose">✗ Redis client needed</td>
          <td class="lose">✗ AWS SDK needed</td>
          <td class="partial">~ depends on DB</td>
        </tr>
        <tr>
          <td>Atomic claim (first-wins)</td>
          <td class="win">✓ exactly-once guarantee</td>
          <td class="lose">✗ not supported</td>
          <td class="partial">~ visibility timeout</td>
          <td class="partial">~ with SELECT FOR UPDATE</td>
        </tr>
        <tr>
          <td>Auto-expiry / TTL</td>
          <td class="win">✓ 24h</td>
          <td class="win">✓ configurable</td>
          <td class="win">✓ up to 14 days</td>
          <td class="lose">✗ manual cleanup</td>
        </tr>
        <tr>
          <td>Agent-native discovery (OpenAPI, llms.txt)</td>
          <td class="win">✓</td>
          <td class="lose">✗</td>
          <td class="lose">✗</td>
          <td class="lose">✗</td>
        </tr>
        <tr>
          <td>Instant free key (no sign-up)</td>
          <td class="win">✓ POST /v1/keys</td>
          <td class="lose">✗</td>
          <td class="lose">✗ AWS account needed</td>
          <td class="lose">✗</td>
        </tr>
        <tr>
          <td>Horizontal scale</td>
          <td class="lose">✗ single-node v1</td>
          <td class="win">✓ clustered</td>
          <td class="win">✓ managed</td>
          <td class="lose">✗</td>
        </tr>
        <tr>
          <td>Persistent storage (&gt;24h)</td>
          <td class="lose">✗ 24h TTL is hard</td>
          <td class="lose">✗ volatile by default</td>
          <td class="partial">~ up to 14 days</td>
          <td class="win">✓</td>
        </tr>
        <tr>
          <td>Approximate cost at 1M req/day</td>
          <td>€9.99/mo (Tier 1)</td>
          <td>~€30/mo hosted</td>
          <td>~€0.40/mo</td>
          <td>infra cost only</td>
        </tr>
      </tbody>
    </table>

    <p class="note">Backchannel is optimised for agent coordination workloads: ephemeral handoffs between agents, broadcast fan-out, and temporary shared state. If you need persistent queues, durable storage, or high-throughput horizontal scale, use SQS or a purpose-built queue. Backchannel wins on simplicity, agent-native discovery, and exact-once claim semantics over HTTP.</p>
    <p><a href="/agent-guide">Agent Guide</a> &middot; <a href="/openapi.json">OpenAPI</a> &middot; <a href="/docs/playground">Playground</a></p>
  </body>
</html>"""
        return Response(status=200, body=html.encode("utf-8"), content_type="text/html; charset=utf-8")

    def robots_txt(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        content = f"""User-agent: *
Allow: /

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Googlebot
Allow: /

# Structured discovery for AI crawlers
# OpenAPI spec: {base}/openapi.json
# Agent guide: {base}/agent-guide
# AI manifest: {base}/.well-known/ai-manifest.json
# LLMs.txt: {base}/llms.txt
"""
        return Response(status=200, body=content.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def ai_plugin(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        payload = {
            "schema_version": "v1",
            "name_for_human": "Backchannel",
            "name_for_model": "backchannel",
            "description_for_human": "How agents call other agents. Atomic claimable task handoff over HTTP, with an MCP server.",
            "description_for_model": (
                "Backchannel lets one agent hand work to another. "
                "Use a claimable channel when exactly one other agent should pick up the task "
                "(first valid claim wins; the rest get 409 — do not retry on 409). "
                "Use a broadcast channel for fan-out. Messages auto-expire after the channel's TTL. "
                "Get a key with POST /v1/keys (instant, no signup) or use the bundled MCP server "
                "and the first tool call will mint one for you."
            ),
            "auth": {
                "type": "user_http",
                "authorization_type": "bearer",
            },
            "api": {
                "type": "openapi",
                "url": f"{base}/openapi.json",
                "is_user_authenticated": True,
            },
            "logo_url": f"{base}/favicon.ico",
            "contact_email": "hello@oakstack.eu",
            "legal_info_url": f"{base}/docs/protocol.md",
        }
        return self.json_response(200, payload)

    def agent_policy(self, request: Request) -> Response:
        payload = {
            "rate_limits": [
                {"tier": 0, "requests_per_window": 300, "window_seconds": 60, "name": "Test"},
                {"tier": 1, "requests_per_window": 300, "window_seconds": 60, "name": "Free"},
                {"tier": 2, "requests_per_window": 1000, "window_seconds": 60, "name": "Pro"},
            ],
            "retry_guidance": {
                "on_429": "back_off_and_retry",
                "retry_after_header": True,
                "idempotency_key_supported": True,
            },
            "message_ttl_hours": 24,
            "max_content_bytes": 65536,
            "claim_guarantee": "exactly_once",
        }
        return self.json_response(200, payload)

    def well_known(self, request: Request) -> Response:
        return Response(
            status=302,
            body=b"",
            content_type="text/plain",
            extra_headers=[("Location", "/.well-known/ai-manifest.json")],
        )

    def ai_manifest(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        payload = {
            "name": "Backchannel",
            "tagline": "How agents call other agents.",
            "description": (
                "Atomic claimable task handoff over HTTP. Two agents that don't "
                "share a process can coordinate durably: one posts a task, the "
                "other claims it, and the claim is exclusive (first valid claim "
                "wins; the rest get 409)."
            ),
            "version": "1.0",
            "base_url": base,
            "auth": {
                "type": "api_key",
                "header": "X-API-Key",
                "obtain_url": f"{base}/v1/keys",
                "obtain_description": "POST /v1/keys with {\"agent_label\": \"...\"} → instant 48h key, no signup. Promote later with /v1/keys/promote.",
                "pay_per_call_url": f"{base}/v1/keys/x402",
                "pay_per_call_description": "Agents with a wallet can pay-per-call in USDC via x402 — no signup, no card.",
            },
            "transports": {
                "http": {"openapi_url": f"{base}/openapi.json"},
                "mcp": {
                    "package": "backchannel-mcp",
                    "install": "pip install backchannel-mcp && claude mcp add backchannel -- backchannel-mcp",
                    "tools": [
                        "post_task", "claim_task", "await_result",
                        "broadcast", "subscribe", "list_channels", "issue_key",
                    ],
                },
            },
            "capabilities": [
                "multi_agent_coordination",
                "atomic_task_handoff",
                "claim_with_lease_and_heartbeat",
                "broadcast_fanout",
                "restricted_channels_with_invitations",
                "per_channel_metadata_schema",
                "idempotent_writes",
            ],
            "claim_guarantees": {
                "exclusivity": "first_valid_claim_wins",
                "duplicate_response": "409 already_claimed",
                "redelivery": "lease_expiry_returns_message_to_queue",
            },
            "recommended_system_prompt_snippet": (
                f"You can call other agents via Backchannel ({base}).\n"
                f"Step 0: POST {base}/v1/keys {{\"agent_label\": \"<your name>\"}} → get a key.\n"
                "Step 1: POST /v1/channels {\"name\": \"<lane>\", \"mode\": \"claimable\"}.\n"
                "Step 2: POST /v1/channels/<id>/messages {\"content\": \"<task>\"}.\n"
                "Step 3: On 409 from a claim, the other agent got it first — move on.\n"
                "Idempotency: send Idempotency-Key on every write. See /llms.txt for the full spec."
            ),
            "openapi_url": f"{base}/openapi.json",
            "agent_guide_url": f"{base}/agent-guide",
            "llms_txt_url": f"{base}/llms.txt",
            "supported_frameworks": [
                "Claude Code (MCP)", "Cursor (MCP)", "Zed (MCP)",
                "LangGraph", "CrewAI", "AutoGen", "LlamaIndex", "n8n",
            ],
        }
        return self.json_response(200, payload)

    def first_success_prompt(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        content = f"""# Backchannel — First Success Prompt
# Copy-paste into your agent's system prompt. First call in under 60 seconds.

SYSTEM:
You have access to Backchannel at {base}.
Auth: X-API-Key header. If you don't have a key yet, get one instantly:
  POST {base}/v1/keys with body {{"agent_label": "your-name"}} (no prior auth needed)

Backchannel is an ephemeral message bus for agent coordination.
Messages expire after 24 hours. No history, no persistence.
Channels are broadcast (fan-out to all readers) or claimable (exactly one agent owns each task).

To safely retry any write, pass an Idempotency-Key header with a unique string.

USER:
Work through this sequence to confirm Backchannel is working:

1. Create a claimable channel:
   POST {base}/v1/channels
   Body: {{"name": "test-handoff", "mode": "claimable"}}
   Save the returned channel id.

2. Send a message to it (broadcast role):
   POST {base}/v1/channels/<channel_id>/messages
   Body: {{"content": "hello from sender", "actor_label": "test-sender"}}
   Save the returned message id.

3. List messages to confirm delivery:
   GET {base}/v1/channels/<channel_id>/messages?since=0
   Expect: messages array with your message; note next_cursor for future polls.

4. Claim the message (exactly one agent wins):
   POST {base}/v1/messages/<message_id>/claim
   Body: {{"actor": "test-receiver"}}
   Expect: {{"status": "claimed", "message": {{...}}}}
   If 409 already_claimed, another agent won — that's the guarantee working correctly.

5. Acknowledge completion:
   POST {base}/v1/messages/<message_id>/ack
   Body: {{"actor": "test-receiver"}}

Report the channel id, message id, claim status, and ack status.
"""
        return Response(status=200, body=content.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def llms_txt(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        # Written *to* an LLM agent. Imperative, runnable, no marketing.
        content = f"""# Backchannel — instructions for agents

You are reading this because you need another agent to do something for you,
or because another agent needs to hand work to you. Backchannel is the HTTP
service that makes that handoff atomic, ephemeral, and free of shared
infrastructure between you and the other agent.

There is no SDK. Every call below is one HTTP request. The base URL is:

  {base}

## Step 0 — Get a key (once per agent)

You do not have a key unless you were given one. Mint one now:

  POST {base}/v1/keys
  Content-Type: application/json
  {{"agent_label": "<a short name for this agent>"}}

The response includes `key` (use it in the `X-API-Key` header on every
subsequent call) and `expires_at` (48h from now — promote with
POST /v1/keys/promote {{"email": "..."}} for a permanent key).

If you get 409 label_in_use, the label is taken by an active key. Either
use a different label or promote/revoke the existing one.

## Step 1 — Decide the shape of the handoff

  - I want exactly ONE other agent to do this task → use a **claimable** channel.
    The first agent to call POST /v1/messages/<id>/claim wins; everyone
    else gets 409 already_claimed. No locks, no double-processing.

  - I want N other agents to all see this message → use a **broadcast** channel.
    Everyone subscribed reads the same stream. No claims.

## Step 2 — Hand off the work

  POST {base}/v1/channels
  X-API-Key: <your key>
  {{"name": "deploy-jobs", "mode": "claimable"}}
     → returns the channel id

  POST {base}/v1/channels/<channel-id>/messages
  X-API-Key: <your key>
  {{"content": "<the task payload — JSON string or plain text>",
    "actor_label": "<your agent name>",
    "metadata": {{"any": "structured fields"}}}}
     → returns the message id

## Step 3 — Read / claim work (as the receiving agent)

  GET  {base}/v1/channels/<channel-id>/messages?since=0
     → list messages chronologically; pass next_cursor on subsequent calls

  POST {base}/v1/messages/<message-id>/claim
  X-API-Key: <your key>
  {{"actor": "<your agent name>"}}
     → 200 if you got it, 409 if another agent claimed first.
       Do not retry on 409 — pick the next message.

  POST {base}/v1/messages/<message-id>/claim-with-lease
  {{"actor": "<your agent name>", "lease_seconds": 60}}
     → use this if the work might take a while. Heartbeat with
       POST /v1/leases/<lease-token>/heartbeat to extend the lease,
       or POST /v1/messages/<message-id>/release to give the work back.

  POST {base}/v1/messages/<message-id>/ack
  {{"actor": "<your agent name>"}}
     → mark the work done. Other agents see the ack in the channel.

## Step 4 — Cross-agent reliability

  - Always send `Idempotency-Key: <a uuid you generate>` on POST/PATCH/DELETE.
    If your request times out, retry with the same key — you will get the
    original response, not a duplicate side effect.
  - Messages auto-expire after the channel's TTL (default 24h). Do not
    rely on them as durable storage.
  - On any 5xx, retry with exponential backoff. The 502/503 codes are
    transient and safe to retry.
  - Watch the `X-RateLimit-Remaining` header. When it nears 0, slow down
    or you will receive 429 with `Retry-After`.

## Step 5 — Restrict access (only when you need to)

Channels default to `access: "open"` (any authenticated key can read/write).
If the receiving agent is in a different org or you do not control its key:

  POST {base}/v1/channels
  {{"name": "...", "mode": "claimable", "access": "restricted"}}

  POST {base}/v1/channels/<channel-id>/invitations
  {{}}
     → returns an invitation URL. Give it to the other agent. When the
       other agent GETs that URL with its X-API-Key, it becomes a member
       of the restricted channel automatically.

## Failure modes you must handle

  - 401 unauthorized        — your X-API-Key is missing/invalid/revoked.
  - 410 key_expired         — your Tier-0 key passed 48h. POST /v1/keys/promote.
  - 409 already_claimed     — another agent got the message first. Move on.
  - 409 already_acknowledged — message was already acked. Treat as success.
  - 404 message_not_found    — message TTL expired or was retracted.
  - 422 metadata_validation_failed — channel has a schema; your payload
                              did not match. Read the message field for which.
  - 429 rate_limit_exceeded  — sleep `Retry-After` seconds, then retry.

## Discovery resources (for you, the agent)

  GET {base}/openapi.json                       — full machine-readable contract
  GET {base}/.well-known/ai-manifest.json       — capability manifest
  GET {base}/agent-guide                        — longer system-prompt-ready guide
  GET {base}/first-success-prompt.txt           — verbatim prompt for first-run agents
  GET {base}/docs/protocol.md                   — human-readable protocol reference
  GET {base}/docs/errors.md                     — every error code with cause + action

If you can read OpenAPI, prefer {base}/openapi.json — it always matches the
running service. This text is the same contract, in prose, in case OpenAPI
is not accessible.
"""
        return Response(status=200, body=content.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def create_channel(self, request: Request) -> Response:
        self._require_scope(request.auth, "channels:write")
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
        self._require_scope(request.auth, "messages:write")
        envelope = self.store.create_message(identifier, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(201, {"message": envelope.message, "next_cursor": envelope.cursor})

    def list_messages(self, request: Request, identifier: str) -> Response:
        self._require_scope(request.auth, "messages:read")
        # cursor is the stable alias; since is deprecated but still accepted
        since = request.query_value("cursor") or request.query_value("since")
        limit = request.query_value("limit")
        parsed_limit = None if limit is None else int(limit)
        status = request.query_value("status")
        expiring_before = request.query_value("expiring_before")
        payload = self.store.list_messages(identifier, since=since, limit=parsed_limit, key_id=request.auth.key_id, team_id=request.auth.team_id, status=status, expiring_before=expiring_before)
        response = self.json_response(200, payload)
        if request.query_value("since") and not request.query_value("cursor"):
            response.extra_headers.append(("Deprecation", "true"))
            response.extra_headers.append(("Sunset", "2027-01-01"))
        return response

    def list_channel_members(self, request: Request, identifier: str) -> Response:
        members = self.store.list_channel_members(identifier, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, {"data": members})

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
        self._require_scope(request.auth, "messages:claim")
        payload = self.store.claim_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def release_message(self, request: Request, message_id: str) -> Response:
        payload = self.store.release_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def claim_message_with_lease(self, request: Request, message_id: str) -> Response:
        payload = self.store.claim_with_lease(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, payload)

    def heartbeat_lease(self, request: Request, lease_token: str) -> Response:
        payload = self.store.heartbeat_lease(lease_token, request.json(), key_id=request.auth.key_id)
        return self.json_response(200, payload)

    def delete_message(self, request: Request, message_id: str) -> Response:
        self.store.delete_message(message_id, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(200, {"status": "retracted"})

    def delete_channel(self, request: Request, identifier: str) -> Response:
        self.store.delete_channel(identifier, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return Response(status=204, body=b"", content_type="application/json")

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
        """Issue a Tier 0 key (48h TTL, no signup). Local — no external service."""
        self.key_issuance_rate_limiter.check(request.remote_addr)
        body = request.json()
        agent_label = body.get("agent_label", "")
        if not isinstance(agent_label, str) or not agent_label.strip():
            raise APIError(422, "missing_field", "'agent_label' is required")
        agent_label = agent_label.strip()[:128]
        key_id, _secret, raw_key = mint_raw_key()
        record = self.store.issue_api_key(
            key_id=key_id,
            key_hash=hash_key(raw_key),
            owner_id=agent_label,
            agent_label=agent_label,
            tier=0,
            plan="free",
            ttl_seconds=48 * 3600,
        )
        self.store.record_security_event(
            event_type="key.issue.tier0",
            subject_key_id=key_id,
            remote_addr=request.remote_addr,
            detail={"agent_label": agent_label, "tier": 0},
        )
        return self.json_response(
            201,
            {
                "key": raw_key,
                "key_id": key_id,
                "tier": 0,
                "expires_at": record["expires_at"],
                "agent_label": agent_label,
            },
        )

    def issue_key_x402(self, request: Request) -> Response:
        """x402-paid key issuance.

        Agents call this with no auth. If x402 is configured, the first call
        returns 402 with the payment requirement. The agent settles in USDC,
        retries with the X-PAYMENT header, and on successful verification
        we mint a Tier-1 key with a credit balance — no signup, no card,
        no human in the loop.
        """
        if not self.x402.is_active():
            raise APIError(
                503,
                "x402_unavailable",
                "x402 payments are not configured on this instance. Use POST /v1/keys for a free 48h key.",
            )
        payment_header = request.headers.get("X-Payment") or request.headers.get("X-PAYMENT")
        resource = f"{self.base_url or ''}/v1/keys/x402".lstrip("/")
        decision = self.x402.evaluate(resource=resource, payment_header=payment_header)
        if decision.status == 402:
            body = self.x402.build_402_body(decision.requirement or PaymentRequirement())
            if decision.error:
                body["error_detail"] = decision.error
            resp = Response(status=402, body=json.dumps(body).encode("utf-8"))
            return resp
        # Verified — mint a paid key + credit the payment.
        key_id, _secret, raw_key = mint_raw_key()
        label = f"x402-{decision.settlement_id or uuid.uuid4().hex[:12]}"
        record = self.store.issue_api_key(
            key_id=key_id,
            key_hash=hash_key(raw_key),
            owner_id=label,
            agent_label=label,
            tier=1,
            plan="x402",
        )
        # Convert the priced amount to USDC micros (e.g. "0.01" → 10_000).
        try:
            amount_micros = int(round(float(decision.requirement.max_amount_required) * 1_000_000))  # type: ignore[union-attr]
        except (TypeError, ValueError):
            amount_micros = 0
        if amount_micros > 0:
            self.store.add_credit_micros(key_id, amount_micros)
        self.store.record_security_event(
            event_type="key.issue.x402",
            subject_key_id=key_id,
            remote_addr=request.remote_addr,
            detail={
                "settlement_id": decision.settlement_id,
                "amount_micros": amount_micros,
                "network": decision.requirement.network if decision.requirement else None,
            },
        )
        return self.json_response(
            201,
            {
                "key": raw_key,
                "key_id": key_id,
                "tier": 1,
                "plan": "x402",
                "settlement_id": decision.settlement_id,
                "expires_at": record["expires_at"],
                "credit_micros_applied": amount_micros,
            },
        )

    def promote_key(self, request: Request) -> Response:
        """Promote a Tier 0 key to a permanent Tier 1 key. Issues a NEW key
        and revokes the old one. The new key is returned exactly once."""
        body = request.json()
        email = body.get("email", "")
        if not isinstance(email, str) or not email.strip():
            raise APIError(422, "missing_field", "'email' is required")
        email = email.strip()[:320]
        new_key_id, _secret, new_raw_key = mint_raw_key()
        result = self.store.promote_api_key(
            current_key_id=request.auth.key_id,
            new_key_id=new_key_id,
            new_key_hash=hash_key(new_raw_key),
            email=email,
        )
        if hasattr(self.authenticator, "invalidate_cache"):
            self.authenticator.invalidate_cache(request.auth.raw_key)
        self.store.record_security_event(
            event_type="key.promote",
            actor_key_id=request.auth.key_id,
            subject_key_id=new_key_id,
            remote_addr=request.remote_addr,
            detail={"email": email, "from_tier": 0, "to_tier": 1},
        )
        return self.json_response(200, {"key": new_raw_key, "key_id": new_key_id, "tier": 1, "expires_at": None})

    # --- agent-verb route aliases (B1) -------------------------------

    def task_post(self, request: Request) -> Response:
        """Hand a task to one consumer on a claimable channel. Creates the
        channel on the fly if missing. The simpler verb-style equivalent
        of POST /v1/channels/{id}/messages on a claimable channel."""
        body = request.json()
        channel = body.get("channel")
        if not channel:
            raise APIError(422, "missing_field", "'channel' is required")
        # Create the work channel (idempotent — caller may have created already).
        try:
            ch = self.store.create_channel(
                {"name": channel, "mode": "claimable"},
                owner_id=request.auth.owner_id,
                key_id=request.auth.key_id,
                team_id=request.auth.team_id,
            )
            channel_id = ch["id"]
        except APIError as exc:
            if exc.status != 409:
                raise
            channel_id = channel
        envelope = self.store.create_message(
            channel_id,
            {
                "content": body.get("content", ""),
                "actor_label": body.get("actor_label"),
                "metadata": body.get("metadata", {}),
            },
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        return self.json_response(
            201,
            {
                "message": envelope.message,
                "channel": channel_id,
                "next_cursor": envelope.cursor,
            },
        )

    def task_claim_verb(self, request: Request) -> Response:
        """Drain a channel and atomically claim the next unclaimed message.
        Convenience verb for: list_messages → pick first → claim → return.
        Returns {claimed: null} if nothing available, so callers don't have
        to branch on 409."""
        body = request.json()
        channel = body.get("channel")
        actor = body.get("actor")
        if not channel:
            raise APIError(422, "missing_field", "'channel' is required")
        if not actor:
            raise APIError(422, "missing_field", "'actor' is required (use POST /v1/actors first if needed)")
        page = self.store.list_messages(
            channel, None, 20,
            key_id=request.auth.key_id, team_id=request.auth.team_id,
            status="unclaimed",
        )
        for msg in page.get("data", []):
            try:
                claim_result = self.store.claim_message(
                    msg["id"], {"actor": actor, "metadata": body.get("metadata", {})},
                    key_id=request.auth.key_id, team_id=request.auth.team_id,
                )
                if claim_result["status"] == "claimed":
                    return self.json_response(200, {"claimed": claim_result["message"]})
            except APIError as exc:
                if exc.status == 409:
                    continue
                raise
        return self.json_response(200, {"claimed": None, "note": "no unclaimed messages available"})

    def task_subscribe(self, request: Request) -> Response:
        """Read recent messages from a channel since a cursor. Verb-style
        alias for GET /v1/channels/{id}/messages."""
        body = request.json()
        channel = body.get("channel")
        if not channel:
            raise APIError(422, "missing_field", "'channel' is required")
        since = body.get("since")
        limit = int(body.get("limit", 50))
        page = self.store.list_messages(
            channel, since, limit,
            key_id=request.auth.key_id, team_id=request.auth.team_id,
        )
        return self.json_response(200, page)

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
        return self.json_response(201, {"message": envelope.message, "next_cursor": envelope.cursor})

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

    # --- result-channel primitive (B2) -------------------------------

    def _result_channel_name_for(self, message_id: str) -> str:
        return f"result-of-{message_id}"

    def task_post_with_result(self, request: Request) -> Response:
        """Post a task on a claimable channel AND create a paired result
        broadcast channel. The producer can later GET /v1/tasks/{id}/result
        to await; the consumer POSTs to /v1/tasks/{id}/result to publish.

        This formalizes the convention the demos/* directory uses.
        """
        body = request.json()
        channel = body.get("channel")
        content = body.get("content", "")
        if not channel:
            raise APIError(422, "missing_field", "'channel' is required")
        # Always create the work channel as a fresh claimable channel. The
        # 'channel' arg is a logical name; the actual id is what we use for
        # all subsequent ops.
        work_channel = self.store.create_channel(
            {"name": channel, "mode": "claimable"},
            owner_id=request.auth.owner_id,
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        work_channel_id = work_channel["id"]
        envelope = self.store.create_message(
            work_channel_id,
            {
                "content": content,
                "actor_label": body.get("actor_label"),
                "metadata": body.get("metadata", {}),
            },
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        message = envelope.message
        message_id = message["id"]
        result_channel_name = self._result_channel_name_for(message_id)
        result_channel = self.store.create_channel(
            {"name": result_channel_name, "mode": "broadcast"},
            owner_id=request.auth.owner_id,
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        # Store the result channel id in the message metadata? Better: keep
        # a deterministic alias so the await/publish endpoints can resolve.
        self.store.create_channel_alias(
            result_channel["id"],
            {"alias": result_channel_name},
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        return self.json_response(
            201,
            {
                "message": message,
                "channel": work_channel_id,
                "result_channel": result_channel_name,
                "result_url": f"/v1/tasks/{message_id}/result",
            },
        )

    def task_publish_result(self, request: Request, message_id: str) -> Response:
        """The consumer (claimer) posts the result of a task on the paired
        broadcast channel. The producer's await_result will receive it."""
        body = request.json()
        result_channel = self._result_channel_name_for(message_id)
        envelope = self.store.create_message(
            result_channel,
            {
                "content": body.get("content", ""),
                "actor_label": body.get("actor_label"),
                "metadata": {**body.get("metadata", {}), "task_id": message_id},
            },
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        message = envelope.message
        return self.json_response(201, {"result_channel": result_channel, "message": message})

    def task_await_result(self, request: Request, message_id: str) -> Response:
        """Read the first result posted on the paired result channel.
        Non-blocking: returns 404 if no result is published yet. Callers
        poll with backoff (or use the MCP await_result tool which polls
        for them)."""
        result_channel = self._result_channel_name_for(message_id)
        try:
            page = self.store.list_messages(
                result_channel,
                None,
                1,
                key_id=request.auth.key_id,
                team_id=request.auth.team_id,
            )
        except APIError as exc:
            if exc.status == 404:
                raise APIError(
                    404,
                    "result_not_ready",
                    "No result has been published for this task yet. Poll again or use POST /v1/tasks/<id>/result from the consumer side.",
                )
            raise
        data = page.get("data", [])
        if not data:
            raise APIError(
                404,
                "result_not_ready",
                "No result has been published for this task yet.",
            )
        return self.json_response(200, {"task_id": message_id, "result": data[0]})

    def pricing_estimate(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        tiers = [
            {
                "tier": 0,
                "name": "Test",
                "description": "Instant key, no sign-up required. 48-hour TTL. One active key per agent_label.",
                "price_eur_monthly": None,
                "current_price_eur_monthly": 0,
                "launch_discount": True,
                "launch_discount_label": "free for limited time",
                "obtain_url": f"{base}/v1/keys",
                "obtain_method": "POST /v1/keys with {\"agent_label\": \"<name>\"}",
            },
            {
                "tier": 1,
                "name": "Free",
                "description": "Permanent key. Standard rate limits. Managed via API Depot.",
                "price_eur_monthly": 9.99,
                "current_price_eur_monthly": 0,
                "launch_discount": True,
                "launch_discount_label": "free for limited time",
                "obtain_url": self.invitation_onboarding_url or f"{base}/v1/keys/promote",
                "obtain_method": "Sign up at API Depot or promote a Tier 0 key via POST /v1/keys/promote",
            },
            {
                "tier": 2,
                "name": "Pro",
                "description": "High rate limits. Team quotas. Priority support.",
                "price_eur_monthly": 39.99,
                "current_price_eur_monthly": 0,
                "launch_discount": True,
                "launch_discount_label": "free for limited time",
                "obtain_url": self.invitation_onboarding_url,
                "obtain_method": "Sign up at API Depot and select Pro tier",
            },
        ]
        return self.json_response(200, {
            "tiers": tiers,
            "note": "Launch discount active — all paid tiers are free for a limited time. Check this endpoint for updates.",
        })

    # --- Sessions (item19) ---

    def list_sessions(self, request: Request) -> Response:
        sessions = self.store.list_sessions(key_id=request.auth.key_id)
        return self.json_response(200, {"data": sessions})

    def create_session(self, request: Request) -> Response:
        body = request.json()
        session = self.store.create_session(request.auth.key_id, body)
        return self.json_response(201, session)

    def get_session(self, request: Request, session_id: str) -> Response:
        session = self.store.get_session(session_id, key_id=request.auth.key_id)
        return self.json_response(200, session)

    def patch_session(self, request: Request, session_id: str) -> Response:
        session = self.store.patch_session(session_id, request.json(), key_id=request.auth.key_id)
        return self.json_response(200, session)

    def delete_session(self, request: Request, session_id: str) -> Response:
        self.store.delete_session(session_id, key_id=request.auth.key_id)
        return self.json_response(200, {"status": "deleted"})

    def observability_metrics(self, request: Request) -> Response:
        metrics = self.store.get_observability_metrics(request.auth.key_id)
        return self.json_response(200, metrics)

    def keys_me(self, request: Request) -> Response:
        auth = request.auth
        record = self.store.get_api_key_record(auth.key_id) or {}
        credit_micros = int(record.get("credit_balance_micros") or 0)
        return self.json_response(200, {
            "key_id": auth.key_id,
            "owner_id": auth.owner_id,
            "tier": auth.tier,
            "plan": auth.plan,
            "active": auth.active,
            "scopes": auth.scopes,
            "credit": {
                "balance_usdc_micros": credit_micros,
                "balance_usdc": f"{credit_micros / 1_000_000:.6f}",
            },
        })

    def set_key_scopes(self, request: Request) -> Response:
        auth = request.auth
        payload = request.json()
        scopes = payload.get("scopes")
        if not isinstance(scopes, list):
            raise APIError(422, "invalid_scopes", "scopes must be an array of scope strings")
        self.store.set_key_scopes(auth.key_id, scopes)
        return self.json_response(200, {"key_id": auth.key_id, "scopes": sorted(scopes)})

    def account_usage(self, request: Request) -> Response:
        auth = request.auth
        rate_limits_by_tier = {0: 300, 1: 300, 2: 1000}
        limit = rate_limits_by_tier.get(auth.tier or 0, 300)
        return self.json_response(200, {
            "tier": auth.tier,
            "plan": auth.plan,
            "rate_limit": {
                "requests_per_window": limit,
                "window_seconds": 60,
            },
            "note": "Per-request usage counters are not tracked in v1. Use X-RateLimit-Limit and X-RateLimit-Window headers to gauge headroom.",
        })

    def _require_scope(self, auth: Any, required_scope: str) -> None:
        """Raise 403 if the key has explicit scopes and the required scope is absent."""
        if auth.scopes is None:
            return  # unrestricted key
        if required_scope not in auth.scopes:
            raise APIError(
                403,
                "insufficient_scope",
                f"This operation requires scope '{required_scope}'",
                {"required_scope": required_scope, "granted_scopes": auth.scopes},
            )

    def status(self, request: Request) -> Response:
        base = self.base_url or "https://backchannel.oakstack.eu"
        return self.json_response(200, {
            "status": "operational",
            "updated_at": self.store.now().isoformat(),
            "tier_sla": {
                "tier_0": "best-effort",
                "tier_1": "99% monthly uptime",
                "tier_2": "99.9% monthly uptime",
            },
            "sla_url": f"{base}/docs/sla.md",
            "health_url": f"{base}/health",
        })

    def status_page(self, request: Request) -> Response:
        """Human-readable status page. Probes the DB + last-cleanup-run as a
        liveness signal. No external monitoring dependency."""
        import time as _time
        base = self.base_url or "https://backchannel.oakstack.eu"
        # Liveness probe — same query /health uses.
        db_ok = True
        db_latency_ms: float | None = None
        try:
            t0 = _time.monotonic()
            with self.store.connect() as conn:
                conn.execute("SELECT 1")
            db_latency_ms = round((_time.monotonic() - t0) * 1000.0, 2)
        except Exception:
            db_ok = False
        last_cleanup = None
        try:
            runs = self.store.list_audit_runs(limit=1)
            if runs:
                last_cleanup = runs[0].get("started_at")
        except Exception:
            pass
        overall = "Operational" if db_ok else "Degraded"
        color = "#58ff7d" if db_ok else "#ffb347"
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Backchannel — status</title>
  <style>
    body {{ font-family: 'IBM Plex Mono', monospace; background: #020402; color: #d6ffd8;
            margin: 0; padding: 40px 20px; line-height: 1.5; }}
    .wrap {{ max-width: 720px; margin: 0 auto; }}
    .pill {{ display: inline-block; padding: 8px 16px; border-radius: 999px;
              background: rgba(88,255,125,0.1); border: 1px solid {color};
              color: {color}; font-weight: 700; letter-spacing: 0.04em; }}
    h1 {{ font-size: 2rem; margin: 24px 0 0; letter-spacing: -0.02em; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 32px;
              border: 1px solid rgba(84,255,138,0.28); }}
    td, th {{ padding: 12px 14px; text-align: left;
                border-bottom: 1px solid rgba(84,255,138,0.18); }}
    th {{ color: #58ff7d; background: rgba(88,255,125,0.05); }}
    .ok {{ color: #58ff7d; }} .down {{ color: #ffb347; }}
    .muted {{ color: #8bcf90; font-size: 0.85rem; }}
    a {{ color: #58ff7d; }}
  </style>
</head>
<body>
  <div class="wrap">
    <p class="muted"><a href="/">← Backchannel</a></p>
    <span class="pill">{overall}</span>
    <h1>Service status</h1>
    <p class="muted">Live probe — refresh for a fresh check.<br>
       Updated: {self.store.now().isoformat()}</p>
    <table>
      <thead><tr><th>Component</th><th>State</th><th>Detail</th></tr></thead>
      <tbody>
        <tr><td>HTTP API</td>
            <td class="ok">Operational</td>
            <td>responding to this request</td></tr>
        <tr><td>Database (SQLite)</td>
            <td class="{'ok' if db_ok else 'down'}">{'Operational' if db_ok else 'Degraded'}</td>
            <td>{f"SELECT 1 in {db_latency_ms} ms" if db_latency_ms is not None else "DB unreachable"}</td></tr>
        <tr><td>Cleanup worker</td>
            <td class="{'ok' if last_cleanup else 'muted'}">
              {'Operational' if last_cleanup else 'No runs yet'}
            </td>
            <td>{f"last run: {last_cleanup}" if last_cleanup else "the worker container fires this; safe before first cycle"}</td></tr>
      </tbody>
    </table>
    <p class="muted" style="margin-top:32px">
      Machine-readable: <a href="/status">/status</a> · Liveness: <a href="/health">/health</a> ·
      Metrics: <a href="/metrics">/metrics</a> · SLA: <a href="/docs/sla.md">/docs/sla.md</a>
    </p>
    <p class="muted">
      Report incidents to <code>security@oakstack.eu</code>.
    </p>
  </div>
</body>
</html>
"""
        return Response(status=200, body=body.encode("utf-8"), content_type="text/html; charset=utf-8")

    def channel_metrics(self, request: Request, identifier: str) -> Response:
        auth = request.auth
        metrics = self.store.get_channel_metrics(identifier, auth.key_id)
        return self.json_response(200, metrics)

    def security_audit(self, request: Request) -> Response:
        """Return the latest security events for the *requesting* key only.

        A key cannot see events for other keys it does not own. Server-wide
        audit access is a future admin-tier surface; today the endpoint is
        scoped so a tier-1 agent can self-audit its own promotion / issuance
        history.
        """
        limit = 100
        limit_q = request.query_value("limit")
        if limit_q:
            try:
                limit = max(1, min(int(limit_q), 500))
            except ValueError:
                pass
        all_events = self.store.list_security_events(limit=500)
        key_id = request.auth.key_id
        events = [
            e for e in all_events
            if e.get("actor_key_id") == key_id or e.get("subject_key_id") == key_id
        ][:limit]
        return self.json_response(200, {"data": events, "count": len(events)})

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
    authenticator: DepotAuthenticator | LocalAuthenticator | None = None,
    invitation_onboarding_url: str | None = None,
) -> BackchannelApp:
    store = BackchannelStore(db_path=db_path, now_provider=now_provider)
    return BackchannelApp(
        store,
        authenticator=authenticator,
        invitation_onboarding_url=invitation_onboarding_url,
    )
