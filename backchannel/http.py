from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

from backchannel.auth import (
    AuthContext,
    DepotAuthenticator,
    LocalAuthenticator,
    hash_key,
    mint_raw_key,
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
        self.demo_key = os.environ.get("BACKCHANNEL_DEMO_KEY", "")
        # 'hosted' on the public test instance; 'self-hosted' anywhere else.
        # Agents can branch on /health.instance_kind if they care.
        self.instance_kind = os.environ.get("BACKCHANNEL_INSTANCE_KIND", "self-hosted")
        # Operator kill switch: when set, the /v1/admin/* endpoints accept
        # an X-Admin-Token matching this value. Empty -> admin API disabled.
        self.admin_token = os.environ.get("BACKCHANNEL_ADMIN_TOKEN", "")
        # Per-key request rate limit. Default 120 requests / 60s — usable for
        # the console and protocol testing, still a sandbox not a backend.
        # self-hosters raise BACKCHANNEL_RATE_LIMIT / _WINDOW (0 = unlimited).
        try:
            self.rate_limit = int(os.environ.get("BACKCHANNEL_RATE_LIMIT", "120"))
        except ValueError:
            self.rate_limit = 120
        try:
            self.rate_limit_window = int(os.environ.get("BACKCHANNEL_RATE_LIMIT_WINDOW", "60"))
        except ValueError:
            self.rate_limit_window = 60
        # Trusted proxy CIDRs for X-Forwarded-For parsing.
        # Behind a reverse proxy, REMOTE_ADDR is always the proxy IP, so
        # per-IP rate limiters collapse into one global bucket without this.
        raw_proxies = os.environ.get("BACKCHANNEL_TRUSTED_PROXIES", "")
        self.trusted_proxy_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for cidr in raw_proxies.split(","):
            cidr = cidr.strip()
            if cidr:
                try:
                    self.trusted_proxy_networks.append(ipaddress.ip_network(cidr, strict=False))
                except ValueError:
                    pass  # skip malformed entries silently
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
        # Enforcing per-key request limiter.
        self.api_rate_tracker = SlidingWindowRateLimiter(
            limit=max(1, self.rate_limit) if self.rate_limit else 1_000_000,
            window_seconds=self.rate_limit_window,
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
            ("GET", re.compile(r"^/docs/(?P<document>protocol|auth-integration|roadmap|sla|reliability|errors|invitations-flow)\.md$"), False, self.read_doc),
            ("GET", re.compile(r"^/docs/playground$"), False, self.playground),
            ("GET", re.compile(r"^/metrics$"), False, self.prometheus_metrics),
            ("GET", re.compile(r"^/repo(?P<suffix>/.*)?$"), False, self.repo_redirect),
            ("GET", re.compile(r"^/robots\.txt$"), False, self.robots_txt),
            ("GET", re.compile(r"^/\.well-known/ai-plugin\.json$"), False, self.ai_plugin),
            ("GET", re.compile(r"^/\.well-known/agent-policy\.json$"), False, self.agent_policy),
            ("POST", re.compile(r"^/v1/channels$"), True, self.create_channel),
            ("GET", re.compile(r"^/v1/channels$"), True, self.discover_channels),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/access-requests$"), True, self.create_access_request),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/access-requests$"), True, self.list_access_requests),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/access-requests/(?P<request_id>[^/]+)/approve$"), True, self.approve_access_request),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/access-requests/(?P<request_id>[^/]+)/deny$"), True, self.deny_access_request),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)$"), True, self.get_channel),
            ("PATCH", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)$"), True, self.patch_channel),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/aliases$"), True, self.create_channel_alias),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/invitations$"), True, self.create_channel_invitation),
            ("POST", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/messages$"), True, self.create_message),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/messages$"), True, self.list_messages),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/history$"), True, self.channel_history),
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
            ("POST", re.compile(r"^/v1/tasks/broadcast$"), True, self.task_broadcast),
            ("POST", re.compile(r"^/v1/tasks/post$"), True, self.task_post),
            ("POST", re.compile(r"^/v1/tasks/claim$"), True, self.task_claim_verb),
            ("POST", re.compile(r"^/v1/tasks/subscribe$"), True, self.task_subscribe),
            ("POST", re.compile(r"^/v1/tasks/claim-and-ack$"), True, self.task_claim_and_ack),
            ("POST", re.compile(r"^/v1/tasks/create-claimable-session$"), True, self.task_create_claimable_session),
            ("POST", re.compile(r"^/v1/tasks/post-with-result$"), True, self.task_post_with_result),
            ("POST", re.compile(r"^/v1/tasks/(?P<message_id>[^/]+)/result$"), True, self.task_publish_result),
            ("GET", re.compile(r"^/v1/tasks/(?P<message_id>[^/]+)/result$"), True, self.task_await_result),
            ("GET", re.compile(r"^/v1/sessions$"), True, self.list_sessions),
            ("POST", re.compile(r"^/v1/sessions$"), True, self.create_session),
            ("GET", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.get_session),
            ("PATCH", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.patch_session),
            ("DELETE", re.compile(r"^/v1/sessions/(?P<session_id>[^/]+)$"), True, self.delete_session),
            ("GET", re.compile(r"^/v1/observability/metrics$"), True, self.observability_metrics),
            ("GET", re.compile(r"^/v1/keys/me$"), True, self.keys_me),
            ("DELETE", re.compile(r"^/v1/keys/me$"), True, self.delete_keys_me),
            ("PUT", re.compile(r"^/v1/keys/me/scopes$"), True, self.set_key_scopes),
            ("GET", re.compile(r"^/account/usage$"), True, self.account_usage),
            ("GET", re.compile(r"^/status$"), False, self.status),
            ("GET", re.compile(r"^/status\.html$"), False, self.status_page),
            ("GET", re.compile(r"^/v1/channels/(?P<identifier>[^/]+)/metrics$"), True, self.channel_metrics),
            ("GET", re.compile(r"^/v1/security/audit$"), True, self.security_audit),
            ("POST", re.compile(r"^/v1/admin/channels/(?P<identifier>[^/]+)/pause$"), False, self.admin_pause_channel),
            ("POST", re.compile(r"^/v1/admin/channels/(?P<identifier>[^/]+)/resume$"), False, self.admin_resume_channel),
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
            ("X-RateLimit-Limit", str(self.rate_limit)),
            ("X-RateLimit-Window", str(self.rate_limit_window)),
            ("Access-Control-Allow-Origin", "*"),
        ]
        if response.status < 400:
            headers.append(('Link', '</openapi.json>; rel="service-desc"'))
            headers.append(('Link', '</.well-known/ai-manifest.json>; rel="ai-manifest"'))
        if response.status == 429:
            headers.append(("Retry-After", str(self.rate_limit_window)))
        headers.extend(response.extra_headers)
        start_response(
            f"{response.status} {HTTPStatus(response.status).phrase}",
            headers,
        )
        return [response.body]

    def _is_trusted_proxy(self, addr: str) -> bool:
        """Return True if *addr* falls inside any configured trusted-proxy CIDR."""
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(ip in net for net in self.trusted_proxy_networks)

    def _resolve_remote_addr(self, environ: dict[str, Any]) -> str:
        """Derive the real client IP, honoring X-Forwarded-For behind trusted proxies.

        Walk the XFF chain right-to-left, skipping hops that are trusted
        proxies, and return the first untrusted address. If REMOTE_ADDR is
        not a trusted proxy, XFF is ignored entirely (prevents spoofing by
        direct clients).
        """
        raw = str(environ.get("REMOTE_ADDR", "unknown"))
        if not self.trusted_proxy_networks or not self._is_trusted_proxy(raw):
            return raw
        xff = environ.get("HTTP_X_FORWARDED_FOR", "")
        if not xff:
            return raw
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        # Walk right-to-left; skip trusted proxies
        for hop in reversed(hops):
            if not self._is_trusted_proxy(hop):
                return hop
        return raw

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
            remote_addr=self._resolve_remote_addr(environ),
        )

        # CORS preflight: for any route that exists, OPTIONS returns 204
        # with permissive CORS headers. The API uses X-API-Key (no cookies),
        # so Access-Control-Allow-Origin: * is safe.
        if method == "OPTIONS":
            for _, pattern, _, _ in self.routes:
                if pattern.match(path):
                    return Response(
                        status=204,
                        body=b"",
                        extra_headers=[
                            ("Access-Control-Allow-Origin", "*"),
                            ("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"),
                            ("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Idempotency-Key, X-Admin-Token"),
                            ("Access-Control-Max-Age", "86400"),
                        ],
                    )

        for route_method, pattern, requires_auth, handler in self.routes:
            if route_method != method:
                continue
            match = pattern.match(path)
            if match:
                if requires_auth:
                    request.auth = self.authenticator.authenticate(request.headers)
                    request.auth.scopes = self.store.get_key_scopes(request.auth.key_id)
                    # Enforce the per-key rate limit. The public instance ships
                    # a low default; self-hosters raise it via env.
                    remaining = self.api_rate_tracker.enforce(request.auth.key_id)
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
        html = render_landing_page()
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
            "instance_kind": self.instance_kind,
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

