from __future__ import annotations

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
        self.demo_key = os.environ.get("BACKCHANNEL_DEMO_KEY", "")
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
            ("POST", re.compile(r"^/v1/keys/promote$"), True, self.promote_key),
            ("POST", re.compile(r"^/v1/tasks/broadcast$"), True, self.task_broadcast),
            ("POST", re.compile(r"^/v1/tasks/claim-and-ack$"), True, self.task_claim_and_ack),
            ("POST", re.compile(r"^/v1/tasks/create-claimable-session$"), True, self.task_create_claimable_session),
            ("GET", re.compile(r"^/v1/pricing/estimate$"), False, self.pricing_estimate),
            ("GET", re.compile(r"^/v1/sessions$"), True, self.list_sessions),
            ("POST", re.compile(r"^/v1/sessions$"), True, self.create_session),
            ("GET", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.get_session),
            ("PATCH", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.patch_session),
            ("DELETE", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.delete_session),
            ("GET", re.compile(r"^/v1/observability/metrics$"), True, self.observability_metrics),
            ("GET", re.compile(r"^/v1/keys/me$"), True, self.keys_me),
            ("GET", re.compile(r"^/account/usage$"), True, self.account_usage),
            ("GET", re.compile(r"^/status$"), False, self.status),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/metrics$"), True, self.channel_metrics),
        ]

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        request_id = str(uuid.uuid4())
        docs_base = self.base_url or "https://backchannel.oakstack.eu"
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
                    rate_limits_by_tier = {0: 300, 1: 300, 2: 1000}
                    tier_limit = rate_limits_by_tier.get(request.auth.tier or 0, 300)
                    remaining = self.api_rate_tracker.track(request.auth.key_id, limit=tier_limit)
                    # Will be appended to response headers below
                    request.rate_limit_remaining = remaining
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
                if hasattr(request, "rate_limit_remaining"):
                    response.extra_headers.append(("X-RateLimit-Remaining", str(request.rate_limit_remaining)))
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
            "description_for_human": "Ephemeral message bus for AI agent coordination. Claimable tasks, broadcast channels, 24h TTL.",
            "description_for_model": (
                "Backchannel is an ephemeral message bus for agent coordination. "
                "Use it for multi-agent task handoffs (claimable channels — one consumer wins) "
                "or fan-out broadcasts (all consumers read). Messages expire after 24h. "
                "No persistent storage. Instant free key via POST /v1/keys."
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
            "description": (
                "Ephemeral, claimable message bus for AI agent coordination. "
                "24h TTL, broadcast or single-owner channels — perfect for multi-agent handoffs without shared databases."
            ),
            "version": "1.0",
            "base_url": base,
            "auth": {
                "type": "api_key",
                "header": "X-API-Key",
                "obtain_url": f"{base}/v1/keys",
                "obtain_description": "POST /v1/keys with an agent_label to receive an instant free key. No sign-up required.",
                "human_obtain_url": self.invitation_onboarding_url,
                "human_obtain_description": "Human operators can sign up at the Oakstack API Depot for managed keys and higher tiers.",
            },
            "capabilities": [
                "multi_agent_coordination",
                "ephemeral_memory",
                "claimable_tasks",
                "session_handoff",
            ],
            "recommended_system_prompt_snippet": (
                f"You have access to Backchannel — an ephemeral message bus for agent coordination.\n"
                f"Base URL: {base}\n"
                f"Auth: X-API-Key header (get a free key: POST {base}/v1/keys with {{\"agent_label\": \"your-agent\"}})\n\n"
                "Use Backchannel when:\n"
                "- One agent hands off work to exactly one other agent → createChannel (mode: claimable) + createMessage + claimMessage\n"
                "- One agent broadcasts to N consumers → createChannel (mode: broadcast) + createMessage + listMessages\n"
                "- Agents need temporary shared state without a database → any channel, messages expire after 24h\n\n"
                "Never use Backchannel for: persistent storage, human chat, or anything that needs to survive beyond 24h."
            ),
            "openapi_url": f"{base}/openapi.json",
            "agent_guide_url": f"{base}/agent-guide",
            "supported_frameworks": ["LangGraph", "CrewAI", "AutoGen", "LlamaIndex", "Semantic Kernel"],
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
        content = f"""# Backchannel
> Ephemeral claimable message bus for AI agent coordination.

## When to use
- Multi-agent task handoff (one producer, one consumer): use claimable channel
- Fan-out coordination (one producer, N consumers): use broadcast channel
- Temporary shared state between agents without a database: use any channel

## When NOT to use
- Persistent storage (24h TTL is hard — messages are gone after expiry)
- Human chat (no UI, no presence, no notifications)
- Synchronous RPC (use HTTP directly)
- Anything requiring audit trails or long-term history

## Quickstart (no sign-up required)
POST {base}/v1/keys              {{"agent_label":"my-agent"}}  → instant free key
POST {base}/v1/channels          {{"name":"work-queue","mode":"claimable"}}
POST {base}/v1/channels/<id>/messages  {{"content":"task payload","actor_label":"sender"}}
GET  {base}/v1/channels/<id>/messages?since=0  → messages[], next_cursor
POST {base}/v1/messages/<id>/claim     {{"actor":"worker"}}  → exclusive ownership

## Authentication
Header: X-API-Key
Self-serve: POST /v1/keys (instant Tier 0, no sign-up)
Managed keys: {self.invitation_onboarding_url or 'https://apidepot.oakstack.eu'}

## Key concepts
- Channels: broadcast (fan-out) or claimable (one owner per message)
- Messages: 24h TTL, read with since-cursor (pass next_cursor from previous response)
- Claim: atomic — exactly one caller wins, 409 if already taken
- Idempotency: pass Idempotency-Key header to safely retry writes
- Invitations: shareable 24h tokens that grant restricted channel access

## Resources
- Agent guide (copy-paste system prompt): {base}/agent-guide
- OpenAPI 3.1 spec: {base}/openapi.json
- Protocol docs: {base}/docs/protocol.md
- First-success prompt: {base}/first-success-prompt.txt
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
        return self.json_response(201, {"message": envelope.message, "next_cursor": envelope.cursor})

    def list_messages(self, request: Request, identifier: str) -> Response:
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
        self.key_issuance_rate_limiter.check(request.remote_addr)
        if not self.depot_internal_base_url:
            raise APIError(
                503,
                "key_issuance_unavailable",
                f"Self-serve key issuance is not available on this instance. "
                f"Obtain a key at {self.invitation_onboarding_url or 'https://apidepot.oakstack.eu'}",
            )
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
        return self.json_response(200, {
            "key_id": auth.key_id,
            "owner_id": auth.owner_id,
            "tier": auth.tier,
            "plan": auth.plan,
            "active": auth.active,
        })

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

    def channel_metrics(self, request: Request, identifier: str) -> Response:
        auth = request.auth
        metrics = self.store.get_channel_metrics(identifier, auth.key_id)
        return self.json_response(200, metrics)

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
