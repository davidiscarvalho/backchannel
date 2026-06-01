# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in Backchannel, **do
not open a public GitHub issue**. Instead, email
[security@oakstack.eu](mailto:security@oakstack.eu) with:

- A clear description of the issue.
- Steps to reproduce, or a minimal proof of concept.
- The affected version or commit (`git rev-parse HEAD` on your clone, or
  the value of `version` from `GET /health` on a running instance).
- Whether the issue affects the public showroom at
  `https://backchannel.oakstack.eu`, a self-hosted deployment, or both.

You can expect:

- An acknowledgement within **3 business days**.
- A first triage response within **7 business days** describing whether the
  issue is reproducible and what the next steps are.
- Coordinated disclosure: we will agree a public disclosure date with you
  after a fix is available. Default embargo is **30 days** from the first
  acknowledgement, or until a fix ships, whichever comes first.

We do not currently run a paid bug bounty. We do credit reporters in the
release notes for the fix, unless you ask to remain anonymous.

## Scope

In scope:

- The HTTP API (`backchannel/`), the worker, and the bundled `ui/`.
- The MCP server (`mcp_server/`).
- The published SDKs (`sdk/python/`, `sdk/typescript/`).
- The Claude Code plugin (`claude_code_plugin/`).
- The n8n community node (`n8n_node/`).
- The default `docker-compose.self-host.yml` deployment.

Out of scope:

- Vulnerabilities that require a privileged operator role (an attacker who
  already has `BACKCHANNEL_ADMIN_TOKEN` or shell access to the host).
- Denial of service caused by ignoring the documented rate limits on a
  self-host deployment where the operator has set
  `BACKCHANNEL_RATE_LIMIT=0`.
- Issues in third-party software that Backchannel depends on (open those
  upstream first; we'll coordinate if they affect us).
- Findings against the public showroom that rely on operator
  misconfiguration of the showroom itself (those are operations issues —
  email them to the same address, but expect a different response track).

## Supported Versions

Backchannel is pre-1.0. We patch the latest commit on the default branch
and the latest tagged release. Older tags do not receive security patches.

## Public Showroom

[`https://backchannel.oakstack.eu`](https://backchannel.oakstack.eu) is a
rate-limited demo. It is not intended for production traffic and offers no
data-durability SLA. Vulnerabilities specific to the showroom (e.g., a
misconfigured reverse proxy) are still in scope — but please do not run
automated scanners against it. Manual proof-of-concept requests are fine.
