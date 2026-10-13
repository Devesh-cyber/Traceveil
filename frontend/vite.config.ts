import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the browser talks to this server, which forwards /api to the backend.
// That keeps everything on one address, so no cross-site settings are needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
