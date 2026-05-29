import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: serve the UI on 5173 and proxy API calls to the FastAPI server on 8000.
// Prod: `npm run build` emits to dist/, which the API mounts at /.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  build: { outDir: "dist" },
});
