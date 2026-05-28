<template>
  <div>
    <h1 class="page-title">Invitations</h1>

    <div class="card" style="margin-bottom:24px">
      <p class="section-title">Create invitation</p>
      <div class="form-group">
        <label>Channel ID or alias</label>
        <input v-model="createForm.channel" placeholder="channel id or alias" />
      </div>
      <p v-if="createError" class="error">{{ createError }}</p>
      <button class="btn" @click="createInvitation" :disabled="creating">
        {{ creating ? 'Creating…' : 'Create invitation' }}
      </button>
    </div>

    <div v-if="created" class="card created-box" style="margin-bottom:20px">
      <p class="section-title">New invitation created</p>
      <div class="row" style="align-items:center;gap:8px;margin-bottom:6px">
        <p class="mono" style="font-size:13px;word-break:break-all;margin:0;flex:1">{{ created.id }}</p>
        <button class="btn-copy" @click="copy(created.id)" :title="'Copy invitation ID'">{{ copied === created.id ? 'copied!' : 'copy' }}</button>
      </div>
      <p class="muted" style="font-size:12px">Expires {{ formatTs(created.expires_at) }}</p>
      <p class="muted" style="font-size:12px;margin-top:4px">Share this ID — it resolves to <strong>{{ created.channel.name }}</strong> without exposing the channel ID directly.</p>
    </div>

    <p class="section-title">Recent invitations</p>
    <div v-if="invitations.length === 0" class="muted">No invitations tracked in this session.</div>
    <div v-else>
      <div v-for="inv in invitations" :key="inv.id" class="card inv-card">
        <div class="row" style="align-items:flex-start">
          <div style="flex:1">
            <p class="mono" style="font-size:12px;word-break:break-all">{{ inv.id }}</p>
            <p class="muted" style="font-size:12px;margin-top:3px">→ {{ inv.channel.name }} · expires {{ formatTs(inv.expires_at) }}</p>
          </div>
          <button class="btn-copy" style="margin-left:8px" @click="copy(inv.id)">{{ copied === inv.id ? 'copied!' : 'copy' }}</button>
          <span v-if="!inv.active" class="badge" style="opacity:0.4;margin-left:4px">inactive</span>
          <button v-else class="btn-danger" style="margin-left:4px;font-size:11px;padding:4px 10px" @click="revoke(inv.id)">Revoke</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const invitations = ref([])
const createForm = ref({ channel: '' })
const creating = ref(false)
const createError = ref('')
const created = ref(null)
const copied = ref('')

async function load() {
  const cached = JSON.parse(localStorage.getItem('bc_invitations') || '[]')
  const results = await Promise.allSettled(
    cached.map(({ invId }) =>
      api.get(`/v1/channel-invitations/${invId}`).catch(() => null)
    )
  )
  invitations.value = results
    .map(r => r.value)
    .filter(Boolean)
}

async function createInvitation() {
  if (!createForm.value.channel) return
  creating.value = true
  createError.value = ''
  created.value = null
  try {
    const inv = await api.post(`/v1/channels/${createForm.value.channel}/invitations`, {})
    created.value = inv
    const cached = JSON.parse(localStorage.getItem('bc_invitations') || '[]')
    cached.unshift({ invId: inv.id })
    localStorage.setItem('bc_invitations', JSON.stringify(cached.slice(0, 50)))
    invitations.value.unshift(inv)
    createForm.value.channel = ''
  } catch (err) {
    createError.value = err.data?.message || err.message
  } finally {
    creating.value = false
  }
}

async function revoke(id) {
  try {
    const updated = await api.delete(`/v1/channel-invitations/${id}`)
    const idx = invitations.value.findIndex(i => i.id === id)
    if (idx !== -1) invitations.value[idx] = updated
  } catch (err) {
    alert(err.data?.message || err.message)
  }
}

function copy(id) {
  navigator.clipboard.writeText(id).then(() => {
    copied.value = id
    setTimeout(() => { if (copied.value === id) copied.value = '' }, 1500)
  })
}

function formatTs(ts) {
  return new Date(ts).toLocaleString()
}

onMounted(load)
</script>

<style scoped>
.inv-card { margin-bottom: 8px; }
.created-box { border-color: rgba(88,255,125,0.4); background: rgba(88,255,125,0.05); }
.btn-copy {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid #444;
  background: transparent;
  color: #8bcf90;
  font-family: var(--font-mono, monospace);
  cursor: pointer;
  white-space: nowrap;
}
</style>
