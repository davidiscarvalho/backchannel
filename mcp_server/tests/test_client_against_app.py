"""Integration test: drive the MCP server's HTTP client against an in-process
Backchannel WSGI app. No network, no auth-as-a-service — uses LocalAuthenticator.

Validates the same wire contract the real MCP tools exercise.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import httpx

# Make the in-tree backchannel package importable from this nested test dir.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MCP_ROOT = Path(__file__).resolve().parents[1]
if str(MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(MCP_ROOT))

from backchannel.http import create_app  # noqa: E402

from backchannel_mcp.client import BackchannelClient, BackchannelError  # noqa: E402


class WSGITransport(httpx.AsyncBaseTransport):
    """Bridge httpx → a synchronous WSGI app, so the async MCP client can call
    the in-process Backchannel app without a network."""

    def __init__(self, app):
        self.app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._call, request
        )

    def _call(self, request: httpx.Request) -> httpx.Response:
        body_bytes = request.content or b""
        environ: dict = {}
        setup_testing_defaults(environ)
        environ["REQUEST_METHOD"] = request.method
        environ["PATH_INFO"] = request.url.path
        environ["QUERY_STRING"] = request.url.query.decode("utf-8") if request.url.query else ""
        environ["CONTENT_LENGTH"] = str(len(body_bytes))
        environ["CONTENT_TYPE"] = request.headers.get("content-type", "application/json")
        environ["wsgi.input"] = BytesIO(body_bytes)
        environ["REMOTE_ADDR"] = "127.0.0.1"
        environ["SERVER_NAME"] = request.url.host or "test"
        environ["SERVER_PORT"] = str(request.url.port or 80)
        environ["wsgi.url_scheme"] = request.url.scheme or "http"
        for hk, hv in request.headers.items():
            key = "HTTP_" + hk.upper().replace("-", "_")
            environ[key] = hv

        status_holder: dict = {}

        def start_response(status: str, headers, exc_info=None):
            status_holder["status"] = status
            status_holder["headers"] = headers

        body = b"".join(self.app(environ, start_response))
        status_code = int(str(status_holder["status"]).split()[0])
        resp_headers = list(status_holder["headers"])
        return httpx.Response(status_code, headers=resp_headers, content=body)


def make_client(app) -> BackchannelClient:
    c = BackchannelClient(api_key=None, base_url="http://backchannel.local")
    # Swap the transport on the underlying AsyncClient
    c._http = httpx.AsyncClient(  # type: ignore[attr-defined]
        timeout=5.0,
        base_url="http://backchannel.local",
        transport=WSGITransport(app),
    )
    return c


class MCPClientIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tempdir.name) / "test.db")

    async def asyncTearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_full_post_claim_ack_flow(self) -> None:
        # 1. Issue a key (producer)
        async with make_client(self.app) as producer:
            key = await producer.issue_key(agent_label="producer-1")
            self.assertTrue(key.key.startswith("bck_"))
            self.assertEqual(key.tier, 0)

        # 2. Producer creates a claimable channel and posts a task
        async with make_client(self.app) as producer:
            producer.api_key = key.key
            channel = await producer.create_channel(name="jobs-q", mode="claimable")
            envelope = await producer.post_message(
                channel["id"], content="deploy v2", actor_label="producer-1"
            )
            message_id = envelope["message"]["id"]

        # 3. A second agent claims it
        async with make_client(self.app) as worker:
            worker_key = await worker.issue_key(agent_label="worker-1")
            worker.api_key = worker_key.key
            claimed = await worker.claim_message(message_id, actor="worker-1")
            self.assertEqual(claimed["status"], "claimed")

            # second claim from a different worker → 409
            second_worker = make_client(self.app)
            try:
                second_worker_key = await second_worker.issue_key(agent_label="worker-2")
                second_worker.api_key = second_worker_key.key
                with self.assertRaises(BackchannelError) as cm:
                    await second_worker.claim_message(message_id, actor="worker-2")
                self.assertEqual(cm.exception.status, 409)
                self.assertEqual(cm.exception.payload.get("error"), "already_claimed")
            finally:
                await second_worker._http.aclose()  # type: ignore[attr-defined]

            # ack closes the loop
            acked = await worker.ack_message(message_id, actor="worker-1")
            self.assertEqual(acked["status"], "acknowledged")

    async def test_discover_and_request_access(self) -> None:
        # Owner creates a discoverable restricted lobby.
        async with make_client(self.app) as owner:
            owner_key = await owner.issue_key(agent_label="owner-1")
            owner.api_key = owner_key.key
            channel = await owner.create_channel(
                name="incident-room", mode="claimable", access="restricted", discoverable=True
            )
            cid = channel["id"]
            self.assertTrue(channel["discoverable"])

        # A different agent discovers it and requests access.
        async with make_client(self.app) as seeker:
            seeker_key = await seeker.issue_key(agent_label="seeker-1")
            seeker.api_key = seeker_key.key
            page = await seeker.discover_channels()
            found = [c for c in page["data"] if c["id"] == cid]
            self.assertEqual(len(found), 1)
            self.assertFalse(found[0]["is_member"])
            result = await seeker.request_access(cid, reason="on call")
            self.assertEqual(result["status"], "pending")

    async def test_missing_key_returns_clear_error(self) -> None:
        async with make_client(self.app) as c:
            # No api_key set, no issue_key call → calling a protected route should fail
            with self.assertRaises(BackchannelError) as cm:
                await c.create_channel(name="x")
            self.assertEqual(cm.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
