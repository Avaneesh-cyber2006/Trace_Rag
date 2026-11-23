import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Development-only same-origin proxy. Neither the UI nor Vite owns credentials.
// Vite is bound to loopback by the npm script; do not expose it on a LAN.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  test: { environment: 'jsdom', include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'], css: false },
});
