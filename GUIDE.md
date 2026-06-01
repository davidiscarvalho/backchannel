# Backchannel — operator's guide

> From a working repo on your laptop to a published, deployed,
> end-to-end working product. Every command, every menu, every gotcha.

**Total time:** ~3 hours, broken into 13 sections. Each section is
independent and reversible — if a step fails, you have a clean
restart point.

**Conventions in this guide:**
- `$LAPTOP>` — run on your local machine.
- `$HETZNER>` — run on the Hetzner server (after `ssh`).
- Outputs shown in code blocks under "**You'll see:**".
- Time estimates are real-world (incl. waiting for npm publish, etc.).

---

## Table of contents

| # | Section | Time |
|---|---------|------|
| 0 | Pre-flight on your laptop | 10 min |
| 1 | Push to GitHub | 2 min |
| 2 | Deploy to Hetzner | 15 min |
| 3 | Smoke-test the live deployment | 5 min |
| 4 | Schedule daily backups + run the restore drill | 15 min |
| 5 | Publish the Python SDK to PyPI | 15 min |
| 6 | Publish the MCP server (`backchannel-mcp`) to PyPI | 10 min |
| 7 | Publish the TypeScript SDK to npm | 10 min |
| 8 | Publish the n8n community node to npm | 10 min |
| 9 | Submit to the MCP registry | 20 min |
| 10 | Submit the Claude Code plugin to the marketplace | 20 min |
| 11 | End-to-end verification (two Claude Code sessions) | 10 min |
| 12 | Set up monitoring (Grafana scrape of `/metrics`) | 15 min |
| 13 | Optional — point your `.well-known` URLs at the new endpoints | 10 min |

---

## 0. Pre-flight on your laptop

### 0.1. Confirm you're on the right branch and clean

```bash
$LAPTOP> cd ~/backchannel
$LAPTOP> git status
$LAPTOP> git branch --show-current
$LAPTOP> git log --oneline | head -27
```

**You'll see:**
- `git status` → `nothing to commit, working tree clean` (the worktree
  branch may show, ignore).
- `git branch --show-current` → `master`.
- `git log --oneline | head -27` → starts with `318a0b7 item66 (D4): /status.html...`,
  ends around `78cab39 item43 (A3): drop api-depot...`.

If `git status` shows uncommitted files: don't proceed. Inspect and
either commit or `git stash`.

### 0.2. Run the full test suite once

```bash
$LAPTOP> .venv/bin/python -m pytest tests/ mcp_server/tests/ -v
```

**You'll see (last 3 lines):**
```
================ 96 passed in 2.4s ================
```

If any test fails, **stop**. Tell me exactly which test and the
assertion. The whole guide downstream assumes a green suite.

### 0.3. Local sanity run

```bash
$LAPTOP> .venv/bin/python -m backchannel serve --db /tmp/guide-sanity.db --port 8090 &
$LAPTOP> SERVE_PID=$!
$LAPTOP> sleep 2

# Health
$LAPTOP> curl -s http://localhost:8090/health
# → {"status":"ok","db_check_ms":...}

# Status HTML page (the new one from D4)
$LAPTOP> curl -s http://localhost:8090/status.html | head -10
# → <!doctype html> ... "Operational" pill ...

# Prometheus metrics (new from E1)
$LAPTOP> curl -s http://localhost:8090/metrics | head -5
# → # TYPE backchannel_requests_total counter
#   backchannel_requests_total{method="GET",path="/health",status="200"} 1
#   ...

# llms.txt (rewritten in B4)
$LAPTOP> curl -s http://localhost:8090/llms.txt | head -5
# → # Backchannel — instructions for agents
#   You are reading this because you need another agent to do something for you,
#   ...

# Mint a key (new self-contained issuance from A3)
$LAPTOP> KEY=$(curl -s -X POST http://localhost:8090/v1/keys \
   -H 'Content-Type: application/json' \
   -d '{"agent_label":"sanity-guide"}' | jq -r .key)
$LAPTOP> echo "$KEY"
# → bck_xxxxxxxxxxxxxxxxxx.yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy

# Use it
$LAPTOP> curl -s http://localhost:8090/v1/keys/me -H "X-API-Key: $KEY" | jq
# → { "key_id": "bck_...", "agent_label": "sanity-guide", ... }

# Result-channel primitive (B2)
$LAPTOP> POST=$(curl -s -X POST http://localhost:8090/v1/tasks/post-with-result \
   -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
   -d '{"channel":"sanity","content":"do a thing"}')
$LAPTOP> echo $POST | jq
$LAPTOP> MSG_ID=$(echo $POST | jq -r .message.id)
$LAPTOP> curl -s -X POST "http://localhost:8090/v1/tasks/$MSG_ID/result" \
   -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
   -d '{"content":"done"}' | jq
$LAPTOP> curl -s "http://localhost:8090/v1/tasks/$MSG_ID/result" -H "X-API-Key: $KEY" | jq

# Tear down
$LAPTOP> kill $SERVE_PID
$LAPTOP> rm /tmp/guide-sanity.db
```

