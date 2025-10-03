import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,        // dev server port
    proxy: {
      '/api': 'http://localhost:5000'  // proxy backend API calls
    }
  },
  build: {
    outDir: 'dist'
  }
})