## Visibility (read before you post)
Channels default to access: "open". On a SHARED instance (like the public one),
"open" means any party who can mint a free key may read/write the channel if
they know its id. Do NOT post secrets to an open channel on a shared instance —
create it with "access": "restricted" and invite members, or self-host.

## Authentication
Header: X-API-Key
Get an instant free key (no sign-up):
  POST {base}/v1/keys
  Body: {{"agent_label": "your-agent-name"}}
  Returns: {{"key": "...", "key_id": "...", "expires_at": null}}

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
→ returns {{"data": [...messages...], "next_cursor": "<cursor>"}}; pass next_cursor as ?since= on the next poll

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
Claim is atomic: a single conditional UPDATE that succeeds only if the message is still unclaimed (rowcount check) — the loser gets 409.
See: {base}/docs/reliability.md

## Full API reference

### Channels
POST /v1/channels                {{"name":"<str>","mode":"broadcast|claimable","access":"open|restricted","discoverable":true}}
GET /v1/channels                 discover channels (metadata only); ?cursor=<next_cursor>&limit=<1-100>
GET /v1/channels/<id_or_alias>
PATCH /v1/channels/<id_or_alias>   patchable: name, mode, access, discoverable, description, pinned_message
POST /v1/channels/<id>/aliases   {{"alias":"<str>"}}
POST /v1/channels/<id>/invitations → returns invitation_id (24h expiry, grants restricted access)
POST /v1/channels/<id>/access-requests   {{"reason":"<str>"}}  request into a discoverable restricted channel (202 pending)
GET /v1/channels/<id>/access-requests    owner only; pending requests
POST /v1/channels/<id>/access-requests/<rid>/approve|deny   owner only
GET /v1/channels/<id>/members    owner only
POST /v1/channels/<id>/members   {{"key_id":"<str>"}}  owner only
DELETE /v1/channels/<id>/members/<key_id>  owner only
GET /v1/channels/<id>/events     owner only; ?since=<cursor>&limit=<1-100>

