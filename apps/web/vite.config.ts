import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The browser only talks to the FastAPI backend, never to Supabase directly.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // The charting library is its own lazily loaded chunk (about 1.2 MB, opened on demand).
    chunkSizeWarningLimit: 1300,
  },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
