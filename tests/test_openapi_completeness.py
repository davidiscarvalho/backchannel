"""Regression guard: every non-meta route in backchannel.http must
appear in /openapi.json with the right HTTP verb. If you add a route
to backchannel/http.py and skip the spec, this test fails.

Meta routes (discovery / health / docs / status / Prometheus) are
intentionally omitted from the spec and listed in META_ROUTES below.
If you add a meta route, add it here too — never bypass this test by
removing a real route from the comparison.
"""
from __future__ import annotations

import re

from backchannel.http import create_app
from backchannel.openapi import build_openapi_spec


# (method, regex-pattern-string-as-it-appears-in-self.routes) — these
# are the routes we intentionally do not document in OpenAPI.
META_ROUTES = {
    ("GET", r"^/$"),
    ("GET", r"^/openapi\.json$"),
    ("GET", r"^/agent-guide$"),
    ("GET", r"^/ai-manifest\.json$"),
    ("GET", r"^/\.well-known/backchannel\.json$"),
    ("GET", r"^/\.well-known/ai-manifest\.json$"),
    ("GET", r"^/\.well-known/openapi\.json$"),
    ("GET", r"^/\.well-known/ai-plugin\.json$"),
    ("GET", r"^/\.well-known/agent-policy\.json$"),
    ("GET", r"^/first-success-prompt\.txt$"),
    ("GET", r"^/llms\.txt$"),
    ("GET", r"^/docs/(?P<document>protocol|auth-integration|roadmap|sla|reliability|errors)\.md$"),
    ("GET", r"^/docs/playground$"),
    ("GET", r"^/metrics$"),
    ("GET", r"^/robots\.txt$"),
    ("GET", r"^/account/usage$"),
    ("GET", r"^/status$"),
    ("GET", r"^/status\.html$"),
}


_NAMED_GROUP = re.compile(r"\(\?P<(?P<name>[^>]+)>[^)]+\)")


def regex_to_openapi_path(pattern: str) -> str:
    """Convert a route regex like '^/v1/channels/(?P<identifier>[^/]+)$'
    to its OpenAPI path: '/v1/channels/{identifier}'."""
    s = pattern
    if s.startswith("^"):
        s = s[1:]
    if s.endswith("$"):
        s = s[:-1]
    s = _NAMED_GROUP.sub(lambda m: "{" + m.group("name") + "}", s)
    # Unescape regex-escaped dots inside meta routes (.well-known etc.) —
    # we do not surface those in the spec, but be robust here.
    s = s.replace(r"\.", ".")
    return s


def test_every_non_meta_route_is_in_openapi(tmp_path) -> None:
    app = create_app(db_path=tmp_path / "openapi-completeness.db")
    spec = build_openapi_spec()
    declared_in_spec: set[tuple[str, str]] = set()
    for path, methods in spec["paths"].items():
        for method in methods:
            if method.lower() in {"get", "post", "patch", "put", "delete"}:
                declared_in_spec.add((method.upper(), path))

    routes_in_app: set[tuple[str, str]] = set()
    for method, pattern, _requires_auth, _handler in app.routes:
        if (method, pattern.pattern) in META_ROUTES:
            continue
        routes_in_app.add((method, regex_to_openapi_path(pattern.pattern)))

    missing_from_spec = routes_in_app - declared_in_spec
    assert not missing_from_spec, (
        "Routes present in backchannel.http but missing from OpenAPI:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(missing_from_spec))
    )

    # Reverse check: spec must not advertise routes the app does not serve.
    extra_in_spec = declared_in_spec - routes_in_app
    assert not extra_in_spec, (
        "OpenAPI advertises routes that the app does not serve:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(extra_in_spec))
    )


def test_mutating_ops_declare_idempotency_key() -> None:
    spec = build_openapi_spec()
    mutating = {"post", "patch", "put", "delete"}
    failures: list[str] = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method.lower() not in mutating:
                continue
            params = op.get("parameters", [])
            has_idem = any(
                p.get("in") == "header" and p.get("name", "").lower() == "idempotency-key"
                for p in params
            )
            if not has_idem:
                failures.append(f"{method.upper()} {path}")
    assert not failures, (
        "Mutating operations missing Idempotency-Key header parameter:\n"
        + "\n".join(f"  {f}" for f in failures)
    )