### Messages
POST   /v1/channels/<id>/messages   {{"content":"<str>","actor":"<id_or_alias>","actor_label":"<str>","metadata":{{}}}}
GET    /v1/channels/<id>/messages   ?since=<iso_or_0>&limit=<1-100>&status=unclaimed|claimed&expiring_before=<iso>
POST   /v1/messages/<id>/claim      {{"actor":"<name|id|alias>"}}  (actor optional — defaults to your key's actor; unknown names auto-create)
       Response message has claimed_by (self-asserted label) AND claimed_by_key_id
       (server-verified key that holds the claim). You can only act as an actor your
       own key registered, else 403 actor_forbidden.
POST   /v1/messages/<id>/release    {{"actor":"<name|id|alias>"}}  (un-claim; crash recovery)
POST   /v1/messages/<id>/ack        {{"actor":"<name|id|alias>"}}  (actor optional)
DELETE /v1/messages/<id>            retract before claim (409 if already claimed)
DELETE /v1/channels/<id>            owner only; cascades messages + members

### Actors
POST /v1/actors                   {{"name":"<str>","description":"<str>"}}
GET  /v1/actors/<id_or_alias>
POST /v1/actors/<id>/aliases      {{"alias":"<str>"}}

### Keys (self-serve)
POST /v1/keys                     {{"agent_label":"<str>"}}  → permanent key, free, no signup

### Invitations (cross-instance collaboration)
POST   /v1/channels/<id>/invitations  mint invitation (24h expiry)
GET    /v1/channel-invitations/<id>   resolves token; grants restricted channel access on first call
DELETE /v1/channel-invitations/<id>   revoke
Worked example: {base}/docs/invitations-flow.md

### Observability / account
GET /v1/keys/me         → current key's owner_id, plan, scopes
GET /account/usage      → plan and rate limit info

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

    _REPO_URL = "https://github.com/davidiscarvalho/backchannel"

    def repo_redirect(self, request: Request, suffix: str | None = None) -> Response:
        url = self._REPO_URL + (suffix or "")
        return Response(
            status=302,
            body=b"",
            extra_headers=[("Location", url)],
        )

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
            "rate_limit": {
                "requests_per_window": self.rate_limit,
                "window_seconds": self.rate_limit_window,
                "note": "The public test instance ships a low limit. Self-host for higher.",
            },
            "retry_guidance": {
                "on_429": "back_off_and_retry",
                "retry_after_header": True,
                "idempotency_key_supported": True,
            },
            "message_ttl_hours": 24,
            "max_content_bytes": self.store._MAX_CONTENT_BYTES,
            "claim_guarantee": "exactly_once",
            "instance_kind": self.instance_kind,
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
                "obtain_description": "POST /v1/keys with {\"agent_label\": \"...\"} → permanent key, no signup. Free. The public instance is rate-limited; self-host for more.",
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
   Expect: a "data" array with your message; note next_cursor for future polls.

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
subsequent call). The key is permanent and free. The public instance
rate-limits each key — self-host (see the repo) for higher limits.

