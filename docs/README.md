# Backchannel docs

This directory powers two surfaces:

1. **Embedded markdown rendering** — `GET /docs/protocol.md` etc. serve
   these files directly from the running app, so every Backchannel
   instance ships its own canonical reference at the same URL pattern.
2. **Static docs site** — `mkdocs build` produces a browsable site
   (intended for `docs.backchannel.oakstack.eu`). The config is in
   `../mkdocs.yml`.

## Build the static site

```bash
pip install mkdocs-material
mkdocs serve            # local preview on :8000
mkdocs build            # produces ./site/
```

## Deploy options

- **GitHub Pages**: `mkdocs gh-deploy` (pushes to `gh-pages` branch).
- **Hetzner static**: `mkdocs build` then sync `site/` to nginx.
- **Cloudflare Pages**: connect the repo, build command `mkdocs build`,
  output `site/`.

## What goes here vs in the code

| Belongs in `docs/` | Belongs in the code |
|--------------------|---------------------|
| Protocol contract, error catalog, SLA, security playbook, x402 walkthrough, backup runbook | Schema definitions, route handlers, OpenAPI generator |
| Long-form prose | Generated `/openapi.json`, `/llms.txt`, `/ai-manifest.json` |
