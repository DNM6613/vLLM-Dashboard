import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:5174',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:5174',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:5174',
        ws: true,
        configure: (proxy) => {
          proxy.on('error', (err: Error & { code?: string }) => {
            if (err.code === 'ECONNRESET' || err.code === 'EPIPE') return
            console.error('[proxy]', err)
          })
        },
      },
    },
  },
})
