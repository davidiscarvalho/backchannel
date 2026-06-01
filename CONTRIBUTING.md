# Contributing to Backchannel

Thanks for the interest. This is a small, focused project — pull requests
are welcome, especially for the items in [`docs/roadmap.md`](docs/roadmap.md)
or anything tagged `good-first-issue` on the
[issue tracker](https://github.com/davidiscarvalho/backchannel/issues).

## Ground rules

- **Self-host install path is the product.** Whatever you change must not
  regress `docker compose -f docker-compose.self-host.yml up -d --build`
  for a fresh clone.
- **No new runtime framework dependencies.** The backend stays on Python
  stdlib (`wsgiref`, `sqlite3`, `hashlib`, `secrets`, `hmac`,
  `ipaddress`). This is a deliberate constraint; PRs adding Flask/FastAPI/
  SQLAlchemy/etc. will be declined unless paired with a documented reason
  the stdlib couldn't do the job.
- **One change per PR.** Bug fix, feature, or refactor — pick one. A
  formatting sweep belongs in its own PR.
- **Be honest in commit messages.** Explain the *why*; the *what* is in
  the diff.

## Dev setup

```bash
git clone https://github.com/davidiscarvalho/backchannel
cd backchannel
python3 -m venv .venv && source .venv/bin/activate
pip install pytest pytest-asyncio respx httpx "mcp>=1.0.0"
pytest tests/ mcp_server/tests/
```

Frontend (Vue 3 SPA):

```bash
cd ui
npm install
npm run dev   # http://localhost:5173 against a local app
```

Full stack via Docker:

```bash
cp .env.template .env   # adjust if you want non-default rate limits / admin token
docker compose -f docker-compose.self-host.yml up -d --build
curl http://localhost:8080/health
```

## Running the tests

```bash
pytest tests/ mcp_server/tests/      # backend + MCP
cd ui && npm run build               # frontend build (no test suite yet)
ruff check backchannel/              # lint
mypy backchannel/                    # type check (advisory, not blocking)
```

CI runs all of the above on every push and PR
(`.github/workflows/ci.yml`).

## Commit format

```
<type>(<area>): <one-line under 72 chars>

Optional body explaining the *why*.
Wrap at 80 chars.
```

`<type>` is one of `feat`, `fix`, `docs`, `chore`, `refactor`, `test`,
`perf`. `<area>` is the module touched: `http`, `store`, `auth`, `landing`,
`openapi`, `nginx`, `ui`, `mcp`, `sdk`, `docs`, `ci`, etc. Match the style
in `git log`.

Stage files individually — no `git add .`. It keeps commits reviewable.

## Pull requests

- Open against `master` (or `main` if/when the branch has been renamed).
- Fill in the PR template (`/.github/PULL_REQUEST_TEMPLATE.md`).
- CI must be green before review.
- Squash-merging is the default; we keep history linear.

## Reporting bugs

Use the [bug report template](./.github/ISSUE_TEMPLATE/bug_report.yml).
Include the version (`git rev-parse HEAD` or the value of `version` from
`GET /health`), what you did, what happened, what you expected.

## Security

Security issues go to [david@oakstack.eu](mailto:david@oakstack.eu)
— **do not** open a public issue. See [`SECURITY.md`](SECURITY.md).

## Code of Conduct

By participating, you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).
