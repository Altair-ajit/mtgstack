import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` -> http://localhost:5173 with hot reload, API proxied to the Python server on :8765.
// `npm run build` -> ../mtgstack/web_dist, which the Python server serves at http://localhost:8765.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../mtgstack/web_dist", emptyOutDir: true },
  server: { host: true, proxy: { "/api": "http://127.0.0.1:8765" } },
});
