import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/v1': 'http://localhost:8080',
      '/health': 'http://localhost:8080',
    },
  },
})
