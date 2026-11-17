import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时接口转发到后端；构建产物由后端直接托管。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
