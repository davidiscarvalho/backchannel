<template>
  <div>
    <div class="row" style="margin-bottom:20px">
      <h1 class="page-title" style="margin:0">Channels</h1>
      <button class="btn" style="margin-left:auto" @click="showCreate = !showCreate">+ New channel</button>
    </div>

    <div v-if="showCreate" class="card" style="margin-bottom:20px">
      <p class="section-title">Create channel</p>
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="ops.alerts" />
      </div>
      <div class="form-group">
        <label>Mode</label>
        <select v-model="form.mode">
          <option value="broadcast">broadcast</option>
          <option value="claimable">claimable</option>
        </select>
      </div>
      <div class="form-group">
        <label>Description</label>
        <input v-model="form.description" placeholder="Optional description" />
      </div>
      <div class="form-group">
        <label>Pinned message</label>
        <input v-model="form.pinned_message" placeholder="Optional guidance for producers" />
      </div>
      <p v-if="createError" class="error">{{ createError }}</p>
      <div class="row">
        <button class="btn" @click="createChannel" :disabled="creating">{{ creating ? 'Creating…' : 'Create' }}</button>
        <button class="btn-ghost" @click="showCreate = false">Cancel</button>
      </div>
    </div>

    <div v-if="loading" class="muted">Loading…</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="channels.length === 0" class="muted">No channels yet.</div>
    <div v-else>
      <RouterLink
        v-for="ch in channels"
        :key="ch.id"
        :to="`/channels/${ch.id}`"
        class="channel-row card"
      >
        <div class="row">
          <span class="mono" style="font-size:14px;flex:1">{{ ch.name }}</span>
          <span class="badge" :class="ch.mode">{{ ch.mode }}</span>
        </div>
        <p v-if="ch.description" class="muted" style="margin-top:5px;font-size:12px">{{ ch.description }}</p>
        <div v-if="ch.aliases.length" style="margin-top:6px">
          <span v-for="a in ch.aliases" :key="a" class="alias-tag">{{ a }}</span>
        </div>
      </RouterLink>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../api.js'

const channels = ref([])
const loading = ref(true)
const error = ref('')
const showCreate = ref(false)
const creating = ref(false)
const createError = ref('')
const form = ref({ name: '', mode: 'broadcast', description: '', pinned_message: '' })

async function load() {
  try {
    // GET /v1/channels is not in the API — there is no list-all. We rebuild
    // the list from IDs captured on create (localStorage), and always pin the
    // public 'sandbox' demo channel so a fresh login lands on something live.
    const cached = JSON.parse(localStorage.getItem('bc_channels') || '[]')
    const ids = [...new Set(['sandbox', ...cached])]
    const results = await Promise.allSettled(ids.map(id => api.get(`/v1/channels/${id}`)))
    const seen = new Set()
    channels.value = results
      .filter(r => r.status === 'fulfilled')
      .map(r => r.value)
      .filter(ch => !seen.has(ch.id) && seen.add(ch.id))
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function createChannel() {
  if (!form.value.name) return
  creating.value = true
  createError.value = ''
  try {
    const body = { name: form.value.name, mode: form.value.mode }
    if (form.value.description) body.description = form.value.description
    if (form.value.pinned_message) body.pinned_message = form.value.pinned_message
    const ch = await api.post('/v1/channels', body)
    const cached = JSON.parse(localStorage.getItem('bc_channels') || '[]')
    cached.unshift(ch.id)
    localStorage.setItem('bc_channels', JSON.stringify([...new Set(cached)]))
    channels.value.unshift(ch)
    form.value = { name: '', mode: 'broadcast', description: '', pinned_message: '' }
    showCreate.value = false
  } catch (err) {
    createError.value = err.data?.message || err.message
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.channel-row { display: block; text-decoration: none; color: inherit; transition: border-color 0.15s; }
.channel-row:hover { border-color: var(--accent); text-decoration: none; }
.alias-tag {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted);
  background: rgba(255,255,255,0.04);
  border-radius: 3px;
  padding: 1px 6px;
  margin-right: 5px;
}
</style>
