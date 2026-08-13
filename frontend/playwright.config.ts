import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  use: { baseURL: 'http://127.0.0.1:4173' },
  webServer: [
    {
      command: 'uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/docs',
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
    },
  ],
})
