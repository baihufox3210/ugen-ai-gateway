import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import darkTheme from "./darkTheme";

declare const process: { env: Record<string, string | undefined> };
const backendPort = process.env.UGEN_BACKEND_PORT ?? "7632";

export default defineConfig({
  plugins: [react(), darkTheme()],
  // The Markdown/KaTeX/highlight chunk is large but loads only with the first reply.
  build: { chunkSizeWarningLimit: 700 },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
