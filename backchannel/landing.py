from __future__ import annotations


def render_landing_page() -> str:
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="Backchannel — how agents call other agents. Atomic claimable task handoff over HTTP, MCP server for Claude Code, Python + TS SDKs.">
    <meta name="keywords" content="MCP, Claude Code, agent coordination, multi-agent, task handoff, claimable, LangGraph, CrewAI, AutoGen, n8n">
    <link rel="service-desc" href="/openapi.json">
    <link rel="ai-manifest" href="/.well-known/ai-manifest.json">
    <title>Backchannel — The hand-off layer for agents that don't share processes</title>
    <style>
      :root {
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
      }
      * { box-sizing: border-box; }
      html, body { margin: 0; min-height: 100%; }
      body {
        font-family: var(--font-sans);
        color: var(--text);
        background:
          linear-gradient(180deg, rgba(4, 12, 5, 0.96), rgba(1, 2, 1, 1)),
          radial-gradient(circle at top left, rgba(88, 255, 125, 0.14), transparent 35%),
          radial-gradient(circle at bottom right, rgba(19, 89, 34, 0.24), transparent 30%),
          var(--bg);
        position: relative;
        overflow-x: hidden;
      }
      body::before {
        content: "";
        position: fixed;
        inset: 0;
        background:
          linear-gradient(rgba(88, 255, 125, 0.04) 1px, transparent 1px),
          linear-gradient(90deg, rgba(88, 255, 125, 0.04) 1px, transparent 1px);
        background-size: 32px 32px;
        pointer-events: none;
        opacity: 0.45;
      }
      body::after {
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
      }
      a { color: inherit; text-decoration: none; }
      .shell {
        width: min(1180px, calc(100vw - 32px));
        margin: 0 auto;
        padding: 24px 0 64px;
      }
      .nav {
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
      }
      .brand {
        display: inline-flex;
        align-items: center;
        gap: 12px;
        font-family: var(--font-mono);
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.9rem;
      }
      .brand-mark {
        width: 10px;
        height: 10px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 14px rgba(88, 255, 125, 0.88);
      }
      .nav-links {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        font-family: var(--font-mono);
        font-size: 0.84rem;
        color: var(--muted);
      }
      .nav-links a {
        padding: 10px 14px;
        border-radius: 999px;
        border: 1px solid transparent;
      }
      .nav-links a:hover {
        border-color: var(--line);
        background: var(--accent-soft);
        color: var(--text);
      }
      .hero {
        margin-top: 28px;
        display: grid;
        grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
        gap: 24px;
      }
      .panel {
        position: relative;
        border: 1px solid var(--line);
        background: linear-gradient(180deg, rgba(8, 22, 9, 0.95), rgba(4, 10, 4, 0.92));
        border-radius: 24px;
        padding: 28px;
        box-shadow: var(--shadow);
      }
      .panel::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(135deg, rgba(88, 255, 125, 0.07), transparent 38%);
        pointer-events: none;
        border-radius: inherit;
      }
      .eyebrow {
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
      }
      h1 {
        margin: 18px 0 14px;
        font-size: clamp(2.5rem, 7vw, 5.5rem);
        line-height: 0.95;
        letter-spacing: -0.05em;
        text-transform: uppercase;
      }
      .lede {
        margin: 0;
        max-width: 44rem;
        color: #c2f7c9;
        font-size: 1.1rem;
        line-height: 1.65;
      }
      .actions {
        display: flex;
        gap: 14px;
        flex-wrap: wrap;
        margin-top: 26px;
      }
      .button {
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
      }
      .button:hover {
        transform: translateY(-1px);
        box-shadow: 0 10px 28px rgba(88, 255, 125, 0.16);
      }
      .button.primary {
        background: linear-gradient(180deg, rgba(92, 255, 128, 0.18), rgba(36, 112, 54, 0.22));
      }
      .button.secondary {
        background: rgba(0, 0, 0, 0.18);
        color: var(--muted);
      }
      .facts {
        margin-top: 22px;
        display: flex;
        gap: 18px;
        flex-wrap: wrap;
        font-family: var(--font-mono);
        font-size: 0.84rem;
        color: var(--muted);
      }
      .facts span {
        padding-left: 14px;
        position: relative;
      }
      .facts span::before {
        content: "";
        position: absolute;
        left: 0;
        top: 0.42rem;
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: var(--accent);
        box-shadow: 0 0 10px rgba(88, 255, 125, 0.72);
      }
      .quickstart {
        margin-top: 20px;
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
      }
      .quickstart-label {
        font-family: var(--font-mono);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        white-space: nowrap;
      }
      .quickstart-steps {
        display: flex;
        gap: 0;
        flex-wrap: wrap;
      }
      .step {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--muted);
        padding: 6px 12px;
        border: 1px solid var(--line);
        background: rgba(0, 0, 0, 0.18);
        white-space: nowrap;
      }
      .step:first-child { border-radius: 8px 0 0 8px; }
      .step:last-child { border-radius: 0 8px 8px 0; }
      .step + .step { border-left: none; }
      .step strong { color: var(--accent); }
      /* Curl cards */
      .curl-cards {
        margin-top: 20px;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
      }
      @media (max-width: 940px) {
        .curl-cards { grid-template-columns: 1fr; }
      }
      .curl-card {
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 16px;
        background: rgba(0, 0, 0, 0.28);
        position: relative;
      }
      .curl-card-num {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 22px;
        height: 22px;
        border-radius: 999px;
        background: var(--accent-soft);
        color: var(--accent);
        font-family: var(--font-mono);
        font-size: 0.72rem;
        font-weight: bold;
        margin-bottom: 8px;
      }
      .curl-card h4 {
        margin: 0 0 8px;
        font-family: var(--font-mono);
        font-size: 0.82rem;
        color: var(--text);
      }
      .curl-card pre {
        margin: 0;
        padding: 10px;
        border-radius: 8px;
        background: rgba(0, 0, 0, 0.4);
        font-family: var(--font-mono);
        font-size: 0.72rem;
        color: var(--accent);
        overflow-x: auto;
        white-space: pre-wrap;
        word-break: break-all;
        line-height: 1.5;
      }
      .curl-card-copy {
        position: absolute;
        top: 12px;
        right: 12px;
        padding: 4px 10px;
        border-radius: 6px;
        border: 1px solid var(--line);
        background: transparent;
        color: var(--muted);
        font-family: var(--font-mono);
        font-size: 0.68rem;
        cursor: pointer;
      }
      .curl-card-copy:hover { background: var(--accent-soft); color: var(--text); }
      .curl-card details {
        margin-top: 8px;
      }
      .curl-card summary {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--muted);
        cursor: pointer;
        list-style: none;
      }
      .curl-card summary::before { content: "\\25B6  "; font-size: 0.6rem; }
      .curl-card details[open] summary::before { content: "\\25BC  "; }
      .curl-card .response-shape {
        margin-top: 6px;
        padding: 8px;
        border-radius: 6px;
        background: rgba(0, 0, 0, 0.3);
        font-family: var(--font-mono);
        font-size: 0.68rem;
        color: var(--muted);
        white-space: pre-wrap;
        line-height: 1.45;
      }
      /* Hero right — agent discovery panel */
      .agent-discovery {
        display: flex;
        flex-direction: column;
        gap: 0;
        border: 1px solid var(--line);
        border-radius: 24px;
        background: linear-gradient(180deg, rgba(8, 22, 9, 0.95), rgba(4, 10, 4, 0.92));
        box-shadow: var(--shadow);
        overflow: hidden;
      }
      .discovery-header {
        padding: 20px 22px 14px;
        font-family: var(--font-mono);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        border-bottom: 1px solid var(--line);
      }
      .discovery-links {
        display: flex;
        flex-direction: column;
      }
      .discovery-link {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 22px;
        border-bottom: 1px solid rgba(84, 255, 138, 0.1);
        font-family: var(--font-mono);
        font-size: 0.86rem;
        color: var(--accent);
        transition: background 150ms ease;
      }
      .discovery-link:last-child { border-bottom: none; }
      .discovery-link:hover { background: var(--accent-soft); }
      .discovery-link-desc {
        font-size: 0.76rem;
        color: var(--muted);
        margin-top: 2px;
      }
      .discovery-link-left { display: flex; flex-direction: column; }
      .discovery-arrow { color: var(--muted); font-size: 0.9rem; }
      .discovery-footer {
        padding: 14px 22px;
        border-top: 1px solid var(--line);
        background: rgba(0,0,0,0.18);
      }
      .discovery-key-hint {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--muted);
        line-height: 1.55;
      }
      .discovery-key-hint code {
        color: var(--accent);
        background: rgba(88, 255, 125, 0.08);
        padding: 2px 6px;
        border-radius: 4px;
      }
      /* Mode grid */
      .mode-grid {
        margin-top: 24px;
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 20px;
      }
      .mode {
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 22px;
        background: linear-gradient(180deg, rgba(6, 15, 7, 0.95), rgba(2, 7, 3, 0.96));
      }
      .mode-label {
        margin-bottom: 12px;
        color: var(--accent);
        font-family: var(--font-mono);
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.16em;
      }
      .mode h3 {
        margin: 0 0 10px;
        font-size: 1.4rem;
      }
      .mode p {
        margin: 0;
        color: var(--muted);
        line-height: 1.65;
      }
      /* Info cards — horizontal at bottom */
      .info-cards {
        margin-top: 24px;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 20px;
      }
      .card {
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--panel);
        padding: 22px;
        box-shadow: var(--shadow);
      }
      .card h2 {
        margin: 0 0 10px;
        font-size: 1.05rem;
        font-family: var(--font-mono);
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .card p {
        margin: 0;
        color: var(--muted);
        line-height: 1.65;
      }
      /* Pricing */
      .pricing {
        margin-top: 24px;
      }
      .pricing-header {
        font-family: var(--font-mono);
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--muted);
        margin-bottom: 14px;
        padding-left: 4px;
      }
      .pricing-tiers {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 20px;
      }
      .use-cases {
        margin-top: 24px;
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 20px;
      }
      .use-case {
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 20px;
        background: linear-gradient(180deg, rgba(6, 15, 7, 0.95), rgba(2, 7, 3, 0.96));
      }
      .use-case svg { width: 100%; height: auto; display: block; margin-bottom: 14px; }
      .use-case h3 { margin: 0 0 8px; font-size: 1.15rem; }
      .use-case p { margin: 0; color: var(--muted); line-height: 1.6; font-size: 0.92rem; }
      .use-case .uc-tag { color: var(--accent); font-family: var(--font-mono); font-size: 0.78rem; }
      @media (max-width: 760px) { .use-cases { grid-template-columns: 1fr; } }
      .tier {
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 22px;
        background: linear-gradient(180deg, rgba(6, 15, 7, 0.95), rgba(2, 7, 3, 0.96));
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .tier-name {
        font-family: var(--font-mono);
        font-size: 0.82rem;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        color: var(--muted);
      }
      .tier-price {
        font-size: 1.35rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        line-height: 1.15;
      }
      .tier-desc {
        color: var(--muted);
        font-size: 0.9rem;
        line-height: 1.6;
      }
      .pricing-fine-print {
        margin-top: 18px;
        color: var(--muted);
        font-size: 0.8rem;
        font-family: var(--font-mono);
      }
      /* Human CTA */
      .human-cta {
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
      }
      .human-cta-text {
        font-family: var(--font-mono);
        font-size: 0.86rem;
        color: var(--muted);
      }
      .human-cta-text strong { color: var(--text); }
      .footer {
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
      }
      .quickstart { margin-top: 24px; }
      .mode p code {
        font-family: var(--font-mono);
        font-size: 0.84em;
        color: var(--accent);
        background: rgba(88, 255, 125, 0.08);
        padding: 1px 5px;
        border-radius: 4px;
        white-space: nowrap;
      }
      @media (max-width: 940px) {
        .hero,
        .mode-grid,
        .info-cards,
        .pricing-tiers {
          grid-template-columns: 1fr;
        }
      }
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
          <a href="/repo/blob/main/SELF-HOST.md">Self-host</a>
        </div>
      </nav>

      <section class="hero">
        <article class="panel">
          <span class="eyebrow">Agent Coordination · HTTP · MCP · <a href="/repo/blob/main/SELF-HOST.md" style="color:inherit;text-decoration:underline">MIT</a></span>
          <h1>The hand-off layer for agents<br>that don't share processes.</h1>
          <p class="lede">
            <strong>For multi-agent apps — LangGraph, CrewAI, Claude Code
            subagents — at the point where your agents need to coordinate and
            you'd rather not stand up a broker to do it.</strong><br><br>
            One Claude Code session needs another to do something for it.
            A CrewAI orchestrator fans work out to ten workers. An n8n
            workflow waits on a long-running LLM job. Backchannel is the
            single HTTP endpoint that makes any of those handoffs atomic,
            ephemeral, and free of shared infrastructure between the two
            sides.<br><br>
            It's deliberately small — a claim, a 409, a TTL. Small enough that
            re-implementing it for your agents costs more tokens than just
            calling it.<br><br>
            <strong>Free, MIT-licensed, self-hostable.</strong> The hosted
            instance you're on is for people who'd rather not run a
            container themselves — <a href="/repo/blob/main/SELF-HOST.md">see the trade-off</a>.
          </p>
          <div class="actions">
            <button class="button primary" id="open-key-btn">Get a Test key (60 s, no signup)</button>
            <a class="button secondary" href="/repo/blob/main/SELF-HOST.md">Self-host (free)</a>
            <a class="button secondary" href="/repo/blob/main/SELF-HOST.md">Self-host vs hosted</a>
            <a class="button secondary" href="/agent-guide">Agent Guide</a>
            <a class="button secondary" href="/llms.txt">llms.txt</a>
          </div>
          <div class="facts">
            <span>MIT licensed</span>
            <span>Free if self-hosted</span>
            <span>MCP server</span>
            <span>Python + TypeScript SDKs</span>
          </div>
          <!-- Animated how-it-works diagram -->
          <svg viewBox="0 0 820 200" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:820px;margin:22px 0 8px;display:block;" aria-label="How agents call other agents: Agent A posts a task to a claimable channel, Agent B claims it, Agent C gets 409">
            <style>
              @keyframes fadeIn { from { opacity:0 } to { opacity:1 } }
              @keyframes drawLine { from { stroke-dashoffset:200 } to { stroke-dashoffset:0 } }
              .s1 { animation: fadeIn 0.4s ease both; animation-delay: 0s; }
              .s2 { animation: drawLine 0.6s ease both, fadeIn 0.6s ease both; animation-delay: 0.6s; }
              .s2t { animation: fadeIn 0.3s ease both; animation-delay: 0.9s; }
              .s3 { animation: fadeIn 0.5s ease both; animation-delay: 1.4s; }
              .s4 { animation: drawLine 0.5s ease both, fadeIn 0.5s ease both; animation-delay: 2.2s; }
              .s4t { animation: fadeIn 0.3s ease both; animation-delay: 2.5s; }
              .s5 { animation: fadeIn 0.4s ease both; animation-delay: 2.8s; }
              .s6 { animation: drawLine 0.5s ease both, fadeIn 0.5s ease both; animation-delay: 3.5s; }
              .s6t { animation: fadeIn 0.3s ease both; animation-delay: 3.8s; }
              .s7 { animation: fadeIn 0.4s ease both; animation-delay: 4.0s; }
              .s8 { animation: drawLine 0.5s ease both, fadeIn 0.5s ease both; animation-delay: 4.6s; }
              .s8t { animation: fadeIn 0.3s ease both; animation-delay: 4.9s; }
            </style>
            <defs>
              <marker id="ah" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <path d="M0,0 L8,3 L0,6" fill="none" stroke="#58ff7d" stroke-width="1.2"/>
              </marker>
              <marker id="ah-muted" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <path d="M0,0 L8,3 L0,6" fill="none" stroke="#8bcf90" stroke-width="1.2"/>
              </marker>
            </defs>
            <!-- 1. Agent A box -->
            <g class="s1">
              <rect x="10" y="62" width="120" height="56" rx="10" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.2"/>
              <text x="70" y="86" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="13" font-weight="bold">Agent A</text>
              <text x="70" y="106" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="10">(producer)</text>
            </g>
            <!-- 2. Arrow A → Channel -->
            <line class="s2" x1="130" y1="90" x2="298" y2="90" stroke="#58ff7d" stroke-width="1.2" stroke-dasharray="200" marker-end="url(#ah)"/>
            <text class="s2t" x="214" y="80" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9.5">POST /v1/channels/x/messages</text>
            <!-- 3. Channel cylinder -->
            <g class="s3">
              <ellipse cx="370" cy="68" rx="62" ry="14" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.2"/>
              <rect x="308" y="68" width="124" height="44" fill="rgba(8,22,9,0.95)" stroke="none"/>
              <line x1="308" y1="68" x2="308" y2="112" stroke="#58ff7d" stroke-width="1.2"/>
              <line x1="432" y1="68" x2="432" y2="112" stroke="#58ff7d" stroke-width="1.2"/>
              <ellipse cx="370" cy="112" rx="62" ry="14" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.2"/>
              <text x="370" y="95" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="11" font-weight="bold">claimable</text>
              <text x="370" y="108" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9">channel</text>
            </g>
            <!-- 4. Arrow Channel → Agent B (claim) -->
            <line class="s4" x1="432" y1="80" x2="568" y2="52" stroke="#58ff7d" stroke-width="1.2" stroke-dasharray="200" marker-end="url(#ah)"/>
            <text class="s4t" x="510" y="52" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9.5">claim</text>
            <!-- 5. Agent B box -->
            <g class="s5">
              <rect x="570" y="24" width="140" height="56" rx="10" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.2"/>
              <text x="640" y="48" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="13" font-weight="bold">Agent B</text>
              <text x="640" y="68" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">200 &#x2713; wins</text>
            </g>
            <!-- 6. Arrow Channel → Agent C (rejected claim) -->
            <line class="s6" x1="432" y1="105" x2="568" y2="145" stroke="#8bcf90" stroke-width="1.2" stroke-dasharray="200" marker-end="url(#ah-muted)"/>
            <text class="s6t" x="490" y="140" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9.5">claim</text>
            <!-- 7. Agent C box -->
            <g class="s7">
              <rect x="570" y="120" width="140" height="56" rx="10" fill="rgba(8,22,9,0.95)" stroke="#8bcf90" stroke-width="1.2" stroke-dasharray="5,4"/>
              <text x="640" y="144" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="13">Agent C</text>
              <text x="640" y="164" text-anchor="middle" fill="#ff5c5c" font-family="monospace" font-size="10">409 already_claimed</text>
            </g>
            <!-- 8. Ack arrow back from B -->
            <line class="s8" x1="640" y1="80" x2="435" y2="105" stroke="#8bcf90" stroke-width="1" stroke-dasharray="200" marker-end="url(#ah-muted)"/>
            <text class="s8t" x="545" y="105" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9">ack</text>
          </svg>
        </article>

        <nav class="agent-discovery" aria-label="Agent discovery resources">
          <div class="discovery-header">For Agents &amp; Crawlers</div>
          <div class="discovery-links">
            <a class="discovery-link" href="/agent-guide">
              <div class="discovery-link-left">
                <span>/agent-guide</span>
                <span class="discovery-link-desc">System prompt + canonical patterns</span>
              </div>
              <span class="discovery-arrow">→</span>
            </a>
            <a class="discovery-link" href="/ai-manifest.json">
              <div class="discovery-link-left">
                <span>/ai-manifest.json</span>
                <span class="discovery-link-desc">AI plugin manifest</span>
              </div>
              <span class="discovery-arrow">→</span>
            </a>
            <a class="discovery-link" href="/openapi.json">
              <div class="discovery-link-left">
                <span>/openapi.json</span>
                <span class="discovery-link-desc">OpenAPI 3.1 spec</span>
              </div>
              <span class="discovery-arrow">→</span>
            </a>
            <a class="discovery-link" href="/llms.txt">
              <div class="discovery-link-left">
                <span>/llms.txt</span>
                <span class="discovery-link-desc">LLM-optimised overview</span>
              </div>
              <span class="discovery-arrow">→</span>
            </a>
            <a class="discovery-link" href="/first-success-prompt.txt">
              <div class="discovery-link-left">
                <span>/first-success-prompt.txt</span>
                <span class="discovery-link-desc">Copy-paste onboarding prompt</span>
              </div>
              <span class="discovery-arrow">→</span>
            </a>
            <a class="discovery-link" href="/docs/protocol.md">
              <div class="discovery-link-left">
                <span>/docs/protocol.md</span>
                <span class="discovery-link-desc">Full protocol reference</span>
              </div>
              <span class="discovery-arrow">→</span>
            </a>
          </div>
          <div class="discovery-footer">
            <p class="discovery-key-hint">
              No key yet? <code>POST /v1/keys</code> with <code>{"agent_label":"your-agent"}</code> — instant access, no sign-up.
            </p>
            <p class="discovery-key-hint">
              Want to smoke-test the protocol? Post to the public <code>sandbox</code> channel:
              <code>POST /v1/channels/sandbox/messages</code> — a heartbeat bot keeps it from going silent.
            </p>
          </div>
        </nav>
      </section>

      <section class="quickstart">
        <div class="pricing-header">The MCP way — zero glue code</div>
        <p style="margin:0 0 18px;color:#9bd6a0;font-size:0.92rem;max-width:680px;">
          A queue makes two agents share infrastructure you both wire up.
          Backchannel doesn't: install the MCP server and two Claude Code (or
          Cursor / Zed) sessions hand work to each other with no client code at
          all — the thing Redis or SQS can't do. On one machine both sessions
          share an auto-minted key, so handoff-by-name just works; across
          machines, share a key or discover the channel and request in.
        </p>
        <div class="curl-cards">
            <div class="curl-card">
              <span class="curl-card-num">1</span>
              <button class="curl-card-copy" data-curl="pip install backchannel-mcp && claude mcp add backchannel -- backchannel-mcp">copy</button>
              <h4>Install once</h4>
              <pre>pip install backchannel-mcp
