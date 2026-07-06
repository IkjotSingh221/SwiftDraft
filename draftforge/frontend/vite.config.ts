/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy convention (see DECISIONS.md): the entire backend is mounted under a
// single /api prefix (including /api/health), and ONLY that prefix is
// proxied to the FastAPI server. Early on we tried proxying the API's
// natural route prefixes directly (/settings, /runs, ...) but that collides
// with the frontend's own client-side routes of the same name (a full page
// load of the Settings page and a fetch to the settings endpoint would both
// match "/settings"). A single /api prefix has no such collision by
// construction.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
