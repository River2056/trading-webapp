import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { allowedHostsFromEnvironment } from './vite-hosts'

export default defineConfig({
  plugins: [vue()],
  server: {
    allowedHosts: allowedHostsFromEnvironment(process.env),
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  test: {
    environment: 'jsdom',
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
