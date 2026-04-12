from __future__ import annotations

import json
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
            conn.commit()

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
            ttl_seconds = 86400
        effective_team_id = team_id
        channel_id = str(uuid.uuid4())
        now = to_timestamp(self.now())

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO channels (id, owner_key_id, owner_id, name, mode, access, team_id, description, metadata_schema, pinned_message, webhook_url, webhook_secret, ttl_seconds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        allowed = {"name", "mode", "access", "description", "metadata_schema", "pinned_message", "related_channels"}
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

    _MAX_CONTENT_BYTES = 65536  # 64 KB

    def create_message(self, channel_identifier: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> MessageEnvelope:
        content = self._required_string(payload, "content")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._MAX_CONTENT_BYTES:
            raise APIError(
                422,
                "content_too_large",
                f"Message content exceeds the {self._MAX_CONTENT_BYTES // 1024}KB limit",
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
            ttl_seconds = channel["ttl_seconds"] if "ttl_seconds" in channel.keys() else 86400
            expires_at = to_timestamp(now + timedelta(seconds=ttl_seconds))
            # Validate metadata against channel's metadata_schema
            channel_schema = json.loads(channel["metadata_schema"]) if isinstance(channel["metadata_schema"], str) else channel["metadata_schema"]
            if channel_schema:
                violations: list[dict[str, str]] = []
                required_fields = channel_schema.get("required", [])
                for field in required_fields:
                    if field not in metadata:
                        violations.append({"field": f"metadata.{field}", "issue": "required field missing"})
                properties = channel_schema.get("properties", {})
                for field, field_schema in properties.items():
                    if field not in metadata:
                        continue
                    value = metadata[field]
                    expected_type = field_schema.get("type")
                    if expected_type:
                        type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}
                        expected_py = type_map.get(expected_type)
                        if expected_py and not isinstance(value, expected_py):
                            violations.append({"field": f"metadata.{field}", "issue": f"expected type '{expected_type}'"})
                            continue
                    allowed_values = field_schema.get("enum")
                    if allowed_values is not None and value not in allowed_values:
                        violations.append({"field": f"metadata.{field}", "issue": f"value must be one of {allowed_values}"})
                if violations:
                    raise APIError(422, "metadata_validation_failed", "Message metadata failed channel schema validation", {"violations": violations})
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

    def ack_message(self, message_id: str, payload: dict[str, Any], key_id: str, team_id: str | None = None) -> dict[str, Any]:
        actor_identifier = self._required_string(payload, "actor")
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")

        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            if parse_timestamp(message["expires_at"]) <= self.now():
                raise APIError(410, "message_expired", "Expired messages can no longer be acknowledged")

            channel = self._get_channel_by_id(conn, message["channel_id"])
            self._check_channel_access(conn, channel, key_id, team_id=team_id)

            actor = self._resolve_actor(conn, actor_identifier)
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
        actor_identifier = self._required_string(payload, "actor")
        metadata = self._ensure_mapping(payload.get("metadata", {}), "metadata")

        with self.connect() as conn:
            message = self._get_message(conn, message_id)
            if parse_timestamp(message["expires_at"]) <= self.now():
                raise APIError(410, "message_expired", "Expired messages can no longer be claimed")

            channel = self._get_channel_by_id(conn, message["channel_id"])
            if channel["mode"] != "claimable":
                raise APIError(409, "channel_not_claimable", "Only messages in claimable channels can be claimed")
            self._check_channel_access(conn, channel, key_id, team_id=team_id)

            actor = self._resolve_actor(conn, actor_identifier)
            if message["claimed_by_actor_id"]:
                if message["claimed_by_actor_id"] == actor["id"]:
                    ack_exists = conn.execute(
                        "SELECT 1 FROM message_events WHERE message_id = ? AND event_type = 'ack' LIMIT 1",
                        (message_id,),
                    ).fetchone()
                    refreshed = self._get_message(conn, message_id)
                    if ack_exists:
                        return {"status": "already_acknowledged", "message": self._serialize_message(conn, refreshed)}
                    return {"status": "already_claimed", "message": self._serialize_message(conn, refreshed)}
                raise APIError(409, "already_claimed", "This message has already been claimed")

            claimed_at = to_timestamp(self.now())
            cursor = conn.execute(
                "UPDATE messages SET claimed_by_actor_id = ?, claimed_at = ? WHERE id = ? AND claimed_by_actor_id IS NULL",
                (actor["id"], claimed_at, message_id),
            )
            if cursor.rowcount == 0:
                raise APIError(409, "already_claimed", "This message has already been claimed")
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
        actor_identifier = self._required_string(payload, "actor")
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
            actor = self._resolve_actor(conn, actor_identifier)
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
        actor_identifier = self._required_string(payload, "actor")
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
            if message["claimed_by_actor_id"]:
                raise APIError(409, "already_claimed", "This message has already been claimed")

            actor = self._resolve_actor(conn, actor_identifier)
            now = self.now()
            claimed_at = to_timestamp(now)
            lease_token = str(uuid.uuid4())
            lease_expires_at = to_timestamp(now + timedelta(seconds=lease_seconds))

            cursor = conn.execute(
                "UPDATE messages SET claimed_by_actor_id = ?, claimed_at = ?, lease_token = ?, lease_expires_at = ? WHERE id = ? AND claimed_by_actor_id IS NULL",
                (actor["id"], claimed_at, lease_token, lease_expires_at, message_id),
            )
            if cursor.rowcount == 0:
                raise APIError(409, "already_claimed", "This message has already been claimed")
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

        return {
            "archived_messages": archived_messages,
            "purged_messages": purged_messages,
            "archived_invitations": archived_invitations,
            "purged_invitations": purged_invitations,
            "archived_channel_events": archived_channel_events,
            "purged_channel_events": purged_channel_events,
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
        from urllib.error import URLError
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
