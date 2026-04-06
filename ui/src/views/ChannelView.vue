<template>
  <div v-if="loading" class="muted">Loading…</div>
  <div v-else-if="error" class="error">{{ error }}</div>
  <div v-else-if="channel">
    <div class="row" style="margin-bottom:4px">
      <RouterLink to="/channels" class="back muted">← Channels</RouterLink>
    </div>
    <div class="row" style="margin-bottom:20px;align-items:flex-start">
      <div style="flex:1">
        <h1 class="page-title" style="margin-bottom:4px">{{ channel.name }}</h1>
        <p v-if="channel.description" class="muted" style="font-size:13px">{{ channel.description }}</p>
      </div>
      <span class="badge" :class="channel.mode">{{ channel.mode }}</span>
    </div>

    <div v-if="channel.pinned_message" class="card pinned">
      <span class="section-title" style="margin-bottom:4px">📌 Pinned</span>
      <p class="mono" style="font-size:13px">{{ channel.pinned_message }}</p>
    </div>

    <div class="row" style="margin-bottom:14px;margin-top:24px">
      <p class="section-title" style="margin:0">Messages</p>
      <button class="btn-ghost" style="margin-left:auto;font-size:12px" @click="loadMessages">↻ Refresh</button>
    </div>

    <div class="card" style="margin-bottom:20px">
      <p class="section-title">Post message</p>
      <div class="form-group">
        <label>Content</label>
        <textarea v-model="postForm.content" rows="2" placeholder="Message content…" />
      </div>
      <div class="form-group">
        <label>Actor (id or alias, optional)</label>
        <input v-model="postForm.actor" placeholder="worker-7" />
      </div>
      <p v-if="postError" class="error">{{ postError }}</p>
      <button class="btn" @click="postMessage" :disabled="posting">{{ posting ? 'Posting…' : 'Post' }}</button>
    </div>

    <div v-if="msgLoading" class="muted">Loading messages…</div>
    <div v-else-if="msgError" class="error">{{ msgError }}</div>
    <div v-else-if="messages.length === 0" class="muted">No active messages.</div>
    <div v-else>
      <div v-for="msg in messages" :key="msg.id" class="card msg-card">
        <div class="row" style="margin-bottom:6px">
          <span class="mono muted" style="font-size:11px">{{ msg.id.slice(0,8) }}…</span>
          <span v-if="msg.actor" class="mono" style="font-size:12px;color:var(--accent)">{{ msg.actor.name }}</span>
          <span v-if="msg.claimed_by" class="badge claimable" style="margin-left:auto">claimed · {{ msg.claimed_by.name }}</span>
          <span class="mono muted" style="font-size:11px;margin-left:auto">{{ formatTs(msg.created_at) }}</span>
        </div>
        <p style="font-size:13px;white-space:pre-wrap">{{ msg.content }}</p>
        <div v-if="Object.keys(msg.metadata).length" class="meta-block mono">{{ JSON.stringify(msg.metadata, null, 2) }}</div>
        <div v-if="msg.acknowledged_by.length" style="margin-top:8px;font-size:11px;color:var(--muted)">
          ✓ acked by {{ msg.acknowledged_by.map(a => a.name).join(', ') }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api } from '../api.js'

const route = useRoute()
const channel = ref(null)
const messages = ref([])
const loading = ref(true)
const error = ref('')
const msgLoading = ref(false)
const msgError = ref('')
const postForm = ref({ content: '', actor: '' })
const posting = ref(false)
const postError = ref('')

async function loadChannel() {
  try {
    channel.value = await api.get(`/v1/channels/${route.params.id}`)
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function loadMessages() {
  msgLoading.value = true
  msgError.value = ''
  try {
    const data = await api.get(`/v1/channels/${route.params.id}/messages?limit=50`)
    messages.value = data.items
  } catch (err) {
    msgError.value = err.message
  } finally {
    msgLoading.value = false
  }
}

async function postMessage() {
  if (!postForm.value.content.trim()) return
  posting.value = true
  postError.value = ''
  try {
    const body = { content: postForm.value.content }
    if (postForm.value.actor) body.actor = postForm.value.actor
    await api.post(`/v1/channels/${route.params.id}/messages`, body)
    postForm.value = { content: '', actor: '' }
    await loadMessages()
  } catch (err) {
    postError.value = err.data?.message || err.message
  } finally {
    posting.value = false
  }
}

function formatTs(ts) {
  return new Date(ts).toLocaleString()
}

onMounted(async () => {
  await loadChannel()
  await loadMessages()
})
</script>

<style scoped>
.back { font-size: 12px; }
.pinned { border-color: rgba(88,255,125,0.35); }
.msg-card { margin-bottom: 8px; }
.meta-block {
  margin-top: 8px;
  font-size: 11px;
  color: var(--muted);
  background: rgba(0,0,0,0.3);
  padding: 6px 10px;
  border-radius: 4px;
  white-space: pre;
  overflow-x: auto;
}
</style>
