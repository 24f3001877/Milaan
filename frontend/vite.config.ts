import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

// The dev server proxies /api to the FastAPI process, so the browser only ever talks to
// its own origin and no CORS preflight happens at all. This replaces the previous setup,
// where the frontend called http://localhost:8000 directly and only worked because the API
// allow-listed exactly http://localhost:5173 — any other port broke silently.
//
// In production, VITE_API_BASE_URL points at the deployed API origin and this proxy is
// not involved. Leave it unset when the API is served under the same origin as the SPA.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_DEV_API_PROXY_TARGET || 'http://localhost:8000'

  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
      },
    },
    build: {
      // A finance desk tool is opened on a desktop over a decent connection; one chunk
      // that loads fully is preferable to route-splitting a four-screen SPA.
      chunkSizeWarningLimit: 900,
    },
  }
})
