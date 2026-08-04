/// <reference types="vitest/config" />
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { internalScriptPlugin } from './src/vite/internalScriptPlugin.ts';

// Backend target for the /api proxy. Defaults to the local IntelliJ backend;
// in a container/K8s pod set BACKEND_URL to the backend Service DNS
// (e.g. http://erd-backend:8080). Only used by `vite` (dev) and `vite preview`
// — a pure static server such as `serve` does NOT proxy and ignores this.
const backendUrl = process.env.BACKEND_URL || 'http://localhost:8080';

// 公司環境的內部 library 以 global script 注入（非 npm 套件，package.json 不受影響）。
// 沿用本檔既有風格從 process.env 讀取，與 BACKEND_URL / ALLOWED_HOSTS 一致。
const internalScriptUrl = process.env.VITE_INTERNAL_SCRIPT_URL;

// ALLOWED_HOSTS: unset → vite default (localhost only); "*" or "true" → allow
// any Host header; otherwise a comma-separated allowlist of hostnames.
const allowedHostsEnv = process.env.ALLOWED_HOSTS?.trim();
const allowedHosts: true | string[] | undefined = !allowedHostsEnv
  ? undefined
  : allowedHostsEnv === '*' || allowedHostsEnv === 'true'
    ? true
    : allowedHostsEnv.split(',').map((host) => host.trim());

// Shared by both the dev server and `vite preview` so either can run in a pod
// and reach the backend. host:true binds 0.0.0.0 (required inside a container;
// vite otherwise binds localhost and is unreachable through a Service).
const serverOptions = {
  host: true,
  port: 3000,
  strictPort: true,
  allowedHosts,
  proxy: { '/api': { target: backendUrl, changeOrigin: true } },
} as const;

export default defineConfig({
  plugins: [react(), tailwindcss(), internalScriptPlugin(internalScriptUrl)],
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
