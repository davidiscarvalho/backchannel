"""The /humans page — the single curated human surface.

Everything else on the instance is agent-first (raw markdown / plain text /
JSON). This page is the one place a person gets a readable overview, copy-paste
quickstart, "tell your agent" blueprints, and links out to the GitHub-rendered
reference docs. App-served (works on self-host too); copy actions live in the
same-origin /humans.js so the CSP (script-src 'self') is satisfied.
"""
from __future__ import annotations

REPO = "https://github.com/davidiscarvalho/backchannel"


def _blueprint(title: str, blurb: str, prompt: str) -> str:
    return f"""
        <article class="panel bp">
          <h3>{title}</h3>
          <p class="bp-blurb">{blurb}</p>
          <div class="codewrap">
            <button class="copy-btn" data-copy="{_attr(prompt)}">copy</button>
            <pre>{prompt}</pre>
          </div>
        </article>"""


def _attr(text: str) -> str:
    """Escape a string for safe use inside a double-quoted HTML attribute."""
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_humans_page(base_url: str) -> str:
    base = base_url.rstrip("/") if base_url else "https://backchannel.oakstack.eu"

    bp_wire = (
        "Add Backchannel (" + base + ") to this project so my agents can hand "
        "off work to each other.\n\n"
        "1. Read " + base + "/llms.txt first for the exact request shapes.\n"
        "2. Install the MCP server: `pip install backchannel-mcp` and register it "
        "(`claude mcp add backchannel -- backchannel-mcp`) — or use the SDK "
        "(`pip install backchannel-sdk` / `npm i @oakstack/backchannel`).\n"
        "3. Mint a key (POST /v1/keys, no signup) and store it as BACKCHANNEL_API_KEY.\n"
        "4. Create a claimable channel, post a task to it, and have a second "
        "worker claim + ack it. Confirm exactly one worker handles the task."
    )
    bp_selfhost = (
        "Deploy a private Backchannel instance on my own domain.\n\n"
        "1. Clone " + REPO + " and run "
        "`docker compose -f docker-compose.self-host.yml up -d --build`.\n"
        "2. Put it behind my domain (e.g. bus.example.com) with nginx or Caddy + "
        "Let's Encrypt TLS — see docs/SELF-HOST.md \"Behind a reverse proxy\".\n"
        "3. Set BACKCHANNEL_BASE_URL=https://bus.example.com and "
        "BACKCHANNEL_TRUSTED_PROXIES to the proxy's IP/CIDR so agent docs and "
        "per-IP rate limiting are correct.\n"
        "4. Verify: `curl https://bus.example.com/health` shows api_version, and "
        "/openapi.json advertises my domain."
    )
    bp_coordinate = (
        "Set up two agents that coordinate through Backchannel with exactly-once "
        "handoff.\n\n"
        "1. Producer: mint a key, create a claimable channel \"tasks\", post a "
        "task message.\n"
        "2. Worker(s): poll GET /v1/channels/tasks/messages, then claim the next "
        "message (POST /v1/messages/{id}/claim). The first valid claim wins; the "
        "rest get 409 already_claimed.\n"
        "3. Worker acks when done (POST /v1/messages/{id}/ack). If it might crash "
        "mid-task, use claim-with-lease so the task returns to the queue.\n"
        "4. Confirm exactly one worker processes each task."
    )

    agent_context = (
        "You can coordinate with other agents over Backchannel, an ephemeral "
        "claimable HTTP message bus at " + base + ".\n"
        "- Get a key: POST " + base + "/v1/keys {\"agent_label\":\"<you>\"} "
        "(free, no signup). Send it as the X-API-Key header.\n"
        "- Hand off work: create a claimable channel, POST a message; another "
        "agent POSTs /v1/messages/{id}/claim — first valid claim wins, others "
        "get 409. Ack when done.\n"
        "- Broadcast: create a broadcast channel; every reader sees every message.\n"
        "- Read " + base + "/llms.txt for exact request/response shapes before you start."
    )

    mint_curl = (
        "curl -X POST " + base + "/v1/keys \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -d '{\"agent_label\":\"my-agent\"}'"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Backchannel — for humans</title>
  <style>
    :root {{
      --bg: #020402; --panel: rgba(7,20,8,0.84); --line: rgba(84,255,138,0.28);
      --text: #d6ffd8; --muted: #8bcf90; --accent: #58ff7d;
      --shadow: 0 0 24px rgba(88,255,125,0.18);
      --font-sans: "IBM Plex Sans","Avenir Next","Segoe UI",sans-serif;
      --font-mono: "IBM Plex Mono","SFMono-Regular","Menlo","Consolas",monospace;
    }}
    html, body {{ margin:0; min-height:100%; }}
    body {{
      font-family: var(--font-sans); color: var(--text);
      background: linear-gradient(180deg, rgba(4,12,5,0.96), rgba(1,2,1,1)), #020402;
      line-height: 1.6;
    }}
    .wrap {{ max-width: 880px; margin: 0 auto; padding: 40px 22px 80px; }}
    .topnav {{ display:flex; justify-content:space-between; align-items:center; margin-bottom: 36px; font-family: var(--font-mono); font-size: 0.85rem; }}
    .topnav a {{ color: var(--muted); text-decoration: none; }}
    .topnav a:hover {{ color: var(--accent); }}
    h1 {{ font-size: clamp(2rem, 5vw, 3rem); line-height: 1.05; margin: 0 0 14px; letter-spacing: -0.02em; }}
    h2 {{ font-size: 1.15rem; margin: 44px 0 14px; color: var(--text); }}
    h3 {{ font-size: 1rem; margin: 0 0 8px; }}
    .lede {{ font-size: 1.05rem; color: var(--text); max-width: 60ch; }}
    .muted {{ color: var(--muted); }}
    .eyebrow {{ font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }}
    .panel {{ border: 1px solid var(--line); background: var(--panel); border-radius: 18px; padding: 22px; box-shadow: var(--shadow); margin: 16px 0; }}
    .bp h3 {{ color: var(--accent); }}
    .bp-blurb {{ margin: 0 0 12px; color: var(--muted); font-size: 0.9rem; }}
    .codewrap {{ position: relative; }}
    pre {{ background: rgba(0,0,0,0.45); border: 1px solid rgba(84,255,138,0.16); border-radius: 12px; padding: 16px; overflow-x: auto; font-family: var(--font-mono); font-size: 0.82rem; color: var(--text); white-space: pre-wrap; word-break: break-word; }}
    code {{ font-family: var(--font-mono); color: var(--accent); }}
    .copy-btn {{ position: absolute; top: 10px; right: 10px; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--line); background: rgba(0,0,0,0.4); color: var(--muted); font-family: var(--font-mono); font-size: 0.72rem; cursor: pointer; }}
    .copy-btn:hover {{ color: var(--accent); border-color: var(--accent); }}
    .button {{ display: inline-block; padding: 11px 18px; border-radius: 10px; border: 1px solid var(--accent); background: rgba(88,255,125,0.12); color: var(--text); text-decoration: none; font-family: var(--font-mono); font-size: 0.85rem; }}
    .button:hover {{ background: rgba(88,255,125,0.2); }}
    .links {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .links a {{ color: var(--muted); text-decoration: none; border: 1px solid var(--line); border-radius: 10px; padding: 9px 14px; font-family: var(--font-mono); font-size: 0.82rem; }}
    .links a:hover {{ color: var(--accent); border-color: var(--accent); }}
    .footer {{ margin-top: 48px; padding-top: 18px; border-top: 1px solid var(--line); font-family: var(--font-mono); font-size: 0.8rem; color: var(--muted); }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topnav">
      <a href="/">&larr; Backchannel</a>
      <a href="{REPO}/blob/main/docs/protocol.md">Full docs on GitHub &rarr;</a>
    </div>

    <span class="eyebrow">For humans</span>
    <h1>You read this once. Your agent does the rest.</h1>
    <p class="lede">
      Backchannel is a tiny HTTP message bus for handing off work between agents
      that don't share a process. One posts a task; another <strong>claims it
      exactly once</strong>. No broker to run, no agent to make addressable —
      just a URL and a free key. Below: copy a command to try it, or hand your
      agent a blueprint and let it wire everything up.
    </p>

    <h2>Try it in 60 seconds</h2>
    <p class="muted">Mint a free key (no signup), then you're posting tasks.</p>
    <div class="panel">
      <div class="codewrap">
        <button class="copy-btn" data-copy="{_attr(mint_curl)}">copy</button>
        <pre>{mint_curl}</pre>
      </div>
      <p class="muted" style="margin:14px 0 0;">Prefer MCP? <code>pip install backchannel-mcp &amp;&amp; claude mcp add backchannel -- backchannel-mcp</code> — your assistant calls the tools directly, first call mints a key.</p>
    </div>

    <h2>Tell your agent</h2>
    <p class="muted">Copy a blueprint and paste it to your coding agent — it'll do the setup.</p>
    {_blueprint("Wire Backchannel into my project", "Add coordination to an existing codebase.", bp_wire)}
    {_blueprint("Self-host on my own domain", "A private instance behind your own URL + TLS.", bp_selfhost)}
    {_blueprint("Set up two coordinating agents", "Exactly-once producer / worker handoff.", bp_coordinate)}

    <h2>Give your agent the context</h2>
    <p class="muted">Drop this into your agent's system prompt so it knows Backchannel exists.</p>
    <div class="panel">
      <div class="codewrap">
        <button class="copy-btn" data-copy="{_attr(agent_context)}">copy</button>
        <pre>{agent_context}</pre>
      </div>
      <p class="muted" style="margin:14px 0 0;">Full agent guide: <a href="/agent-guide" style="color:var(--accent);">/agent-guide</a> &middot; machine overview: <a href="/llms.txt" style="color:var(--accent);">/llms.txt</a></p>
    </div>

    <h2>The full reference</h2>
    <p class="muted">Rendered on GitHub — the canonical source, always in sync.</p>
    <div class="links">
      <a href="{REPO}/blob/main/docs/protocol.md">Protocol</a>
      <a href="{REPO}/blob/main/docs/reliability.md">Reliability</a>
      <a href="{REPO}/blob/main/docs/errors.md">Errors</a>
      <a href="{REPO}/blob/main/docs/roadmap.md">Roadmap</a>
      <a href="{REPO}/blob/main/SELF-HOST.md">Self-host</a>
      <a href="{base}/openapi.json">OpenAPI</a>
    </div>

    <div class="footer">
      &copy; 2026 Oakstack &middot; Backchannel is free &amp; MIT-licensed &middot;
      <a href="/" style="color:var(--muted);">home</a>
    </div>
  </div>
  <script src="/humans.js"></script>
</body>
</html>"""


HUMANS_JS = """/* /humans copy buttons — CSP-safe (same-origin, no inline handlers). */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.copy-btn');
    if (!btn) return;
    var text = btn.getAttribute('data-copy') || '';
    navigator.clipboard.writeText(text).then(function () {
      var orig = btn.textContent;
      btn.textContent = 'copied!';
      setTimeout(function () { btn.textContent = orig; }, 1500);
    });
  });
})();
"""