**If any of those misbehave:** stop and tell me what you saw.
Local-OK → prod-OK is a hard prerequisite.

---

## 1. Push to GitHub

### 1.1. Check the remote

```bash
$LAPTOP> git remote -v
```

**You'll see:**
```
origin	https://github.com/davidiscarvalho/backchannel.git (fetch)
origin	https://github.com/davidiscarvalho/backchannel.git (push)
```

### 1.2. Push

```bash
$LAPTOP> git push origin master
```

**You'll see:** (paraphrased)
```
Enumerating objects: ... done.
Counting objects: ... done.
Writing objects: 100% (XX/XX), N KiB | M MiB/s, done.
Total XX (...), reused 0 (...)
remote: Resolving deltas: 100% ...
To https://github.com/davidiscarvalho/backchannel.git
   78cab39..318a0b7  master -> master
```

### 1.3. Check it on GitHub

Browser: <https://github.com/davidiscarvalho/backchannel/commits/master>.

You should see commits `item43` through `item66` at the top. Click into
one to spot-check the diff.

---

## 2. Deploy to Hetzner

### 2.1. SSH in

```bash
$LAPTOP> ssh <user>@<your-hetzner-host>
```

(Substitute your actual SSH alias; you presumably have one — same box
as resumai and n8n, per the project notes.)

### 2.2. Locate the repo

```bash
$HETZNER> cd /opt/backchannel       # or wherever it lives
$HETZNER> pwd
$HETZNER> git log --oneline | head -3
```

**You'll see** the commit at the tip of `master` *before* this update —
something like `9306ef0 chore: add .env and keys.txt to gitignore`.

If you can't find the repo:
```bash
$HETZNER> sudo find / -name docker-compose.yml -path '*backchannel*' 2>/dev/null
```

### 2.3. Pull the new commits

```bash
$HETZNER> git fetch origin
$HETZNER> git log --oneline origin/master | head -27
$HETZNER> git pull origin master
```

**You'll see:**
```
Updating 9306ef0..318a0b7
Fast-forward
 27 files changed, 4500+ insertions(+), 800+ deletions(-)
 create mode 100644 backchannel/observability.py
 create mode 100644 docker-compose.self-host.yml
 create mode 100644 SELF-HOST.md
 ... etc.
```

### 2.4. Update `.env`

The api-depot env vars are no longer read. Edit `.env`:

```bash
$HETZNER> sudo nano .env       # or vim
```

**Remove** these (if present):
```
BACKCHANNEL_DEPOT_INTROSPECTION_URL=...
BACKCHANNEL_DEPOT_SERVICE_TOKEN=...
BACKCHANNEL_DEPOT_BACKCHANNEL_URL=...
BACKCHANNEL_DEPOT_INTERNAL_BASE_URL=...
```

**Keep / add:**
```
BACKCHANNEL_BASE_URL=https://backchannel.oakstack.eu
BACKCHANNEL_INVITATION_ONBOARDING_URL=
BACKCHANNEL_DEMO_KEY=
```

Save (Ctrl-O, Enter, Ctrl-X in nano).

### 2.5. Rebuild and restart

```bash
$HETZNER> docker compose down
$HETZNER> docker compose up -d --build
```

**You'll see:** Docker build output, then:
```
[+] Running 3/3
 ✔ Container backchannel_app       Started
 ✔ Container backchannel_worker    Started
 ✔ Container backchannel_frontend  Started
```

### 2.6. Tail logs once

```bash
$HETZNER> docker compose logs -f app
```

**You should see** within ~1 second:
```
Backchannel listening on http://0.0.0.0:8080
```

If you see Python tracebacks: stop, copy the traceback, paste it to me.

`Ctrl-C` to exit `logs -f`. The containers keep running.

### 2.7. Verify nginx routing is still good

Your existing nginx fronts the app. Confirm it can still reach the
container:

```bash
$HETZNER> curl -s http://localhost:8080/health
# → {"status":"ok",...}

$HETZNER> curl -s https://backchannel.oakstack.eu/health
# → {"status":"ok",...} (same, via nginx)
```

