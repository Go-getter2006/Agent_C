import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
// 字体跟着前端一起打包（服务器不连外网）。得意黑按字拆成小文件，浏览器只下载用到的字；西文、代码字体只带拉丁字母
import "@chinese-fonts/dyh/dist/SmileySans-Oblique/result.css";
import "@fontsource/atkinson-hyperlegible-next/latin-400.css";
import "@fontsource/atkinson-hyperlegible-next/latin-500.css";
import "@fontsource/atkinson-hyperlegible-next/latin-600.css";
import "@fontsource/atkinson-hyperlegible-next/latin-700.css";
import "@fontsource/maple-mono/latin-400.css";
import "@fontsource/maple-mono/latin-600.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/pages.css";
import "./styles/desktop.css";
import "./styles/login.css";
import "./styles/homework-nav.css";
import "./styles/type.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
