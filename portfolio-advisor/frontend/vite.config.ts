import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  base: process.env.APP_BASE_PATH ? process.env.APP_BASE_PATH.replace(/\/$/, '') + '/' : '/',
  server: {
    allowedHosts: ['5173-cfhafcnzznpoycox5fmc.sbx.kosuke.app'],
    proxy: {
      '/api': 'http://127.0.0.1:8780',
      '/auth': 'http://127.0.0.1:8780',
    },
  },
});