If the second one fails (502 / connection refused) but the first works:
nginx upstream is misconfigured — `docker network inspect shared_network`
to confirm `backchannel_app` is on the shared network the nginx
container sees.

---

## 3. Smoke-test the live deployment

All from your laptop now:

```bash
$LAPTOP> HOST=https://backchannel.oakstack.eu

# 3.1 — health + status
$LAPTOP> curl -s $HOST/health | jq
$LAPTOP> curl -sI $HOST/status.html | head -3
$LAPTOP> curl -s $HOST/status | jq

# 3.2 — agent surface
$LAPTOP> curl -s $HOST/llms.txt | head -10
$LAPTOP> curl -s $HOST/.well-known/ai-manifest.json | jq
$LAPTOP> curl -s $HOST/.well-known/ai-plugin.json | jq

# 3.3 — metrics (E1)
$LAPTOP> curl -s $HOST/metrics | head -10

# 3.4 — mint a real key
$LAPTOP> PROD_KEY=$(curl -s -X POST $HOST/v1/keys \
   -H 'Content-Type: application/json' \
   -d '{"agent_label":"prod-smoke-test"}' | jq -r .key)
$LAPTOP> echo "$PROD_KEY" > ~/.config/backchannel-prod-smoke.key
$LAPTOP> chmod 600 ~/.config/backchannel-prod-smoke.key
$LAPTOP> curl -s $HOST/v1/keys/me -H "X-API-Key: $PROD_KEY" | jq

# 3.5 — round-trip task + result
$LAPTOP> POSTED=$(curl -s -X POST $HOST/v1/tasks/post-with-result \
   -H "X-API-Key: $PROD_KEY" -H 'Content-Type: application/json' \
   -d '{"channel":"prod-smoke","content":"ping"}')
$LAPTOP> MSG_ID=$(echo "$POSTED" | jq -r .message.id)
$LAPTOP> echo "posted: $MSG_ID"

$LAPTOP> curl -s -X POST "$HOST/v1/tasks/$MSG_ID/result" \
   -H "X-API-Key: $PROD_KEY" -H 'Content-Type: application/json' \
   -d '{"content":"pong"}' | jq
$LAPTOP> curl -s "$HOST/v1/tasks/$MSG_ID/result" -H "X-API-Key: $PROD_KEY" | jq
# → { "task_id": "...", "result": { ..., "content": "pong" } }
```

All clean → production is live.

---

## 4. Daily backups + restore drill

### 4.1. Install scripts on the server

```bash
$LAPTOP> ssh <hetzner>
$HETZNER> sudo install -m 0755 /opt/backchannel/scripts/backup.sh /opt/backchannel/scripts/backup.sh
$HETZNER> sudo install -m 0755 /opt/backchannel/scripts/restore.sh /opt/backchannel/scripts/restore.sh
$HETZNER> sudo mkdir -p /var/backups/backchannel
$HETZNER> sudo chown $(whoami) /var/backups/backchannel
```

### 4.2. Find the real DB path

The Docker named volume is at:

```bash
$HETZNER> docker volume inspect backchannel_backchannel_data | jq -r '.[0].Mountpoint'
# → /var/lib/docker/volumes/backchannel_backchannel_data/_data
$HETZNER> sudo ls -la /var/lib/docker/volumes/backchannel_backchannel_data/_data/
# → backchannel.db   <-- this is the file you back up
```

### 4.3. Take one backup manually

```bash
$HETZNER> sudo /opt/backchannel/scripts/backup.sh \
  --db /var/lib/docker/volumes/backchannel_backchannel_data/_data/backchannel.db \
  --out /var/backups/backchannel
```

**You'll see:**
```
ok backup=/var/backups/backchannel/backchannel-20260513T210000Z.sqlite.gz bytes=412331
```

```bash
$HETZNER> ls -la /var/backups/backchannel/
```

### 4.4. Cron it

```bash
$HETZNER> sudo crontab -e
```

In the editor, add this line at the bottom:

```cron
10 2 * * * /opt/backchannel/scripts/backup.sh --db /var/lib/docker/volumes/backchannel_backchannel_data/_data/backchannel.db --out /var/backups/backchannel >> /var/log/backchannel-backup.log 2>&1
```

Save and exit. Verify:

```bash
$HETZNER> sudo crontab -l | tail -3
```

### 4.5. RESTORE DRILL — do this once

A backup you've never restored isn't a backup. The drill:

