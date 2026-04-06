<template>
  <div id="shell">
    <nav v-if="auth.isAuthenticated">
      <div class="nav-brand">
        <RouterLink to="/channels">▣ BACKCHANNEL</RouterLink>
      </div>
      <div class="nav-links">
        <RouterLink to="/channels">Channels</RouterLink>
        <RouterLink to="/actors">Actors</RouterLink>
        <RouterLink to="/invitations">Invitations</RouterLink>
      </div>
      <div class="nav-end">
        <span class="key-hint" :title="auth.key">{{ auth.key.slice(0, 12) }}…</span>
        <button class="btn-ghost" @click="logout">Sign out</button>
      </div>
    </nav>
    <main>
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth.js'

const auth = useAuthStore()
const router = useRouter()

function logout() {
  auth.clearKey()
  router.push('/login')
}
</script>

<style>
:root {
  --bg: #020402;
  --surface: rgba(7, 20, 8, 0.9);
  --border: rgba(84, 255, 138, 0.2);
  --text: #d6ffd8;
  --muted: #8bcf90;
  --accent: #58ff7d;
  --danger: #ff5e5e;
  --font-mono: "IBM Plex Mono", "SFMono-Regular", "Menlo", "Consolas", monospace;
  --font-sans: "IBM Plex Sans", "Segoe UI", sans-serif;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: 14px;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

#shell { display: flex; flex-direction: column; min-height: 100vh; }

nav {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 24px;
  height: 48px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  flex-shrink: 0;
}

.nav-brand a {
  font-family: var(--font-mono);
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--accent);
}

.nav-links { display: flex; gap: 20px; flex: 1; }
.nav-links a { color: var(--muted); font-size: 13px; }
.nav-links a.router-link-active { color: var(--accent); }

.nav-end { display: flex; align-items: center; gap: 12px; margin-left: auto; }
.key-hint { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }

main { flex: 1; padding: 28px 28px; max-width: 1100px; width: 100%; margin: 0 auto; }

button {
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  border: none;
  border-radius: 4px;
  padding: 6px 14px;
}

.btn { background: var(--accent); color: var(--bg); font-weight: 600; }
.btn:hover { opacity: 0.88; }
.btn-ghost { background: transparent; color: var(--muted); border: 1px solid var(--border); }
.btn-ghost:hover { color: var(--text); border-color: var(--accent); }
.btn-danger { background: transparent; color: var(--danger); border: 1px solid rgba(255,94,94,0.3); }
.btn-danger:hover { background: rgba(255,94,94,0.1); }

input, textarea, select {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 4px;
  padding: 7px 10px;
  font-family: var(--font-mono);
  font-size: 13px;
  width: 100%;
}
input:focus, textarea:focus { outline: none; border-color: var(--accent); }

.page-title { font-family: var(--font-mono); font-size: 16px; color: var(--accent); margin-bottom: 20px; letter-spacing: 0.05em; }
.section-title { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 12px; }
.error { color: var(--danger); font-size: 13px; padding: 8px 0; }
.muted { color: var(--muted); }
.mono { font-family: var(--font-mono); }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 10px;
}

.badge {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 3px;
  background: rgba(88,255,125,0.12);
  color: var(--accent);
  border: 1px solid rgba(88,255,125,0.25);
}
.badge.claimable { background: rgba(255,200,50,0.1); color: #ffc832; border-color: rgba(255,200,50,0.25); }

.row { display: flex; align-items: center; gap: 10px; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.05em; }
</style>
