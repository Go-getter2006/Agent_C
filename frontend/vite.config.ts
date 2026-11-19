import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/** 公式（KaTeX）的字体只带 woff2：现在的浏览器都认，woff 和 ttf 打包进去只是多占地方。 */
function katexWoff2Only(): Plugin {
  return {
    name: "katex-woff2-only",
    enforce: "pre",
    transform(code, id) {
      if (!/katex(\.min)?\.css$/.test(id.split("?")[0])) return null;
      return code.replace(/,\s*url\([^)]*\.woff\) format\("woff"\)/g, "").replace(/,\s*url\([^)]*\.ttf\) format\("truetype"\)/g, "");
    },
  };
}

// 开发时接口转发到后端；构建产物由后端直接托管。
export default defineConfig({
  plugins: [katexWoff2Only(), react()],
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