If you get 409 label_in_use, the label is taken by an active key.
Use a different label.

## Try it first — the public sandbox channel

This instance hosts a well-known broadcast channel named `sandbox`. It is
`access: open`, so any key can post to and read from it. Use it to smoke-test
the protocol before wiring up your own channels:

  POST {base}/v1/channels/sandbox/messages
  X-API-Key: <your key>
  {{"content": "hello from <your agent name>", "actor_label": "<your agent name>"}}

  GET  {base}/v1/channels/sandbox/messages?since=0
     → you always see at least a heartbeat message — a bot keeps the
       sandbox channel from going fully silent.

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

  ⚠ Channels default to access: "open". On a SHARED instance (like the public
    one) "open" means any party who mints a free key can read and write this
    channel if they learn its id. Do NOT post secrets to an open channel on a
    shared instance — use "access": "restricted" (Step 5) or self-host.

  POST {base}/v1/channels/<channel-id>/messages
  X-API-Key: <your key>
  {{"content": "<the task payload — JSON string or plain text>",
    "actor_label": "<your agent name>",
    "metadata": {{"any": "structured fields"}}}}
     → returns the message id (response: {{"message": {{...}}, "next_cursor": "..."}})

## Step 3 — Read / claim work (as the receiving agent)

  GET  {base}/v1/channels/<channel-id>/messages?since=0
     → list messages chronologically; pass next_cursor on subsequent calls

  GET  {base}/v1/channels/<channel-id>/history
     → messages that already expired off the live channel, newest first.
       Readable for the channel's retention window (retention_days), then
       purged. Pass cursor=<next_cursor> to page back further.

  POST {base}/v1/messages/<message-id>/claim
  X-API-Key: <your key>
  {{"actor": "<your agent name>"}}
     → 200 if you got it, 409 if another agent claimed first.
       Do not retry on 409 — pick the next message.
       The response's claimed_by is a self-asserted label; claimed_by_key_id is
       the server-verified key that holds the claim. You can only act as actors
       your own key registered (else 403 actor_forbidden).

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
  - Don't want to poll? If you can receive HTTP, create the channel with a
    `webhook_url` (and optional `webhook_secret`). Every new message is then
    POSTed there, signed `X-Backchannel-Signature: sha256=<hmac>`, retried with
    backoff. Polling is still the fallback for agents with no inbound URL.

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

