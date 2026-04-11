from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.http import create_app
from backchannel.store import BackchannelStore


class FrozenClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self) -> datetime:
        return self.current

    def advance(self, *, hours: int = 0, minutes: int = 0) -> None:
        self.current = self.current + timedelta(hours=hours, minutes=minutes)


class BackchannelProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.db"
        self.clock = FrozenClock(datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc))
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: {
                "test-key-owner-1": AuthContext(raw_key, "key_owner_1", "owner_1", "free"),
                "test-key-owner-2": AuthContext(raw_key, "key_owner_2", "owner_2", "free"),
            }[raw_key]
        )
        self.app = create_app(
            db_path=self.db_path,
            now_provider=self.clock.now,
            authenticator=self.authenticator,
            invitation_onboarding_url="https://depot.test/backchannel",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        api_key: str | None = "test-key-owner-1",
    ) -> tuple[int, dict]:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        environ["REQUEST_METHOD"] = method
        if "?" in path:
            raw_path, query = path.split("?", 1)
        else:
            raw_path, query = path, ""
        environ["PATH_INFO"] = raw_path
        environ["QUERY_STRING"] = query
        environ["CONTENT_LENGTH"] = str(len(body))
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(body)
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key is not None:
            environ["HTTP_X_API_KEY"] = api_key

        status_holder: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]], exc_info=None) -> None:
            status_holder["status"] = status
            status_holder["headers"] = headers

        response_body = b"".join(self.app(environ, start_response))
        status_code = int(str(status_holder["status"]).split()[0])
        return status_code, json.loads(response_body.decode("utf-8"))

    def request_raw(self, method: str, path: str, *, api_key: str | None = None) -> tuple[int, dict[str, str], str]:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = method
        if "?" in path:
            raw_path, query = path.split("?", 1)
        else:
            raw_path, query = path, ""
        environ["PATH_INFO"] = raw_path
        environ["QUERY_STRING"] = query
        environ["CONTENT_LENGTH"] = "0"
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(b"")
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key is not None:
            environ["HTTP_X_API_KEY"] = api_key

        status_holder: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]], exc_info=None) -> None:
            status_holder["status"] = status
            status_holder["headers"] = headers

        response_body = b"".join(self.app(environ, start_response))
        status_code = int(str(status_holder["status"]).split()[0])
        headers = {name: value for name, value in status_holder["headers"]}  # type: ignore[index]
        return status_code, headers, response_body.decode("utf-8")

    def create_channel(self, name: str, mode: str, **extra) -> dict:
        status, payload = self.request(
            "POST",
            "/v1/channels",
            {"name": name, "mode": mode, **extra},
        )
        self.assertEqual(status, 201)
        return payload

    def create_actor(self, name: str) -> dict:
        status, payload = self.request("POST", "/v1/actors", {"name": name})
        self.assertEqual(status, 201)
        return payload

    def test_channel_actor_message_flow_with_aliases_and_since_cursor(self) -> None:
        ops = self.create_channel(
            "Ops Alerts",
            "broadcast",
            description="Operational notifications",
            metadata_schema={"severity": "string"},
            pinned_message="Post concise alerts.",
        )
        status, ops_with_alias = self.request(
            "POST",
            f"/v1/channels/{ops['id']}/aliases",
            {"alias": "ops.alerts"},
        )
        self.assertEqual(status, 201)
        self.assertIn("ops.alerts", ops_with_alias["aliases"])

        actor = self.create_actor("observer-1")
        status, actor_with_alias = self.request(
            "POST",
            f"/v1/actors/{actor['id']}/aliases",
            {"alias": "observer-1"},
        )
        self.assertEqual(status, 201)
        self.assertIn("observer-1", actor_with_alias["aliases"])

        status, created = self.request(
            "POST",
            "/v1/channels/ops.alerts/messages",
            {
                "actor": "observer-1",
                "content": "incident-421 is now mitigated",
                "metadata": {"severity": "high"},
            },
        )
        self.assertEqual(status, 201)
        message = created["message"]
        self.assertEqual(message["actor"]["name"], "observer-1")
        self.assertEqual(created["next_cursor"], message["created_at"])

        status, listed = self.request("GET", "/v1/channels/ops.alerts/messages?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]), 1)
        self.assertEqual(listed["data"][0]["id"], message["id"])
        self.assertEqual(listed["next_cursor"], message["created_at"])

        status, empty_page = self.request(
            "GET",
            f"/v1/channels/ops.alerts/messages?{urlencode({'since': message['created_at'], 'limit': 10})}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(empty_page["data"], [])

    def test_root_page_uses_matrix_inspired_html_and_docs_are_public(self) -> None:
        status, headers, body = self.request_raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Backchannel", body)
        self.assertIn("claimable", body)
        self.assertIn("/v1/keys", body)

        status, headers, body = self.request_raw("GET", "/docs/protocol.md")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("# Backchannel Protocol", body)

        status, headers, body = self.request_raw("GET", "/docs/roadmap.md")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("## V1 Non-Goals", body)

    def test_claimable_channel_enforces_single_claim_and_tracks_acks(self) -> None:
        queue = self.create_channel("Queue Jobs", "claimable")
        worker_a = self.create_actor("worker-a")
        worker_b = self.create_actor("worker-b")

        status, created = self.request(
            "POST",
            f"/v1/channels/{queue['id']}/messages",
            {"content": "process job-77"},
        )
        self.assertEqual(status, 201)
        message_id = created["message"]["id"]

        status, claimed = self.request(
            "POST",
            f"/v1/messages/{message_id}/claim",
            {"actor": worker_a["id"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["message"]["claimed_by"]["name"], "worker-a")

        status, second_claim = self.request(
            "POST",
            f"/v1/messages/{message_id}/claim",
            {"actor": worker_b["id"]},
        )
        self.assertEqual(status, 409)
        self.assertEqual(second_claim["error"], "already_claimed")

        status, acked = self.request(
            "POST",
            f"/v1/messages/{message_id}/ack",
            {"actor": worker_a["id"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(acked["status"], "acknowledged")
        self.assertEqual(len(acked["message"]["acknowledged_by"]), 1)

        status, duplicate_ack = self.request(
            "POST",
            f"/v1/messages/{message_id}/ack",
            {"actor": worker_a["id"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(duplicate_ack["status"], "already_acknowledged")

    def test_protected_routes_require_a_valid_depot_key(self) -> None:
        status, missing_key = self.request("POST", "/v1/channels", {"name": "Ops", "mode": "broadcast"}, api_key=None)
        self.assertEqual(status, 401)
        self.assertEqual(missing_key["error"], "unauthorized")

        status, invalid_key = self.request("POST", "/v1/channels", {"name": "Ops", "mode": "broadcast"}, api_key="bad-key")
        self.assertEqual(status, 401)
        self.assertEqual(invalid_key["error"], "unauthorized")

        channel = self.create_channel("Owner One", "broadcast")
        status, cross_owner = self.request("GET", f"/v1/channels/{channel['id']}", api_key="test-key-owner-2")
        self.assertEqual(status, 200)
        self.assertEqual(cross_owner["id"], channel["id"])

    def test_channel_invitations_allow_safe_discovery_and_public_onboarding(self) -> None:
        channel = self.create_channel("Shared Queue", "broadcast")

        status, invitation = self.request(
            "POST",
            f"/v1/channels/{channel['id']}/invitations",
            {},
        )
        self.assertEqual(status, 201)
        invitation_id = invitation["id"]
        self.assertTrue(invitation["active"])

        status, onboarding = self.request(
            "GET",
            f"/v1/channel-invitations/{invitation_id}",
            api_key=None,
        )
        self.assertEqual(status, 401)
        self.assertEqual(onboarding["error"], "api_key_required")
        self.assertEqual(onboarding["redirect_to"], "https://depot.test/backchannel")

        status, resolved = self.request(
            "GET",
            f"/v1/channel-invitations/{invitation_id}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(resolved["channel"]["id"], channel["id"])

        status, cross_owner = self.request(
            "GET",
            f"/v1/channel-invitations/{invitation_id}",
            api_key="test-key-owner-2",
        )
        self.assertEqual(status, 200)
        self.assertEqual(cross_owner["channel"]["id"], channel["id"])

        status, revoked = self.request(
            "DELETE",
            f"/v1/channel-invitations/{invitation_id}",
        )
        self.assertEqual(status, 200)
        self.assertIsNotNone(revoked["revoked_at"])

        status, expired = self.request(
            "GET",
            f"/v1/channel-invitations/{invitation_id}",
        )
        self.assertEqual(status, 410)
        self.assertEqual(expired["error"], "invitation_revoked")

    def test_channel_invitation_lookup_has_a_tighter_rate_limit(self) -> None:
        channel = self.create_channel("Rate Limited", "broadcast")
        status, invitation = self.request(
            "POST",
            f"/v1/channels/{channel['id']}/invitations",
            {},
        )
        self.assertEqual(status, 201)
        invitation_id = invitation["id"]

        for _ in range(10):
            status, payload = self.request(
                "GET",
                f"/v1/channel-invitations/{invitation_id}",
                api_key=None,
            )
            self.assertEqual(status, 401)
            self.assertEqual(payload["error"], "api_key_required")

        status, limited = self.request(
            "GET",
            f"/v1/channel-invitations/{invitation_id}",
            api_key=None,
        )
        self.assertEqual(status, 429)
        self.assertEqual(limited["error"], "rate_limit_exceeded")

    def test_cleanup_archives_expired_records_before_purging_live_rows(self) -> None:
        channel = self.create_channel("Leads", "broadcast")

        status, created = self.request(
            "POST",
            f"/v1/channels/{channel['id']}/messages",
            {"content": "lead-123 arrived"},
        )
        self.assertEqual(status, 201)
        message_id = created["message"]["id"]

        status, invitation = self.request(
            "POST",
            f"/v1/channels/{channel['id']}/invitations",
            {},
        )
        self.assertEqual(status, 201)
        invitation_id = invitation["id"]

        self.clock.advance(hours=25)

        status, listed = self.request("GET", f"/v1/channels/{channel['id']}/messages")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"], [])

        store = BackchannelStore(self.db_path, now_provider=self.clock.now)
        summary = store.archive_and_cleanup_expired_records()
        self.assertEqual(summary["archived_messages"], 1)
        self.assertEqual(summary["purged_messages"], 1)
        self.assertEqual(summary["archived_invitations"], 1)
        self.assertEqual(summary["purged_invitations"], 1)

        with store.connect() as conn:
            row = conn.execute("SELECT id FROM messages WHERE id = ?", (message_id,)).fetchone()
            invitation_row = conn.execute("SELECT id FROM channel_invitations WHERE id = ?", (invitation_id,)).fetchone()
            audit_message = conn.execute(
                "SELECT live_message_id, channel_snapshot_json FROM audit_messages WHERE live_message_id = ?",
                (message_id,),
            ).fetchone()
            audit_invitation = conn.execute(
                "SELECT live_invitation_id FROM audit_channel_invitations WHERE live_invitation_id = ?",
                (invitation_id,),
            ).fetchone()
            run = conn.execute(
                "SELECT status FROM audit_cleanup_runs WHERE id = ?",
                (summary["run_id"],),
            ).fetchone()
        self.assertIsNone(row)
        self.assertIsNone(invitation_row)
        self.assertIsNotNone(audit_message)
        self.assertIsNotNone(audit_invitation)
        self.assertEqual(run["status"], "completed")
        self.assertIn(channel["id"], audit_message["channel_snapshot_json"])

    def test_patch_channel_updates_fields(self) -> None:
        channel = self.create_channel("Original Name", "broadcast", description="old desc")
        other = self.create_channel("Other Channel", "broadcast")

        status, patched = self.request(
            "PATCH",
            f"/v1/channels/{channel['id']}",
            {
                "name": "Updated Name",
                "description": "new desc",
                "mode": "claimable",
                "metadata_schema": {"priority": "string"},
                "pinned_message": "Check priority before claiming.",
                "related_channels": [other["id"]],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["name"], "Updated Name")
        self.assertEqual(patched["description"], "new desc")
        self.assertEqual(patched["mode"], "claimable")
        self.assertEqual(patched["metadata_schema"], {"priority": "string"})
        self.assertEqual(patched["pinned_message"], "Check priority before claiming.")
        self.assertEqual(len(patched["related_channels"]), 1)
        self.assertEqual(patched["related_channels"][0]["id"], other["id"])

        status, bad = self.request("PATCH", f"/v1/channels/{channel['id']}", {"unknown_field": "x"})
        self.assertEqual(status, 422)
        self.assertEqual(bad["error"], "invalid_fields")

    def test_channel_links_are_included_in_get_response(self) -> None:
        alpha = self.create_channel("Alpha", "broadcast")
        beta = self.create_channel("Beta", "broadcast")
        gamma = self.create_channel("Gamma", "claimable", related_channels=[alpha["id"], beta["id"]])

        status, fetched = self.request("GET", f"/v1/channels/{gamma['id']}")
        self.assertEqual(status, 200)
        related_ids = {r["id"] for r in fetched["related_channels"]}
        self.assertIn(alpha["id"], related_ids)
        self.assertIn(beta["id"], related_ids)

    def test_message_actor_label(self) -> None:
        channel = self.create_channel("Labeled", "broadcast")
        status, created = self.request(
            "POST",
            f"/v1/channels/{channel['id']}/messages",
            {"content": "hello from external", "actor_label": "external-hook-v2"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["message"]["actor_label"], "external-hook-v2")
        self.assertIsNone(created["message"]["actor"])

        status, listed = self.request("GET", f"/v1/channels/{channel['id']}/messages")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"][0]["actor_label"], "external-hook-v2")

    def test_list_messages_limit_validation(self) -> None:
        channel = self.create_channel("Limits", "broadcast")
        status, too_low = self.request("GET", f"/v1/channels/{channel['id']}/messages?limit=0")
        self.assertEqual(status, 422)
        self.assertEqual(too_low["error"], "invalid_limit")

        status, too_high = self.request("GET", f"/v1/channels/{channel['id']}/messages?limit=101")
        self.assertEqual(status, 422)
        self.assertEqual(too_high["error"], "invalid_limit")

    def test_cleanup_failure_does_not_delete_live_data(self) -> None:
        channel = self.create_channel("Failure Case", "broadcast")
        status, created = self.request(
            "POST",
            f"/v1/channels/{channel['id']}/messages",
            {"content": "keep me safe"},
        )
        self.assertEqual(status, 201)
        message_id = created["message"]["id"]
        self.clock.advance(hours=25)

        store = BackchannelStore(self.db_path, now_provider=self.clock.now)
        with mock.patch.object(store, "_archive_cleanup_transaction", side_effect=RuntimeError("archive broke")):
            with self.assertRaises(RuntimeError):
                store.archive_and_cleanup_expired_records()

        with store.connect() as conn:
            live_row = conn.execute("SELECT id FROM messages WHERE id = ?", (message_id,)).fetchone()
            failed_run = conn.execute(
                "SELECT status, failure_message FROM audit_cleanup_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(live_row)
        self.assertEqual(failed_run["status"], "failed")
        self.assertIn("archive broke", failed_run["failure_message"])

    def test_restricted_channel_rejects_non_members(self) -> None:
        # owner-1 creates a restricted channel
        channel = self.create_channel("Private Ops", "broadcast", access="restricted")
        self.assertEqual(channel["access"], "restricted")
        channel_id = channel["id"]

        # owner-1 (creator) can access their own restricted channel
        status, _ = self.request("GET", f"/v1/channels/{channel_id}")
        self.assertEqual(status, 200)

        # owner-2 (non-member) cannot GET the channel
        status, denied = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 403)
        self.assertEqual(denied["error"], "channel_access_denied")

        # owner-2 cannot post messages to the restricted channel
        status, denied_msg = self.request(
            "POST",
            f"/v1/channels/{channel_id}/messages",
            {"content": "sneaky"},
            api_key="test-key-owner-2",
        )
        self.assertEqual(status, 403)
        self.assertEqual(denied_msg["error"], "channel_access_denied")

    def test_invitation_grants_channel_access(self) -> None:
        # owner-1 creates a restricted channel and an invitation
        channel = self.create_channel("Members Only", "broadcast", access="restricted")
        channel_id = channel["id"]

        status, invitation = self.request("POST", f"/v1/channels/{channel_id}/invitations", {})
        self.assertEqual(status, 201)
        invitation_id = invitation["id"]

        # owner-2 cannot access the channel yet
        status, _ = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 403)

        # owner-2 resolves the invitation — membership is granted
        status, resolved = self.request(
            "GET", f"/v1/channel-invitations/{invitation_id}", api_key="test-key-owner-2"
        )
        self.assertEqual(status, 200)
        self.assertEqual(resolved["channel"]["id"], channel_id)

        # owner-2 can now access the channel
        status, ch = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 200)
        self.assertEqual(ch["id"], channel_id)

        # owner-2 can post messages
        status, msg = self.request(
            "POST",
            f"/v1/channels/{channel_id}/messages",
            {"content": "i'm in"},
            api_key="test-key-owner-2",
        )
        self.assertEqual(status, 201)
        self.assertEqual(msg["message"]["content"], "i'm in")

        # membership row references the invitation
        status, members = self.request("GET", f"/v1/channels/{channel_id}/members")
        self.assertEqual(status, 200)
        member_keys = [m["key_id"] for m in members["data"]]
        self.assertIn("key_owner_2", member_keys)
        invitation_member = next(m for m in members["data"] if m["key_id"] == "key_owner_2")
        self.assertEqual(invitation_member["granted_via_invitation_id"], invitation_id)

    def test_channel_member_management(self) -> None:
        channel = self.create_channel("Managed Channel", "broadcast", access="restricted")
        channel_id = channel["id"]

        # owner-2 has no access initially
        status, _ = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 403)

        # owner-1 adds owner-2 as a member
        status, member = self.request(
            "POST", f"/v1/channels/{channel_id}/members", {"key_id": "key_owner_2"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(member["key_id"], "key_owner_2")
        self.assertIsNone(member["granted_via_invitation_id"])

        # owner-2 can now access
        status, _ = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 200)

        # owner-1 lists members
        status, listed = self.request("GET", f"/v1/channels/{channel_id}/members")
        self.assertEqual(status, 200)
        member_keys = [m["key_id"] for m in listed["data"]]
        self.assertIn("key_owner_2", member_keys)

        # non-owner cannot list members
        status, denied = self.request("GET", f"/v1/channels/{channel_id}/members", api_key="test-key-owner-2")
        self.assertEqual(status, 403)

        # owner-1 removes owner-2
        status, removed = self.request(
            "DELETE", f"/v1/channels/{channel_id}/members/key_owner_2"
        )
        self.assertEqual(status, 200)

        # owner-2 can no longer access
        status, _ = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 403)

    def test_open_channel_access_is_unaffected(self) -> None:
        # default channel (open) — any key can access, no membership needed
        channel = self.create_channel("Open Ops", "broadcast")
        self.assertEqual(channel["access"], "open")
        channel_id = channel["id"]

        status, ch = self.request("GET", f"/v1/channels/{channel_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 200)
        self.assertEqual(ch["access"], "open")

        status, msg = self.request(
            "POST",
            f"/v1/channels/{channel_id}/messages",
            {"content": "open access"},
            api_key="test-key-owner-2",
        )
        self.assertEqual(status, 201)

    def test_agent_discovery_endpoints(self) -> None:
        # /openapi.json — valid JSON with key fields
        status, _, body = self.request_raw("GET", "/openapi.json")
        self.assertEqual(status, 200)
        spec = json.loads(body)
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("/v1/channels", spec["paths"])
        self.assertIn("ApiKeyAuth", spec["components"]["securitySchemes"])

        # /agent-guide — plain text, contains key sections
        status, headers, body = self.request_raw("GET", "/agent-guide")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        self.assertIn("X-API-Key", body)
        self.assertIn("POST /v1/channels", body)
        self.assertIn("24 hours", body)

        # /.well-known/backchannel.json — redirects to ai-manifest.json
        status, headers, _ = self.request_raw("GET", "/.well-known/backchannel.json")
        self.assertEqual(status, 302)
        self.assertIn("ai-manifest.json", headers.get("Location", ""))

        # /.well-known/ai-manifest.json — canonical manifest
        status, _, body = self.request_raw("GET", "/.well-known/ai-manifest.json")
        self.assertEqual(status, 200)
        meta = json.loads(body)
        self.assertEqual(meta["name"], "Backchannel")
        self.assertIn("openapi_url", meta)
        self.assertIn("agent_guide_url", meta)

        # /llms.txt — plain text
        status, headers, body = self.request_raw("GET", "/llms.txt")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        self.assertIn("Backchannel", body)
        self.assertIn("/agent-guide", body)
        self.assertIn("/openapi.json", body)

    def test_channel_events_endpoint(self) -> None:
        # Create a restricted channel as owner-1
        status, channel = self.request(
            "POST", "/v1/channels",
            {"name": "evt-channel", "mode": "broadcast", "access": "restricted"},
            api_key="test-key-owner-1",
        )
        self.assertEqual(status, 201)
        channel_id = channel["id"]

        # Initially no events
        status, page = self.request("GET", f"/v1/channels/{channel_id}/events", api_key="test-key-owner-1")
        self.assertEqual(status, 200)
        self.assertEqual(page["data"], [])

        # Add a member explicitly — expect member_added event
        status, _ = self.request(
            "POST", f"/v1/channels/{channel_id}/members",
            {"key_id": "key_owner_2"},
            api_key="test-key-owner-1",
        )
        self.assertEqual(status, 201)

        status, page = self.request("GET", f"/v1/channels/{channel_id}/events", api_key="test-key-owner-1")
        self.assertEqual(status, 200)
        self.assertEqual(len(page["data"]), 1)
        evt = page["data"][0]
        self.assertEqual(evt["event_type"], "member_added")
        self.assertEqual(evt["actor_key_id"], "key_owner_1")
        self.assertEqual(evt["subject_key_id"], "key_owner_2")
        self.assertIsNone(evt["invitation_id"])

        # Remove the member — expect member_removed event
        status, _ = self.request(
            "DELETE", f"/v1/channels/{channel_id}/members/key_owner_2",
            api_key="test-key-owner-1",
        )
        self.assertEqual(status, 200)

        # Create invitation then resolve it — expect invitation_resolved event
        status, inv = self.request(
            "POST", f"/v1/channels/{channel_id}/invitations",
            {},
            api_key="test-key-owner-1",
        )
        self.assertEqual(status, 201)
        inv_id = inv["id"]

        status, _ = self.request("GET", f"/v1/channel-invitations/{inv_id}", api_key="test-key-owner-2")
        self.assertEqual(status, 200)

        # Revoke a second invitation — expect invitation_revoked event
        status, inv2 = self.request(
            "POST", f"/v1/channels/{channel_id}/invitations",
            {},
            api_key="test-key-owner-1",
        )
        inv2_id = inv2["id"]
        status, _ = self.request(
            "DELETE", f"/v1/channel-invitations/{inv2_id}",
            api_key="test-key-owner-1",
        )
        self.assertEqual(status, 200)

        # All four event types should now be present
        status, page = self.request("GET", f"/v1/channels/{channel_id}/events", api_key="test-key-owner-1")
        self.assertEqual(status, 200)
        event_types = [e["event_type"] for e in page["data"]]
        self.assertIn("member_added", event_types)
        self.assertIn("member_removed", event_types)
        self.assertIn("invitation_resolved", event_types)
        self.assertIn("invitation_revoked", event_types)

        # invitation_resolved event should carry the invitation_id
        resolved = next(e for e in page["data"] if e["event_type"] == "invitation_resolved")
        self.assertEqual(resolved["invitation_id"], inv_id)
        self.assertEqual(resolved["subject_key_id"], "key_owner_2")

        # Non-owner gets 403
        status, _ = self.request("GET", f"/v1/channels/{channel_id}/events", api_key="test-key-owner-2")
        self.assertEqual(status, 403)

        # Events survive cleanup until they expire
        event_count = len(page["data"])
        self.clock.advance(hours=1)
        self.app.store.archive_and_cleanup_expired_records()
        status, page2 = self.request("GET", f"/v1/channels/{channel_id}/events", api_key="test-key-owner-1")
        self.assertEqual(len(page2["data"]), event_count)  # not expired yet

        # Advance past TTL — events purged
        self.clock.advance(hours=25)
        self.app.store.archive_and_cleanup_expired_records()
        status, page3 = self.request("GET", f"/v1/channels/{channel_id}/events", api_key="test-key-owner-1")
        self.assertEqual(page3["data"], [])
