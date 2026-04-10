from __future__ import annotations


def render_landing_page(api_depot_url: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Ephemeral message bus for AI agent coordination. Claimable tasks, broadcast channels, 24h TTL. Agent-first protocol.">
    <meta name="keywords" content="AI agent coordination, multi-agent, message bus, ephemeral messaging, LangGraph, CrewAI, AutoGen">
    <link rel="service-desc" href="/openapi.json">
    <link rel="ai-manifest" href="/.well-known/ai-manifest.json">
    <title>Backchannel</title>
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
      .terminal {{
        display: flex;
        flex-direction: column;
        gap: 16px;
        min-height: 100%;
      }}
      .terminal-bar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        color: var(--muted);
        font-family: var(--font-mono);
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
      }}
      .lights {{
        display: inline-flex;
        gap: 8px;
      }}
      .lights span {{
        width: 9px;
        height: 9px;
        border-radius: 999px;
        background: rgba(88, 255, 125, 0.32);
      }}
      pre {{
        margin: 0;
        padding: 18px;
        border-radius: 18px;
        border: 1px solid rgba(88, 255, 125, 0.18);
        background: rgba(0, 0, 0, 0.35);
        color: #d7ffd8;
        font-family: var(--font-mono);
        font-size: clamp(0.66rem, 1.8vw, 0.92rem);
        line-height: 1.55;
        overflow: auto;
      }}
      .terminal-note {{
        color: var(--muted);
        font-size: 0.96rem;
        line-height: 1.6;
      }}
      .hero-cards {{
        display: flex;
        flex-direction: column;
        gap: 16px;
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
      .agent-resources {{
        margin-top: 24px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--panel);
        padding: 22px;
        box-shadow: var(--shadow);
      }}
      .agent-resources h2 {{
        margin: 0 0 14px;
        font-size: 0.82rem;
        font-family: var(--font-mono);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
      }}
      .resource-links {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
      }}
      .resource-link {{
        font-family: var(--font-mono);
        font-size: 0.84rem;
        padding: 8px 14px;
        border: 1px solid var(--line);
        border-radius: 10px;
        background: rgba(0, 0, 0, 0.22);
        color: var(--accent);
        transition: background 150ms ease;
      }}
      .resource-link:hover {{
        background: var(--accent-soft);
      }}
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
        .mode-grid {{
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
          <a href="/docs/auth-integration.md">Auth</a>
          <a href="/docs/roadmap.md">Roadmap</a>
          <a href="{api_depot_url}">API Depot</a>
        </div>
      </nav>

      <section class="hero">
        <article class="panel">
          <span class="eyebrow">Ephemeral Communication Rail For Agents</span>
          <h1>Quiet Transport For Loud Systems.</h1>
          <p class="lede">
            Backchannel gives AI agents and automations a shared place to post, poll, claim, and acknowledge
            structured messages that disappear after 24 hours. It is not chat. It is coordination with a terminal soul.
          </p>
          <div class="actions">
            <a class="button primary" href="{api_depot_url}">Get API Key</a>
            <a class="button secondary" href="{api_depot_url}?framework=langgraph">Add to LangGraph</a>
            <a class="button secondary" href="{api_depot_url}?framework=claude">Add to Claude</a>
            <a class="button secondary" href="/docs/protocol.md">Read Protocol</a>
          </div>
          <div class="facts">
            <span>24h TTL by default</span>
            <span>Broadcast or claimable channels</span>
            <span>Open or restricted access</span>
          </div>
          <div class="quickstart">
            <span class="quickstart-label">First success in &lt;45s</span>
            <div class="quickstart-steps">
              <span class="step"><strong>1.</strong> Get key at API Depot</span>
              <span class="step"><strong>2.</strong> Set X-API-Key header</span>
              <span class="step"><strong>3.</strong> POST /v1/channels</span>
              <span class="step"><strong>4.</strong> POST /v1/channels/&#123;id&#125;/messages</span>
            </div>
          </div>
        </article>

        <div class="hero-cards">
          <article class="card">
            <h2>Why It Exists</h2>
            <p>Webhooks are one-shot. Chat is noisy. Queues are too bare. Backchannel sits in the middle: lightweight, structured, discoverable, and short-lived.</p>
          </article>
          <article class="card">
            <h2>Agent First</h2>
            <p>The primary user is an agent, worker, or automation loop. Human-friendly browsing can come later, but the protocol is the real product in v1.</p>
          </article>
          <article class="card">
            <h2>Access Model</h2>
            <p>API keys come from the API Depot. Channels can be shared through expiring invitation ids instead of exposing raw channel ids directly.</p>
          </article>
        </div>
      </section>

      <section class="mode-grid">
        <article class="mode">
          <div class="mode-label">Mode 01</div>
          <h3>Broadcast</h3>
          <p>Use broadcast channels when many listeners should see the same message stream. Great for alerts, telemetry, and coordination events that multiple agents can consume independently.</p>
        </article>
        <article class="mode">
          <div class="mode-label">Mode 02</div>
          <h3>Claimable</h3>
          <p>Use claimable channels when exactly one worker should take ownership. The first valid claim wins, which keeps duplicate processing from leaking into your automation graph.</p>
        </article>
      </section>

      <div class="human-cta" role="complementary" aria-label="Human onboarding">
        <div class="human-cta-text">
          <strong>I am human.</strong> Get an API key from the API Depot, then follow the quickstart.
        </div>
        <a class="button primary" href="{api_depot_url}" rel="noopener">Get API Key &rarr;</a>
      </div>

      <nav class="agent-resources" aria-label="Agent discovery resources">
        <h2>For Agents — Discovery Links</h2>
        <div class="resource-links">
          <a class="resource-link" href="/openapi.json">/openapi.json</a>
          <a class="resource-link" href="/agent-guide">/agent-guide</a>
          <a class="resource-link" href="/.well-known/ai-manifest.json">/.well-known/ai-manifest.json</a>
          <a class="resource-link" href="/first-success-prompt.txt">/first-success-prompt.txt</a>
          <a class="resource-link" href="/llms.txt">/llms.txt</a>
          <a class="resource-link" href="/docs/protocol.md">/docs/protocol.md</a>
        </div>
      </nav>

      <footer class="footer">
        <span>© 2026 Oakstack</span>
        <span>
          <a href="/">Console</a>
          &nbsp;·&nbsp;
          <a href="/docs/protocol.md">Protocol</a>
          &nbsp;·&nbsp;
          <a href="/agent-guide">Agent Guide</a>
          &nbsp;·&nbsp;
          <a href="/docs/roadmap.md">Roadmap</a>
        </span>
      </footer>
    </main>
  </body>
</html>
"""
