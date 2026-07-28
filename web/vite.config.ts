/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    env: {
      // Browsers resolve a relative VITE_API_BASE_URL against the page
      // origin automatically; Node's fetch (what Vitest runs under) does
      // not, so tests need an absolute URL. Production keeps the relative
      // default in src/api/env.ts unchanged.
      VITE_API_BASE_URL: 'http://localhost/api/v1',
    },
  },
})
