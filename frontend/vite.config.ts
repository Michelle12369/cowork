/// <reference types="vitest/config" />
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Backend target for the dev/preview proxy——本機開發唯一路線，不吃任何環境變數
// （容器/部署一律走 nginx 靜態版，不經 vite，此設定與其無關）。
const backendUrl = 'http://localhost:8080';

const serverOptions = {
  host: true,
  port: 3000,
  strictPort: true,
  proxy: {
    '/api': { target: backendUrl, changeOrigin: true },
    '/tsso': { target: backendUrl, changeOrigin: true },
  },
} as const;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: serverOptions,
  preview: serverOptions,
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
});