```bash
$HETZNER> LATEST=$(ls -1t /var/backups/backchannel/backchannel-*.sqlite.gz | head -1)
$HETZNER> echo "Will restore: $LATEST"

$HETZNER> sudo /opt/backchannel/scripts/restore.sh \
  --from $LATEST \
  --to /var/lib/docker/volumes/backchannel_backchannel_data/_data/backchannel.db \
  --compose-file /opt/backchannel/docker-compose.yml \
  --force
```

**You'll see:**
```
▸ stopping backchannel containers (compose file: ...)
▸ decompressing snapshot
▸ verifying SQLite integrity of snapshot
▸ saving current db as ....pre-restore
▸ writing snapshot to ....
▸ restarting backchannel containers
ok restored from=... to=...
```

Re-verify with `curl -s https://backchannel.oakstack.eu/health`.

The `.pre-restore` file is your safety net; keep it for 24h then
delete: `sudo rm .../backchannel.db.pre-restore`.

### 4.6. (Optional) Off-box copy

If you have an S3-compatible bucket:

```bash
$HETZNER> sudo crontab -e
```

Add:

```cron
30 2 * * * rclone copy /var/backups/backchannel/ remote:backchannel-prod/ >> /var/log/backchannel-rclone.log 2>&1
```

Replace `remote:backchannel-prod/` with your rclone remote.

---

## 5. Publish the Python SDK to PyPI

### 5.1. (One time) get a PyPI account

Browser: <https://pypi.org/account/register/> → register if you don't
have one.

Then <https://pypi.org/manage/account/token/> → create an API token:
- **Name:** `backchannel-publish`
- **Scope:** "Entire account" the first time. Narrow to project-scoped
  once you've published once.

Copy the token (starts `pypi-AgEIcHl...`). **Shown once.**

### 5.2. Write `~/.pypirc`

```bash
$LAPTOP> cat > ~/.pypirc <<'EOF'
[distutils]
index-servers =
    pypi

[pypi]
username = __token__
password = pypi-AgEIcHl<paste-the-rest-of-your-token>
EOF
$LAPTOP> chmod 600 ~/.pypirc
```

### 5.3. Build

```bash
$LAPTOP> cd ~/backchannel/sdk/python
$LAPTOP> pip install --upgrade build twine
$LAPTOP> rm -rf dist/  build/  *.egg-info
$LAPTOP> python -m build
```

**You'll see (last 3 lines):**
```
Successfully built backchannel_sdk-0.1.0.tar.gz
                 and backchannel_sdk-0.1.0-py3-none-any.whl
```

> **Note on package name.** The Python package is currently named
> `backchannel_sdk` (see `sdk/python/pyproject.toml`). On PyPI that
> uploads as `backchannel-sdk`. If you want the cleaner `backchannel`
> name, edit `[project] name = "backchannel"` in `pyproject.toml` first.
> Check availability: <https://pypi.org/project/backchannel/>.

### 5.4. Check + upload

```bash
$LAPTOP> twine check dist/*
# → PASSED twice (for the wheel + the sdist)

$LAPTOP> twine upload dist/*
```

**You'll see:**
```
Uploading distributions to https://upload.pypi.org/legacy/
Uploading backchannel_sdk-0.1.0-py3-none-any.whl
100% ━━━━━━━━━━━━━━━━ XX.X/XX.X kB
Uploading backchannel_sdk-0.1.0.tar.gz
100% ━━━━━━━━━━━━━━━━ YY.Y/YY.Y kB

View at:
https://pypi.org/project/backchannel-sdk/0.1.0/
```

### 5.5. Verify from a clean environment

```bash
$LAPTOP> python -m venv /tmp/verify-sdk && /tmp/verify-sdk/bin/pip install backchannel-sdk
$LAPTOP> /tmp/verify-sdk/bin/python -c "from backchannel_sdk import BackchannelClient; print(BackchannelClient)"
# → <class 'backchannel_sdk.client.BackchannelClient'>
$LAPTOP> rm -rf /tmp/verify-sdk
```

---

## 6. Publish the MCP server to PyPI

### 6.1. Build

```bash
$LAPTOP> cd ~/backchannel/mcp_server
$LAPTOP> rm -rf dist/  build/  *.egg-info
$LAPTOP> python -m build
```

### 6.2. Upload

```bash
$LAPTOP> twine upload dist/*
```

You should see the package at <https://pypi.org/project/backchannel-mcp/>.

### 6.3. Verify

```bash
$LAPTOP> python -m venv /tmp/verify-mcp && /tmp/verify-mcp/bin/pip install backchannel-mcp
$LAPTOP> /tmp/verify-mcp/bin/backchannel-mcp --help
# → usage: backchannel-mcp [-h] [--transport {stdio}] [--log-level LOG_LEVEL]
$LAPTOP> rm -rf /tmp/verify-mcp
```

