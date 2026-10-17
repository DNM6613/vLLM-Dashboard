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
  build: {
    rollupOptions: {
      output: {
        // Split heavy vendor libraries out of the entry chunk (audit
        // 2026-10-17 A-4: main chunk was 720.89 kB, above the 500 kB
        // advisory).
        manualChunks(id: string) {
          const p = id.replace(/\\/g, '/')
          if (!p.includes('node_modules/')) return undefined
          if (p.includes('@xterm/')) return 'terminal'
          if (p.includes('react-router')) return 'router'
          if (p.includes('node_modules/react-dom/') || p.includes('node_modules/scheduler/') || p.includes('node_modules/react/')) return 'react-vendor'
          return 'vendor'
        },
      },
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
