import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built assets are served by sorpple_web.py from web/dist, so the base is
// relative and the dev server proxies the API to the Python process.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://127.0.0.1:7331',
    },
  },
})
