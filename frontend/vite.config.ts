import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时接口转发到后端；构建产物由后端直接托管。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900,
    // 字体文件不内联进 CSS：得意黑按字拆成了几十个小文件，浏览器按页面上用到的字去取
    assetsInlineLimit: (file: string) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : undefined),
  },
});
