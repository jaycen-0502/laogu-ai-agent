import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    build: {
    // CodeMirror is lazy-loaded only when the script center is opened.
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes('/node_modules/@codemirror/') ||
            id.includes('/node_modules/@lezer/') ||
            id.includes('/node_modules/@uiw/react-codemirror/') ||
            id.includes('/node_modules/codemirror/')
          ) return 'code-editor'
        },
      },
    },
    },
    server: {
      port: 5173,
      proxy: { '/api': env.VITE_API_PROXY || 'http://127.0.0.1:8000' },
    },
  }
})
