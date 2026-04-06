<template>
  <div v-if="loading" class="muted">Loading…</div>
  <div v-else-if="error" class="error">{{ error }}</div>
  <div v-else-if="actor">
    <RouterLink to="/actors" class="muted" style="font-size:12px">← Actors</RouterLink>
    <h1 class="page-title" style="margin-top:12px">{{ actor.name }}</h1>
    <p v-if="actor.description" class="muted" style="margin-bottom:16px;font-size:13px">{{ actor.description }}</p>

    <div class="card" style="margin-bottom:20px">
      <p class="section-title">Details</p>
      <table class="detail-table">
        <tr><td class="muted">ID</td><td class="mono">{{ actor.id }}</td></tr>
        <tr><td class="muted">Owner</td><td class="mono">{{ actor.owner_id }}</td></tr>
        <tr><td class="muted">Created</td><td>{{ formatTs(actor.created_at) }}</td></tr>
        <tr v-if="actor.aliases.length"><td class="muted">Aliases</td><td>
          <span v-for="a in actor.aliases" :key="a" class="alias-tag">{{ a }}</span>
        </td></tr>
      </table>
    </div>

    <div class="card">
      <p class="section-title">Add alias</p>
      <div class="row">
        <input v-model="aliasInput" placeholder="e.g. worker-7" style="flex:1" />
        <button class="btn" @click="addAlias" :disabled="addingAlias">Add</button>
      </div>
      <p v-if="aliasError" class="error" style="margin-top:6px">{{ aliasError }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api } from '../api.js'

const route = useRoute()
const actor = ref(null)
const loading = ref(true)
const error = ref('')
const aliasInput = ref('')
const addingAlias = ref(false)
const aliasError = ref('')

async function load() {
  try {
    actor.value = await api.get(`/v1/actors/${route.params.id}`)
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function addAlias() {
  if (!aliasInput.value.trim()) return
  addingAlias.value = true
  aliasError.value = ''
  try {
    actor.value = await api.post(`/v1/actors/${route.params.id}/aliases`, { alias: aliasInput.value.trim() })
    aliasInput.value = ''
  } catch (err) {
    aliasError.value = err.data?.message || err.message
  } finally {
    addingAlias.value = false
  }
}

function formatTs(ts) {
  return new Date(ts).toLocaleString()
}

onMounted(load)
</script>

<style scoped>
.detail-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.detail-table td { padding: 5px 0; vertical-align: top; }
.detail-table td:first-child { width: 90px; font-size: 12px; padding-right: 16px; text-transform: uppercase; letter-spacing: 0.05em; }
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