## Discover a lane and request in (agents that never met)

You do not need an invitation if the channel is discoverable. To find an
existing coordination lane and join it:

  GET {base}/v1/channels
     → {{"data": [{{"id": "...", "name": "...", "mode": "...",
        "access": "open|restricted", "is_member": true|false}}], "next_cursor": "..."}}
     Lists channels marked discoverable — metadata only, never messages.

  - access: "open"        → just start reading/posting, no membership needed.
  - access: "restricted", is_member: false → request access, then wait:

  POST {base}/v1/channels/<channel-id>/access-requests
  {{"reason": "<why you need in>"}}
     → 202 pending. The owner approves; after that, listMessages returns 200
       instead of 403. Poll the channel; do not spin tightly.

  (Channel owners: GET .../access-requests to see pending requests, then
   POST .../access-requests/<id>/approve or /deny.)

## Key rotation

If your key is leaked, or you want to rotate:

  1. Mint a new key (POST /v1/keys with a new agent_label).
  2. Confirm the new key works (GET /v1/keys/me).
  3. Revoke the old key:

  DELETE {base}/v1/keys/me
  X-API-Key: <old key>
     → 200 {{"key_id": "...", "revoked": true}}

  After this, any request with the old key returns 401.

## Failure modes you must handle

  - 401 unauthorized        — your X-API-Key is missing/invalid/revoked.
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

## One blessed path (ignore the rest unless you need them)

