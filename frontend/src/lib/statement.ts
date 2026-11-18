export interface StatementBlock { kind: "h" | "p" | "sample"; text: string }

// 只放宽标题识别，不对样例正文做 Unicode 归一化或去空格。
const GAP = "[\\s\\u200b-\\u200d\\u2060\\ufeff\\u00ad]*";
const TITLES = ["题目描述", "问题描述", "题目内容", "输入格式", "输出格式", "输入说明", "输出说明",
  "样例输入", "样例输出", "示例输入", "示例输出", "输入样例", "输出样例", "输入示例", "输出示例",
  "样例说明", "示例说明", "样例解释", "示例解释", "样例解析", "数据范围", "输入", "输出", "提示", "说明", "注意"];
const TITLE = TITLES.map(t => [...t].join(GAP)).join("|");
const HEADING = new RegExp(`^${GAP}(?:#{1,6}\\s*)?(?:[（(]?[0-9０-９]+[）).、．]${GAP})?(?:\\*\\*|__)?(${TITLE})${GAP}([0-9０-９一二三四五六七八九十]*)${GAP}(?:\\*\\*|__)?${GAP}(?:[:：︰﹕]${GAP}(?:\\*\\*|__)?[^\\S\\r\\n]*(.*)|$)`);

/** 样例只承载原始数据；编号、同行标题和样例说明都形成独立分段。 */
export function statementBlocks(text: string): StatementBlock[] {
  const out: StatementBlock[] = [];
  let current: StatementBlock | null = null;
  let sample = false;
  for (const line of text.replace(/\r\n?|[\u2028\u2029\f]/g, "\n").split("\n")) {
    const match = HEADING.exec(line.trim());
    if (match) {
      const title = match[1].replace(/[\s\u200b-\u200d\u2060\ufeff\u00ad]/g, "") + (match[2] ? " " + match[2].normalize("NFKC") : "");
      out.push({kind:"h",text:title});
      sample = /(?:样例|示例)/.test(title) && /输入|输出/.test(title);
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
