import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// During development the Vue dev server runs on :5173 and proxies /api calls
// to the FastAPI backend on :8000, so there are no CORS surprises locally.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