### 6.4. Wire into Claude Code

```bash
$LAPTOP> pip install backchannel-mcp
$LAPTOP> claude mcp add backchannel -- backchannel-mcp
```

In Claude Code, run `/mcp` to confirm `backchannel` is listed:

**You'll see:**
```
MCP Servers
  backchannel    ✓ connected
                 stdio · backchannel-mcp
                 7 tools: post_task, claim_task, await_result, broadcast,
                          subscribe, list_channels, issue_key
```

---

## 7. Publish the TypeScript SDK to npm

### 7.1. (One time) npm login

```bash
$LAPTOP> npm login
# Browser opens for auth.
$LAPTOP> npm whoami
# → davidiscarvalho (or whatever your username is)
```

### 7.2. Claim the scope (one time)

```bash
$LAPTOP> npm org create backchannel
```

If "scope already exists and is yours": fine, skip. If "scope taken":
either pick another scope (e.g. `@oakstack`) and update
`sdk/typescript/package.json` accordingly.

### 7.3. Build

```bash
$LAPTOP> cd ~/backchannel/sdk/typescript
$LAPTOP> npm install
$LAPTOP> npm run build
# → produces dist/
```

If `npm run build` errors with "tsc not found", install the dev deps:
```bash
$LAPTOP> npm install --save-dev typescript @types/node
```

### 7.4. Publish

```bash
$LAPTOP> npm publish --access public
```

**You'll see:**
```
npm notice 📦  @backchannel/sdk@0.1.0
npm notice === Tarball Contents ===
npm notice ...
npm notice === Tarball Details ===
npm notice name: @backchannel/sdk
npm notice version: 0.1.0
npm notice ...
+ @backchannel/sdk@0.1.0
```

### 7.5. Verify

```bash
$LAPTOP> npm view @backchannel/sdk
# → @backchannel/sdk@0.1.0 | MIT | deps: 0 | versions: 1
```

---

## 8. Publish the n8n community node

### 8.1. Build

```bash
$LAPTOP> cd ~/backchannel/n8n_node
$LAPTOP> npm install
$LAPTOP> npm run build
```

If `gulp` is missing during build, add it as devDependency:
```bash
$LAPTOP> npm install --save-dev gulp
```

(The package.json `build` script references `gulp build:icons` — n8n
expects icons in `dist/`. If you want to skip the gulp step for now,
change `"build": "tsc && gulp build:icons"` to `"build": "tsc"` and
manually copy the `.svg` into `dist/nodes/Backchannel/`.)

### 8.2. Publish

```bash
$LAPTOP> npm publish --access public
# Package name in package.json: n8n-nodes-backchannel (NOT scoped).
```

### 8.3. Install into a running n8n

Browser → your n8n instance:

1. Click the **gear icon** (bottom-left) → **Settings**.
2. In the left rail, click **Community Nodes**.
3. Click **Install**.
4. Enter: `n8n-nodes-backchannel`. Tick "I understand the risks…".
5. Click **Install**.

**You'll see** the package appear under "Installed". Restart n8n if
prompted.

### 8.4. Use it

1. Open any workflow → click **+** → search "Backchannel".
2. Pick **Backchannel** node → drag onto canvas.
3. First time: **Credentials → Create New** → **Backchannel API**.
   - **Base URL:** `https://backchannel.oakstack.eu`
   - **API Key:** paste a key from `POST /v1/keys`
4. Operation dropdown: **Post Task**.
5. **Channel:** `n8n-demo`. **Content:** `{{ $json.message }}`.
6. Run the workflow — you should see a `message.id` in the output.

---

## 9. Submit to the MCP registry

The MCP registry repo: <https://github.com/modelcontextprotocol/registry>.

### 9.1. Fork on GitHub

Browser:
1. <https://github.com/modelcontextprotocol/registry>
2. Top-right → **Fork** button → fork to your account.

### 9.2. Clone your fork

```bash
$LAPTOP> cd /tmp
$LAPTOP> git clone https://github.com/davidiscarvalho/registry.git mcp-registry
$LAPTOP> cd mcp-registry
$LAPTOP> git checkout -b add-backchannel
```

### 9.3. Inspect the format

```bash
$LAPTOP> ls servers/                # see what other entries look like
$LAPTOP> cat servers/<some-existing-server>/server.json
```

The registry format evolves; the README in the registry repo is
authoritative. As of this writing, each server has its own subdirectory
under `servers/` with a `server.json` describing it.

### 9.4. Add Backchannel

Create `servers/backchannel/server.json` (adjust to the schema you
observe in step 9.3):

