"""Tests for per-channel JSON-Schema-subset metadata validation (B3 extension)."""

from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backchannel.http import create_app


class MetadataSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")
        status, body = self._req("POST", "/v1/keys", body={"agent_label": "schema-tests"})
        self.assertEqual(status, 201)
        self.key = body["key"]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _req(self, method: str, path: str, *, body: dict | None = None, api_key: str | None = None):
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = method
        environ["PATH_INFO"] = path
        environ["QUERY_STRING"] = ""
        payload = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        environ["CONTENT_LENGTH"] = str(len(payload))
        environ["CONTENT_TYPE"] = "application/json"
        environ["wsgi.input"] = BytesIO(payload)
        environ["REMOTE_ADDR"] = "127.0.0.1"
        if api_key:
            environ["HTTP_X_API_KEY"] = api_key
        holder: dict = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status

        out = b"".join(self.app(environ, start_response))
        return int(str(holder["status"]).split()[0]), json.loads(out.decode("utf-8"))

    def _create_channel(self, schema: dict) -> str:
        status, body = self._req(
            "POST",
            "/v1/channels",
            body={"name": f"sch-{id(schema)}", "mode": "broadcast", "metadata_schema": schema},
            api_key=self.key,
        )
        self.assertEqual(status, 201, body)
        return body["id"]

    def _post_msg(self, cid: str, metadata: dict) -> tuple[int, dict]:
        return self._req(
            "POST", f"/v1/channels/{cid}/messages",
            body={"content": "x", "metadata": metadata}, api_key=self.key,
        )

    def test_minLength_maxLength_string(self) -> None:
        cid = self._create_channel({
            "properties": {"code": {"type": "string", "minLength": 3, "maxLength": 5}},
        })
        ok_status, _ = self._post_msg(cid, {"code": "abcd"})
        self.assertEqual(ok_status, 201)
        bad_status, bad = self._post_msg(cid, {"code": "ab"})
        self.assertEqual(bad_status, 422)
        self.assertEqual(bad["error"], "metadata_validation_failed")
        self.assertIn("minLength", str(bad["violations"]))

    def test_pattern_string(self) -> None:
        cid = self._create_channel({
            "properties": {"sku": {"type": "string", "pattern": "^SKU-[0-9]+$"}},
        })
        self.assertEqual(self._post_msg(cid, {"sku": "SKU-100"})[0], 201)
        bad_status, bad = self._post_msg(cid, {"sku": "not-a-sku"})
        self.assertEqual(bad_status, 422)
        self.assertIn("pattern", str(bad["violations"]))

    def test_minimum_maximum_number(self) -> None:
        cid = self._create_channel({
            "properties": {"priority": {"type": "integer", "minimum": 1, "maximum": 10}},
        })
        self.assertEqual(self._post_msg(cid, {"priority": 5})[0], 201)
        bad_status, bad = self._post_msg(cid, {"priority": 99})
        self.assertEqual(bad_status, 422)
        self.assertIn("maximum", str(bad["violations"]))

    def test_additionalProperties_false_rejects_unknown(self) -> None:
        cid = self._create_channel({
            "properties": {"known": {"type": "string"}},
            "additionalProperties": False,
        })
        self.assertEqual(self._post_msg(cid, {"known": "ok"})[0], 201)
        bad_status, bad = self._post_msg(cid, {"known": "ok", "surprise": "boom"})
        self.assertEqual(bad_status, 422)
        self.assertIn("additional property not allowed", str(bad["violations"]))

    def test_required_field_still_works(self) -> None:
        # Sanity: didn't regress the existing behavior.
        cid = self._create_channel({
            "required": ["who"],
            "properties": {"who": {"type": "string"}},
        })
        bad_status, bad = self._post_msg(cid, {})
        self.assertEqual(bad_status, 422)
        self.assertIn("required field missing", str(bad["violations"]))

    def test_enum_still_works(self) -> None:
        cid = self._create_channel({
            "properties": {"severity": {"type": "string", "enum": ["low", "high"]}},
        })
        self.assertEqual(self._post_msg(cid, {"severity": "high"})[0], 201)
        bad_status, _ = self._post_msg(cid, {"severity": "medium"})
        self.assertEqual(bad_status, 422)


if __name__ == "__main__":
    unittest.main()
