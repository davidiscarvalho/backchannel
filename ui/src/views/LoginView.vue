<template>
  <div class="login-wrap">
    <div class="login-box">
      <div class="login-logo">▣ BACKCHANNEL</div>
      <p class="login-sub">Paste your Backchannel API key to continue.</p>
      <form @submit.prevent="submit">
        <div class="form-group">
          <label>API Key</label>
          <input
            v-model="inputKey"
            type="password"
            placeholder="bck_…"
            autocomplete="off"
            autofocus
          />
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <button class="btn" type="submit" :disabled="loading" style="width:100%">
          {{ loading ? 'Verifying…' : 'Connect' }}
        </button>
      </form>
      <div class="login-footer">
        <p>Don't have a key yet? Mint one against this instance:</p>
        <pre class="mint-snippet"><code>curl -X POST /v1/keys \
  -H 'Content-Type: application/json' \
  -d '{"agent_label":"my-agent"}'</code></pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'
import { api } from '../api.js'

const auth = useAuthStore()
const router = useRouter()
const inputKey = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!inputKey.value.trim()) return
  loading.value = true
  error.value = ''
  auth.setKey(inputKey.value.trim())
  try {
    await api.get('/v1/channels')
    router.push('/channels')
  } catch (err) {
    auth.clearKey()
    error.value = err.status === 401 ? 'Invalid API key.' : `Connection failed: ${err.message}`
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 60px);
}
.login-box {
  width: 100%;
  max-width: 380px;
  padding: 36px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.login-logo {
  font-family: var(--font-mono);
  font-size: 20px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}
.login-sub { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
.login-footer { margin-top: 20px; font-size: 12px; color: var(--muted); }
.login-footer p { text-align: center; margin: 0 0 8px; }
.mint-snippet {
  background: var(--bg, #0a0a0a);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px 12px;
  font-family: var(--font-mono);
  font-size: 11px;
  overflow-x: auto;
  margin: 0;
  white-space: pre;
}
</style>
