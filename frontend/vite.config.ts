import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (docker compose in ../backend, port 8000).
// Proxying /api keeps the browser on one origin, so no CORS setup is needed.
const API_TARGET = process.env.API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true, ws: true },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true, ws: true },
    },
  },
});
