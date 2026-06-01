from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from wsgiref.simple_server import make_server

from backchannel.__main__ import ThreadingWSGIServer
from backchannel.http import create_app


def _req(base: str, method: str, path: str, body: dict | None = None, key: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if key:
        r.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class ThreadedServerConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        db = Path(self.tempdir.name) / "c.db"
        # Generous per-key limit so the concurrency itself is what's under test.
        os.environ["BACKCHANNEL_RATE_LIMIT"] = "0"
        app = create_app(db_path=db)
        self.server = make_server("127.0.0.1", 0, app, server_class=ThreadingWSGIServer)
        self.host, self.port = self.server.server_address
        self.base = f"http://127.0.0.1:{self.port}"
        self.t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.t.join(timeout=5)
        self.tempdir.cleanup()
        os.environ.pop("BACKCHANNEL_RATE_LIMIT", None)

    def test_burst_of_concurrent_connections_is_not_refused(self) -> None:
        # Stock single-threaded wsgiref (backlog 5) would refuse most of these.
        with ThreadPoolExecutor(max_workers=40) as pool:
            results = list(pool.map(lambda _: _req(self.base, "GET", "/health")[0], range(40)))
        self.assertTrue(all(s == 200 for s in results), f"some requests failed: {results}")

    def test_atomic_claim_under_concurrent_race(self) -> None:
        _, key_resp = _req(self.base, "POST", "/v1/keys", {"agent_label": "conc"})
        key = key_resp["key"]
        _, ch = _req(self.base, "POST", "/v1/channels", {"name": "q", "mode": "claimable"}, key)
        mids = []
        for i in range(15):
            _, m = _req(self.base, "POST", f"/v1/channels/{ch['id']}/messages", {"content": f"t{i}"}, key)
            mids.append(m["message"]["id"])

        winners_total = 0
        doubles = 0
        for mid in mids:
            with ThreadPoolExecutor(max_workers=8) as pool:
                statuses = list(
                    pool.map(
                        lambda a: _req(self.base, "POST", f"/v1/messages/{mid}/claim", {"actor": f"w{a}"}, key),
                        range(8),
                    )
                )
            winners = [s for s, d in statuses if s == 200 and d.get("status") == "claimed"]
            if len(winners) == 1:
                winners_total += 1
            if len(winners) > 1:
                doubles += 1
        self.assertEqual(doubles, 0, "a message was claimed by more than one actor")
        self.assertEqual(winners_total, len(mids), "every message should have exactly one winner")


if __name__ == "__main__":
    unittest.main()
