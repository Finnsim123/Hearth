import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: SPA on :5173 proxying API to the backend on :8420.
// Prod: `npm run build` output is baked into the backend image (see Dockerfile).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8420",
      "/ws": { target: "ws://localhost:8420", ws: true },
    },
  },
});
