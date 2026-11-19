// C 代码的语法着色：关键字、类型、函数名、字符串（里面的转义和 %d 这类格式另标）、数字、注释、预处理。
// 对话、提交、小题里的代码块都是 C（数据结构那边也是 C 写的），不用再判断语言。
// 只是给字上色，不改字、不标行；跨行的 /* */ 注释按行接着算。颜色在 tokens.css 的 --syn-*。

export type SynKind = "kw" | "type" | "fn" | "str" | "esc" | "num" | "com" | "pre";
export interface SynTok { k: SynKind | null; s: string }

const KEYWORDS = new Set(["if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue",
  "return", "goto", "sizeof", "typedef", "struct", "union", "enum"]);
const TYPES = new Set(["int", "char", "float", "double", "void", "long", "short", "unsigned", "signed", "const",
  "static", "extern", "register", "volatile", "auto", "inline", "restrict", "bool", "_Bool", "size_t", "FILE",
  "int8_t", "int16_t", "int32_t", "int64_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t"]);
const CONSTANTS = new Set(["NULL", "EOF", "true", "false", "INT_MAX", "INT_MIN", "LLONG_MAX", "LLONG_MIN",
  "UINT_MAX", "CHAR_MAX", "RAND_MAX", "EXIT_SUCCESS", "EXIT_FAILURE", "stdin", "stdout", "stderr"]);

const NUM = /^(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)[uUlLfF]*/;
const IDENT = /^[A-Za-z_]\w*/;
// 字符串里的转义（\n、\\、\0、\x41）和 printf / scanf 的格式（%d、%5.2f、%lld、%%）
const STR_PART = /\\(?:x[0-9a-fA-F]+|[0-7]{1,3}|.)|%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|L|z|j|t)?[diouxXeEfFgGaAcspn%]/;

function quoted(line: string, i: number, quote: string, out: SynTok[]): number {
  let j = i + 1;
  while (j < line.length && line[j] !== quote) j += line[j] === "\\" ? 2 : 1;
  const end = Math.min(line.length, j + 1);
  const body = line.slice(i, end);
  let rest = body;
  while (rest) {
    const m = STR_PART.exec(rest);
    if (!m) { out.push({ k: "str", s: rest }); break; }
    if (m.index) out.push({ k: "str", s: rest.slice(0, m.index) });
    out.push({ k: "esc", s: m[0] });
    rest = rest.slice(m.index + m[0].length);
  }
  return end;
}

/** 一段代码逐行着色。返回和 lines 一一对应的片段，拼起来就是原来那一行。 */
export function highlightC(lines: string[]): SynTok[][] {
  let inComment = false;
  return lines.map((line) => {
    const out: SynTok[] = [];
    let plain = "";
    const push = (k: SynKind | null, s: string) => {
      if (!s) return;
      if (k === null) { plain += s; return; }
      if (plain) { out.push({ k: null, s: plain }); plain = ""; }
      out.push({ k, s });
    };
    let i = 0;
    if (inComment) {
      const end = line.indexOf("*/");
      if (end < 0) return [{ k: "com", s: line }];
      push("com", line.slice(0, end + 2));
      i = end + 2;
      inComment = false;
    }
    const pre = /^(\s*)(#\s*[a-z]+)/.exec(line.slice(i));
    if (i === 0 && pre) {
      push(null, pre[1]);
      push("pre", pre[2]);
      i = pre[0].length;
      const header = /^(\s*)(<[^>]*>)/.exec(line.slice(i));
      if (/include/.test(pre[2]) && header) {
        push(null, header[1]);
        push("str", header[2]);
        i += header[0].length;
      }
    }
    while (i < line.length) {
      const ch = line[i];
      const rest = line.slice(i);
      if (rest.startsWith("//")) { push("com", rest); break; }
      if (rest.startsWith("/*")) {
        const end = line.indexOf("*/", i + 2);
        if (end < 0) { push("com", rest); inComment = true; break; }
        push("com", line.slice(i, end + 2));
        i = end + 2;
        continue;
      }
      if (ch === '"' || ch === "'") {
        const parts: SynTok[] = [];
        i = quoted(line, i, ch, parts);
        parts.forEach((p) => push(p.k, p.s));
        continue;
      }
      if (/[0-9]/.test(ch) || (ch === "." && /[0-9]/.test(line[i + 1] ?? ""))) {
        const prev = line[i - 1] ?? "";
        const m = /[A-Za-z_0-9]/.test(prev) ? null : NUM.exec(rest);
        if (m) { push("num", m[0]); i += m[0].length; continue; }
      }
      const id = IDENT.exec(rest);
      if (id) {
        const w = id[0];
        const kind: SynKind | null = KEYWORDS.has(w) ? "kw" : TYPES.has(w) ? "type" : CONSTANTS.has(w) ? "num"
          : /^\s*\(/.test(line.slice(i + w.length)) ? "fn" : null;
        push(kind, w);
        i += w.length;
        continue;
      }
      push(null, ch);
      i += 1;
    }
    if (plain) out.push({ k: null, s: plain });
    return out;
  });
}
