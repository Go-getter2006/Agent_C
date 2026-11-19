// KaTeX 0.16 不带类型声明；这里只声明用到的那一个函数。
declare module "katex" {
  interface KatexOptions { displayMode?: boolean; throwOnError?: boolean; output?: "html" | "mathml" | "htmlAndMathml"; strict?: boolean | string; trust?: boolean }
  const katex: { renderToString(tex: string, options?: KatexOptions): string };
  export default katex;
}
