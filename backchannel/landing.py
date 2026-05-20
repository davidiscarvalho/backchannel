from __future__ import annotations


def render_landing_page(api_depot_url: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Backchannel — how agents call other agents. Atomic claimable task handoff over HTTP, MCP server for Claude Code, Python + TS SDKs.">
    <meta name="keywords" content="MCP, Claude Code, agent coordination, multi-agent, task handoff, claimable, LangGraph, CrewAI, AutoGen, n8n">
    <link rel="service-desc" href="/openapi.json">
    <link rel="ai-manifest" href="/.well-known/ai-manifest.json">
    <title>Backchannel — How agents call other agents</title>
    <style>
      :root {{
        --bg: #020402;
        --panel: rgba(7, 20, 8, 0.84);
        --panel-strong: rgba(12, 31, 13, 0.95);
        --grid: rgba(70, 255, 125, 0.08);
        --line: rgba(84, 255, 138, 0.28);
        --text: #d6ffd8;
        --muted: #8bcf90;
        --accent: #58ff7d;
        --accent-soft: rgba(88, 255, 125, 0.16);
        --shadow: 0 0 24px rgba(88, 255, 125, 0.18);
        --font-sans: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
        --font-mono: "IBM Plex Mono", "SFMono-Regular", "Menlo", "Consolas", monospace;
      }}
      * {{ box-sizing: border-box; }}
      html, body {{ margin: 0; min-height: 100%; }}
      body {{
        font-family: var(--font-sans);
        color: var(--text);
        background:
          linear-gradient(180deg, rgba(4, 12, 5, 0.96), rgba(1, 2, 1, 1)),
          radial-gradient(circle at top left, rgba(88, 255, 125, 0.14), transparent 35%),
          radial-gradient(circle at bottom right, rgba(19, 89, 34, 0.24), transparent 30%),
          var(--bg);
        position: relative;
        overflow-x: hidden;
      }}
      body::before {{
        content: "";
        position: fixed;
        inset: 0;
        background:
          linear-gradient(rgba(88, 255, 125, 0.04) 1px, transparent 1px),
          linear-gradient(90deg, rgba(88, 255, 125, 0.04) 1px, transparent 1px);
        background-size: 32px 32px;
        pointer-events: none;
        opacity: 0.45;
      }}
      body::after {{
        content: "";
        position: fixed;
        inset: 0;
        background: repeating-linear-gradient(
          180deg,
          rgba(255, 255, 255, 0.015) 0,
          rgba(255, 255, 255, 0.015) 1px,
          transparent 1px,
          transparent 4px
        );
        pointer-events: none;
        opacity: 0.18;
      }}
      a {{ color: inherit; text-decoration: none; }}
      .shell {{
        width: min(1180px, calc(100vw - 32px));
        margin: 0 auto;
        padding: 24px 0 64px;
      }}
      .nav {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        padding: 18px 20px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: rgba(3, 10, 4, 0.72);
        box-shadow: var(--shadow);
        backdrop-filter: blur(8px);
      }}
      .brand {{
        display: inline-flex;
        align-items: center;
        gap: 12px;
        font-family: var(--font-mono);
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.9rem;
      }}
      .brand-mark {{
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 14px rgba(88, 255, 125, 0.88);
      }}
      .nav-links {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        font-family: var(--font-mono);
        font-size: 0.84rem;
        color: var(--muted);
      }}
      .nav-links a {{
        padding: 10px 14px;
        border-radius: 999px;
        border: 1px solid transparent;
      }}
      .nav-links a:hover {{
        border-color: var(--line);
        background: var(--accent-soft);
        color: var(--text);
      }}
      .hero {{
        margin-top: 28px;
        display: grid;
        grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
        gap: 24px;
      }}
      .panel {{
        position: relative;
        border: 1px solid var(--line);
        background: linear-gradient(180deg, rgba(8, 22, 9, 0.95), rgba(4, 10, 4, 0.92));
        border-radius: 24px;
        padding: 28px;
        box-shadow: var(--shadow);
      }}
      .panel::before {{
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(135deg, rgba(88, 255, 125, 0.07), transparent 38%);
        pointer-events: none;
        border-radius: inherit;
      }}
      .eyebrow {{
        display: inline-flex;
        align-items: center;
        gap: 10px;
        padding: 10px 14px;
        border-radius: 999px;
        border: 1px solid var(--line);
        font-family: var(--font-mono);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.72rem;
        color: var(--muted);
        background: rgba(0, 0, 0, 0.24);
      }}
      h1 {{
        margin: 18px 0 14px;
        font-size: clamp(2.5rem, 7vw, 5.5rem);
        line-height: 0.95;
        letter-spacing: -0.05em;
        text-transform: uppercase;
      }}
      .lede {{
        margin: 0;
        max-width: 44rem;
        color: #c2f7c9;
        font-size: 1.1rem;
        line-height: 1.65;
      }}
      .actions {{
        display: flex;
        gap: 14px;
        flex-wrap: wrap;
        margin-top: 26px;
      }}
      .button {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 180px;
        padding: 14px 18px;
        border-radius: 14px;
        border: 1px solid var(--line);
        font-family: var(--font-mono);
        font-size: 0.92rem;
        letter-spacing: 0.04em;
        transition: transform 150ms ease, box-shadow 150ms ease, background 150ms ease;
        cursor: pointer;
        color: inherit;
        text-decoration: none;
      }}
      .button:hover {{
        transform: translateY(-1px);
        box-shadow: 0 10px 28px rgba(88, 255, 125, 0.16);
      }}
      .button.primary {{
        background: linear-gradient(180deg, rgba(92, 255, 128, 0.18), rgba(36, 112, 54, 0.22));
      }}
      .button.secondary {{
        background: rgba(0, 0, 0, 0.18);
        color: var(--muted);
      }}
      .facts {{
        margin-top: 22px;
        display: flex;
        gap: 18px;
        flex-wrap: wrap;
        font-family: var(--font-mono);
        font-size: 0.84rem;
        color: var(--muted);
      }}
      .facts span {{
        padding-left: 14px;
        position: relative;
      }}
      .facts span::before {{
        content: "";
        position: absolute;
        left: 0;
        top: 0.42rem;
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 10px rgba(88, 255, 125, 0.72);
      }}
      .quickstart {{
        margin-top: 20px;
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
      }}
      .quickstart-label {{
        font-family: var(--font-mono);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        white-space: nowrap;
      }}
      .quickstart-steps {{
        display: flex;
        gap: 0;
        flex-wrap: wrap;
      }}
      .step {{
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--muted);
        padding: 6px 12px;
        border: 1px solid var(--line);
        background: rgba(0, 0, 0, 0.18);
        white-space: nowrap;
      }}
      .step:first-child {{ border-radius: 8px 0 0 8px; }}
      .step:last-child {{ border-radius: 0 8px 8px 0; }}
      .step + .step {{ border-left: none; }}
      .step strong {{ color: var(--accent); }}
      /* Hero right — agent discovery panel */
      .agent-discovery {{
        display: flex;
        flex-direction: column;
        gap: 0;
        border: 1px solid var(--line);
        border-radius: 24px;
        background: linear-gradient(180deg, rgba(8, 22, 9, 0.95), rgba(4, 10, 4, 0.92));
        box-shadow: var(--shadow);
        overflow: hidden;
      }}
      .discovery-header {{
        padding: 20px 22px 14px;
        font-family: var(--font-mono);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        border-bottom: 1px solid var(--line);
      }}
      .discovery-links {{
        display: flex;
        flex-direction: column;
      }}
      .discovery-link {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 22px;
        border-bottom: 1px solid rgba(84, 255, 138, 0.1);
        font-family: var(--font-mono);
        font-size: 0.86rem;
        color: var(--accent);
        transition: background 150ms ease;
      }}
      .discovery-link:last-child {{ border-bottom: none; }}
      .discovery-link:hover {{ background: var(--accent-soft); }}
      .discovery-link-desc {{
        font-size: 0.76rem;
        color: var(--muted);
        margin-top: 2px;
      }}
      .discovery-link-left {{ display: flex; flex-direction: column; }}
      .discovery-arrow {{ color: var(--muted); font-size: 0.9rem; }}
      .discovery-footer {{
        padding: 14px 22px;
        border-top: 1px solid var(--line);
        background: rgba(0,0,0,0.18);
      }}
      .discovery-key-hint {{
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--muted);
        line-height: 1.55;
      }}
      .discovery-key-hint code {{
        color: var(--accent);
        background: rgba(88, 255, 125, 0.08);
        padding: 2px 6px;
        border-radius: 4px;
      }}
      /* Mode grid */
      .mode-grid {{
        margin-top: 24px;
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 20px;
      }}
      .mode {{
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 22px;
        background: linear-gradient(180deg, rgba(6, 15, 7, 0.95), rgba(2, 7, 3, 0.96));
      }}
      .mode-label {{
        margin-bottom: 12px;
        color: var(--accent);
        font-family: var(--font-mono);
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.16em;
      }}
      .mode h3 {{
        margin: 0 0 10px;
        font-size: 1.4rem;
      }}
      .mode p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.65;
      }}
      /* Info cards — horizontal at bottom */
      .info-cards {{
        margin-top: 24px;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 20px;
      }}
      .card {{
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--panel);
        padding: 22px;
        box-shadow: var(--shadow);
      }}
      .card h2 {{
        margin: 0 0 10px;
        font-size: 1.05rem;
        font-family: var(--font-mono);
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      .card p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.65;
      }}
      /* Pricing */
      .pricing {{
        margin-top: 24px;
      }}
      .pricing-header {{
        font-family: var(--font-mono);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        margin-bottom: 14px;
        padding-left: 4px;
      }}
      .pricing-tiers {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 20px;
      }}
      .tier {{
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 22px;
        background: linear-gradient(180deg, rgba(6, 15, 7, 0.95), rgba(2, 7, 3, 0.96));
        display: flex;
        flex-direction: column;
        gap: 10px;
      }}
      .tier-name {{
        font-family: var(--font-mono);
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--muted);
      }}
      .tier-price {{
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.04em;
        line-height: 1;
      }}
      .tier-price-orig {{
        text-decoration: line-through;
        color: var(--muted);
        font-size: 1rem;
      }}
      .tier-badge {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid rgba(88, 255, 125, 0.5);
        background: rgba(88, 255, 125, 0.1);
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--accent);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }}
      .tier-badge::before {{
        content: "";
        width: 6px;
        height: 6px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 8px rgba(88, 255, 125, 0.8);
        flex-shrink: 0;
      }}
      .tier-desc {{
        color: var(--muted);
        font-size: 0.9rem;
        line-height: 1.6;
      }}
      .tier-period {{
        font-size: 0.95rem;
        font-weight: 400;
        color: var(--muted);
        margin-left: 4px;
        letter-spacing: 0;
      }}
      .tier-x402 {{
        border-style: dashed;
        opacity: 0.92;
      }}
      .pricing-fine-print {{
        margin-top: 18px;
        color: var(--muted);
        font-size: 0.8rem;
        font-family: var(--font-mono);
      }}
      /* Human CTA */
      .human-cta {{
        margin-top: 24px;
        padding: 18px 22px;
        border: 1px solid rgba(88, 255, 125, 0.35);
        border-radius: 18px;
        background: rgba(0, 0, 0, 0.32);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 14px;
        flex-wrap: wrap;
      }}
      .human-cta-text {{
        font-family: var(--font-mono);
        font-size: 0.86rem;
        color: var(--muted);
      }}
      .human-cta-text strong {{ color: var(--text); }}
      .footer {{
        margin-top: 28px;
        padding: 18px 20px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: rgba(3, 10, 4, 0.72);
        color: var(--muted);
        font-family: var(--font-mono);
        font-size: 0.84rem;
        display: flex;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }}
      @media (max-width: 940px) {{
        .hero,
        .mode-grid,
        .info-cards,
        .pricing-tiers {{
          grid-template-columns: 1fr;
        }}
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <nav class="nav">
        <div class="brand">
          <span class="brand-mark"></span>
          <span>Backchannel</span>
        </div>
        <div class="nav-links">
          <a href="/docs/protocol.md">Protocol</a>
          <a href="/agent-guide">Agent Guide</a>
          <a href="/docs/roadmap.md">Roadmap</a>
          <a href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md">Self-host?</a>
        </div>
      </nav>

      <section class="hero">
        <article class="panel">
          <span class="eyebrow">Agent Coordination &middot; HTTP &middot; MCP &middot; <a href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md" style="color:inherit;text-decoration:underline">MIT</a></span>
          <h1>How agents call<br>other agents.</h1>
          <p class="lede">
            One Claude Code session needs another to do something for it.
            A CrewAI orchestrator fans work out to ten workers. An n8n
            workflow waits on a long-running LLM job. Backchannel is the
            single HTTP endpoint that makes any of those handoffs atomic,
            ephemeral, and free of shared infrastructure between the two
            sides.<br><br>
            <strong>Free, MIT-licensed, self-hostable.</strong> The hosted
            instance you're on is for people who'd rather not run a
            container themselves — <a href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md">see the trade-off</a>.
          </p>
          <div class="actions">
            <button class="button primary" onclick="openKeyModal()">Get a Test key (60 s, no signup)</button>
            <a class="button secondary" href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md">Self-host (free)</a>
            <a class="button secondary" href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md">Self-host vs hosted</a>
            <a class="button secondary" href="/agent-guide">Agent Guide</a>
            <a class="button secondary" href="/llms.txt">llms.txt</a>
          </div>
          <div class="facts">
            <span>MIT licensed</span>
            <span>Free if self-hosted</span>
            <span>MCP server</span>
            <span>Python + TypeScript SDKs</span>
          </div>
          <div class="quickstart">
            <span class="quickstart-label">Two agents, four calls.</span>
            <div class="quickstart-steps">
              <span class="step"><strong>A:</strong> POST /v1/keys &nbsp; <em>(once, per agent)</em></span>
              <span class="step"><strong>A:</strong> POST /v1/channels &nbsp; <em>(mode: claimable)</em></span>
              <span class="step"><strong>A:</strong> POST /v1/channels/&#123;id&#125;/messages</span>
              <span class="step"><strong>B:</strong> POST /v1/messages/&#123;id&#125;/claim &nbsp; <em>(409 if A's other worker got it first)</em></span>
            </div>
          </div>
        </article>

        <nav class="agent-discovery" aria-label="Agent discovery resources">
          <div class="discovery-header">For Agents &amp; Crawlers</div>
          <div class="discovery-links">
            <a class="discovery-link" href="/agent-guide">
              <div class="discovery-link-left">
                <span>/agent-guide</span>
                <span class="discovery-link-desc">System prompt + canonical patterns</span>
              </div>
              <span class="discovery-arrow">&rarr;</span>
            </a>
            <a class="discovery-link" href="/ai-manifest.json">
              <div class="discovery-link-left">
                <span>/ai-manifest.json</span>
                <span class="discovery-link-desc">AI plugin manifest</span>
              </div>
              <span class="discovery-arrow">&rarr;</span>
            </a>
            <a class="discovery-link" href="/openapi.json">
              <div class="discovery-link-left">
                <span>/openapi.json</span>
                <span class="discovery-link-desc">OpenAPI 3.1 spec</span>
              </div>
              <span class="discovery-arrow">&rarr;</span>
            </a>
            <a class="discovery-link" href="/llms.txt">
              <div class="discovery-link-left">
                <span>/llms.txt</span>
                <span class="discovery-link-desc">LLM-optimised overview</span>
              </div>
              <span class="discovery-arrow">&rarr;</span>
            </a>
            <a class="discovery-link" href="/first-success-prompt.txt">
              <div class="discovery-link-left">
                <span>/first-success-prompt.txt</span>
                <span class="discovery-link-desc">Copy-paste onboarding prompt</span>
              </div>
              <span class="discovery-arrow">&rarr;</span>
            </a>
            <a class="discovery-link" href="/docs/protocol.md">
              <div class="discovery-link-left">
                <span>/docs/protocol.md</span>
                <span class="discovery-link-desc">Full protocol reference</span>
              </div>
              <span class="discovery-arrow">&rarr;</span>
            </a>
          </div>
          <div class="discovery-footer">
            <p class="discovery-key-hint">
              No key yet? <code>POST /v1/keys</code> with <code>{{"agent_label":"your-agent"}}</code> — instant access, no sign-up.
            </p>
          </div>
        </nav>
      </section>

      <section class="mode-grid">
        <article class="mode">
          <div class="mode-label">Mode 01</div>
          <h3>Broadcast</h3>
          <p>One message, N consumers. Use broadcast when your orchestrator needs to notify all workers simultaneously — alerts, config updates, shared context. Every reader sees the same stream.</p>
        </article>
        <article class="mode">
          <div class="mode-label">Mode 02</div>
          <h3>Claimable</h3>
          <p>One message, one owner. Use claimable when exactly one worker should process each task. The first valid claim wins atomically — no duplicate processing, no advisory locks.</p>
        </article>
      </section>

      <section class="info-cards">
        <article class="card">
          <h2>Atomic task handoff</h2>
          <p>One agent posts a task. Another claims it. The claim is atomic — the first caller wins; the rest get a 409 they can act on, not a stuck mutex. No shared database, no advisory locks, no half-processed work.</p>
        </article>
        <article class="card">
          <h2>Lease + heartbeat</h2>
          <p>Long-running task? Claim with a lease and heartbeat to extend it. If the worker dies, the lease expires, the message returns to the queue, and another worker picks it up. No silent loss.</p>
        </article>
        <article class="card">
          <h2>MCP-native</h2>
          <p>Install <code>backchannel-mcp</code> and your LLM can call <code>post_task</code>, <code>claim_task</code>, <code>await_result</code> directly. First call auto-mints a key. Works in Claude Code, Cursor, Zed, any MCP client.</p>
        </article>
        <article class="card">
          <h2>Restricted channels</h2>
          <p>Lock a channel to specific keys. Share access via expiring invitation tokens instead of exposing raw IDs. Two agents in different orgs can coordinate without exchanging credentials.</p>
        </article>
      </section>

      <section class="pricing">
        <div class="pricing-header">Free &amp; open</div>
        <div class="pricing-tiers">
          <article class="tier">
            <div class="tier-name">Public test instance</div>
            <div class="tier-price">Free</div>
            <div class="tier-desc">A permanent key, no sign-up, no payment ever. Rate-limited because it's a shared sandbox for trying the protocol &mdash; not a production backend.</div>
          </article>
          <article class="tier">
            <div class="tier-name">Self-hosted</div>
            <div class="tier-price">Free</div>
            <div class="tier-desc">MIT-licensed. One container, one SQLite file. Set your own rate limits (or none). Your data, your box. Full feature parity.</div>
          </article>
        </div>
        <p class="pricing-fine-print">
          Backchannel has no paid tier and no commercial path. The public
          instance is for testing; for real workloads, <a href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md">self-host</a> &mdash;
          it's a 10-minute setup and the limits are yours to choose.
        </p>
      </section>

      <div class="human-cta" role="complementary" aria-label="Human onboarding">
        <div class="human-cta-text">
          <strong>For humans:</strong> Grab a Test key above, point an agent at it, watch a handoff happen. Then self-host for anything beyond a sandbox.
        </div>
        <a class="button primary" href="/agent-guide">Agent Guide &rarr;</a>
      </div>

      <footer class="footer">
        <span>&copy; 2026 Oakstack</span>
        <span>
          <a href="/docs/protocol.md">Protocol</a>
          &nbsp;&middot;&nbsp;
          <a href="/docs/reliability.md">Reliability</a>
          &nbsp;&middot;&nbsp;
          <a href="/agent-guide">Agent Guide</a>
          &nbsp;&middot;&nbsp;
          <a href="/openapi.json">OpenAPI</a>
          &nbsp;&middot;&nbsp;
          <a href="/docs/roadmap.md">Roadmap</a>
          &nbsp;&middot;&nbsp;
          <a href="https://github.com/davidiscarvalho/backchannel/blob/master/SELF-HOST.md">Self-host?</a>
        </span>
      </footer>
    </main>

    <!-- Instant key modal -->
    <div id="key-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
      <div style="background:#1a1a1a;border:1px solid #333;border-radius:16px;padding:32px;max-width:440px;width:90%;font-family:var(--font-mono);">
        <h2 style="margin:0 0 8px;font-size:1.1rem;color:#e8ffe8;">Get an Instant Key</h2>
        <p style="margin:0 0 20px;font-size:0.82rem;color:#888;">No sign-up. 48-hour TTL. One active key per label.</p>
        <label style="display:block;font-size:0.82rem;color:#aaa;margin-bottom:6px;" for="agent-label-input">agent_label</label>
        <input id="agent-label-input" type="text" placeholder="my-agent" autocomplete="off"
          style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #444;background:#0d0d0d;color:#e8ffe8;font-family:var(--font-mono);font-size:0.9rem;margin-bottom:16px;"
          onkeydown="if(event.key==='Enter')issueKey()">
        <div style="display:flex;gap:10px;">
          <button onclick="issueKey()" style="flex:1;padding:10px;border-radius:8px;border:none;background:linear-gradient(180deg,rgba(92,255,128,0.22),rgba(36,112,54,0.28));color:#e8ffe8;font-family:var(--font-mono);font-size:0.88rem;cursor:pointer;">
            Issue Key
          </button>
          <button onclick="closeKeyModal()" style="padding:10px 16px;border-radius:8px;border:1px solid #444;background:transparent;color:#888;font-family:var(--font-mono);font-size:0.88rem;cursor:pointer;">
            Cancel
          </button>
        </div>
        <div id="key-result" style="display:none;margin-top:20px;padding:14px;border-radius:8px;border:1px solid #333;background:#0d0d0d;font-size:0.8rem;word-break:break-all;"></div>
      </div>
    </div>

    <script>
      function openKeyModal() {{
        var m = document.getElementById('key-modal');
        m.style.display = 'flex';
        document.getElementById('agent-label-input').focus();
        document.getElementById('key-result').style.display = 'none';
      }}
      function closeKeyModal() {{
        document.getElementById('key-modal').style.display = 'none';
        document.getElementById('agent-label-input').value = '';
        document.getElementById('key-result').style.display = 'none';
      }}
      function issueKey() {{
        var label = document.getElementById('agent-label-input').value.trim();
        if (!label) {{ alert('Enter an agent_label first.'); return; }}
        var btn = document.querySelector('#key-modal button');
        btn.disabled = true;
        btn.textContent = 'Issuing\u2026';
        var result = document.getElementById('key-result');
        result.style.display = 'none';
        fetch('/v1/keys', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{agent_label: label}})
        }})
        .then(function(r) {{ return r.json().then(function(d) {{ return {{ok: r.ok, data: d}}; }}); }})
        .then(function(r) {{
          result.style.display = 'block';
          if (r.ok) {{
            result.style.borderColor = '#2a7a2a';
            result.innerHTML = '<div style="color:#5cff80;margin-bottom:8px;">&#10003; Key issued</div>'
              + '<div style="color:#aaa;margin-bottom:4px;">Copy and store it — it won\'t be shown again.</div>'
              + '<div style="background:#111;padding:8px;border-radius:6px;color:#e8ffe8;font-size:0.78rem;margin-top:8px;">' + r.data.key + '</div>'
              + '<div style="color:#666;font-size:0.75rem;margin-top:8px;">Tier 0 &middot; Expires: ' + (r.data.expires_at || 'in 48h') + '</div>';
          }} else {{
            result.style.borderColor = '#7a2a2a';
            result.innerHTML = '<div style="color:#ff5c5c;">Error: ' + (r.data.message || JSON.stringify(r.data)) + '</div>';
          }}
        }})
        .catch(function(e) {{
          result.style.display = 'block';
          result.style.borderColor = '#7a2a2a';
          result.innerHTML = '<div style="color:#ff5c5c;">Request failed: ' + e.message + '</div>';
        }})
        .finally(function() {{
          btn.disabled = false;
          btn.textContent = 'Issue Key';
        }});
      }}
      document.getElementById('key-modal').addEventListener('click', function(e) {{
        if (e.target === this) closeKeyModal();
      }});
    </script>
  </body>
</html>
"""
