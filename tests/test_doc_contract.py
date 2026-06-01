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

    def test_agent_facing_surfaces_drop_retired_internal_names(self) -> None:
        surfaces = {
            "/openapi.json": self.text("/openapi.json"),
            "/agent-guide": self.text("/agent-guide"),
            "/llms.txt": self.text("/llms.txt"),
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
