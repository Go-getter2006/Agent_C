export interface StatementBlock { kind: "h" | "p" | "sample"; text: string }

const TITLE = "(?:题目描述|问题描述|题目内容|输入格式|输出格式|输入说明|输出说明|输入|输出|(?:样例|示例)(?:输入|输出)|(?:输入|输出)(?:样例|示例)|样例说明|示例说明|样例解释|示例解释|样例解析|提示|说明|数据范围|注意)";
const HEADING = new RegExp(`^(?:[（(]?\\d+[）).、．]\\s*)?(${TITLE}\\s*(?:\\d+|[一二三四五六七八九十])?)\\s*(?:[:：]\\s*(.*)|$)`);

/** 样例只承载原始数据；编号、同行标题和样例说明都形成独立分段。 */
export function statementBlocks(text: string): StatementBlock[] {
  const out: StatementBlock[] = [];
  let current: StatementBlock | null = null;
  let sample = false;
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const match = HEADING.exec(line.trim());
    if (match) {
      const title = match[1].trim();
      out.push({kind:"h",text:title});
      sample = /(?:样例|示例)/.test(title) && /输入|输出/.test(title);
      current = null;
      if (match[2]?.trim()) {
        current = {kind:sample ? "sample" : "p",text:match[2]};
        out.push(current);
      }
      continue;
    }
    const kind = sample ? "sample" : "p";
    if (!current || current.kind !== kind) { current = {kind,text:line}; out.push(current); }
    else current.text += "\n" + line;
  }
  return out.map(b=>({...b,text:b.text.replace(/^\n+|\n+$/g,"")})).filter(b=>b.kind==="h" || b.text.trim());
}
