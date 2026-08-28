import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Dev runs standalone at / (SPA routes live at the root); production builds
// with default base=/ so assets resolve at /assets/... and the FastAPI process
// serves the bundle from wiwi/server/static at the root.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: "../wiwi/server/static",
    emptyOutDir: true,
  },
  server: {
    allowedHosts: true,
    proxy: {
      "/admin": "http://localhost:4000",
      "/auth": "http://localhost:4000",
      "/public": "http://localhost:4000",
      "/v1": "http://localhost:4000",
      "/health": "http://localhost:4000",
    },
  },
});
