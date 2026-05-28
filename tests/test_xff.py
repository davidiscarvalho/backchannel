"""Tests for trusted-proxy X-Forwarded-For resolution (T3)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backchannel.http import BackchannelApp
from backchannel.store import BackchannelStore


def _make_app(trusted_proxies: str = "") -> BackchannelApp:
    old = os.environ.get("BACKCHANNEL_TRUSTED_PROXIES")
    os.environ["BACKCHANNEL_TRUSTED_PROXIES"] = trusted_proxies
    try:
        store = BackchannelStore(":memory:")
        return BackchannelApp(store=store)
    finally:
        if old is None:
            os.environ.pop("BACKCHANNEL_TRUSTED_PROXIES", None)
        else:
            os.environ["BACKCHANNEL_TRUSTED_PROXIES"] = old


def test_xff_trusted_proxy_extracts_client_ip():
    """When REMOTE_ADDR is a trusted proxy, the rightmost untrusted XFF hop is used."""
    app = _make_app("127.0.0.1/32")
    environ = {
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_X_FORWARDED_FOR": "1.2.3.4, 127.0.0.1",
    }
    assert app._resolve_remote_addr(environ) == "1.2.3.4"


def test_xff_untrusted_remote_addr_ignores_xff():
    """When REMOTE_ADDR is NOT a trusted proxy, XFF is ignored (prevents spoofing)."""
    app = _make_app("127.0.0.1/32")
    environ = {
        "REMOTE_ADDR": "10.0.0.5",
        "HTTP_X_FORWARDED_FOR": "1.2.3.4",
    }
    assert app._resolve_remote_addr(environ) == "10.0.0.5"


def test_xff_no_trusted_proxies_configured():
    """With no trusted proxies, REMOTE_ADDR is always returned."""
    app = _make_app("")
    environ = {
        "REMOTE_ADDR": "172.18.0.3",
        "HTTP_X_FORWARDED_FOR": "1.2.3.4",
    }
    assert app._resolve_remote_addr(environ) == "172.18.0.3"


def test_xff_multiple_trusted_hops():
    """Multiple proxy hops in XFF — skip all trusted, return first untrusted."""
    app = _make_app("10.0.0.0/8,172.16.0.0/12")
    environ = {
        "REMOTE_ADDR": "10.0.0.1",
        "HTTP_X_FORWARDED_FOR": "203.0.113.50, 172.17.0.2, 10.0.0.2",
    }
    assert app._resolve_remote_addr(environ) == "203.0.113.50"


def test_xff_all_hops_trusted_falls_back_to_remote_addr():
    """If every XFF hop is trusted, fall back to REMOTE_ADDR."""
    app = _make_app("10.0.0.0/8,127.0.0.0/8")
    environ = {
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_X_FORWARDED_FOR": "10.0.0.1, 10.0.0.2",
    }
    assert app._resolve_remote_addr(environ) == "127.0.0.1"


def test_xff_no_header_falls_back_to_remote_addr():
    """Trusted proxy but no XFF header — use REMOTE_ADDR."""
    app = _make_app("127.0.0.1/32")
    environ = {"REMOTE_ADDR": "127.0.0.1"}
    assert app._resolve_remote_addr(environ) == "127.0.0.1"


def test_xff_cidr_range():
    """CIDR range matching — Docker bridge network."""
    app = _make_app("172.16.0.0/12")
    environ = {
        "REMOTE_ADDR": "172.21.0.5",
        "HTTP_X_FORWARDED_FOR": "8.8.8.8",
    }
    assert app._resolve_remote_addr(environ) == "8.8.8.8"