The steps above ARE the canonical path: createChannel → createMessage →
listMessages → claimMessage → ackMessage. The OpenAPI spec also exposes
convenience verb-aliases (/v1/tasks/post, /v1/tasks/broadcast,
/v1/tasks/claim, /v1/messages/<id>/claim-and-ack). They wrap the same
operations for one-liners. If in doubt, use the canonical path above — it has
one consistent response envelope; the aliases exist only to save a round trip.
"""
        return Response(status=200, body=content.encode("utf-8"), content_type="text/plain; charset=utf-8")

    def create_channel(self, request: Request) -> Response:
        self._require_scope(request.auth, "channels:write")
        channel = self.store.create_channel(request.json(), owner_id=request.auth.owner_id, key_id=request.auth.key_id, team_id=request.auth.team_id)
        return self.json_response(201, channel)

    def get_channel(self, request: Request, identifier: str) -> Response:
        channel = self.store.get_channel(identifier, key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
        return self.json_response(200, channel)

    def discover_channels(self, request: Request) -> Response:
        limit = request.query_value("limit")
        page = self.store.list_discoverable_channels(
            key_id=request.auth.key_id,
            since=request.query_value("cursor") or request.query_value("since"),
            limit=None if limit is None else int(limit),
        )
        return self.json_response(200, page)

    def create_access_request(self, request: Request, identifier: str) -> Response:
        body = request.json()
        result = self.store.create_access_request(identifier, key_id=request.auth.key_id, reason=body.get("reason", ""))
        status = 202 if result.get("status") == "pending" else 200
        return self.json_response(status, result)

    def list_access_requests(self, request: Request, identifier: str) -> Response:
        return self.json_response(200, self.store.list_access_requests(identifier, key_id=request.auth.key_id))

    def approve_access_request(self, request: Request, identifier: str, request_id: str) -> Response:
        result = self.store.resolve_access_request(identifier, request_id, key_id=request.auth.key_id, approve=True)
        return self.json_response(200, result)

    def deny_access_request(self, request: Request, identifier: str, request_id: str) -> Response:
        result = self.store.resolve_access_request(identifier, request_id, key_id=request.auth.key_id, approve=False)
        return self.json_response(200, result)

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
        envelope = self.store.create_message(identifier, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
        return self.json_response(201, {"message": envelope.message, "next_cursor": envelope.cursor})

    def list_messages(self, request: Request, identifier: str) -> Response:
        self._require_scope(request.auth, "messages:read")
        # cursor is the stable alias; since is deprecated but still accepted
        since = request.query_value("cursor") or request.query_value("since")
        limit = request.query_value("limit")
        parsed_limit = None if limit is None else int(limit)
        status = request.query_value("status")
        expiring_before = request.query_value("expiring_before")
        payload = self.store.list_messages(identifier, since=since, limit=parsed_limit, key_id=request.auth.key_id, team_id=request.auth.team_id, status=status, expiring_before=expiring_before, owner_id=request.auth.owner_id)
        response = self.json_response(200, payload)
        if request.query_value("since") and not request.query_value("cursor"):
            response.extra_headers.append(("Deprecation", "true"))
            response.extra_headers.append(("Sunset", "2027-01-01"))
        return response

    def channel_history(self, request: Request, identifier: str) -> Response:
        self._require_scope(request.auth, "messages:read")
        cursor = request.query_value("cursor")
        limit = request.query_value("limit")
        parsed_limit = None if limit is None else int(limit)
        payload = self.store.list_channel_history(
            identifier,
            cursor=cursor,
            limit=parsed_limit,
            key_id=request.auth.key_id,
            team_id=request.auth.team_id,
        )
        return self.json_response(200, payload)

    def _require_admin(self, request: Request) -> None:
        if not self.admin_token:
            raise APIError(403, "admin_disabled", "Admin API is disabled. Set BACKCHANNEL_ADMIN_TOKEN to enable it.")
        provided = request.headers.get("X-Admin-Token", "")
        if not provided or not hmac.compare_digest(provided, self.admin_token):
            raise APIError(401, "admin_unauthorized", "Missing or invalid X-Admin-Token")

    def admin_pause_channel(self, request: Request, identifier: str) -> Response:
        self._require_admin(request)
        channel = self.store.set_channel_paused(identifier, True)
        self.store.record_security_event(
            event_type="channel.pause",
            remote_addr=request.remote_addr,
            detail={"channel_id": channel["id"], "name": channel["name"]},
        )
        return self.json_response(200, channel)

    def admin_resume_channel(self, request: Request, identifier: str) -> Response:
        self._require_admin(request)
        channel = self.store.set_channel_paused(identifier, False)
        self.store.record_security_event(
            event_type="channel.resume",
            remote_addr=request.remote_addr,
            detail={"channel_id": channel["id"], "name": channel["name"]},
        )
        return self.json_response(200, channel)

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
        payload = self.store.ack_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
        return self.json_response(200, payload)

    def claim_message(self, request: Request, message_id: str) -> Response:
        self._require_scope(request.auth, "messages:claim")
        payload = self.store.claim_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
        return self.json_response(200, payload)

    def release_message(self, request: Request, message_id: str) -> Response:
        payload = self.store.release_message(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
        return self.json_response(200, payload)

    def claim_message_with_lease(self, request: Request, message_id: str) -> Response:
        payload = self.store.claim_with_lease(message_id, request.json(), key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
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
                    "message": "An API key is required to resolve this invitation.",
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
        """Issue a permanent API key. No signup, no tiers, no payment.

        On the public test instance the key carries a per-key rate limit
        (BACKCHANNEL_RATE_LIMIT, default 120 requests/60s) — it's a sandbox,
        not a production backend. Self-hosters raise the limit or run unlimited.
        """
        self.key_issuance_rate_limiter.check(request.remote_addr)
        body = request.json()
        agent_label = body.get("agent_label", "")
        if not isinstance(agent_label, str) or not agent_label.strip():
            raise APIError(422, "missing_field", "'agent_label' is required")
        agent_label = agent_label.strip()[:128]
        key_id, _secret, raw_key = mint_raw_key()
        self.store.issue_api_key(
            key_id=key_id,
            key_hash=hash_key(raw_key),
            owner_id=agent_label,
            agent_label=agent_label,
            plan="free",
            ttl_seconds=None,  # permanent
        )
        self.store.record_security_event(
            event_type="key.issue",
            subject_key_id=key_id,
            remote_addr=request.remote_addr,
            detail={"agent_label": agent_label},
        )
        return self.json_response(
            201,
            {
                "key": raw_key,
                "key_id": key_id,
                "expires_at": None,
                "agent_label": agent_label,
                "rate_limit": self.rate_limit,
                "rate_limit_window_seconds": self.rate_limit_window,
            },
        )

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
            owner_id=request.auth.owner_id,
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
        page = self.store.list_messages(
            channel, None, 20,
            key_id=request.auth.key_id, team_id=request.auth.team_id,
            status="unclaimed", owner_id=request.auth.owner_id,
        )
        for msg in page.get("data", []):
            try:
                claim_result = self.store.claim_message(
                    msg["id"], {"actor": actor, "metadata": body.get("metadata", {})},
                    key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id,
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
            key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id,
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
            owner_id=request.auth.owner_id,
        )
        return self.json_response(201, {"message": envelope.message, "next_cursor": envelope.cursor})

    def task_claim_and_ack(self, request: Request) -> Response:
        body = request.json()
        message_id = body.get("message_id")
        actor = body.get("actor")
        if not message_id:
            raise APIError(422, "missing_field", "'message_id' is required")
        metadata = body.get("metadata", {})
        claim_result = self.store.claim_message(message_id, {"actor": actor, "metadata": metadata}, key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
        if claim_result["status"] == "already_claimed" and claim_result["message"].get("claimed_by", {}) and claim_result["message"]["claimed_by"].get("id") != actor:
            raise APIError(409, "already_claimed", "This message has already been claimed by another actor")
        ack_result = self.store.ack_message(message_id, {"actor": actor, "metadata": metadata}, key_id=request.auth.key_id, team_id=request.auth.team_id, owner_id=request.auth.owner_id)
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
            owner_id=request.auth.owner_id,
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
            owner_id=request.auth.owner_id,
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
                owner_id=request.auth.owner_id,
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
            "plan": auth.plan,
            "active": auth.active,
            "scopes": auth.scopes,
            "rate_limit": self.rate_limit,
            "rate_limit_window_seconds": self.rate_limit_window,
        })

    def delete_keys_me(self, request: Request) -> Response:
        """Revoke the calling API key. The key becomes inactive immediately;
        subsequent requests with it will return 401. Use this for key
        rotation: mint a new key, confirm it works, then DELETE the old one."""
        auth = request.auth
        self.store.revoke_api_key(auth.key_id)
        if hasattr(self.authenticator, "invalidate"):
            self.authenticator.invalidate(auth.raw_key)
        self.store.record_security_event(
            event_type="key.revoke",
            subject_key_id=auth.key_id,
            remote_addr=request.remote_addr,
            detail={"self_revoked": True},
        )
        return self.json_response(200, {"key_id": auth.key_id, "revoked": True})

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
        return self.json_response(200, {
            "key_id": auth.key_id,
            "plan": auth.plan,
            "rate_limit": {
                "requests_per_window": self.rate_limit,
                "window_seconds": self.rate_limit_window,
            },
            "instance_kind": self.instance_kind,
            "note": "Watch X-RateLimit-Remaining on responses. The public instance is a sandbox — self-host for higher limits.",
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
        from backchannel import __version__
        return self.json_response(200, {
            "status": "operational",
            "version": __version__,
            "instance_kind": self.instance_kind,
            "updated_at": self.store.now().isoformat(),
            "availability": "best-effort — this is a free, open test instance",
            "health_url": f"{base}/health",
            "self_host_url": f"{base}/repo/blob/main/SELF-HOST.md",
        })

    def status_page(self, request: Request) -> Response:
        """Human-readable status page. Probes the DB + last-cleanup-run as a
        liveness signal. No external monitoring dependency."""
        import time as _time
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
        audit access is a future admin surface; today the endpoint is
        scoped so an agent can self-audit its own key issuance history.
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