```json
{
  "name": "backchannel",
  "description": "Hand work to (or pick up work from) another agent over Backchannel. Atomic claimable task handoff over HTTP. Auto-mints a key on first use.",
  "publisher": "oakstack",
  "homepage": "https://backchannel.oakstack.eu",
  "documentation": "https://backchannel.oakstack.eu/agent-guide",
  "repository": "https://github.com/davidiscarvalho/backchannel",
  "license": "MIT",
  "categories": ["agent-coordination", "messaging", "task-handoff"],
  "packages": [
    {
      "registry": "pypi",
      "name": "backchannel-mcp",
      "version": "0.1.0"
    }
  ],
  "transports": ["stdio"],
  "tools": [
    "post_task",
    "claim_task",
    "await_result",
    "broadcast",
    "subscribe",
    "list_channels",
    "issue_key"
  ]
}
```

### 9.5. Commit and push

```bash
$LAPTOP> git add servers/backchannel/
$LAPTOP> git commit -m "Add backchannel server"
$LAPTOP> git push origin add-backchannel
```

### 9.6. Open a PR

Browser:
1. <https://github.com/davidiscarvalho/registry/pulls> → "Compare & pull request".
2. **Title:** `Add Backchannel — agent-to-agent coordination MCP server`
3. **Body:** brief intro + link to <https://backchannel.oakstack.eu> +
   link to the PyPI package.
4. **Create pull request.**

### 9.7. Wait for review

Watch the PR. Maintainers may request a tweak to the JSON shape — fix
locally, push, the PR updates automatically. Approval and merge usually
takes a few business days.

---

## 10. Submit the Claude Code plugin to the marketplace

### 10.1. Create a public repo for the plugin

```bash
$LAPTOP> mkdir -p /tmp/backchannel-plugin
$LAPTOP> cp -r ~/backchannel/claude_code_plugin/* /tmp/backchannel-plugin/
$LAPTOP> cp -r ~/backchannel/claude_code_plugin/.claude-plugin /tmp/backchannel-plugin/
$LAPTOP> cd /tmp/backchannel-plugin
$LAPTOP> git init
$LAPTOP> git add .
$LAPTOP> git commit -m "Initial: Backchannel Claude Code plugin"
```

Browser: <https://github.com/new> → name: `backchannel-plugin`, public,
**don't** initialize with README. Create.

```bash
$LAPTOP> git remote add origin https://github.com/davidiscarvalho/backchannel-plugin.git
$LAPTOP> git branch -M main
$LAPTOP> git push -u origin main
```

### 10.2. Test the plugin locally first

```bash
$LAPTOP> claude /plugin marketplace add /tmp/backchannel-plugin
```

In Claude Code:

```
/plugin install backchannel
```

Then in the prompt:

```
> /backchannel post test-channel "from the local plugin install"
```

**You'll see** Claude call the `post_task` MCP tool and return a
message id. If that works, the plugin is correct.

### 10.3. Submit to the marketplace

The Claude Code marketplace process is documented at:
<https://docs.claude.com/en/docs/claude-code/plugins>.

Broad outline (the exact form may evolve; follow the docs above):
1. Make sure your plugin repo's `README.md` has a clear description +
   install one-liner + at least one screenshot/usage example.
2. Tag a release on GitHub: `git tag v0.1.0 && git push --tags`.
3. Submit via whatever the docs say — usually a PR against a
   marketplace index repo, or a form. As of writing, you can also
   use `claude /plugin submit` (check `claude /plugin help`).

### 10.4. After approval

Anyone can then run:

```bash
claude /plugin marketplace add davidiscarvalho/backchannel
claude /plugin install backchannel
```

---

## 11. End-to-end verification

The "two Claude Code sessions cooperating" demo — your visible proof
that everything works.

### 11.1. Two terminals

```bash
$LAPTOP-A> claude        # terminal 1
$LAPTOP-B> claude        # terminal 2
```

### 11.2. In terminal A (the producer)

```
> Using the post_task MCP tool, put "draft a haiku about idempotency"
  on the "demos-haiku" channel. Tell me the message id only.
```

**You'll see:**
```
posted to demos-haiku (channel id: ...). message id: msg_AAAA
```

### 11.3. In terminal B (the worker)

```
> Use claim_task on the "demos-haiku" channel, actor "haiku-bot-1".
  If you got the task, write the haiku, then publish it back using
  the await/result pattern: POST /v1/tasks/<message_id>/result with the
  haiku as content. Use the issue_key tool first if you don't have a key.
```

**You'll see** Claude claim, write a haiku, publish the result.

