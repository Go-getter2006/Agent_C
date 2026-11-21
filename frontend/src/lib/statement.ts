export interface StatementBlock { kind: "h" | "p" | "sample"; text: string }

// 只放宽标题识别，不对样例正文做 Unicode 归一化或去空格。
const GAP = "[\\s\\u200b-\\u200d\\u2060\\ufeff\\u00ad]*";
const TITLES = ["题目描述", "问题描述", "题目内容", "输入格式", "输出格式", "输入说明", "输出说明",
  "样例输入", "样例输出", "示例输入", "示例输出", "输入样例", "输出样例", "输入示例", "输出示例",
  "样例说明", "示例说明", "样例解释", "示例解释", "样例解析", "测试用例", "数据范围", "输入", "输出", "提示", "说明", "注意"];
const TITLE = TITLES.map(t => [...t].join(GAP)).join("|");
const HEADING = new RegExp(`^${GAP}(?:#{1,6}\\s*)?(?:[（(]?[0-9０-９]+[）).、．]${GAP})?(?:\\*\\*|__)?(${TITLE})${GAP}([0-9０-９一二三四五六七八九十]*)${GAP}(?:\\*\\*|__)?${GAP}(?:[:：︰﹕](?:\\*\\*|__)?[ \\t]?(.*)|$)`);

/** 样例只承载原始数据；编号、同行标题和样例说明都形成独立分段。 */
export function statementBlocks(text: string): StatementBlock[] {
  const out: StatementBlock[] = [];
  let current: StatementBlock | null = null;
  let sample = false;
  let group = false;
  for (const line of text.replace(/\r\n?|[\u2028\u2029\f]/g, "\n").split("\n")) {
    const framed = /^\s*[【\[]([^】\]\n]+)[】\]]\s*[:：]?\s*$/.exec(line);
    const match = HEADING.exec(framed?.[1] ?? line);
    if (match) {
      const title = match[1].replace(/[\s\u200b-\u200d\u2060\ufeff\u00ad]/g, "") + (match[2] ? " " + match[2].normalize("NFKC") : "");
      out.push({kind:"h",text:title});
      const name = title.split(" ")[0];
      if (name === "测试用例") group = true;
      else if (!/输入|输出/.test(name) || !/样例|示例|^(输入|输出)$/.test(name)) group = false;
      sample = /输入|输出/.test(name) && (/样例|示例/.test(name) || group && /^(输入|输出)$/.test(name));
      current = null;
      if (match[3]?.trim()) {
        current = {kind:sample ? "sample" : "p",text:match[3]};
        out.push(current);
      }
      continue;
    }
    if (out[out.length - 1]?.kind === "h" && /^[\s\u200b\ufeff]*[:：︰﹕]\s*$/.test(line)) continue;
    const kind = sample ? "sample" : "p";
    if (!current || current.kind !== kind) { current = {kind,text:line}; out.push(current); }
    else current.text += "\n" + line;
  }
  return out.map(b=>({...b,text:b.text.replace(/^\n+|\n+$/g,"")})).filter(b=>b.kind==="h" || b.text.trim());
}

export type MathPiece = { kind: "text"; text: string } | { kind: "math"; tex: string; display: boolean };

const EXPLICIT = /\$\$([^$]+?)\$\$|\$([^$\n]+?)\$/g;
// 以前整理的题面里常见的纯文本写法，程序认得出的才排成公式；认不出的照原样，不猜
const NUM = String.raw`-?\d+(?:\.\d+)?(?:\s*\^\s*\d+)?`;
const VAR = String.raw`[A-Za-z](?:_[A-Za-z0-9])?(?:\s*\[\s*[A-Za-z0-9]+\s*\])?`;
const LE = String.raw`(?:<=|≤|<|＜|≦)`;
const RANGE = new RegExp(String.raw`(${NUM})\s*(${LE})\s*(${VAR}(?:\s*,\s*${VAR})*)\s*(${LE})\s*(${NUM})`, "g");
const POWER = /(?<![\w.^])(\d+)\s*\^\s*(\d+)(\s*[+-]\s*\d+)?(?![\w^])/g;

function texNum(s: string): string {
  return s.replace(/\s+/g, "").replace(/\^(\d+)/, "^{$1}");
}
function texVar(s: string): string {
  return s.replace(/\s+/g, "").replace(/,/g, ",\\ ");
}
function texRel(s: string): string {
  return s === "<" || s === "＜" ? "<" : "\\le";
}

/** 纯文本里程序认得出的公式（1<=n<=1000、10^9+7）换成 LaTeX；别的不动。 */
function autoMath(text: string): MathPiece[] {
  const out: MathPiece[] = [];
  const marks: { start: number; end: number; tex: string }[] = [];
  for (const m of text.matchAll(RANGE)) {
    marks.push({ start: m.index!, end: m.index! + m[0].length,
      tex: `${texNum(m[1])} ${texRel(m[2])} ${texVar(m[3])} ${texRel(m[4])} ${texNum(m[5])}` });
  }
  for (const m of text.matchAll(POWER)) {
    const start = m.index!, end = start + m[0].length;
    if (marks.some((x) => start < x.end && end > x.start)) continue;
    marks.push({ start, end, tex: `${m[1]}^{${m[2]}}${m[3] ? m[3].replace(/\s+/g, "") : ""}` });
  }
  marks.sort((a, b) => a.start - b.start);
  let at = 0;
  for (const x of marks) {
    if (x.start > at) out.push({ kind: "text", text: text.slice(at, x.start) });
    out.push({ kind: "math", tex: x.tex, display: false });
    at = x.end;
  }
  if (at < text.length) out.push({ kind: "text", text: text.slice(at) });
  return out;
}

/** 一段题面文字拆成普通文字和公式：大模型整理时写的 $...$、$$...$$，加上程序认得出的纯文本写法。样例不要用它。 */
export function mathPieces(text: string): MathPiece[] {
  const out: MathPiece[] = [];
  let at = 0;
  for (const m of text.matchAll(EXPLICIT)) {
    if (m.index! > at) out.push(...autoMath(text.slice(at, m.index!)));
    out.push({ kind: "math", tex: (m[1] ?? m[2]).trim(), display: m[1] !== undefined });
    at = m.index! + m[0].length;
  }
  if (at < text.length) out.push(...autoMath(text.slice(at)));
  return out.filter((p) => p.kind === "math" || p.text);
}
