"""Contract guard: the field names the agent-facing surfaces promise must
match the field names the running server actually emits.

Backchannel's core bet is that an LLM can integrate from the live docs alone.
That bet breaks the moment a hint tells an agent to read response["messages"]
or response["next_since"] when the server emits "data" / "next_cursor". This
test pins both directions:

  1. real responses expose the documented keys (data, next_cursor, claimed_by)
  2. no agent-facing surface (OpenAPI, /agent-guide, /llms.txt) still mentions
     the retired internal names (next_since, claimed_by_actor_id, messages[]).

If you rename a response field, update the docs in the same commit or this
fails.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.auth import AuthContext, DepotAuthenticator
from backchannel.http import create_app

# Tokens that used to leak from the internal SQL/schema layer into the
# agent-facing copy. They must never reappear in a surface an agent reads.
RETIRED_AGENT_FACING_TOKENS = ("next_since", "claimed_by_actor_id")


class DocContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "contract.db"
        self.authenticator = DepotAuthenticator(
            introspector=lambda raw_key: AuthContext(raw_key, "key_owner_1", "owner_1", "free")
        )
        self.app = create_app(
            db_path=self.db_path,
            now_provider=lambda: datetime(2026, 4, 6, 14, 0, tzinfo=timezone.utc),
            authenticator=self.authenticator,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
        environ: dict[str, object] = {}
        setup_testing_defaults(environ)
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        raw_path, query = (path.split("?", 1) + [""])[:2]
        environ.update(
            REQUEST_METHOD=method,
            PATH_INFO=raw_path,
            QUERY_STRING=query,
            CONTENT_LENGTH=str(len(body)),
            CONTENT_TYPE="application/json",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_API_KEY="test-key",
        )
        environ["wsgi.input"] = BytesIO(body)
        holder: dict[str, str] = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response)).decode("utf-8")
        return int(holder["status"].split()[0]), out

    def json(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        status, body = self._call(method, path, payload)
        return status, json.loads(body)

    def text(self, path: str) -> str:
        status, body = self._call("GET", path)
        self.assertEqual(status, 200, f"{path} returned {status}")
        return body

    def test_version_fields_are_unambiguous(self) -> None:
        # /status reports the software version (__version__). /health and the
        # ai-manifest report the API/contract version under a distinct key, so
        # an agent never sees two different "version" values across surfaces.
        from backchannel import __version__

        status, health = self.json("GET", "/health")
        self.assertEqual(status, 200, health)
        self.assertEqual(health.get("api_version"), "1.0", health)
        self.assertNotIn("version", health)

        status, manifest = self.json("GET", "/ai-manifest.json")
        self.assertEqual(status, 200, manifest)
        self.assertEqual(manifest.get("api_version"), "1.0", manifest)
        self.assertNotIn("version", manifest)

        status, st = self.json("GET", "/status")
        self.assertEqual(status, 200, st)
        self.assertEqual(st.get("version"), __version__, st)

    def test_real_responses_use_documented_field_names(self) -> None:
        status, channel = self.json("POST", "/v1/channels", {"name": "contract-q", "mode": "claimable"})
        self.assertEqual(status, 201, channel)
        cid = channel["id"]

        status, posted = self.json(
            "POST", f"/v1/channels/{cid}/messages", {"content": "do the thing", "actor_label": "poster"}
        )
        self.assertEqual(status, 201, posted)
        # createMessage envelope: documented as {message, next_cursor}.
        self.assertIn("message", posted)
        self.assertIn("next_cursor", posted)
        self.assertNotIn("next_since", posted)
        mid = posted["message"]["id"]
        # Message object exposes claimed_by, never the internal column name.
        self.assertIn("claimed_by", posted["message"])
        self.assertNotIn("claimed_by_actor_id", posted["message"])

        # listMessages: documented as {data, next_cursor}.
        status, listing = self.json("GET", f"/v1/channels/{cid}/messages?since=0")
        self.assertEqual(status, 200, listing)
        self.assertIn("data", listing)
        self.assertIn("next_cursor", listing)
        self.assertNotIn("messages", listing)
        self.assertNotIn("next_since", listing)
        self.assertTrue(any(m["id"] == mid for m in listing["data"]))

        # claim: the winning message reports claimed_by, not claimed_by_actor_id.
        status, claimed = self.json("POST", f"/v1/messages/{mid}/claim", {"actor": "worker"})
        self.assertEqual(status, 200, claimed)
        self.assertIn("claimed_by", claimed["message"])
        self.assertNotIn("claimed_by_actor_id", claimed["message"])
        # Server-verified attribution is exposed alongside the self-asserted label.
        self.assertIn("claimed_by_key_id", claimed["message"])
        self.assertEqual(claimed["message"]["claimed_by_key_id"], "key_owner_1")

    def test_task_alias_envelopes_match_documented_shapes(self) -> None:
        # The /v1/tasks/* verb-aliases each return a different envelope; pin
        # every one so the alias surface can't drift the way the canonical
        # path did. (Closes the gap that the canonical-only contract left.)
        status, posted = self.json("POST", "/v1/tasks/post", {"channel": "alias-q", "content": "do x"})
        self.assertEqual(status, 201, posted)
        for key in ("message", "channel", "next_cursor"):
            self.assertIn(key, posted)
        self.assertNotIn("next_since", posted)
        alias_cid = posted["channel"]  # task_post returns the channel id

        status, sub = self.json("POST", "/v1/tasks/subscribe", {"channel": alias_cid})
        self.assertEqual(status, 200, sub)
        self.assertIn("data", sub)
        self.assertIn("next_cursor", sub)
        self.assertNotIn("messages", sub)

        status, claimed = self.json("POST", "/v1/tasks/claim", {"channel": alias_cid, "actor": "w"})
        self.assertEqual(status, 200, claimed)
        self.assertIn("claimed", claimed)
        if claimed["claimed"]:
            self.assertIn("claimed_by_key_id", claimed["claimed"])
            self.assertNotIn("claimed_by_actor_id", claimed["claimed"])

        _, bc_ch = self.json("POST", "/v1/channels", {"name": "bc-q", "mode": "broadcast"})
        status, bc = self.json("POST", "/v1/tasks/broadcast", {"channel": bc_ch["id"], "content": "fanout"})
        self.assertEqual(status, 201, bc)
        self.assertIn("message", bc)
        self.assertIn("next_cursor", bc)
        self.assertNotIn("next_since", bc)

        # claim-and-ack returns {status, message} with verified attribution.
        _, ch = self.json("POST", "/v1/channels", {"name": "caa-q", "mode": "claimable"})
        _, m = self.json("POST", f"/v1/channels/{ch['id']}/messages", {"content": "y"})
        status, caa = self.json("POST", "/v1/tasks/claim-and-ack", {"message_id": m["message"]["id"], "actor": "w2"})
        self.assertEqual(status, 200, caa)
        self.assertIn("status", caa)
        self.assertIn("message", caa)
        self.assertIn("claimed_by_key_id", caa["message"])

    def test_agent_facing_surfaces_drop_retired_internal_names(self) -> None:
        surfaces = {
            "/openapi.json": self.text("/openapi.json"),
            "/agent-guide": self.text("/agent-guide"),
            "/llms.txt": self.text("/llms.txt"),
            "/docs/protocol.md": self.text("/docs/protocol.md"),
        }
        for path, body in surfaces.items():
            for token in RETIRED_AGENT_FACING_TOKENS:
                self.assertNotIn(
                    token,
                    body,
                    f"{path} still mentions retired field name '{token}' — "
                    f"agents will read a field the server does not emit.",
                )


if __name__ == "__main__":
    unittest.main()