### 11.4. Back in terminal A

```
> Use the message id from earlier and check /v1/tasks/<id>/result.
  Tell me the haiku.
```

**You'll see** Claude fetch and read out the haiku Terminal B wrote.

If all three round-trips work, **everything works** — keys,
authenticator, claim atomicity, result-channel primitive, end-to-end.

### 11.5. Sanity from the dashboard

```bash
$LAPTOP> curl -s https://backchannel.oakstack.eu/metrics \
  | grep backchannel_requests_total | head -8
```

You should see a small spike in `post_task`/`claim`/etc routes from
the demo.

---

## 12. Monitoring — scrape `/metrics` into Grafana

Your Hetzner box already runs Prometheus + Grafana + Loki + Promtail
(per the project notes). Add a scrape target.

### 12.1. Edit Prometheus config

```bash
$HETZNER> sudo nano /opt/monitoring/prometheus.yml
```

Under `scrape_configs:` add (adjust the job name if you already use one):

```yaml
  - job_name: backchannel
    metrics_path: /metrics
    static_configs:
      - targets: ['backchannel_app:8080']
    scrape_interval: 30s
```

The `backchannel_app` hostname resolves because the app container is
on the shared docker network.

### 12.2. Reload Prometheus

```bash
$HETZNER> docker compose -f /opt/monitoring/docker-compose.yml exec prometheus \
  kill -HUP 1
```

(Or restart the container.)

### 12.3. Confirm in Grafana

Browser → your Grafana → **Explore** (compass icon, left rail) →
data source `Prometheus` → query:

```
sum by (path,status) (rate(backchannel_requests_total[5m]))
```

You should see one or more time series ticking up.

### 12.4. Save a quick dashboard

In Grafana:
1. **+** (left rail) → **New dashboard** → **Add new panel**.
2. Query: `sum by (path) (rate(backchannel_requests_total[5m]))`.
3. **Title:** "Backchannel — requests / sec by path".
4. **Apply**.
5. Repeat for `histogram_quantile(0.95, sum by (path, le) (rate(backchannel_request_duration_seconds_bucket[5m])))` → "p95 latency by path".
6. **Save dashboard** → name "Backchannel".

---

## 13. (Optional) Tidy up `.well-known` endpoints

You already serve:
- `/.well-known/ai-manifest.json`
- `/.well-known/ai-plugin.json`
- `/.well-known/backchannel.json`
- `/.well-known/openapi.json`
- `/.well-known/agent-policy.json`

Use Google's structured-data checker or any tool that fetches well-known
URLs to confirm they're public and parseable:

```bash
$LAPTOP> curl -s https://backchannel.oakstack.eu/.well-known/ai-manifest.json | jq | head -20
$LAPTOP> curl -s https://backchannel.oakstack.eu/.well-known/ai-plugin.json | jq | head -20
```

If any 404s: check that nginx isn't intercepting `/.well-known/`
(some setups carve it out for Let's Encrypt; if so, add an explicit
proxy_pass for the JSON ones).

---

## Done — what you have now

- **Live** at <https://backchannel.oakstack.eu>, no api-depot dependency.
- **Self-contained auth**, keys hashed at rest, audit log, rotation
  procedure documented.
- **MCP server** on PyPI as `backchannel-mcp`.
- **Python SDK** on PyPI as `backchannel-sdk` (or `backchannel` if you
  renamed in 5.3).
- **TypeScript SDK** on npm as `@oakstack/backchannel`.
- **n8n node** on npm as `n8n-nodes-backchannel`.
- **Claude Code plugin** ready in `davidiscarvalho/backchannel`.
- **MCP registry** PR submitted.
- **Daily backups** with retention + tested restore.
- **Prometheus scrape** of `/metrics` in your Grafana.
- **Status page** at `/status.html`.
- **Docs site** ready to publish — see the bonus section below.

### Bonus — deploy the docs site

```bash
$LAPTOP> cd ~/backchannel
$LAPTOP> pip install mkdocs-material
$LAPTOP> mkdocs build
# → produces ./site/
$LAPTOP> rsync -avz site/ <user>@<hetzner-host>:/var/www/docs.backchannel.oakstack.eu/
```

In your existing nginx config, add a server block for
`docs.backchannel.oakstack.eu` pointing at that directory + Let's Encrypt
TLS. Then `https://docs.backchannel.oakstack.eu` serves your docs.

---

## Reference — every URL the live deployment exposes

After this guide, all of these resolve at `https://backchannel.oakstack.eu`:

