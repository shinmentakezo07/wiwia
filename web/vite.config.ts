import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// Dev runs standalone at / (SPA routes live at the root); production builds
// with --base=/admin/ui/ (see package.json) so the FastAPI process can serve
// the bundle from wiwi/server/static at /admin/ui.
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
    proxy: {
      "/admin": "http://localhost:4000",
      "/auth": "http://localhost:4000",
      "/public": "http://localhost:4000",
      "/v1": "http://localhost:4000",
      "/health": "http://localhost:4000",
    },
  },
});
