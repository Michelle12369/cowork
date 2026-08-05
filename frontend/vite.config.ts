/// <reference types="vitest/config" />
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// Backend target for the /api proxy. Defaults to the local IntelliJ backend;
// in a container/K8s pod set BACKEND_URL to the backend Service DNS
// (e.g. http://erd-backend:8080). Only used by `vite` (dev) and `vite preview`
// — a pure static server such as `serve` does NOT proxy and ignores this.
const backendUrl = process.env.BACKEND_URL || 'http://localhost:8080';

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