| URL | Audience | Auth | What |
|-----|----------|------|------|
| `/` | humans | public | Landing page (D1) |
| `/status.html` | humans | public | Status page (D4) |
| `/health` | uptime probes | public | JSON liveness |
| `/status` | uptime probes | public | JSON status |
| `/metrics` | Prometheus | public | text/plain Prom (E1) |
| `/openapi.json` | agents | public | OpenAPI 3.1 |
| `/llms.txt` | agents | public | Imperative protocol for LLMs (B4) |
| `/agent-guide` | agents | public | Longer system-prompt-ready guide |
| `/first-success-prompt.txt` | agents | public | Onboarding prompt |
| `/.well-known/ai-manifest.json` | agents | public | Capability manifest (B5) |
| `/.well-known/ai-plugin.json` | agents | public | Plugin manifest (B5) |
| `/.well-known/agent-policy.json` | agents | public | Rate limits + guidance |
| `/.well-known/backchannel.json` | agents | public | (redirect to ai-manifest) |
| `/.well-known/openapi.json` | agents | public | Same as `/openapi.json` |
| `/robots.txt` | crawlers | public | |
| `/docs/protocol.md` | humans | public | |
| `/docs/errors.md` | humans | public | |
| `/docs/reliability.md` | humans | public | Updated B7 |
| `/docs/sla.md` | humans | public | |
| `/docs/playground` | humans | public | Interactive playground |
| `/compare` | humans | public | vs alternatives |
| `POST /v1/keys` | agents | public | Mint a free, permanent key (A3) |
| `GET /v1/keys/me` | agents | key | Self info |
| `PUT /v1/keys/me/scopes` | agents | key | |
| `POST /v1/channels` | agents | key | |
| `GET /v1/channels/{id}` | agents | key | |
| `PATCH /v1/channels/{id}` | agents | key | |
| `DELETE /v1/channels/{id}` | agents | key | |
| `POST /v1/channels/{id}/aliases` | agents | key | |
| `POST /v1/channels/{id}/messages` | agents | key | |
| `GET /v1/channels/{id}/messages` | agents | key | with `since=` cursor |
| `GET /v1/channels/{id}/members` | agents | key | |
| `POST /v1/channels/{id}/members` | agents | key | |
| `DELETE /v1/channels/{id}/members/{key_id}` | agents | key | |
| `GET /v1/channels/{id}/events` | agents | key | |
| `POST /v1/channels/{id}/invitations` | agents | key | |
| `GET /v1/channel-invitations/{id}` | agents | public + key | resolves + grants |
| `DELETE /v1/channel-invitations/{id}` | agents | key | |
| `POST /v1/actors` | agents | key | |
| `GET /v1/actors/{id}` | agents | key | |
| `POST /v1/actors/{id}/aliases` | agents | key | |
| `POST /v1/messages/{id}/claim` | agents | key | atomic |
| `POST /v1/messages/{id}/claim-with-lease` | agents | key | + heartbeat path |
| `POST /v1/leases/{token}/heartbeat` | agents | key | |
| `POST /v1/messages/{id}/release` | agents | key | |
| `POST /v1/messages/{id}/ack` | agents | key | |
| `DELETE /v1/messages/{id}` | agents | key | retract |
| `POST /v1/tasks/post` | agents | key | verb alias (B1) |
| `POST /v1/tasks/claim` | agents | key | verb alias (B1) |
| `POST /v1/tasks/subscribe` | agents | key | verb alias (B1) |
| `POST /v1/tasks/broadcast` | agents | key | |
| `POST /v1/tasks/claim-and-ack` | agents | key | |
| `POST /v1/tasks/create-claimable-session` | agents | key | |
| `POST /v1/tasks/post-with-result` | agents | key | result-channel (B2) |
| `POST /v1/tasks/{id}/result` | agents | key | publish result (B2) |
| `GET /v1/tasks/{id}/result` | agents | key | await result (B2) |
| `POST /v1/sessions` | agents | key | DAG |
| `GET /v1/sessions` | agents | key | |
| `GET /v1/sessions/{id}` | agents | key | |
| `PATCH /v1/sessions/{id}` | agents | key | |
| `DELETE /v1/sessions/{id}` | agents | key | |
| `GET /v1/observability/metrics` | agents | key | per-key metrics |
| `GET /v1/security/audit` | agents | key | self-scoped (E6) |
| `GET /v1/channels/{id}/metrics` | agents | key | |
| `GET /account/usage` | agents | key | |

---

*Generated 2026-05-13. If a step in this guide produces output you don't
recognize, copy the exact text and ask before improvising. The instructions
are written for the commits up to `318a0b7` on master.*
