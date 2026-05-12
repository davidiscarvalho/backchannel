"""Authentication for Backchannel.

Self-contained: keys are issued, hashed at rest, and verified locally.
No external dependency (the previous DepotAuthenticator and its depot
introspection HTTP contract are gone — see auth_compat.py for the legacy
shim retained only for the existing test suite).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from backchannel.store import APIError

if TYPE_CHECKING:
    from backchannel.store import BackchannelStore


# --- Key format ------------------------------------------------------------

KEY_PREFIX = "bck"
KEY_ID_BYTES = 9   # 12 chars base64 -> 18 chars w/ prefix
SECRET_BYTES = 24  # 32 chars base64
TIER_0_TTL = timedelta(hours=48)


def _b64(nbytes: int) -> str:
    # url-safe, stripped of padding so keys never contain '='
    return secrets.token_urlsafe(nbytes).rstrip("=")


def mint_raw_key() -> tuple[str, str, str]:
    """Return (key_id, secret, raw_key). raw_key = f'{key_id}.{secret}'."""
    key_id = f"{KEY_PREFIX}_{_b64(KEY_ID_BYTES)}"
    secret = _b64(SECRET_BYTES)
    return key_id, secret, f"{key_id}.{secret}"


def hash_key(raw_key: str) -> str:
    """Constant-form digest of a raw key for at-rest storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def split_key(raw_key: str) -> tuple[str, str]:
    """Return (key_id, secret). Raises ValueError if malformed."""
    if "." not in raw_key:
        raise ValueError("Key must be formatted as '<key_id>.<secret>'")
    key_id, secret = raw_key.split(".", 1)
    if not key_id or not secret:
        raise ValueError("Key id and secret must both be present")
    return key_id, secret


# --- Auth context ----------------------------------------------------------


@dataclass
class AuthContext:
    raw_key: str
    key_id: str
    owner_id: str
    plan: str
    active: bool = True
    tier: int = 1
    team_id: str | None = None
    team_name: str | None = None
    scopes: list[str] | None = None


# --- Local authenticator (default) ----------------------------------------


class LocalAuthenticator:
    """Authenticates against the local api_keys table.

    The store is the single source of truth. Keys are hashed at rest; the
    raw key is never persisted. A small in-memory cache (60s) prevents a
    DB hit on every request without weakening revocation by more than the
    cache TTL.
    """

    def __init__(self, store: "BackchannelStore", cache_ttl_seconds: int = 60):
        self.store = store
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[AuthContext, float]] = {}

    def authenticate(self, headers: dict[str, str]) -> AuthContext:
        raw_key = headers.get("X-Api-Key") or headers.get("X-API-Key")
        if not raw_key:
            raise APIError(401, "unauthorized", "Missing X-API-Key header")

        cached = self._cache.get(raw_key)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]

        try:
            key_id, _ = split_key(raw_key)
        except ValueError as exc:
            raise APIError(401, "unauthorized", "Malformed API key") from exc

        record = self.store.lookup_api_key(key_id=key_id, key_hash=hash_key(raw_key))
        if record is None:
            raise APIError(401, "unauthorized", "Invalid API key")
        if not record.get("active", True):
            raise APIError(401, "unauthorized", "Inactive API key")
        if record.get("expires_at"):
            expires = record["expires_at"]
            if isinstance(expires, datetime) and expires <= datetime.now(timezone.utc):
                raise APIError(410, "key_expired", "This key has expired", {"upgrade_url": "/v1/keys/promote"})

        ctx = AuthContext(
            raw_key=raw_key,
            key_id=record["key_id"],
            owner_id=record["owner_id"],
            plan=record.get("plan") or "free",
            active=bool(record.get("active", True)),
            tier=int(record.get("tier") or 0),
            team_id=record.get("team_id"),
            team_name=record.get("team_name"),
        )
        self._cache[raw_key] = (ctx, time.monotonic() + self.cache_ttl_seconds)
        return ctx

    def invalidate_cache(self, raw_key: str | None = None) -> None:
        if raw_key is None:
            self._cache.clear()
            return
        self._cache.pop(raw_key, None)


# --- Legacy / test compatibility ------------------------------------------


class DepotAuthenticator:
    """LEGACY shim. Preserved only so the existing test suite keeps working
    without modification while we migrate.

    Tests construct ``DepotAuthenticator(introspector=callable)`` to inject
    a lambda that returns ``AuthContext``. New code should use
    ``LocalAuthenticator`` against the store.
    """

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


# --- Factories -------------------------------------------------------------


def authenticator_from_env(store: "BackchannelStore") -> LocalAuthenticator:
    """Build the default LocalAuthenticator from the store. No env vars
    required — the depot URL/token contract is retired."""
    return LocalAuthenticator(store=store)
