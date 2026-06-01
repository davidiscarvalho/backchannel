from __future__ import annotations

import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_timestamp_or_none(value: str | None) -> datetime | None:
    """Parse a timestamp string, returning None for missing/invalid/sentinel values.
    Treats '0', '', and unparseable strings as 'from the beginning' (no filter)."""
    if not value or value.strip() == "0":
        return None
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError):
        return None


# --- Sandbox firehose channel ---------------------------------------------

# A public, well-known channel any agent can post to and read from to test
# the protocol. Resolvable by the alias below; provisioned by the worker.
SANDBOX_CHANNEL_ALIAS = "sandbox"
HEARTBEAT_BOT_LABEL = "sandbox-heartbeat-bot"
# The heartbeat bot posts when the sandbox has been silent for at least this
# long, so a lone visiting agent always has a fresh message to read.
SANDBOX_HEARTBEAT_QUIET_SECONDS = 60


class APIError(Exception):
    def __init__(self, status: int, error: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload = {"error": self.error, "message": self.message}
        payload.update(self.details)
        return payload


@dataclass
class MessageEnvelope:
    message: dict[str, Any]
    cursor: str


def _validate_against_schema(value: Any, schema: dict[str, Any], *, prefix: str = "") -> list[dict[str, str]]:
    """Tiny JSON-Schema-subset validator. Supports:
      type, required, properties, additionalProperties (bool only),
      enum, minLength, maxLength, minimum, maximum, pattern.

    Returns a list of {field, issue} dicts. Empty list means valid.
    """
    import re as _re

    violations: list[dict[str, str]] = []
    expected_type = schema.get("type")
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    if expected_type:
        py = type_map.get(expected_type)
        if py is not None and not isinstance(value, py):
            violations.append({"field": prefix or "(root)", "issue": f"expected type '{expected_type}'"})
            return violations  # don't cascade further

    if expected_type == "string":
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if isinstance(min_len, int) and len(value) < min_len:
            violations.append({"field": prefix, "issue": f"minLength {min_len}"})
        if isinstance(max_len, int) and len(value) > max_len:
            violations.append({"field": prefix, "issue": f"maxLength {max_len}"})
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if not _re.search(pattern, value):
                    violations.append({"field": prefix, "issue": f"pattern '{pattern}' did not match"})
            except _re.error as exc:
                violations.append({"field": prefix, "issue": f"invalid pattern in schema: {exc}"})

    if expected_type in ("number", "integer"):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            violations.append({"field": prefix, "issue": f"minimum {minimum}"})
        if maximum is not None and value > maximum:
            violations.append({"field": prefix, "issue": f"maximum {maximum}"})

    enum = schema.get("enum")
    if enum is not None and value not in enum:
        violations.append({"field": prefix, "issue": f"value must be one of {enum}"})

    if expected_type == "object" or (expected_type is None and isinstance(value, dict)):
        required_fields = schema.get("required", []) or []
        for f in required_fields:
            if f not in value:
                violations.append({"field": f"{prefix}.{f}" if prefix else f, "issue": "required field missing"})
        properties = schema.get("properties", {}) or {}
        for f, sub_schema in properties.items():
            if f in value:
                sub_violations = _validate_against_schema(
                    value[f], sub_schema, prefix=f"{prefix}.{f}" if prefix else f
                )
                violations.extend(sub_violations)
        if schema.get("additionalProperties") is False:
            allowed_keys = set(properties.keys())
            for actual_key in value.keys():
                if actual_key not in allowed_keys:
                    violations.append(
                        {
                            "field": f"{prefix}.{actual_key}" if prefix else actual_key,
                            "issue": "additional property not allowed",
                        }
                    )

    return violations


class BackchannelStore:
    def __init__(self, db_path: str | Path, now_provider: Callable[[], datetime] | None = None):
        self.db_path = str(db_path)
        self.now_provider = now_provider or utc_now
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def now(self) -> datetime:
        return self.now_provider().astimezone(timezone.utc)

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY,
                    owner_key_id TEXT,
                    owner_id TEXT,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('broadcast', 'claimable')),
                    description TEXT NOT NULL DEFAULT '',
                    metadata_schema TEXT NOT NULL DEFAULT '{}',
                    pinned_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel_aliases (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel_links (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    related_channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS actors (
                    id TEXT PRIMARY KEY,
                    owner_key_id TEXT,
                    owner_id TEXT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS actor_aliases (
                    id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    actor_id TEXT REFERENCES actors(id) ON DELETE SET NULL,
                    actor_label TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    claimed_by_actor_id TEXT REFERENCES actors(id) ON DELETE SET NULL,
                    claimed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS message_events (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    actor_id TEXT REFERENCES actors(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ('ack', 'claim')),
                    metadata TEXT NOT NULL DEFAULT '{}',
                    occurred_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel_invitations (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    owner_id TEXT NOT NULL,
                    created_by_key_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_messages_channel_created
                    ON messages(channel_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_messages_expiry
                    ON messages(expires_at);
                CREATE INDEX IF NOT EXISTS idx_events_message_type_actor
                    ON message_events(message_id, event_type, actor_id);
                CREATE INDEX IF NOT EXISTS idx_channel_invitations_expiry
                    ON channel_invitations(expires_at);

                CREATE TABLE IF NOT EXISTS channel_members (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    key_id TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    granted_via_invitation_id TEXT REFERENCES channel_invitations(id) ON DELETE SET NULL,
                    UNIQUE(channel_id, key_id)
                );

                CREATE INDEX IF NOT EXISTS idx_channel_members_channel_key
                    ON channel_members(channel_id, key_id);

                CREATE TABLE IF NOT EXISTS channel_events (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL CHECK (event_type IN ('member_added', 'member_removed', 'invitation_resolved', 'invitation_revoked')),
                    actor_key_id TEXT NOT NULL,
                    subject_key_id TEXT,
                    invitation_id TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_channel_events_channel_created
                    ON channel_events(channel_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_channel_events_expiry
                    ON channel_events(expires_at);

                CREATE TABLE IF NOT EXISTS audit_channel_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES audit_cleanup_runs(id) ON DELETE CASCADE,
                    live_event_id TEXT NOT NULL,
                    live_channel_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_key_id TEXT NOT NULL,
                    subject_key_id TEXT,
                    invitation_id TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_cleanup_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    archived_messages INTEGER NOT NULL DEFAULT 0,
                    purged_messages INTEGER NOT NULL DEFAULT 0,
                    archived_invitations INTEGER NOT NULL DEFAULT 0,
                    purged_invitations INTEGER NOT NULL DEFAULT 0,
                    failure_message TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_channels (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES audit_cleanup_runs(id) ON DELETE CASCADE,
                    live_channel_id TEXT NOT NULL,
                    owner_id TEXT,
                    created_by_key_id TEXT,
                    snapshot_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_messages (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES audit_cleanup_runs(id) ON DELETE CASCADE,
                    live_message_id TEXT NOT NULL,
                    live_channel_id TEXT NOT NULL,
                    owner_id TEXT,
                    actor_id TEXT,
                    actor_name TEXT,
                    actor_label TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    claimed_by_actor_id TEXT,
                    claimed_by_actor_name TEXT,
                    claimed_at TEXT,
                    channel_snapshot_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_message_events (
                    id TEXT PRIMARY KEY,
                    audit_message_id TEXT NOT NULL REFERENCES audit_messages(id) ON DELETE CASCADE,
                    live_event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    actor_name TEXT,
                    metadata TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_channel_invitations (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES audit_cleanup_runs(id) ON DELETE CASCADE,
                    live_invitation_id TEXT NOT NULL,
                    live_channel_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_by_key_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    channel_snapshot_json TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "channels", "owner_id", "TEXT")
            self._ensure_column(conn, "actors", "owner_id", "TEXT")
            self._ensure_column(conn, "channels", "access", "TEXT NOT NULL DEFAULT 'open'")
            self._ensure_column(conn, "channels", "team_id", "TEXT")
            self._ensure_column(conn, "channels", "webhook_url", "TEXT")
            self._ensure_column(conn, "channels", "webhook_secret", "TEXT")
            self._ensure_column(conn, "messages", "depends_on", "TEXT")
            self._ensure_column(conn, "channels", "ttl_seconds", "INTEGER NOT NULL DEFAULT 86400")
            self._ensure_column(conn, "channels", "retention_days", "INTEGER NOT NULL DEFAULT 7")
            # Abuse controls: cap stored messages (ring buffer), throttle
            # writes per minute (keyless), and a pause/kill switch.
            self._ensure_column(conn, "channels", "max_messages", "INTEGER")
            self._ensure_column(conn, "channels", "max_writes_per_minute", "INTEGER")
            self._ensure_column(conn, "channels", "paused", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "messages", "lease_token", "TEXT")
            self._ensure_column(conn, "messages", "lease_expires_at", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS key_scopes (
                    key_id TEXT PRIMARY KEY,
                    scopes TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "audit_cleanup_runs", "archived_channel_events", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "audit_cleanup_runs", "purged_channel_events", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_cache (
                    id TEXT PRIMARY KEY,
                    response_status INTEGER NOT NULL,
                    response_body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_idempotency_cache_expiry ON idempotency_cache(expires_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_webhooks (
                    id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    webhook_secret TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER NOT NULL,
                    delivered_at INTEGER,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_webhooks_next ON pending_webhooks(next_attempt_at) WHERE delivered_at IS NULL"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    owner_key_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_key_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    agent_label TEXT,
                    plan TEXT NOT NULL DEFAULT 'free',
                    tier INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    team_id TEXT,
                    team_name TEXT,
                    email TEXT,
                    credit_balance_micros INTEGER NOT NULL DEFAULT 0,
                    promoted_at TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    last_used_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_owner ON api_keys(owner_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_label ON api_keys(agent_label) WHERE active = 1"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_audit (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_key_id TEXT,
                    subject_key_id TEXT,
                    event_type TEXT NOT NULL,
                    remote_addr TEXT,
                    detail TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_time ON security_audit(occurred_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_actor ON security_audit(actor_key_id)"
            )
            conn.commit()

    # --- security audit log -----------------------------------------

    def record_security_event(
        self,
        *,
        event_type: str,
        actor_key_id: str | None = None,
        subject_key_id: str | None = None,
        remote_addr: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Write an append-only security event. Used for sensitive ops:
        key issuance, key revocation, channel deletion, member add/remove.
        Never raises — security logging must not block the user op."""
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO security_audit
                        (id, occurred_at, actor_key_id, subject_key_id, event_type, remote_addr, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        to_timestamp(self.now()),
                        actor_key_id,
                        subject_key_id,
                        event_type,
                        remote_addr,
                        json.dumps(detail or {}, default=str),
                    ),
                )
                conn.commit()
        except Exception:
            # Swallow — never fail the user op due to audit logging.
            pass

    def list_security_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, occurred_at, actor_key_id, subject_key_id, event_type, remote_addr, detail "
                "FROM security_audit ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    **dict(row),
                    "detail": json.loads(row["detail"]) if row["detail"] else {},
                }
                for row in rows
            ]

    def create_channel(self, payload: dict[str, Any], owner_id: str, key_id: str, team_id: str | None = None) -> dict[str, Any]:
        name = self._required_string(payload, "name")
        mode = self._required_string(payload, "mode")
        if mode not in {"broadcast", "claimable"}:
            raise APIError(422, "invalid_mode", "Channel mode must be 'broadcast' or 'claimable'")
        access = payload.get("access", "open")
        if access not in {"open", "restricted"}:
            raise APIError(422, "invalid_access", "Channel access must be 'open' or 'restricted'")

        description = self._optional_string(payload.get("description"), default="")
        metadata_schema = self._ensure_mapping(payload.get("metadata_schema", {}), "metadata_schema")
        pinned_message = self._optional_string(payload.get("pinned_message"), allow_none=True)
        related_channels = payload.get("related_channels", [])
        webhook_url = self._optional_string(payload.get("webhook_url"), allow_none=True)
        webhook_secret = self._optional_string(payload.get("webhook_secret"), allow_none=True)
        raw_ttl = payload.get("ttl_seconds")
        if raw_ttl is not None:
            if not isinstance(raw_ttl, int) or raw_ttl < 300 or raw_ttl > 2592000:
                raise APIError(422, "invalid_ttl_seconds", "ttl_seconds must be an integer between 300 and 2592000")
            ttl_seconds = raw_ttl
        else:
            ttl_seconds = self._DEFAULT_TTL_SECONDS
        raw_retention = payload.get("retention_days")
        if raw_retention is not None:
            if not isinstance(raw_retention, int) or isinstance(raw_retention, bool) or raw_retention < 1 or raw_retention > 365:
                raise APIError(422, "invalid_retention_days", "retention_days must be an integer between 1 and 365")
            retention_days = raw_retention
        else:
            retention_days = self._DEFAULT_RETENTION_DAYS
        max_messages = self._validate_optional_count(payload.get("max_messages"), "max_messages")
        max_writes_per_minute = self._validate_optional_count(payload.get("max_writes_per_minute"), "max_writes_per_minute")
        effective_team_id = team_id
        channel_id = str(uuid.uuid4())
        now = to_timestamp(self.now())

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO channels (id, owner_key_id, owner_id, name, mode, access, team_id, description, metadata_schema, pinned_message, webhook_url, webhook_secret, ttl_seconds, retention_days, max_messages, max_writes_per_minute, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    key_id,
                    owner_id,
                    name,
                    mode,
                    access,
                    effective_team_id,
                    description,
                    json.dumps(metadata_schema, sort_keys=True),
                    pinned_message,
                    webhook_url,
                    webhook_secret,
                    ttl_seconds,
                    retention_days,
                    max_messages,
                    max_writes_per_minute,
                    now,
                    now,
                ),
            )
            self._replace_channel_links(conn, channel_id, related_channels)
            self._grant_channel_access(conn, channel_id, key_id)
            conn.commit()
            channel = self._get_channel_by_id(conn, channel_id)
            return self._serialize_channel(conn, channel)

    def get_channel(self, identifier: str, key_id: str, team_id: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            channel = self._resolve_channel(conn, identifier, key_id=key_id, team_id=team_id)
            return self._serialize_channel(conn, channel)

    def update_channel(self, identifier: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        allowed = {"name", "mode", "access", "description", "metadata_schema", "pinned_message", "related_channels", "retention_days", "max_messages", "max_writes_per_minute", "paused"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise APIError(422, "invalid_fields", "Unknown channel fields", {"fields": unknown})

        with self.connect() as conn:
            channel = self._resolve_channel(conn, identifier, key_id=key_id, team_id=team_id)
            updates: list[tuple[str, Any]] = []
            if "name" in payload:
                updates.append(("name", self._required_string(payload, "name")))
            if "mode" in payload:
                mode = self._required_string(payload, "mode")
                if mode not in {"broadcast", "claimable"}:
                    raise APIError(422, "invalid_mode", "Channel mode must be 'broadcast' or 'claimable'")
                updates.append(("mode", mode))
            if "access" in payload:
                access = payload["access"]
                if access not in {"open", "restricted"}:
                    raise APIError(422, "invalid_access", "Channel access must be 'open' or 'restricted'")
                updates.append(("access", access))
            if "description" in payload:
                updates.append(("description", self._optional_string(payload.get("description"), default="")))
            if "metadata_schema" in payload:
                updates.append(("metadata_schema", json.dumps(self._ensure_mapping(payload["metadata_schema"], "metadata_schema"), sort_keys=True)))
            if "pinned_message" in payload:
                updates.append(("pinned_message", self._optional_string(payload.get("pinned_message"), allow_none=True)))
            if "retention_days" in payload:
                raw_retention = payload["retention_days"]
                if not isinstance(raw_retention, int) or isinstance(raw_retention, bool) or raw_retention < 1 or raw_retention > 365:
                    raise APIError(422, "invalid_retention_days", "retention_days must be an integer between 1 and 365")
                updates.append(("retention_days", raw_retention))
            if "max_messages" in payload:
                updates.append(("max_messages", self._validate_optional_count(payload["max_messages"], "max_messages")))
            if "max_writes_per_minute" in payload:
                updates.append(("max_writes_per_minute", self._validate_optional_count(payload["max_writes_per_minute"], "max_writes_per_minute")))
            if "paused" in payload:
                paused_value = payload["paused"]
                if not isinstance(paused_value, bool):
                    raise APIError(422, "invalid_paused", "paused must be a boolean")
                updates.append(("paused", 1 if paused_value else 0))

            if updates:
                clauses = ", ".join(f"{column} = ?" for column, _ in updates) + ", updated_at = ?"
                params = [value for _, value in updates]
                params.extend([to_timestamp(self.now()), channel["id"]])
                conn.execute(f"UPDATE channels SET {clauses} WHERE id = ?", params)

            if "related_channels" in payload:
                self._replace_channel_links(conn, channel["id"], payload.get("related_channels", []))

            conn.commit()
            updated = self._get_channel_by_id(conn, channel["id"])
            return self._serialize_channel(conn, updated)

    def create_channel_alias(self, identifier: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        alias = self._required_string(payload, "alias")
        with self.connect() as conn:
            channel = self._resolve_channel(conn, identifier, key_id=key_id, team_id=team_id)
            self._insert_alias(conn, "channel_aliases", "channel_id", channel["id"], alias)
            conn.commit()
            return self._serialize_channel(conn, self._get_channel_by_id(conn, channel["id"]))

    def create_actor(self, payload: dict[str, Any], owner_id: str, key_id: str) -> dict[str, Any]:
        name = self._required_string(payload, "name")
        description = self._optional_string(payload.get("description"), default="")
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")
        actor_id = str(uuid.uuid4())
        now = to_timestamp(self.now())

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO actors (id, owner_key_id, owner_id, name, description, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (actor_id, key_id, owner_id, name, description, json.dumps(metadata, sort_keys=True), now, now),
            )
            conn.commit()
            actor = self._get_actor_by_id(conn, actor_id)
            return self._serialize_actor(conn, actor)

    def get_actor(self, identifier: str) -> dict[str, Any]:
        with self.connect() as conn:
            actor = self._resolve_actor(conn, identifier)
            return self._serialize_actor(conn, actor)

    def create_actor_alias(self, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        alias = self._required_string(payload, "alias")
        with self.connect() as conn:
            actor = self._resolve_actor(conn, identifier)
            self._insert_alias(conn, "actor_aliases", "actor_id", actor["id"], alias)
            conn.commit()
            return self._serialize_actor(conn, self._get_actor_by_id(conn, actor["id"]))

    # Max message body size in UTF-8 bytes. 10000 bytes = 10k ASCII chars or
    # ~2500 four-byte emoji. Operators tune via BACKCHANNEL_MAX_MESSAGE_BYTES.
    try:
        _MAX_CONTENT_BYTES = int(os.environ.get("BACKCHANNEL_MAX_MESSAGE_BYTES", "10000"))
    except ValueError:
        _MAX_CONTENT_BYTES = 10000

    # Instance-wide defaults for new channels when the create payload omits
    # ttl_seconds / retention_days. Per-channel values still override these.
    # TTL = how long a message lives before expiring; retention = how long the
    # expired message stays readable via /history before it is purged.
    try:
        _DEFAULT_TTL_SECONDS = int(os.environ.get("BACKCHANNEL_DEFAULT_TTL_SECONDS", "86400"))
    except ValueError:
        _DEFAULT_TTL_SECONDS = 86400
    try:
        _DEFAULT_RETENTION_DAYS = int(os.environ.get("BACKCHANNEL_DEFAULT_RETENTION_DAYS", "7"))
    except ValueError:
        _DEFAULT_RETENTION_DAYS = 7

    def create_message(self, channel_identifier: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> MessageEnvelope:
        content = self._required_string(payload, "content")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._MAX_CONTENT_BYTES:
            raise APIError(
                422,
                "content_too_large",
                f"Message content exceeds the {self._MAX_CONTENT_BYTES}-byte limit",
                {"max_content_bytes": self._MAX_CONTENT_BYTES, "received_bytes": len(content_bytes)},
            )
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")
        actor_identifier = payload.get("actor")
        actor_label = self._optional_string(payload.get("actor_label"), allow_none=True)
        now = self.now()
        created_at = to_timestamp(now)
        message_id = str(uuid.uuid4())

        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            # Abuse controls (see _init_db). Checked before any write work.
            if "paused" in channel.keys() and channel["paused"]:
                raise APIError(
                    503,
                    "channel_paused",
                    "This channel is paused and not accepting new messages",
                    {"retry_after": 30},
                )
            max_writes_per_minute = channel["max_writes_per_minute"] if "max_writes_per_minute" in channel.keys() else None
            if max_writes_per_minute is not None:
                window_start = to_timestamp(now - timedelta(seconds=60))
                recent_writes = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE channel_id = ? AND created_at > ?",
                    (channel["id"], window_start),
                ).fetchone()["n"]
                if recent_writes >= max_writes_per_minute:
                    raise APIError(
                        429,
                        "channel_write_rate_exceeded",
                        f"This channel accepts at most {max_writes_per_minute} messages per minute",
                        {"retry_after": 60},
                    )
            ttl_seconds = channel["ttl_seconds"] if "ttl_seconds" in channel.keys() else self._DEFAULT_TTL_SECONDS
            expires_at = to_timestamp(now + timedelta(seconds=ttl_seconds))
            # Validate metadata against channel's metadata_schema
            channel_schema = json.loads(channel["metadata_schema"]) if isinstance(channel["metadata_schema"], str) else channel["metadata_schema"]
            if channel_schema:
                violations = _validate_against_schema(metadata, channel_schema, prefix="metadata")
                if violations:
                    raise APIError(
                        422,
                        "metadata_validation_failed",
                        "Message metadata failed channel schema validation",
                        {"violations": violations},
                    )
            actor = None
            if actor_identifier is not None:
                actor = self._resolve_actor(conn, self._optional_string(actor_identifier))

            conn.execute(
                """
                INSERT INTO messages (id, channel_id, actor_id, actor_label, content, metadata, created_at, expires_at, claimed_by_actor_id, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    message_id,
                    channel["id"],
                    actor["id"] if actor else None,
                    actor_label,
                    content,
                    json.dumps(metadata, sort_keys=True),
                    created_at,
                    expires_at,
                ),
            )
            # Ring-buffer cap: keep only the newest max_messages. Trimmed
            # messages are discarded outright, not archived to /history.
            # Ordered by rowid (monotonic insertion order) so a burst of
            # messages sharing a created_at timestamp still trims oldest-first.
            max_messages = channel["max_messages"] if "max_messages" in channel.keys() else None
            if max_messages is not None:
                conn.execute(
                    """
                    DELETE FROM messages
                    WHERE channel_id = ?
                      AND rowid NOT IN (
                        SELECT rowid FROM messages
                        WHERE channel_id = ?
                        ORDER BY rowid DESC
                        LIMIT ?
                      )
                    """,
                    (channel["id"], channel["id"], max_messages),
                )
            conn.commit()
            message = self._get_message(conn, message_id)
            envelope = MessageEnvelope(message=self._serialize_message(conn, message), cursor=created_at)

        webhook_url = channel["webhook_url"] if "webhook_url" in channel.keys() else None
        if webhook_url:
            webhook_secret = channel["webhook_secret"] if "webhook_secret" in channel.keys() else None
            self.queue_webhook(
                channel["id"],
                "message.created",
                {"message": envelope.message},
                webhook_url,
                webhook_secret,
            )
        return envelope

    def list_messages(self, channel_identifier: str, since: str | None, limit: int | None, key_id: str, team_id: str | None = None, status: str | None = None, expiring_before: str | None = None) -> dict[str, Any]:
        page_size = 50 if limit is None else limit
        if page_size < 1 or page_size > 100:
            raise APIError(422, "invalid_limit", "limit must be between 1 and 100")
        if status is not None and status not in {"unclaimed", "claimed"}:
            raise APIError(422, "invalid_status", "status must be 'unclaimed' or 'claimed'")

        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            now = to_timestamp(self.now())
            params: list[Any] = [channel["id"], now]
            extra_clauses = ""
            since_dt = parse_timestamp_or_none(since)
            if since_dt is not None:
                extra_clauses += " AND created_at > ?"
                params.append(to_timestamp(since_dt))
            if status == "unclaimed":
                extra_clauses += " AND claimed_by_actor_id IS NULL"
            elif status == "claimed":
                extra_clauses += " AND claimed_by_actor_id IS NOT NULL"
            if expiring_before:
                expiring_dt = parse_timestamp_or_none(expiring_before)
                if expiring_dt is not None:
                    extra_clauses += " AND expires_at < ?"
                    params.append(to_timestamp(expiring_dt))
            params.append(page_size)
            rows = conn.execute(
                f"""
                SELECT *
                FROM messages
                WHERE channel_id = ?
                  AND expires_at > ?
                  {extra_clauses}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                params,
            ).fetchall()

            items = [self._serialize_message(conn, row) for row in rows]
            next_cursor = items[-1]["created_at"] if items else since
            return {
                "data": items,
                "limit": page_size,
                "next_cursor": next_cursor,
            }

    def list_channel_history(self, channel_identifier: str, cursor: str | None, limit: int | None, key_id: str, team_id: str | None = None) -> dict[str, Any]:
        """Return archived (expired-then-cleaned-up) messages for a channel,
        newest first. Rows live in audit_messages until the cleanup worker
        purges them past the channel's retention_days window."""
        page_size = 50 if limit is None else limit
        if page_size < 1 or page_size > 100:
            raise APIError(422, "invalid_limit", "limit must be between 1 and 100")
        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            params: list[Any] = [channel["id"]]
            extra_clauses = ""
            cursor_dt = parse_timestamp_or_none(cursor)
            if cursor_dt is not None:
                extra_clauses = " AND created_at < ?"
                params.append(to_timestamp(cursor_dt))
            params.append(page_size)
            rows = conn.execute(
                f"""
                SELECT *
                FROM audit_messages
                WHERE live_channel_id = ?
                  {extra_clauses}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            items = [self._serialize_audit_message(row) for row in rows]
            next_cursor = items[-1]["created_at"] if items else None
            return {
                "data": items,
                "limit": page_size,
                "next_cursor": next_cursor,
            }

    def ack_message(self, message_id: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        actor_identifier = self._optional_string(payload.get("actor"), allow_none=True)
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")

        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            if parse_timestamp(message["expires_at"]) <= self.now():
                raise APIError(410, "message_expired", "Expired messages can no longer be acknowledged")

            channel = self._get_channel_by_id(conn, message["channel_id"])
            self._check_channel_access(conn, channel, key_id, team_id=team_id)

            actor = self._resolve_or_create_actor(conn, actor_identifier, key_id)
            existing = conn.execute(
                """
                SELECT id
                FROM message_events
                WHERE message_id = ? AND actor_id = ? AND event_type = 'ack'
                """,
                (message_id, actor["id"]),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO message_events (id, message_id, channel_id, actor_id, event_type, metadata, occurred_at)
                    VALUES (?, ?, ?, ?, 'ack', ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        message_id,
                        message["channel_id"],
                        actor["id"],
                        json.dumps(metadata, sort_keys=True),
                        to_timestamp(self.now()),
                    ),
                )
                conn.commit()

            refreshed = self._get_message(conn, message_id)
            return {
                "status": "acknowledged" if existing is None else "already_acknowledged",
                "message": self._serialize_message(conn, refreshed),
            }

    def claim_message(self, message_id: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        actor_identifier = self._optional_string(payload.get("actor"), allow_none=True)
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")

        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            if parse_timestamp(message["expires_at"]) <= self.now():
                raise APIError(410, "message_expired", "Expired messages can no longer be claimed")

            channel = self._get_channel_by_id(conn, message["channel_id"])
            if channel["mode"] != "claimable":
                raise APIError(409, "channel_not_claimable", "Only messages in claimable channels can be claimed")
            self._check_channel_access(conn, channel, key_id, team_id=team_id)

            actor = self._resolve_or_create_actor(conn, actor_identifier, key_id)
            now = self.now()
            previous_holder_id = message["claimed_by_actor_id"]
            if previous_holder_id:
                ack_exists = conn.execute(
                    "SELECT 1 FROM message_events WHERE message_id = ? AND event_type = 'ack' LIMIT 1",
                    (message_id,),
                ).fetchone()
                lease_expires_at = message["lease_expires_at"]
                lease_expired = lease_expires_at is not None and parse_timestamp(lease_expires_at) <= now
                if previous_holder_id == actor["id"]:
                    refreshed = self._get_message(conn, message_id)
                    if ack_exists:
                        return {"status": "already_acknowledged", "message": self._serialize_message(conn, refreshed)}
                    return {"status": "already_claimed", "message": self._serialize_message(conn, refreshed)}
                # A different actor may only take over a claim whose lease has
                # expired and was never acked — this is the crash-recovery path.
                if ack_exists or not lease_expired:
                    raise APIError(409, "already_claimed", "This message has already been claimed")

            claimed_at = to_timestamp(now)
            cursor = conn.execute(
                "UPDATE messages SET claimed_by_actor_id = ?, claimed_at = ?, lease_token = NULL, lease_expires_at = NULL "
                "WHERE id = ? AND (claimed_by_actor_id IS NULL OR (lease_expires_at IS NOT NULL AND lease_expires_at <= ?))",
                (actor["id"], claimed_at, message_id, claimed_at),
            )
            if cursor.rowcount == 0:
                raise APIError(409, "already_claimed", "This message has already been claimed")
            if previous_holder_id and previous_holder_id != actor["id"]:
                # Takeover of an expired lease — note the prior holder on the
                # claim event so the recovery is auditable within the allowed
                # event types ('ack', 'claim').
                metadata = {**metadata, "reclaimed_from": previous_holder_id}
            conn.execute(
                """
                INSERT INTO message_events (id, message_id, channel_id, actor_id, event_type, metadata, occurred_at)
                VALUES (?, ?, ?, ?, 'claim', ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    message_id,
                    message["channel_id"],
                    actor["id"],
                    json.dumps(metadata, sort_keys=True),
                    claimed_at,
                ),
            )
            conn.commit()
            refreshed = self._get_message(conn, message_id)
            return {"status": "claimed", "message": self._serialize_message(conn, refreshed)}

    def release_message(self, message_id: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        actor_identifier = self._optional_string(payload.get("actor"), allow_none=True)
        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            if parse_timestamp(message["expires_at"]) <= self.now():
                raise APIError(410, "message_expired", "Expired messages cannot be released")
            channel = self._get_channel_by_id(conn, message["channel_id"])
            if channel["mode"] != "claimable":
                raise APIError(409, "channel_not_claimable", "Only messages in claimable channels can be released")
            self._check_channel_access(conn, channel, key_id, team_id=team_id)
            if not message["claimed_by_actor_id"]:
                raise APIError(409, "not_claimed", "This message is not claimed and cannot be released")
            actor = self._resolve_or_create_actor(conn, actor_identifier, key_id)
            if message["claimed_by_actor_id"] != actor["id"]:
                raise APIError(403, "forbidden", "Only the claiming actor can release this message")
            conn.execute(
                "UPDATE messages SET claimed_by_actor_id = NULL, claimed_at = NULL WHERE id = ?",
                (message_id,),
            )
            conn.commit()
            refreshed = self._get_message(conn, message_id)
            return {"status": "released", "message": self._serialize_message(conn, refreshed)}

    def claim_with_lease(self, message_id: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        actor_identifier = self._optional_string(payload.get("actor"), allow_none=True)
        lease_seconds = payload.get("lease_seconds", 300)
        if not isinstance(lease_seconds, int) or lease_seconds < 30 or lease_seconds > 3600:
            raise APIError(422, "invalid_lease_seconds", "lease_seconds must be an integer between 30 and 3600")
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")

        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            if parse_timestamp(message["expires_at"]) <= self.now():
                raise APIError(410, "message_expired", "Expired messages can no longer be claimed")
            channel = self._get_channel_by_id(conn, message["channel_id"])
            if channel["mode"] != "claimable":
                raise APIError(409, "channel_not_claimable", "Only messages in claimable channels can be claimed")
            self._check_channel_access(conn, channel, key_id, team_id=team_id)
            actor = self._resolve_or_create_actor(conn, actor_identifier, key_id)
            now = self.now()
            previous_holder_id = message["claimed_by_actor_id"]
            if previous_holder_id:
                ack_exists = conn.execute(
                    "SELECT 1 FROM message_events WHERE message_id = ? AND event_type = 'ack' LIMIT 1",
                    (message_id,),
                ).fetchone()
                existing_lease = message["lease_expires_at"]
                lease_expired = existing_lease is not None and parse_timestamp(existing_lease) <= now
                # Allow re-leasing only an expired, un-acked claim (crash recovery).
                if ack_exists or not lease_expired:
                    raise APIError(409, "already_claimed", "This message has already been claimed")

            claimed_at = to_timestamp(now)
            lease_token = str(uuid.uuid4())
            lease_expires_at = to_timestamp(now + timedelta(seconds=lease_seconds))

            cursor = conn.execute(
                "UPDATE messages SET claimed_by_actor_id = ?, claimed_at = ?, lease_token = ?, lease_expires_at = ? "
                "WHERE id = ? AND (claimed_by_actor_id IS NULL OR (lease_expires_at IS NOT NULL AND lease_expires_at <= ?))",
                (actor["id"], claimed_at, lease_token, lease_expires_at, message_id, claimed_at),
            )
            if cursor.rowcount == 0:
                raise APIError(409, "already_claimed", "This message has already been claimed")
            if previous_holder_id and previous_holder_id != actor["id"]:
                metadata = {**metadata, "reclaimed_from": previous_holder_id}
            conn.execute(
                "INSERT INTO message_events (id, message_id, channel_id, actor_id, event_type, metadata, occurred_at) VALUES (?, ?, ?, ?, 'claim', ?, ?)",
                (str(uuid.uuid4()), message_id, channel["id"], actor["id"], json.dumps(metadata, sort_keys=True), claimed_at),
            )
            conn.commit()
            refreshed = self._get_message(conn, message_id)
            return {
                "lease_token": lease_token,
                "expires_at": lease_expires_at,
                "message": self._serialize_message(conn, refreshed),
            }

    def heartbeat_lease(self, lease_token: str, payload: dict[str, Any], key_id: str) -> dict[str, Any]:
        lease_seconds = payload.get("lease_seconds", 300)
        if not isinstance(lease_seconds, int) or lease_seconds < 30 or lease_seconds > 3600:
            raise APIError(422, "invalid_lease_seconds", "lease_seconds must be an integer between 30 and 3600")

        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE lease_token = ?", (lease_token,)
            ).fetchone()
            if row is None:
                raise APIError(410, "lease_expired", "Lease not found or already expired")
            now = self.now()
            lease_expires_at_str = row["lease_expires_at"]
            if lease_expires_at_str is None or parse_timestamp(lease_expires_at_str) <= now:
                raise APIError(410, "lease_expired", "Lease has already expired")
            new_expires_at = to_timestamp(now + timedelta(seconds=lease_seconds))
            conn.execute(
                "UPDATE messages SET lease_expires_at = ? WHERE lease_token = ?",
                (new_expires_at, lease_token),
            )
            conn.commit()
            return {"lease_token": lease_token, "expires_at": new_expires_at}

    def reclaim_expired_leases(self) -> int:
        """Release messages whose lease expired without an ack back into the
        unclaimed pool, so another agent can pick them up. This is what makes
        the 'auto-released if the holder crashes' guarantee true even when no
        new claimer happens to race in. Returns the number reclaimed."""
        now = to_timestamp(self.now())
        reclaimed = 0
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, channel_id, claimed_by_actor_id FROM messages "
                "WHERE lease_expires_at IS NOT NULL AND lease_expires_at <= ? "
                "AND claimed_by_actor_id IS NOT NULL "
                "AND id NOT IN (SELECT message_id FROM message_events WHERE event_type = 'ack')",
                (now,),
            ).fetchall()
            for row in rows:
                cur = conn.execute(
                    "UPDATE messages SET claimed_by_actor_id = NULL, claimed_at = NULL, lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = ? AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                    (row["id"], now),
                )
                if cur.rowcount:
                    reclaimed += 1
            conn.commit()
        return reclaimed

    def delete_message(self, message_id: str, key_id: str, team_id: str | None = None) -> None:
        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            channel = self._get_channel_by_id(conn, message["channel_id"])
            self._check_channel_access(conn, channel, key_id, team_id=team_id)
            if message["claimed_by_actor_id"]:
                raise APIError(409, "message_claimed", "Cannot retract a message that has already been claimed")
            # Archive before deleting (item5 pattern) — use a standalone audit run
            run_id = str(uuid.uuid4())
            started_at = to_timestamp(self.now())
            conn.execute(
                """
                INSERT INTO audit_cleanup_runs (id, started_at, finished_at, status, archived_messages, purged_messages, archived_invitations, purged_invitations, failure_message)
                VALUES (?, ?, NULL, 'running', 0, 0, 0, 0, NULL)
                """,
                (run_id, started_at),
            )
            audit_message_id = str(uuid.uuid4())
            actor_name = self._actor_name(conn, message["actor_id"])
            claimed_by_actor_name = self._actor_name(conn, message["claimed_by_actor_id"])
            channel_snapshot = json.dumps(dict(channel), sort_keys=True)
            archived_at = to_timestamp(self.now())
            conn.execute(
                """
                INSERT INTO audit_messages (
                    id, run_id, live_message_id, live_channel_id, owner_id, actor_id, actor_name, actor_label, content, metadata, created_at, expires_at,
                    claimed_by_actor_id, claimed_by_actor_name, claimed_at, channel_snapshot_json, archived_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_message_id,
                    run_id,
                    message["id"],
                    message["channel_id"],
                    channel["owner_id"],
                    message["actor_id"],
                    actor_name,
                    message["actor_label"],
                    message["content"],
                    message["metadata"],
                    message["created_at"],
                    message["expires_at"],
                    message["claimed_by_actor_id"],
                    claimed_by_actor_name,
                    message["claimed_at"],
                    channel_snapshot,
                    archived_at,
                ),
            )
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            finished_at = to_timestamp(self.now())
            conn.execute(
                """
                UPDATE audit_cleanup_runs
                SET finished_at = ?, status = 'completed', archived_messages = 1, purged_messages = 1
                WHERE id = ?
                """,
                (finished_at, run_id),
            )
            conn.commit()

    def delete_channel(self, channel_identifier: str, key_id: str, team_id: str | None = None) -> None:
        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            if key_id != channel["owner_key_id"]:
                raise APIError(403, "forbidden", "Only the channel owner can delete a channel")
            # ON DELETE CASCADE handles messages, members, invitations, events
            conn.execute("DELETE FROM channels WHERE id = ?", (channel["id"],))
            conn.commit()

    def create_channel_invitation(self, channel_identifier: str, owner_id: str, key_id: str, team_id: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            created_at = self.now()
            invitation_id = secrets.token_urlsafe(18)
            expires_at = created_at + timedelta(hours=24)
            conn.execute(
                """
                INSERT INTO channel_invitations (id, channel_id, owner_id, created_by_key_id, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    invitation_id,
                    channel["id"],
                    owner_id,
                    key_id,
                    to_timestamp(created_at),
                    to_timestamp(expires_at),
                ),
            )
            conn.commit()
            invitation = self._get_invitation(conn, invitation_id)
            return self._serialize_invitation(conn, invitation)

    def get_channel_invitation(self, invitation_id: str, key_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            invitation = self._get_active_invitation(conn, invitation_id)
            self._grant_channel_access(conn, invitation["channel_id"], key_id, invitation_id=invitation_id)
            self._record_event(conn, invitation["channel_id"], "invitation_resolved", key_id, subject_key_id=key_id, invitation_id=invitation_id)
            conn.commit()
            return self._serialize_invitation(conn, invitation)

    def revoke_channel_invitation(self, invitation_id: str, key_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            invitation = self._get_invitation(conn, invitation_id)
            if invitation["revoked_at"] is None:
                conn.execute(
                    "UPDATE channel_invitations SET revoked_at = ? WHERE id = ?",
                    (to_timestamp(self.now()), invitation_id),
                )
                self._record_event(conn, invitation["channel_id"], "invitation_revoked", key_id, invitation_id=invitation_id)
                conn.commit()
            refreshed = self._get_invitation(conn, invitation_id)
            return self._serialize_invitation(conn, refreshed)

    def list_channel_members(self, channel_identifier: str, key_id: str, team_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            if key_id != channel["owner_key_id"]:
                raise APIError(403, "forbidden", "Only the channel owner can list members")
            rows = conn.execute(
                "SELECT * FROM channel_members WHERE channel_id = ? ORDER BY granted_at ASC",
                (channel["id"],),
            ).fetchall()
            return [self._serialize_member(row) for row in rows]

    def add_channel_member(self, channel_identifier: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        member_key_id = self._required_string(payload, "key_id")
        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            if key_id != channel["owner_key_id"]:
                raise APIError(403, "forbidden", "Only the channel owner can add members")
            self._grant_channel_access(conn, channel["id"], member_key_id)
            self._record_event(conn, channel["id"], "member_added", key_id, subject_key_id=member_key_id)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM channel_members WHERE channel_id = ? AND key_id = ?",
                (channel["id"], member_key_id),
            ).fetchone()
            return self._serialize_member(row)

    def remove_channel_member(self, channel_identifier: str, member_key_id: str, key_id: str, team_id: str | None = None) -> None:
        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            if key_id != channel["owner_key_id"]:
                raise APIError(403, "forbidden", "Only the channel owner can remove members")
            if member_key_id == channel["owner_key_id"]:
                raise APIError(409, "cannot_remove_owner", "Cannot remove the channel owner from members")
            conn.execute(
                "DELETE FROM channel_members WHERE channel_id = ? AND key_id = ?",
                (channel["id"], member_key_id),
            )
            self._record_event(conn, channel["id"], "member_removed", key_id, subject_key_id=member_key_id)
            conn.commit()

    def list_channel_events(self, channel_identifier: str, since: str | None, limit: int | None, key_id: str, team_id: str | None = None) -> dict[str, Any]:
        page_size = 50 if limit is None else limit
        if page_size < 1 or page_size > 100:
            raise APIError(422, "invalid_limit", "limit must be between 1 and 100")

        with self.connect() as conn:
            channel = self._resolve_channel(conn, channel_identifier, key_id=key_id, team_id=team_id)
            if key_id != channel["owner_key_id"]:
                raise APIError(403, "forbidden", "Only the channel owner can read events")
            now = to_timestamp(self.now())
            params: list[Any] = [channel["id"], now]
            since_clause = ""
            since_dt = parse_timestamp_or_none(since)
            if since_dt is not None:
                since_clause = "AND created_at > ?"
                params.append(to_timestamp(since_dt))
            params.append(page_size)
            rows = conn.execute(
                f"""
                SELECT * FROM channel_events
                WHERE channel_id = ?
                  AND expires_at > ?
                  {since_clause}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            items = [self._serialize_event(row) for row in rows]
            next_cursor = items[-1]["created_at"] if items else since
            return {"data": items, "limit": page_size, "next_cursor": next_cursor}

    def cleanup_expired_messages(self) -> int:
        summary = self.archive_and_cleanup_expired_records()
        return int(summary["purged_messages"])

    def archive_and_cleanup_expired_records(self) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        started_at = to_timestamp(self.now())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_cleanup_runs (id, started_at, finished_at, status, archived_messages, purged_messages, archived_invitations, purged_invitations, failure_message)
                VALUES (?, ?, NULL, 'running', 0, 0, 0, 0, NULL)
                """,
                (run_id, started_at),
            )
            conn.commit()

        try:
            with self.connect() as conn:
                summary = self._archive_cleanup_transaction(conn, run_id)
                conn.execute(
                    """
                    UPDATE audit_cleanup_runs
                    SET finished_at = ?, status = 'completed',
                        archived_messages = ?, purged_messages = ?,
                        archived_invitations = ?, purged_invitations = ?,
                        archived_channel_events = ?, purged_channel_events = ?,
                        failure_message = NULL
                    WHERE id = ?
                    """,
                    (
                        to_timestamp(self.now()),
                        summary["archived_messages"],
                        summary["purged_messages"],
                        summary["archived_invitations"],
                        summary["purged_invitations"],
                        summary["archived_channel_events"],
                        summary["purged_channel_events"],
                        run_id,
                    ),
                )
                conn.commit()
                summary["run_id"] = run_id
                return summary
        except Exception as exc:
            with self.connect() as conn:
                conn.execute(
                    """
                    UPDATE audit_cleanup_runs
                    SET finished_at = ?, status = 'failed', failure_message = ?
                    WHERE id = ?
                    """,
                    (to_timestamp(self.now()), str(exc), run_id),
                )
                conn.commit()
            raise

    def list_audit_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM audit_cleanup_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_idempotent_response(self, cache_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            now = to_timestamp(self.now())
            row = conn.execute(
                "SELECT response_status, response_body FROM idempotency_cache WHERE id = ? AND expires_at > ?",
                (cache_key, now),
            ).fetchone()
            if row is None:
                return None
            return {"status": row["response_status"], "body": row["response_body"]}

    def cache_idempotent_response(self, cache_key: str, status: int, body: str) -> None:
        now = self.now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO idempotency_cache (id, response_status, response_body, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cache_key, status, body, to_timestamp(now), to_timestamp(now + timedelta(hours=24))),
            )
            conn.commit()

    def cleanup_idempotency_cache(self) -> int:
        with self.connect() as conn:
            count = conn.execute(
                "DELETE FROM idempotency_cache WHERE expires_at <= ?",
                (to_timestamp(self.now()),),
            ).rowcount
            conn.commit()
            return count

    def list_audit_messages(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT live_message_id, live_channel_id, owner_id, actor_name, content, created_at, expires_at, archived_at
                FROM audit_messages
                ORDER BY archived_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _archive_cleanup_transaction(self, conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
        now = to_timestamp(self.now())
        expired_messages = conn.execute(
            """
            SELECT *
            FROM messages
            WHERE expires_at <= ?
            ORDER BY expires_at ASC
            """,
            (now,),
        ).fetchall()
        expired_invitations = conn.execute(
            """
            SELECT *
            FROM channel_invitations
            WHERE expires_at <= ? OR revoked_at IS NOT NULL
            ORDER BY expires_at ASC
            """,
            (now,),
        ).fetchall()

        channel_snapshots: dict[str, str] = {}
        archived_at = to_timestamp(self.now())
        archived_messages = 0
        archived_invitations = 0

        for row in expired_messages:
            channel_snapshot_json = self._ensure_audit_channel_snapshot(
                conn,
                run_id=run_id,
                channel_id=row["channel_id"],
                archived_at=archived_at,
                cache=channel_snapshots,
            )
            audit_message_id = str(uuid.uuid4())
            actor_name = self._actor_name(conn, row["actor_id"])
            claimed_by_actor_name = self._actor_name(conn, row["claimed_by_actor_id"])
            channel = self._get_channel_by_id(conn, row["channel_id"])
            conn.execute(
                """
                INSERT INTO audit_messages (
                    id, run_id, live_message_id, live_channel_id, owner_id, actor_id, actor_name, actor_label, content, metadata, created_at, expires_at,
                    claimed_by_actor_id, claimed_by_actor_name, claimed_at, channel_snapshot_json, archived_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_message_id,
                    run_id,
                    row["id"],
                    row["channel_id"],
                    channel["owner_id"],
                    row["actor_id"],
                    actor_name,
                    row["actor_label"],
                    row["content"],
                    row["metadata"],
                    row["created_at"],
                    row["expires_at"],
                    row["claimed_by_actor_id"],
                    claimed_by_actor_name,
                    row["claimed_at"],
                    channel_snapshot_json,
                    archived_at,
                ),
            )
            for event_row in conn.execute(
                """
                SELECT *
                FROM message_events
                WHERE message_id = ?
                ORDER BY occurred_at ASC
                """,
                (row["id"],),
            ).fetchall():
                conn.execute(
                    """
                    INSERT INTO audit_message_events (id, audit_message_id, live_event_id, event_type, actor_id, actor_name, metadata, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        audit_message_id,
                        event_row["id"],
                        event_row["event_type"],
                        event_row["actor_id"],
                        self._actor_name(conn, event_row["actor_id"]),
                        event_row["metadata"],
                        event_row["occurred_at"],
                    ),
                )
            archived_messages += 1

        for row in expired_invitations:
            channel_snapshot_json = self._ensure_audit_channel_snapshot(
                conn,
                run_id=run_id,
                channel_id=row["channel_id"],
                archived_at=archived_at,
                cache=channel_snapshots,
            )
            conn.execute(
                """
                INSERT INTO audit_channel_invitations (
                    id, run_id, live_invitation_id, live_channel_id, owner_id, created_by_key_id, created_at, expires_at, revoked_at,
                    channel_snapshot_json, archived_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    row["id"],
                    row["channel_id"],
                    row["owner_id"],
                    row["created_by_key_id"],
                    row["created_at"],
                    row["expires_at"],
                    row["revoked_at"],
                    channel_snapshot_json,
                    archived_at,
                ),
            )
            archived_invitations += 1

        expired_channel_events = conn.execute(
            "SELECT * FROM channel_events WHERE expires_at <= ? ORDER BY expires_at ASC",
            (now,),
        ).fetchall()
        archived_channel_events = 0
        for row in expired_channel_events:
            conn.execute(
                """
                INSERT INTO audit_channel_events (
                    id, run_id, live_event_id, live_channel_id, event_type,
                    actor_key_id, subject_key_id, invitation_id, metadata,
                    created_at, expires_at, archived_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    row["id"],
                    row["channel_id"],
                    row["event_type"],
                    row["actor_key_id"],
                    row["subject_key_id"],
                    row["invitation_id"],
                    row["metadata"],
                    row["created_at"],
                    row["expires_at"],
                    archived_at,
                ),
            )
            archived_channel_events += 1

        # Release expired leases (revert to unclaimed)
        conn.execute(
            """
            UPDATE messages SET claimed_by_actor_id = NULL, claimed_at = NULL, lease_token = NULL, lease_expires_at = NULL
            WHERE lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
              AND id NOT IN (SELECT message_id FROM message_events WHERE event_type = 'ack')
            """,
            (now,),
        )
        purged_messages = conn.execute("DELETE FROM messages WHERE expires_at <= ?", (now,)).rowcount
        purged_invitations = conn.execute(
            "DELETE FROM channel_invitations WHERE expires_at <= ? OR revoked_at IS NOT NULL",
            (now,),
        ).rowcount
        purged_channel_events = conn.execute(
            "DELETE FROM channel_events WHERE expires_at <= ?", (now,)
        ).rowcount
        # Clean up expired sessions
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        # Clean up delivered or permanently failed webhooks older than 7 days
        import time as _time
        week_ago = int(_time.time()) - 7 * 86400
        conn.execute(
            "DELETE FROM pending_webhooks WHERE delivered_at IS NOT NULL AND delivered_at < ?",
            (week_ago,),
        )
        conn.execute(
            "DELETE FROM pending_webhooks WHERE attempts >= 5 AND created_at < ?",
            (week_ago,),
        )
        conn.execute("DELETE FROM idempotency_cache WHERE expires_at <= ?", (now,))

        # Purge archived messages past their channel's retention window.
        # Without this the audit_messages archive grows forever; the
        # /history endpoint exposes exactly this retention window.
        purged_audit_messages = 0
        archived_channel_ids = [
            r["live_channel_id"]
            for r in conn.execute(
                "SELECT DISTINCT live_channel_id FROM audit_messages"
            ).fetchall()
        ]
        for channel_id in archived_channel_ids:
            channel_row = conn.execute(
                "SELECT retention_days FROM channels WHERE id = ?", (channel_id,)
            ).fetchone()
            retention_days = channel_row["retention_days"] if channel_row is not None else 7
            cutoff = to_timestamp(self.now() - timedelta(days=retention_days))
            purged_audit_messages += conn.execute(
                "DELETE FROM audit_messages WHERE live_channel_id = ? AND archived_at <= ?",
                (channel_id, cutoff),
            ).rowcount

        return {
            "archived_messages": archived_messages,
            "purged_messages": purged_messages,
            "archived_invitations": archived_invitations,
            "purged_invitations": purged_invitations,
            "archived_channel_events": archived_channel_events,
            "purged_channel_events": purged_channel_events,
            "purged_audit_messages": purged_audit_messages,
        }

    def _record_event(
        self,
        conn: sqlite3.Connection,
        channel_id: str,
        event_type: str,
        actor_key_id: str,
        subject_key_id: str | None = None,
        invitation_id: str | None = None,
    ) -> None:
        now = self.now()
        conn.execute(
            """
            INSERT INTO channel_events (id, channel_id, event_type, actor_key_id, subject_key_id, invitation_id, metadata, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                str(uuid.uuid4()),
                channel_id,
                event_type,
                actor_key_id,
                subject_key_id,
                invitation_id,
                to_timestamp(now),
                to_timestamp(now + timedelta(hours=24)),
            ),
        )

    def _serialize_event(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "event_type": row["event_type"],
            "actor_key_id": row["actor_key_id"],
            "subject_key_id": row["subject_key_id"],
            "invitation_id": row["invitation_id"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }

    def _resolve_channel(self, conn: sqlite3.Connection, identifier: str, key_id: str | None = None, team_id: str | None = None) -> sqlite3.Row:
        channel = conn.execute("SELECT * FROM channels WHERE id = ?", (identifier,)).fetchone()
        if channel is not None:
            if key_id is not None:
                self._check_channel_access(conn, channel, key_id, team_id=team_id)
            return channel
        channel = conn.execute(
            """
            SELECT c.*
            FROM channel_aliases ca
            JOIN channels c ON c.id = ca.channel_id
            WHERE ca.alias = ?
            """,
            (identifier,),
        ).fetchone()
        if channel is None:
            raise APIError(404, "channel_not_found", f"Unknown channel '{identifier}'")
        if key_id is not None:
            self._check_channel_access(conn, channel, key_id, team_id=team_id)
        return channel

    def _check_channel_access(self, conn: sqlite3.Connection, channel: sqlite3.Row, key_id: str, team_id: str | None = None) -> None:
        if channel["access"] == "open":
            return
        if key_id == channel["owner_key_id"]:
            return
        # Team-scoped access: if the channel belongs to a team and the requester is on that team, allow
        channel_team_id = channel["team_id"] if "team_id" in channel.keys() else None
        if channel_team_id and team_id and channel_team_id == team_id:
            return
        row = conn.execute(
            "SELECT id FROM channel_members WHERE channel_id = ? AND key_id = ?",
            (channel["id"], key_id),
        ).fetchone()
        if row is None:
            raise APIError(403, "channel_access_denied", "You are not a member of this channel")

    def _grant_channel_access(self, conn: sqlite3.Connection, channel_id: str, key_id: str, invitation_id: str | None = None) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO channel_members (id, channel_id, key_id, granted_at, granted_via_invitation_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), channel_id, key_id, to_timestamp(self.now()), invitation_id),
        )

    def _resolve_actor(self, conn: sqlite3.Connection, identifier: str) -> sqlite3.Row:
        actor = conn.execute("SELECT * FROM actors WHERE id = ?", (identifier,)).fetchone()
        if actor is not None:
            return actor
        actor = conn.execute(
            """
            SELECT a.*
            FROM actor_aliases aa
            JOIN actors a ON a.id = aa.actor_id
            WHERE aa.alias = ?
            """,
            (identifier,),
        ).fetchone()
        if actor is None:
            raise APIError(404, "actor_not_found", f"Unknown actor '{identifier}'")
        return actor

    def _owner_for_key(self, conn: sqlite3.Connection, key_id: str) -> tuple[str | None, str | None]:
        row = conn.execute(
            "SELECT owner_id, agent_label FROM api_keys WHERE key_id = ?",
            (key_id,),
        ).fetchone()
        if row is None:
            return None, None
        return row["owner_id"], row["agent_label"]

    def _resolve_or_create_actor(
        self, conn: sqlite3.Connection, identifier: str | None, key_id: str
    ) -> sqlite3.Row:
        """Resolve an actor by id, alias, or name (name scoped to the key's
        owner). If `identifier` is empty, fall back to the key's own default
        actor (named after the agent label). The actor is auto-created when it
        does not exist yet, so callers never have to pre-register one — claim,
        ack and release just work with a plain name or with no actor at all."""
        owner_id, agent_label = self._owner_for_key(conn, key_id)
        name = (identifier or "").strip()
        if not name:
            name = agent_label or owner_id or "default"

        # 1. exact id
        actor = conn.execute("SELECT * FROM actors WHERE id = ?", (name,)).fetchone()
        if actor is not None:
            return actor
        # 2. alias
        actor = conn.execute(
            """
            SELECT a.*
            FROM actor_aliases aa
            JOIN actors a ON a.id = aa.actor_id
            WHERE aa.alias = ?
            """,
            (name,),
        ).fetchone()
        if actor is not None:
            return actor
        # 3. name, scoped to this key's owner (avoids cross-tenant collisions)
        if owner_id is not None:
            actor = conn.execute(
                "SELECT * FROM actors WHERE name = ? AND owner_id = ? ORDER BY created_at LIMIT 1",
                (name, owner_id),
            ).fetchone()
            if actor is not None:
                return actor
        # 4. create it (within the caller's transaction)
        actor_id = str(uuid.uuid4())
        now = to_timestamp(self.now())
        conn.execute(
            """
            INSERT INTO actors (id, owner_key_id, owner_id, name, description, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, '', '{}', ?, ?)
            """,
            (actor_id, key_id, owner_id, name, now, now),
        )
        return conn.execute("SELECT * FROM actors WHERE id = ?", (actor_id,)).fetchone()

    def _replace_channel_links(self, conn: sqlite3.Connection, channel_id: str, related_channels: Any) -> None:
        if related_channels is None:
            return
        if not isinstance(related_channels, list):
            raise APIError(422, "invalid_related_channels", "related_channels must be an array")
        conn.execute("DELETE FROM channel_links WHERE channel_id = ?", (channel_id,))
        now = to_timestamp(self.now())
        for related_identifier in related_channels:
            related = self._resolve_channel(conn, self._optional_string(related_identifier))
            if related["id"] == channel_id:
                continue
            conn.execute(
                """
                INSERT INTO channel_links (id, channel_id, related_channel_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), channel_id, related["id"], now),
            )

    def _insert_alias(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        foreign_key_column: str,
        foreign_key_value: str,
        alias: str,
    ) -> None:
        existing = conn.execute(f"SELECT id FROM {table_name} WHERE alias = ?", (alias,)).fetchone()
        if existing is not None:
            raise APIError(409, "alias_conflict", f"Alias '{alias}' already exists")
        conn.execute(
            f"""
            INSERT INTO {table_name} (id, {foreign_key_column}, alias, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), foreign_key_value, alias, to_timestamp(self.now())),
        )

    def _serialize_channel(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        aliases = [
            alias_row["alias"]
            for alias_row in conn.execute(
                "SELECT alias FROM channel_aliases WHERE channel_id = ? ORDER BY alias ASC",
                (row["id"],),
            ).fetchall()
        ]
        related = [
            {
                "id": link_row["id"],
                "name": link_row["name"],
                "mode": link_row["mode"],
            }
            for link_row in conn.execute(
                """
                SELECT c.id, c.name, c.mode
                FROM channel_links cl
                JOIN channels c ON c.id = cl.related_channel_id
                WHERE cl.channel_id = ?
                ORDER BY c.name ASC
                """,
                (row["id"],),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "created_by_key_id": row["owner_key_id"],
            "name": row["name"],
            "mode": row["mode"],
            "access": row["access"],
            "description": row["description"],
            "metadata_schema": json.loads(row["metadata_schema"]),
            "pinned_message": row["pinned_message"],
            "ttl_seconds": row["ttl_seconds"] if "ttl_seconds" in row.keys() else 86400,
            "retention_days": row["retention_days"] if "retention_days" in row.keys() else 7,
            "max_messages": row["max_messages"] if "max_messages" in row.keys() else None,
            "max_writes_per_minute": row["max_writes_per_minute"] if "max_writes_per_minute" in row.keys() else None,
            "paused": bool(row["paused"]) if "paused" in row.keys() else False,
            "aliases": aliases,
            "related_channels": related,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _serialize_actor(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        aliases = [
            alias_row["alias"]
            for alias_row in conn.execute(
                "SELECT alias FROM actor_aliases WHERE actor_id = ? ORDER BY alias ASC",
                (row["id"],),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "created_by_key_id": row["owner_key_id"],
            "name": row["name"],
            "description": row["description"],
            "metadata": json.loads(row["metadata"]),
            "aliases": aliases,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _serialize_message(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        actor = None
        if row["actor_id"]:
            actor_row = self._get_actor_by_id(conn, row["actor_id"])
            actor = {"id": actor_row["id"], "name": actor_row["name"]}

        claimed_by = None
        if row["claimed_by_actor_id"]:
            claimed_actor = self._get_actor_by_id(conn, row["claimed_by_actor_id"])
            claimed_by = {"id": claimed_actor["id"], "name": claimed_actor["name"]}

        acks = [
            {
                "id": ack_row["id"],
                "name": ack_row["name"],
                "occurred_at": ack_row["occurred_at"],
            }
            for ack_row in conn.execute(
                """
                SELECT a.id, a.name, me.occurred_at
                FROM message_events me
                JOIN actors a ON a.id = me.actor_id
                WHERE me.message_id = ? AND me.event_type = 'ack'
                ORDER BY me.occurred_at ASC
                """,
                (row["id"],),
            ).fetchall()
        ]
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "actor": actor,
            "actor_label": row["actor_label"],
            "content": row["content"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "claimed_by": claimed_by,
            "claimed_at": row["claimed_at"],
            "acknowledged_by": acks,
            "active": parse_timestamp(row["expires_at"]) > self.now(),
        }

    def _serialize_audit_message(self, row: sqlite3.Row) -> dict[str, Any]:
        actor = None
        if row["actor_id"]:
            actor = {"id": row["actor_id"], "name": row["actor_name"]}
        claimed_by = None
        if row["claimed_by_actor_id"]:
            claimed_by = {"id": row["claimed_by_actor_id"], "name": row["claimed_by_actor_name"]}
        return {
            "id": row["live_message_id"],
            "channel_id": row["live_channel_id"],
            "actor": actor,
            "actor_label": row["actor_label"],
            "content": row["content"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "claimed_by": claimed_by,
            "claimed_at": row["claimed_at"],
            "archived_at": row["archived_at"],
            "active": False,
        }

    def _serialize_member(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "channel_id": row["channel_id"],
            "key_id": row["key_id"],
            "granted_at": row["granted_at"],
            "granted_via_invitation_id": row["granted_via_invitation_id"],
        }

    def _serialize_invitation(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        channel = self._get_channel_by_id(conn, row["channel_id"])
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "created_by_key_id": row["created_by_key_id"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "active": row["revoked_at"] is None and parse_timestamp(row["expires_at"]) > self.now(),
            "channel": {
                "id": channel["id"],
                "name": channel["name"],
                "mode": channel["mode"],
                "description": channel["description"],
            },
        }

    def _get_channel_by_id(self, conn: sqlite3.Connection, channel_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if row is None:
            raise APIError(404, "channel_not_found", f"Unknown channel '{channel_id}'")
        return row

    def _get_actor_by_id(self, conn: sqlite3.Connection, actor_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM actors WHERE id = ?", (actor_id,)).fetchone()
        if row is None:
            raise APIError(404, "actor_not_found", f"Unknown actor '{actor_id}'")
        return row

    def _get_message(self, conn: sqlite3.Connection, message_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise APIError(404, "message_not_found", f"Unknown message '{message_id}'")
        return row

    def _get_invitation(self, conn: sqlite3.Connection, invitation_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM channel_invitations WHERE id = ?", (invitation_id,)).fetchone()
        if row is None:
            raise APIError(404, "invitation_not_found", f"Unknown invitation '{invitation_id}'")
        return row

    def _get_active_invitation(self, conn: sqlite3.Connection, invitation_id: str) -> sqlite3.Row:
        row = self._get_invitation(conn, invitation_id)
        if row["revoked_at"] is not None:
            raise APIError(410, "invitation_revoked", "This invitation is no longer valid")
        if parse_timestamp(row["expires_at"]) <= self.now():
            raise APIError(410, "invitation_expired", "This invitation has expired")
        return row

    def _ensure_audit_channel_snapshot(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        channel_id: str,
        archived_at: str,
        cache: dict[str, str],
    ) -> str:
        if channel_id in cache:
            return cache[channel_id]

        channel = self._get_channel_by_id(conn, channel_id)
        snapshot_json = json.dumps(self._serialize_channel(conn, channel), sort_keys=True)
        conn.execute(
            """
            INSERT INTO audit_channels (id, run_id, live_channel_id, owner_id, created_by_key_id, snapshot_json, archived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                channel_id,
                channel["owner_id"],
                channel["owner_key_id"],
                snapshot_json,
                archived_at,
            ),
        )
        cache[channel_id] = snapshot_json
        return snapshot_json

    def _actor_name(self, conn: sqlite3.Connection, actor_id: str | None) -> str | None:
        if actor_id is None:
            return None
        actor = conn.execute("SELECT name FROM actors WHERE id = ?", (actor_id,)).fetchone()
        return actor["name"] if actor is not None else None

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _validate_optional_count(self, value: Any, field_name: str) -> int | None:
        """Validate a nullable positive-integer channel limit. None means
        'no limit'; otherwise it must be an int in [1, 1_000_000]."""
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 1_000_000:
            raise APIError(
                422,
                f"invalid_{field_name}",
                f"{field_name} must be an integer between 1 and 1000000, or null",
            )
        return value

    def _required_string(self, payload: dict[str, Any], field_name: str) -> str:
        if field_name not in payload:
            raise APIError(422, "missing_field", f"'{field_name}' is required")
        return self._optional_string(payload[field_name], field_name=field_name)

    def _optional_string(
        self,
        value: Any,
        field_name: str | None = None,
        default: str | None = None,
        allow_none: bool = False,
    ) -> str | None:
        if value is None:
            if allow_none:
                return None
            if default is not None:
                return default
            field = field_name or "value"
            raise APIError(422, "invalid_string", f"'{field}' must be a non-empty string")
        if not isinstance(value, str) or not value.strip():
            field = field_name or "value"
            raise APIError(422, "invalid_string", f"'{field}' must be a non-empty string")
        return value.strip()

    def _ensure_mapping(self, value: Any, field_name: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise APIError(422, "invalid_object", f"'{field_name}' must be an object")
        return value

    # --- Sessions ---

    def list_sessions(self, key_id: str) -> list[dict[str, Any]]:
        now = to_timestamp(self.now())
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE owner_key_id = ? AND expires_at > ? ORDER BY created_at DESC",
                (key_id, now),
            ).fetchall()
            return [self._serialize_session(row) for row in rows]

    def create_session(self, key_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        name = self._required_string(payload, "name")
        state = self._ensure_mapping(payload.get("state", {}), "state")
        session_id = str(uuid.uuid4())
        now = self.now()
        now_ts = to_timestamp(now)
        expires_at = to_timestamp(now + timedelta(hours=24))
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, owner_key_id, name, state, created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, key_id, name, json.dumps(state, sort_keys=True), now_ts, now_ts, expires_at),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._serialize_session(row)

    def get_session(self, session_id: str, key_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ? AND owner_key_id = ?", (session_id, key_id)).fetchone()
            if row is None:
                raise APIError(404, "session_not_found", f"Session '{session_id}' not found")
            return self._serialize_session(row)

    def patch_session(self, session_id: str, patch: dict[str, Any], key_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ? AND owner_key_id = ?", (session_id, key_id)).fetchone()
            if row is None:
                raise APIError(404, "session_not_found", f"Session '{session_id}' not found")
            current_state = json.loads(row["state"])
            new_state = {**current_state, **patch.get("state", patch)}
            now_ts = to_timestamp(self.now())
            conn.execute(
                "UPDATE sessions SET state = ?, updated_at = ? WHERE id = ?",
                (json.dumps(new_state, sort_keys=True), now_ts, session_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._serialize_session(row)

    def delete_session(self, session_id: str, key_id: str) -> None:
        with self.connect() as conn:
            result = conn.execute("DELETE FROM sessions WHERE id = ? AND owner_key_id = ?", (session_id, key_id))
            if result.rowcount == 0:
                raise APIError(404, "session_not_found", f"Session '{session_id}' not found")
            conn.commit()

    def _serialize_session(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "state": json.loads(row["state"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
        }

    # --- Webhooks ---

    def queue_webhook(self, channel_id: str, event_type: str, payload_dict: dict[str, Any], webhook_url: str, webhook_secret: str | None = None) -> None:
        import time as _time
        now_epoch = int(_time.time())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO pending_webhooks (id, channel_id, event_type, payload, webhook_url, webhook_secret, attempts, next_attempt_at, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (str(uuid.uuid4()), channel_id, event_type, json.dumps(payload_dict), webhook_url, webhook_secret, now_epoch, now_epoch),
            )
            conn.commit()

    def deliver_pending_webhooks(self) -> int:
        import hashlib
        import hmac
        import time as _time
        from urllib.request import Request as URLRequest
        from urllib.request import urlopen

        now_epoch = int(_time.time())
        delivered = 0
        with self.connect() as conn:
            due = conn.execute(
                "SELECT * FROM pending_webhooks WHERE delivered_at IS NULL AND attempts < 5 AND next_attempt_at <= ? ORDER BY next_attempt_at ASC LIMIT 50",
                (now_epoch,),
            ).fetchall()

        for row in due:
            payload_bytes = row["payload"].encode("utf-8")
            headers = {"Content-Type": "application/json", "User-Agent": "Backchannel-Webhook/1"}
            if row["webhook_secret"]:
                sig = hmac.new(row["webhook_secret"].encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()  # type: ignore[attr-defined]
                headers["X-Backchannel-Signature"] = f"sha256={sig}"
            req = URLRequest(url=row["webhook_url"], data=payload_bytes, method="POST")
            for k, v in headers.items():
                req.add_header(k, v)
            success = False
            try:
                with urlopen(req, timeout=10) as resp:
                    if resp.status < 300:
                        success = True
            except Exception:
                pass

            attempt_number = row["attempts"] + 1
            with self.connect() as conn:
                if success:
                    conn.execute(
                        "UPDATE pending_webhooks SET delivered_at = ?, attempts = ? WHERE id = ?",
                        (now_epoch, attempt_number, row["id"]),
                    )
                    delivered += 1
                else:
                    backoff = min(300, 30 * (2 ** attempt_number))
                    conn.execute(
                        "UPDATE pending_webhooks SET attempts = ?, next_attempt_at = ? WHERE id = ?",
                        (attempt_number, now_epoch + backoff, row["id"]),
                    )
                conn.commit()

        return delivered

    # --- Observability ---

    def get_observability_metrics(self, key_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            channels_owned = conn.execute(
                "SELECT COUNT(*) as n FROM channels WHERE owner_key_id = ?", (key_id,)
            ).fetchone()["n"]
            messages_sent = conn.execute(
                """
                SELECT COUNT(*) as n FROM messages m
                JOIN channels c ON m.channel_id = c.id
                WHERE c.owner_key_id = ?
                """,
                (key_id,),
            ).fetchone()["n"]
            messages_claimed = conn.execute(
                """
                SELECT COUNT(*) as n FROM messages m
                JOIN channels c ON m.channel_id = c.id
                WHERE c.owner_key_id = ? AND m.claimed_by_actor_id IS NOT NULL
                """,
                (key_id,),
            ).fetchone()["n"]
            active_sessions = conn.execute(
                "SELECT COUNT(*) as n FROM sessions WHERE owner_key_id = ? AND expires_at > ?",
                (key_id, to_timestamp(self.now())),
            ).fetchone()["n"]
            return {
                "key_id": key_id,
                "channels_owned": channels_owned,
                "messages_in_owned_channels": messages_sent,
                "messages_claimed_in_owned_channels": messages_claimed,
                "active_sessions": active_sessions,
            }

    def get_key_scopes(self, key_id: str) -> list[str] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT scopes FROM key_scopes WHERE key_id = ?", (key_id,)).fetchone()
            if row is None:
                return None
            return json.loads(row["scopes"])

    def set_key_scopes(self, key_id: str, scopes: list[str]) -> None:
        valid_scopes = {
            "messages:read", "messages:write", "messages:claim", "messages:ack",
            "channels:read", "channels:write", "channels:manage",
        }
        for s in scopes:
            if s not in valid_scopes:
                raise APIError(422, "invalid_scope", f"Unknown scope: {s}. Valid scopes: {sorted(valid_scopes)}")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO key_scopes (key_id, scopes) VALUES (?, ?) ON CONFLICT(key_id) DO UPDATE SET scopes = excluded.scopes",
                (key_id, json.dumps(sorted(scopes))),
            )
            conn.commit()

    # --- API keys -------------------------------------------------------

    def issue_api_key(
        self,
        *,
        key_id: str,
        key_hash: str,
        owner_id: str,
        agent_label: str | None,
        plan: str = "free",
        team_id: str | None = None,
        team_name: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        now = self.now()
        expires_at = None
        if ttl_seconds is not None:
            expires_at = to_timestamp(now + timedelta(seconds=ttl_seconds))
        with self.connect() as conn:
            if agent_label:
                existing = conn.execute(
                    "SELECT key_id FROM api_keys WHERE agent_label = ? AND active = 1",
                    (agent_label,),
                ).fetchone()
                if existing is not None:
                    raise APIError(
                        409,
                        "label_in_use",
                        "An active key for this agent label already exists. Use a different label.",
                    )
            conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, key_hash, owner_id, agent_label, plan, tier, active,
                    team_id, team_name, email, credit_balance_micros, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, NULL, 0, ?, ?)
                """,
                (
                    key_id, key_hash, owner_id, agent_label, plan,
                    team_id, team_name, to_timestamp(now), expires_at,
                ),
            )
            # Provision a default actor so claim/ack/release work immediately
            # and the new key sees an identity in the console. Named after the
            # agent label; claim/ack default to it when no actor is given.
            default_actor_name = agent_label or owner_id
            if default_actor_name:
                conn.execute(
                    """
                    INSERT INTO actors (id, owner_key_id, owner_id, name, description, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, '', '{}', ?, ?)
                    """,
                    (str(uuid.uuid4()), key_id, owner_id, default_actor_name, to_timestamp(now), to_timestamp(now)),
                )
            conn.commit()
        return {
            "key_id": key_id,
            "owner_id": owner_id,
            "agent_label": agent_label,
            "plan": plan,
            "team_id": team_id,
            "team_name": team_name,
            "expires_at": expires_at,
        }

    def lookup_api_key(self, *, key_id: str, key_hash: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT key_id, owner_id, agent_label, plan, active, team_id, team_name,
                       expires_at, created_at
                FROM api_keys
                WHERE key_id = ? AND key_hash = ?
                """,
                (key_id, key_hash),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (to_timestamp(self.now()), key_id),
            )
            conn.commit()
            return {
                "key_id": row["key_id"],
                "owner_id": row["owner_id"],
                "agent_label": row["agent_label"],
                "plan": row["plan"],
                "active": bool(row["active"]),
                "team_id": row["team_id"],
                "team_name": row["team_name"],
                "expires_at": parse_timestamp(row["expires_at"]) if row["expires_at"] else None,
                "created_at": row["created_at"],
            }

    def revoke_api_key(self, key_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE api_keys SET active = 0 WHERE key_id = ?", (key_id,))
            conn.commit()

    def get_api_key_record(self, key_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT key_id, owner_id, agent_label, plan, active, expires_at, created_at, last_used_at FROM api_keys WHERE key_id = ?",
                (key_id,),
            ).fetchone()
            return dict(row) if row else None

    # --- Sandbox firehose channel + heartbeat bot -----------------------

    def ensure_heartbeat_bot_key(self) -> str:
        """Return the key_id of the sandbox heartbeat bot, minting one the
        first time. Idempotent — the labelled key is the bot's identity."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT key_id FROM api_keys WHERE agent_label = ? AND active = 1",
                (HEARTBEAT_BOT_LABEL,),
            ).fetchone()
            if row is not None:
                return row["key_id"]
        from backchannel.auth import hash_key, mint_raw_key

        key_id, _secret, raw_key = mint_raw_key()
        self.issue_api_key(
            key_id=key_id,
            key_hash=hash_key(raw_key),
            owner_id="backchannel",
            agent_label=HEARTBEAT_BOT_LABEL,
            plan="free",
            ttl_seconds=None,
        )
        return key_id

    def ensure_sandbox_channel(
        self,
        owner_key_id: str,
        *,
        ttl_seconds: int = 600,
        max_messages: int = 200,
        max_writes_per_minute: int = 60,
    ) -> str:
        """Idempotently provision the public 'sandbox' broadcast channel and
        return its id. Owned by the heartbeat bot key so there are no
        synthetic identities in channel ownership. The abuse-control limits
        are (re)applied on every call so an operator can retune them by
        restarting the worker — but `paused` is never touched here, so an
        operator's kill switch survives a restart."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT channel_id FROM channel_aliases WHERE alias = ?",
                (SANDBOX_CHANNEL_ALIAS,),
            ).fetchone()
        if row is not None:
            channel_id = row["channel_id"]
            with self.connect() as conn:
                conn.execute(
                    "UPDATE channels SET ttl_seconds = ?, max_messages = ?, max_writes_per_minute = ?, updated_at = ? WHERE id = ?",
                    (ttl_seconds, max_messages, max_writes_per_minute, to_timestamp(self.now()), channel_id),
                )
                conn.commit()
            return channel_id
        try:
            channel = self.create_channel(
                {
                    "name": "sandbox",
                    "mode": "broadcast",
                    "access": "open",
                    "description": "Public firehose — any agent can post and read here to test the Backchannel protocol.",
                    "ttl_seconds": ttl_seconds,
                    "max_messages": max_messages,
                    "max_writes_per_minute": max_writes_per_minute,
                },
                owner_id="backchannel",
                key_id=owner_key_id,
            )
            self.create_channel_alias(
                channel["id"], {"alias": SANDBOX_CHANNEL_ALIAS}, key_id=owner_key_id
            )
            return channel["id"]
        except APIError as exc:
            # A concurrent provisioner won the race — re-resolve the alias.
            if exc.status == 409:
                with self.connect() as conn:
                    row = conn.execute(
                        "SELECT channel_id FROM channel_aliases WHERE alias = ?",
                        (SANDBOX_CHANNEL_ALIAS,),
                    ).fetchone()
                if row is not None:
                    return row["channel_id"]
            raise

    def set_channel_paused(self, identifier: str, paused: bool) -> dict[str, Any]:
        """Pause or resume a channel, bypassing ownership checks. This is the
        operator/admin kill switch — it resolves the channel without a key_id
        so it works on channels the operator does not own (e.g. the sandbox,
        owned by the discarded heartbeat-bot key)."""
        with self.connect() as conn:
            channel = self._resolve_channel(conn, identifier)
            conn.execute(
                "UPDATE channels SET paused = ?, updated_at = ? WHERE id = ?",
                (1 if paused else 0, to_timestamp(self.now()), channel["id"]),
            )
            conn.commit()
            return self._serialize_channel(conn, self._get_channel_by_id(conn, channel["id"]))

    def post_sandbox_heartbeat_if_quiet(self, channel_id: str, bot_key_id: str) -> bool:
        """Post a heartbeat message if the channel has had no new message in
        the last SANDBOX_HEARTBEAT_QUIET_SECONDS. Returns True if it posted.
        Skips silently while the channel is paused (kill switch / auto-trip)."""
        with self.connect() as conn:
            channel = conn.execute(
                "SELECT paused FROM channels WHERE id = ?", (channel_id,)
            ).fetchone()
            if channel is not None and channel["paused"]:
                return False
            row = conn.execute(
                "SELECT MAX(created_at) AS latest FROM messages WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        latest = row["latest"] if row is not None else None
        if latest is not None:
            quiet_seconds = (self.now() - parse_timestamp(latest)).total_seconds()
            if quiet_seconds < SANDBOX_HEARTBEAT_QUIET_SECONDS:
                return False
        self.create_message(
            channel_id,
            {
                "content": "heartbeat — the sandbox channel is alive. Post a message here to test the Backchannel protocol.",
                "actor_label": HEARTBEAT_BOT_LABEL,
            },
            key_id=bot_key_id,
        )
        return True

    # --- Channel metrics ------------------------------------------------

    def get_channel_metrics(self, identifier: str, key_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            channel = self._resolve_channel(conn, identifier, key_id=key_id)
            channel_id = channel["id"]

            counts = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN claimed_by_actor_id IS NULL THEN 1 ELSE 0 END) as unclaimed,
                    SUM(CASE WHEN claimed_by_actor_id IS NOT NULL THEN 1 ELSE 0 END) as claimed
                FROM messages WHERE channel_id = ?
                """,
                (channel_id,),
            ).fetchone()

            ack_count = conn.execute(
                """
                SELECT COUNT(DISTINCT message_id) as n
                FROM message_events WHERE channel_id = ? AND event_type = 'ack'
                """,
                (channel_id,),
            ).fetchone()["n"]

            avg_row = conn.execute(
                """
                SELECT AVG(ack.occurred_at - claim.occurred_at) * 1000 as avg_ms
                FROM message_events claim
                JOIN message_events ack ON ack.message_id = claim.message_id
                    AND ack.event_type = 'ack'
                WHERE claim.channel_id = ? AND claim.event_type = 'claim'
                """,
                (channel_id,),
            ).fetchone()

            avg_ms = avg_row["avg_ms"]
            return {
                "channel_id": channel_id,
                "message_counts": {
                    "total": counts["total"] or 0,
                    "unclaimed": counts["unclaimed"] or 0,
                    "claimed": counts["claimed"] or 0,
                    "acknowledged": ack_count,
                },
                "avg_claim_to_ack_ms": round(avg_ms) if avg_ms is not None else None,
                "computed_at": self.now().isoformat(),
            }
