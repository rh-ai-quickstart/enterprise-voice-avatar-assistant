import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Two entries: the chat (index.html) and the admin portal (admin/index.html). The portal's
// PatternFly, router and query client never reach the chat bundle.
// In development, /api is proxied to a local RAG API (or a port-forward to the cluster).
// In the container, nginx proxies /api to the rag-api Service and serves /admin/* from admin/index.html.

/** The portal's own routes (/admin/tickets/REQ-1, ...) load admin/index.html in development too. */
function adminFallback(): Plugin {
  return {
    name: "admin-fallback",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const path = (req.url ?? "").split("?")[0];
        if (path === "/admin" || (path.startsWith("/admin/") && !path.slice(7).includes("."))) req.url = "/admin/index.html";
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), adminFallback()],
  build: {
    rolldownOptions: {
      input: {
        main: fileURLToPath(new URL("index.html", import.meta.url)),
        admin: fileURLToPath(new URL("admin/index.html", import.meta.url)),
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
