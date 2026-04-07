from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.landing import render_landing_page
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
        self.invitation_rate_limiter = invitation_rate_limiter or SlidingWindowRateLimiter(
            limit=10,
            window_seconds=60,
            now_provider=self.store.now,
        )
        self.routes: list[tuple[str, re.Pattern[str], bool, RouteHandler]] = [
            ("GET", re.compile(r"^/$"), False, self.root),
            ("GET", re.compile(r"^/health$"), False, self.health),
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
            ("POST", re.compile(r"^/v1/actors$"), True, self.create_actor),
            ("GET", re.compile(r"^/v1/actors/(?P<identifier>[^/]+)$"), True, self.get_actor),
            ("POST", re.compile(r"^/v1/actors/(?P<identifier>[^/]+)/aliases$"), True, self.create_actor_alias),
            ("GET", re.compile(r"^/v1/channel-invitations/(?P<invitation_id>[^/]+)$"), False, self.get_channel_invitation),
            ("DELETE", re.compile(r"^/v1/channel-invitations/(?P<invitation_id>[^/]+)$"), True, self.revoke_channel_invitation),
            ("POST", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)/ack$"), True, self.ack_message),
            ("POST", re.compile(r"^/v1/messages/(?P<message_id>[^/]+)/claim$"), True, self.claim_message),
        ]

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        try:
            response = self.dispatch(environ)
        except APIError as exc:
            response = self.json_response(exc.status, exc.to_payload())
        except Exception as exc:  # pragma: no cover - defensive safeguard
            payload = {"error": "internal_server_error", "message": str(exc)}
            response = self.json_response(500, payload)

        start_response(
            f"{response.status} {HTTPStatus(response.status).phrase}",
            [
                ("Content-Type", response.content_type),
                ("Content-Length", str(len(response.body))),
            ],
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
                return handler(request, **match.groupdict())

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

    def create_channel(self, request: Request) -> Response:
        channel = self.store.create_channel(request.json(), owner_id=request.auth.owner_id, key_id=request.auth.key_id)
        return self.json_response(201, channel)

    def get_channel(self, request: Request, identifier: str) -> Response:
        channel = self.store.get_channel(identifier, key_id=request.auth.key_id)
        return self.json_response(200, channel)

    def patch_channel(self, request: Request, identifier: str) -> Response:
        channel = self.store.update_channel(identifier, request.json(), key_id=request.auth.key_id)
        return self.json_response(200, channel)

    def create_channel_alias(self, request: Request, identifier: str) -> Response:
        channel = self.store.create_channel_alias(identifier, request.json(), key_id=request.auth.key_id)
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
        )
        return self.json_response(201, invitation)

    def create_message(self, request: Request, identifier: str) -> Response:
        envelope = self.store.create_message(identifier, request.json(), key_id=request.auth.key_id)
        return self.json_response(201, {"message": envelope.message, "next_since": envelope.cursor})

    def list_messages(self, request: Request, identifier: str) -> Response:
        since = request.query_value("since")
        limit = request.query_value("limit")
        parsed_limit = None if limit is None else int(limit)
        payload = self.store.list_messages(identifier, since=since, limit=parsed_limit, key_id=request.auth.key_id)
        return self.json_response(200, payload)

    def list_channel_members(self, request: Request, identifier: str) -> Response:
        members = self.store.list_channel_members(identifier, key_id=request.auth.key_id)
        return self.json_response(200, {"items": members})

    def add_channel_member(self, request: Request, identifier: str) -> Response:
        member = self.store.add_channel_member(identifier, request.json(), key_id=request.auth.key_id)
        return self.json_response(201, member)

    def remove_channel_member(self, request: Request, identifier: str, member_key_id: str) -> Response:
        self.store.remove_channel_member(identifier, member_key_id, key_id=request.auth.key_id)
        return self.json_response(200, {"status": "removed"})

    def ack_message(self, request: Request, message_id: str) -> Response:
        payload = self.store.ack_message(message_id, request.json(), key_id=request.auth.key_id)
        return self.json_response(200, payload)

    def claim_message(self, request: Request, message_id: str) -> Response:
        payload = self.store.claim_message(message_id, request.json(), key_id=request.auth.key_id)
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
        invitation = self.store.revoke_channel_invitation(invitation_id)
        return self.json_response(200, invitation)

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
