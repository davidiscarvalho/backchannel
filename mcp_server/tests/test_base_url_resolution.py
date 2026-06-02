"""The MCP client warns once when it defaults to the shared public sandbox,
and honors BACKCHANNEL_BASE_URL / explicit base_url silently."""

from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

MCP_ROOT = Path(__file__).resolve().parents[1]
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

import backchannel_mcp.client as client  # noqa: E402


class BaseUrlResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        client._warned_public = False

    def _warns(self, *calls_kwargs, env=None):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False):
            if env is None:
                os.environ.pop("BACKCHANNEL_BASE_URL", None)
            old = sys.stderr
            sys.stderr = buf
            try:
                for kw in calls_kwargs:
                    client._resolve_base_url(kw)
            finally:
                sys.stderr = old
        return buf.getvalue().count("PUBLIC SANDBOX")

    def test_warns_once_on_default(self):
        self.assertEqual(self._warns(None, None, None), 1)

    def test_silent_when_explicit(self):
        self.assertEqual(self._warns("http://localhost:8099"), 0)

    def test_silent_when_env_set(self):
        self.assertEqual(self._warns(None, env={"BACKCHANNEL_BASE_URL": "http://localhost:8099"}), 0)

    def test_env_resolves_to_value(self):
        with mock.patch.dict(os.environ, {"BACKCHANNEL_BASE_URL": "http://x:1/"}):
            self.assertEqual(client._resolve_base_url(None), "http://x:1")


if __name__ == "__main__":
    unittest.main()