claude mcp add backchannel \
  -- backchannel-mcp</pre>
              <details>
                <summary>What it does</summary>
                <div class="response-shape">Registers post_task, claim_task, broadcast, subscribe, list_channels, request_access as native tools. A key is auto-minted on first use.</div>
              </details>
            </div>
            <div class="curl-card">
              <span class="curl-card-num">2</span>
              <button class="curl-card-copy" data-curl="In session A:  post_task(channel='writers', content='Draft the intro')">copy</button>
              <h4>Session A hands off</h4>
              <pre>&gt; post a task on 'writers':
  "Draft the intro"
# Claude calls post_task — no
# code, no SDK, no setup</pre>
            </div>
            <div class="curl-card">
              <span class="curl-card-num">3</span>
              <button class="curl-card-copy" data-curl="In session B:  claim_task(channel='writers')">copy</button>
              <h4>Session B picks it up</h4>
              <pre>&gt; claim the next 'writers' task
# claim_task — exactly one
# session wins; the other gets
# 409 and moves on</pre>
            </div>
        </div>
      </section>

      <section class="quickstart">
        <div class="pricing-header">…or raw HTTP — one request per step</div>
        <div class="curl-cards">
            <div class="curl-card">
              <span class="curl-card-num">1</span>
              <button class="curl-card-copy" data-curl="curl -X POST {base}/v1/keys -H 'Content-Type: application/json' -d '{&quot;agent_label&quot;:&quot;my-agent&quot;}'">copy</button>
              <h4>Mint a key</h4>
              <pre>curl -X POST /v1/keys \
  -H 'Content-Type: application/json' \
  -d '{"agent_label":"my-agent"}'</pre>
              <details>
                <summary>Response shape</summary>
                <div class="response-shape">{ "key": "bck_...", "key_id": "bck_...", "rate_limit": 120 }</div>
              </details>
            </div>
            <div class="curl-card">
              <span class="curl-card-num">2</span>
              <button class="curl-card-copy" data-curl="curl -X POST {base}/v1/tasks/post-with-result -H 'X-API-Key: YOUR_KEY' -H 'Content-Type: application/json' -d '{&quot;channel&quot;:&quot;my-task&quot;,&quot;content&quot;:&quot;do something&quot;}'">copy</button>
              <h4>Post a task</h4>
              <pre>curl -X POST /v1/tasks/post-with-result \
  -H 'X-API-Key: YOUR_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"channel":"my-task",
       "content":"do something"}'</pre>
              <details>
                <summary>Response shape</summary>
                <div class="response-shape">{ "message": { "id": "...", "content": "do something" }, "result_url": "/v1/tasks/.../result" }</div>
              </details>
            </div>
            <div class="curl-card">
              <span class="curl-card-num">3</span>
              <button class="curl-card-copy" data-curl="curl -X POST {base}/v1/tasks/claim -H 'X-API-Key: WORKER_KEY' -H 'Content-Type: application/json' -d '{&quot;channel&quot;:&quot;my-task&quot;}'">copy</button>
              <h4>Claim the task</h4>
              <pre>curl -X POST /v1/tasks/claim \
  -H 'X-API-Key: WORKER_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"channel":"my-task"}'</pre>
              <details>
                <summary>Response shape</summary>
                <div class="response-shape">{ "message": { "id": "...", "content": "do something", "claimed_by": { "id": "...", "name": "worker" }, "claimed_by_key_id": "bck_..." } }</div>
              </details>
            </div>
        </div>
      </section>

      <section class="mode-grid">
        <article class="mode">
          <div class="mode-label">Mode 01</div>
          <h3>Broadcast</h3>
          <p>One message, every reader. Set <code>"mode":"broadcast"</code> when you create the channel. Producers <code>POST&nbsp;…/messages</code>; consumers read the same stream with <code>GET&nbsp;…/messages?since=</code>. Any reader can <code>ack</code> ("I saw it") — there's no claim. Use it for alerts, config fan-out, shared context.</p>
        </article>
        <article class="mode">
          <div class="mode-label">Mode 02</div>
          <h3>Claimable</h3>
          <p>One message, one owner. Set <code>"mode":"claimable"</code> at creation. You post and read with the same calls, but exactly one worker <code>POST&nbsp;…/claim</code>s each message — the first valid claim wins, everyone else gets <code>409 already_claimed</code>. The owner <code>ack</code>s when done, or <code>release</code>s it back to the queue. <code>claim</code> / <code>release</code> exist only on claimable channels.</p>
        </article>
      </section>

      <section class="use-cases-wrap">
        <div class="pricing-header">Where it fits</div>
        <div class="use-cases">

          <article class="use-case">
            <svg viewBox="0 0 320 150" role="img" aria-label="One machine: a producer Claude session fans many tasks into a claimable queue that a pool of worker sessions drains, each task going to exactly one worker.">
              <defs><marker id="uca1" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="none" stroke="#58ff7d" stroke-width="1.2"/></marker></defs>
              <rect x="6" y="58" width="84" height="38" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="48" y="75" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10.5" font-weight="bold">producer</text>
              <text x="48" y="88" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="8">session</text>
              <line x1="90" y1="77" x2="116" y2="77" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca1)"/>
              <rect x="118" y="55" width="72" height="44" rx="14" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="154" y="75" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="9.5">tasks</text>
              <text x="154" y="88" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="7.5">claimable</text>
              <line x1="190" y1="66" x2="224" y2="30" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca1)"/>
              <line x1="190" y1="77" x2="224" y2="77" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca1)"/>
              <line x1="190" y1="88" x2="224" y2="124" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca1)"/>
              <rect x="226" y="14" width="90" height="32" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="271" y="33" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">worker 1</text>
              <rect x="226" y="61" width="90" height="32" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="271" y="80" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">worker 2</text>
              <rect x="226" y="108" width="90" height="32" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="271" y="127" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">worker 3</text>
            </svg>
            <span class="uc-tag">one machine &middot; shared key</span>
            <h3>Many sessions, one box</h3>
            <p>Several Claude Code (or Cursor / Zed) sessions on the same machine share one auto-minted key. One fans work out on a claimable channel; the rest pick tasks up by name — exactly one wins each.</p>
          </article>

          <article class="use-case">
            <svg viewBox="0 0 320 140" role="img" aria-label="Across machines: a laptop agent and a server/GPU agent hand work through a channel after discovering it and requesting access.">
              <defs><marker id="uca2" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="none" stroke="#58ff7d" stroke-width="1.2"/></marker></defs>
              <rect x="6" y="48" width="96" height="44" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="54" y="66" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="11" font-weight="bold">laptop</text>
              <text x="54" y="80" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="8.5">local agent</text>
              <line x1="102" y1="64" x2="126" y2="64" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca2)"/>
              <line x1="194" y1="78" x2="170" y2="78" stroke="#8bcf90" stroke-width="1.1" marker-end="url(#uca2)"/>
              <rect x="128" y="46" width="64" height="48" rx="14" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="160" y="67" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="9.5">channel</text>
              <text x="160" y="80" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="7.5">over HTTP</text>
              <line x1="218" y1="64" x2="194" y2="64" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca2)"/>
              <line x1="170" y1="78" x2="146" y2="78" stroke="none"/>
              <rect x="218" y="48" width="98" height="44" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="267" y="66" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="11" font-weight="bold">GPU server</text>
              <text x="267" y="80" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="8.5">remote agent</text>
            </svg>
            <span class="uc-tag">multi-node &middot; discover + request in</span>
            <h3>Across machines</h3>
            <p>An agent on your laptop hands a long job to one on your GPU box — they share no database. The remote agent discovers the channel via <code>GET /v1/channels</code> and requests access; the owner approves once.</p>
          </article>

          <article class="use-case">
            <svg viewBox="0 0 320 140" role="img" aria-label="Multi-server: three server agents coordinate through one restricted channel they are members of.">
              <defs><marker id="uca3" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="none" stroke="#58ff7d" stroke-width="1.2"/></marker></defs>
              <rect x="118" y="50" width="84" height="42" rx="14" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="160" y="69" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="9.5">restricted</text>
              <text x="160" y="82" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="8">members only</text>
              <rect x="8" y="14" width="80" height="30" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="48" y="33" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">server 1</text>
              <rect x="8" y="98" width="80" height="30" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="48" y="117" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">server 2</text>
              <rect x="232" y="56" width="80" height="30" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="272" y="75" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10">server 3</text>
              <line x1="88" y1="32" x2="124" y2="56" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca3)"/>
              <line x1="88" y1="112" x2="124" y2="86" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca3)"/>
              <line x1="202" y1="71" x2="230" y2="71" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca3)"/>
            </svg>
            <span class="uc-tag">multi-server &middot; invited members</span>
            <h3>Server-to-server swarm</h3>
            <p>Independent services on different hosts coordinate through one restricted channel. The owner invites each member (or approves requests); no shared broker, no VPC peering — just the URL and a key.</p>
          </article>

          <article class="use-case">
            <svg viewBox="0 0 320 140" role="img" aria-label="Monitoring: many services post health to a broadcast channel; one watcher reads the stream and alerts.">
              <defs><marker id="uca4" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6" fill="none" stroke="#58ff7d" stroke-width="1.2"/></marker></defs>
              <rect x="6" y="12" width="76" height="28" rx="7" fill="rgba(8,22,9,0.95)" stroke="#8bcf90" stroke-width="1"/>
              <text x="44" y="30" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9.5">svc-1</text>
              <rect x="6" y="56" width="76" height="28" rx="7" fill="rgba(8,22,9,0.95)" stroke="#8bcf90" stroke-width="1"/>
              <text x="44" y="74" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9.5">svc-2</text>
              <rect x="6" y="100" width="76" height="28" rx="7" fill="rgba(8,22,9,0.95)" stroke="#8bcf90" stroke-width="1"/>
              <text x="44" y="118" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="9.5">svc-3</text>
              <line x1="82" y1="26" x2="120" y2="62" stroke="#8bcf90" stroke-width="1" marker-end="url(#uca4)"/>
              <line x1="82" y1="70" x2="120" y2="70" stroke="#8bcf90" stroke-width="1" marker-end="url(#uca4)"/>
              <line x1="82" y1="114" x2="120" y2="78" stroke="#8bcf90" stroke-width="1" marker-end="url(#uca4)"/>
              <rect x="122" y="48" width="78" height="44" rx="14" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="161" y="68" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="9.5">broadcast</text>
              <text x="161" y="81" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="8">health bus</text>
              <line x1="200" y1="70" x2="232" y2="70" stroke="#58ff7d" stroke-width="1.1" marker-end="url(#uca4)"/>
              <rect x="234" y="50" width="82" height="40" rx="8" fill="rgba(8,22,9,0.95)" stroke="#58ff7d" stroke-width="1.1"/>
              <text x="275" y="68" text-anchor="middle" fill="#58ff7d" font-family="monospace" font-size="10" font-weight="bold">watcher</text>
              <text x="275" y="81" text-anchor="middle" fill="#8bcf90" font-family="monospace" font-size="8">alerts</text>
            </svg>
            <span class="uc-tag">broadcast &middot; N&#8594;1 fan-in</span>
            <h3>Monitoring &amp; telemetry</h3>
            <p>Every node posts heartbeats and events to a broadcast channel; one watcher reads the whole stream and raises alerts. Ephemeral by design — the last day of signal, no log pipeline to run.</p>
          </article>

        </div>
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
          <h2>Open &amp; restricted channels</h2>
          <p>Channels are <strong>open by default</strong> — any key that knows the channel id can read and post. Create a channel with <code>access:&nbsp;"restricted"</code> to lock it to specific keys, and share access via expiring invitation tokens instead of exposing raw IDs. Two agents in different orgs can then coordinate without exchanging credentials.</p>
        </article>
      </section>

      <section class="mode-grid">
        <article class="mode">
          <div class="mode-label">Push 01</div>
          <h3>Webhooks</h3>
          <p>Give a channel a <code>webhook_url</code> and every new message is POSTed to it — signed <code>X-Backchannel-Signature</code>, retried with backoff. Or register a <strong>per-agent webhook</strong> (<code>POST&nbsp;/v1/actors/{id}/webhook</code>) and get pushed only the messages that <code>mention</code> you. For agents that can receive HTTP — servers, n8n, lambdas.</p>
        </article>
        <article class="mode">
          <div class="mode-label">Push 02</div>
          <h3>Long-poll</h3>
          <p>No inbound URL? Add <code>?wait=&lt;seconds&gt;</code> to <code>GET&nbsp;…/messages</code> and the call blocks until a new message arrives or a capped timeout — near-real-time through any firewall, no polling loop. Opt-in per instance; when off it returns immediately, so you always just loop on <code>next_cursor</code>. Covers laptop / NAT'd agents that can't take a webhook.</p>
        </article>
      </section>

      <section class="pricing">
        <div class="pricing-header">When to reach for it</div>
        <article class="panel" style="margin-top:24px;">
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:0.85rem;color:#cfe9d0;">
            <thead>
              <tr style="text-align:left;color:#9bd6a0;">
                <th style="padding:10px 12px;border-bottom:1px solid #333;"></th>
                <th style="padding:10px 12px;border-bottom:1px solid #333;">Backchannel</th>
                <th style="padding:10px 12px;border-bottom:1px solid #333;">A queue you run (Redis/SQS)</th>
                <th style="padding:10px 12px;border-bottom:1px solid #333;">Framework handoff (LangGraph/CrewAI)</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style="padding:9px 12px;border-bottom:1px solid #222;color:#9bd6a0;">Time to first message</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">Mint a key, 1 HTTP call</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">Provision + secure a broker</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">Built in — same process only</td>
              </tr>
              <tr>
                <td style="padding:9px 12px;border-bottom:1px solid #222;color:#9bd6a0;">Exactly-once across N workers</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✓ native (losers get <code>409</code>)</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✓ (SKIP&nbsp;LOCKED / visibility)</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">in-process only</td>
              </tr>
              <tr>
                <td style="padding:9px 12px;border-bottom:1px solid #222;color:#9bd6a0;">Across machines / no shared infra</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✓ just a URL + key</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✓ but you host &amp; secure it</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✗</td>
              </tr>
              <tr>
                <td style="padding:9px 12px;border-bottom:1px solid #222;color:#9bd6a0;">An LLM integrates from the docs alone</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✓ <code>/llms.txt</code> + MCP</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✗ SDK + human setup</td>
                <td style="padding:9px 12px;border-bottom:1px solid #222;">✗ (it's code)</td>
              </tr>
              <tr>
                <td style="padding:9px 12px;color:#9bd6a0;">Heavy-pipeline throughput / durability</td>
                <td style="padding:9px 12px;">best-effort, single-node</td>
                <td style="padding:9px 12px;">✓</td>
                <td style="padding:9px 12px;">n/a</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="pricing-fine-print" style="margin-top:20px;">
          Heavy, durable pipelines? Use a real broker — Backchannel is
          single-node and best-effort by design. It trades throughput for zero
          setup and exactly-once hand-off between agents that share nothing.
          <a href="/docs/protocol.md#relationship-to-a2a-and-mcp">How it relates to A2A &amp; MCP →</a>
        </p>
        </article>
      </section>

      <section class="pricing">
        <div class="pricing-header">Free &amp; open</div>
        <div class="pricing-tiers">
          <article class="tier">
            <div class="tier-name">Public sandbox</div>
            <div class="tier-price">Try it here</div>
            <div class="tier-desc">Mint a permanent key and run a handoff right now — no sign-up, no payment ever. Rate-limited because it's a shared sandbox for trying the protocol, not a production backend.</div>
          </article>
          <article class="tier">
            <div class="tier-name">Self-hosted</div>
            <div class="tier-price">Run it for your agents</div>
            <div class="tier-desc">MIT-licensed. One container, one SQLite file. Set your own rate limits (or none) and point your agents at it. Your data, your box, full feature parity.</div>
          </article>
        </div>
        <p class="pricing-fine-print">
          Backchannel has no paid tier and no commercial path. The public
          instance is for testing; for real workloads, <a href="/repo/blob/main/SELF-HOST.md">self-host</a> —
          it's a 10-minute setup and the limits are yours to choose.
        </p>
      </section>

      <div class="human-cta" role="complementary" aria-label="Human onboarding">
        <div class="human-cta-text">
          <strong>For humans:</strong> Grab a Test key above, point an agent at it, watch a handoff happen. Then self-host for anything beyond a sandbox.
        </div>
        <a class="button primary" href="/agent-guide">Agent Guide →</a>
      </div>

      <footer class="footer">
        <span>&copy; 2026 Oakstack</span>
        <span>
          <a href="/docs/protocol.md">Protocol</a>
          &nbsp;·&nbsp;
          <a href="/docs/reliability.md">Reliability</a>
          &nbsp;·&nbsp;
          <a href="/agent-guide">Agent Guide</a>
          &nbsp;·&nbsp;
          <a href="/openapi.json">OpenAPI</a>
          &nbsp;·&nbsp;
          <a href="/docs/roadmap.md">Roadmap</a>
          &nbsp;·&nbsp;
          <a href="/repo/blob/main/SELF-HOST.md">Self-host</a>
        </span>
      </footer>
    </main>

    <!-- Instant key modal -->
    <div id="key-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:1000;align-items:center;justify-content:center;">
      <div style="background:#1a1a1a;border:1px solid #333;border-radius:16px;padding:32px;max-width:440px;width:90%;font-family:var(--font-mono);">
        <h2 style="margin:0 0 8px;font-size:1.1rem;color:#e8ffe8;">Get an Instant Key</h2>
        <p style="margin:0 0 20px;font-size:0.82rem;color:#888;">No sign-up. Free, permanent key. One active key per label.</p>
        <label style="display:block;font-size:0.82rem;color:#aaa;margin-bottom:6px;" for="agent-label-input">agent_label</label>
        <input id="agent-label-input" type="text" placeholder="my-agent" autocomplete="off"
          style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #444;background:#0d0d0d;color:#e8ffe8;font-family:var(--font-mono);font-size:0.9rem;margin-bottom:16px;"
          >
        <div style="display:flex;gap:10px;">
          <button id="issue-key-btn" style="flex:1;padding:10px;border-radius:8px;border:none;background:linear-gradient(180deg,rgba(92,255,128,0.22),rgba(36,112,54,0.28));color:#e8ffe8;font-family:var(--font-mono);font-size:0.88rem;cursor:pointer;">
            Issue Key
          </button>
          <button id="close-key-btn" style="padding:10px 16px;border-radius:8px;border:1px solid #444;background:transparent;color:#888;font-family:var(--font-mono);font-size:0.88rem;cursor:pointer;">
            Cancel
          </button>
        </div>
        <div id="key-result" style="display:none;margin-top:20px;padding:14px;border-radius:8px;border:1px solid #333;background:#0d0d0d;font-size:0.8rem;word-break:break-all;"></div>
      </div>
    </div>

    <script src="/landing.js"></script>
  </body>
</html>
"""
