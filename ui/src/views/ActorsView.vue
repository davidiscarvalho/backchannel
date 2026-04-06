<template>
  <div>
    <div class="row" style="margin-bottom:20px">
      <h1 class="page-title" style="margin:0">Actors</h1>
      <button class="btn" style="margin-left:auto" @click="showCreate = !showCreate">+ New actor</button>
    </div>

    <div v-if="showCreate" class="card" style="margin-bottom:20px">
      <p class="section-title">Create actor</p>
      <div class="form-group">
        <label>Name</label>
        <input v-model="form.name" placeholder="worker-7" />
      </div>
      <div class="form-group">
        <label>Description</label>
        <input v-model="form.description" placeholder="Optional description" />
      </div>
      <p v-if="createError" class="error">{{ createError }}</p>
      <div class="row">
        <button class="btn" @click="createActor" :disabled="creating">{{ creating ? 'Creating…' : 'Create' }}</button>
        <button class="btn-ghost" @click="showCreate = false">Cancel</button>
      </div>
    </div>

    <div v-if="loading" class="muted">Loading…</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="actors.length === 0" class="muted">No actors yet.</div>
    <div v-else>
      <RouterLink
        v-for="actor in actors"
        :key="actor.id"
        :to="`/actors/${actor.id}`"
        class="actor-row card"
      >
        <div class="row">
          <span class="mono" style="font-size:14px;flex:1">{{ actor.name }}</span>
          <span class="mono muted" style="font-size:11px">{{ actor.id.slice(0,8) }}…</span>
        </div>
        <p v-if="actor.description" class="muted" style="margin-top:4px;font-size:12px">{{ actor.description }}</p>
        <div v-if="actor.aliases.length" style="margin-top:6px">
          <span v-for="a in actor.aliases" :key="a" class="alias-tag">{{ a }}</span>
        </div>
      </RouterLink>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../api.js'

const actors = ref([])
const loading = ref(true)
const error = ref('')
const showCreate = ref(false)
const creating = ref(false)
const createError = ref('')
const form = ref({ name: '', description: '' })

async function load() {
  try {
    const cached = JSON.parse(localStorage.getItem('bc_actors') || '[]')
    const results = await Promise.allSettled(cached.map(id => api.get(`/v1/actors/${id}`)))
    actors.value = results.filter(r => r.status === 'fulfilled').map(r => r.value)
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function createActor() {
  if (!form.value.name) return
  creating.value = true
  createError.value = ''
  try {
    const body = { name: form.value.name }
    if (form.value.description) body.description = form.value.description
    const actor = await api.post('/v1/actors', body)
    const cached = JSON.parse(localStorage.getItem('bc_actors') || '[]')
    cached.unshift(actor.id)
    localStorage.setItem('bc_actors', JSON.stringify([...new Set(cached)]))
    actors.value.unshift(actor)
    form.value = { name: '', description: '' }
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
.actor-row { display: block; text-decoration: none; color: inherit; transition: border-color 0.15s; }
.actor-row:hover { border-color: var(--accent); }
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
