"""Unit tests for the startup base-URL advisory in backchannel.__main__."""

from backchannel.__main__ import _base_url_advisory


def test_warns_when_localhost_default_and_publicly_bound():
    msg = _base_url_advisory("http://localhost:8080", "0.0.0.0")
    assert msg is not None
    assert "BACKCHANNEL_BASE_URL" in msg
    assert "0.0.0.0" in msg


def test_warns_when_unset_and_publicly_bound():
    msg = _base_url_advisory("", "0.0.0.0")
    assert msg is not None
    assert "(unset)" in msg


def test_warns_for_127_0_0_1_default():
    assert _base_url_advisory("http://127.0.0.1:8080", "0.0.0.0") is not None


def test_warns_when_bound_to_ipv6_any():
    assert _base_url_advisory("http://localhost:8080", "::") is not None


def test_silent_when_public_url_set():
    assert _base_url_advisory("https://bus.example.com", "0.0.0.0") is None


def test_silent_when_bound_to_loopback_only():
    # Local-only dev: localhost everywhere is correct, no warning.
    assert _base_url_advisory("http://localhost:8080", "127.0.0.1") is None
    assert _base_url_advisory("", "127.0.0.1") is None
