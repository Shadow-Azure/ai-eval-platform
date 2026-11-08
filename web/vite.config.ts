import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
  "/healthz": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
  "/readyz": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  },
} as const;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: apiProxy,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
