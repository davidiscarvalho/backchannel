from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backchannel.store import APIError


@dataclass
class AuthContext:
    raw_key: str
    key_id: str
    owner_id: str
    plan: str
    active: bool = True


class DepotAuthenticator:
    def __init__(self, introspector: Callable[[str], AuthContext]):
        self.introspector = introspector

    def authenticate(self, headers: dict[str, str]) -> AuthContext:
        raw_key = headers.get("X-Api-Key") or headers.get("X-API-Key")
        if not raw_key:
            raise APIError(401, "unauthorized", "Missing X-API-Key header")

        try:
            context = self.introspector(raw_key)
        except LookupError as exc:
            raise APIError(401, "unauthorized", "Invalid API key") from exc
        if not context.active:
            raise APIError(401, "unauthorized", "Inactive API key")
        return context

    @classmethod
    def from_env(cls) -> "DepotAuthenticator":
        url = os.environ.get("BACKCHANNEL_DEPOT_INTROSPECTION_URL")
        token = os.environ.get("BACKCHANNEL_DEPOT_SERVICE_TOKEN")
        return cls(http_introspector(url=url, service_token=token))


def http_introspector(url: str | None, service_token: str | None = None, timeout: int = 5) -> Callable[[str], AuthContext]:
    def introspect(raw_key: str) -> AuthContext:
        if not url:
            raise APIError(
                503,
                "auth_not_configured",
                "BACKCHANNEL_DEPOT_INTROSPECTION_URL is not configured",
            )

        request = Request(url=url, method="GET")
        request.add_header("X-API-Key", raw_key)
        if service_token:
            request.add_header("Authorization", f"Bearer {service_token}")

        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise APIError(401, "unauthorized", "Invalid API key") from exc
            raise APIError(502, "depot_error", "API Depot introspection failed") from exc
        except URLError as exc:
            raise APIError(502, "depot_unreachable", "API Depot introspection is unavailable") from exc

        return auth_context_from_payload(raw_key, payload)

    return introspect


def auth_context_from_payload(raw_key: str, payload: dict[str, Any]) -> AuthContext:
    if not isinstance(payload, dict):
        raise APIError(502, "depot_error", "API Depot introspection returned an invalid payload")

    key_id = payload.get("key_id")
    owner_id = payload.get("owner_id")
    plan = payload.get("plan", "unknown")
    active = payload.get("active", False)

    if not isinstance(key_id, str) or not key_id:
        raise APIError(502, "depot_error", "API Depot introspection did not include key_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise APIError(502, "depot_error", "API Depot introspection did not include owner_id")
    if not isinstance(plan, str):
        raise APIError(502, "depot_error", "API Depot introspection returned an invalid plan")
    if not isinstance(active, bool):
        raise APIError(502, "depot_error", "API Depot introspection returned an invalid active flag")

    return AuthContext(
        raw_key=raw_key,
        key_id=key_id,
        owner_id=owner_id,
        plan=plan,
        active=active,
    )
