import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` -> http://localhost:5173 with hot reload, API proxied to the Python server on :8765.
// `npm run build` -> ../mtgstack/web_dist, which the Python server serves at http://localhost:8765.
// Phone build (GitHub Pages): VITE_LOCAL=1 PAGES_BASE=/repo/ PAGES_OUT=../site npm run build:pages
const pages = process.env.VITE_LOCAL === "1";
export default defineConfig({
  plugins: [react()],
  base: pages ? process.env.PAGES_BASE || "/" : "/",
  build: { outDir: pages ? process.env.PAGES_OUT || "../site" : "../mtgstack/web_dist", emptyOutDir: true },
  server: { host: true, proxy: { "/api": "http://127.0.0.1:8765" } },
});
