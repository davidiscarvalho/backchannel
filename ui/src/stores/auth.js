import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const key = ref(localStorage.getItem('bc_api_key') || '')

  const isAuthenticated = computed(() => key.value.length > 0)

  function setKey(value) {
    key.value = value
    localStorage.setItem('bc_api_key', value)
  }

  function clearKey() {
    key.value = ''
    localStorage.removeItem('bc_api_key')
  }

  return { key, isAuthenticated, setKey, clearKey }
})
