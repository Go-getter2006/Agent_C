// 题面里的公式：$...$ 行内、$$...$$ 单独一行，用 KaTeX 排（跟着前端打包，不连外网）。
// 只有题面里真有公式时才加载 KaTeX（lazy import），没有公式的题不多下载东西。
import katex from "katex";
import "katex/dist/katex.min.css";

export function renderMath(tex: string, display: boolean): string {
  // trust 默认关着：\href、\includegraphics 这类不执行；写错的公式显示成红字原文，不报错
  return katex.renderToString(tex, { displayMode: display, throwOnError: false, output: "htmlAndMathml", strict: false });
}
