import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy API calls to the FastAPI server so no CORS setup is needed.
const API = process.env.API_URL || "http://localhost:8002";
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": { target: API, rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
