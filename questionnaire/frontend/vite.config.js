import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    // Dev server proxy for `npm run dev` alongside the API on port 8000.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
