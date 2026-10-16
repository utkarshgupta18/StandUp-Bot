import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API serves the built app at /app/. In dev (`npm run dev`), API calls
// are proxied to the FastAPI server so no CORS setup is needed.
const API = process.env.STANDUP_API_URL ?? "http://localhost:8000";
const apiPaths = ["/auth", "/projects", "/ingest", "/query", "/dashboard", "/connectors", "/sources", "/users", "/health", "/scheduler"];

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(apiPaths.map((p) => [p, { target: API, changeOrigin: true }])),
  },
});
