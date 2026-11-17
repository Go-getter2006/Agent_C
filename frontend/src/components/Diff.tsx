// 差异视图：两次提交并排（手机上下排）。改动的行用浅黄底，删除的行用浅红底。
import { api } from "../api";
import { useEscape, useLoad } from "../lib/hooks";
import type { DiffRow } from "../types";
import { CodeBlock, Icon, Loading, Problem, resultLine } from "./ui";

interface Props {
  problemKey: string;
  a: string;
  b: string;
  onClose: () => void;
}

export default function DiffOverlay({ problemKey, a, b, onClose }: Props) {
  useEscape(onClose);
  const diff = useLoad(() => api.diff(problemKey, a, b), [problemKey, a, b]);
  const d = diff.data;
  return (
    <div className="diff" role="dialog" aria-modal="true" aria-label="两次提交改了什么">
      <div className="diff-head">
        <div className="chat-head-main">
          <h1>{d ? `第 ${d.from.attemptNo} 次到第 ${d.to.attemptNo} 次改了什么` : "两次提交改了什么"}</h1>
          {d && <span className="chat-head-brief">{d.problem.title}，{d.changed === 0 ? "代码没有变化" : `改动了 ${d.changed} 行`}</span>}
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭差异视图" autoFocus>
          <Icon.close />
        </button>
      </div>
      <div className="diff-body">
        {diff.loading && !d && <Loading what="正在比较两次提交" />}
        {diff.error && <Problem message={diff.error} onRetry={diff.reload} />}
        {d && (
          <>
            <div className="diff-legend">
              <span><i className="swatch" style={{ background: "var(--mark)" }} />改动或新加的行</span>
              <span><i className="swatch" style={{ background: "var(--removed)" }} />删掉的行</span>
            </div>
            <table className="diff-table">
              <colgroup>
                <col style={{ width: "var(--lineno-w)" }} /><col /><col style={{ width: "var(--lineno-w)" }} /><col />
              </colgroup>
              <thead>
                <tr>
                  <th colSpan={2} scope="col">{resultLine(d.from, true)}</th>
                  <th colSpan={2} scope="col">{resultLine(d.to, true)}</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((row, i) => <Row key={i} row={row} />)}
              </tbody>
            </table>
            <div className="diff-stack">
              <Side caption={resultLine(d.from, true)} rows={d.rows} side="left" />
              <Side caption={resultLine(d.to, true)} rows={d.rows} side="right" />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Row({ row }: { row: DiffRow }) {
  const left = row.type === "del" ? "diff-removed" : row.type === "change" ? "diff-changed" : "";
  const right = row.type === "add" || row.type === "change" ? "diff-changed" : "";
  return (
    <tr>
      <td className={`diff-no ${left}`}>{row.left?.n ?? ""}</td>
      <td className={`diff-text ${left}`}>{row.left?.text ?? ""}</td>
      <td className={`diff-no diff-right-no ${right}`}>{row.right?.n ?? ""}</td>
      <td className={`diff-text ${right}`}>{row.right?.text ?? ""}</td>
    </tr>
  );
}

function Side({ caption, rows, side }: { caption: string; rows: DiffRow[]; side: "left" | "right" }) {
  const mine = rows.filter((r) => r[side] !== null);
  const lines = mine.map((r) => r[side]!.text);
  const numbers = mine.map((r) => r[side]!.n);
  const mark = mine.filter((r) => r.type === "change" || (side === "right" && r.type === "add")).map((r) => r[side]!.n);
  const removed = side === "left" ? mine.filter((r) => r.type === "del").map((r) => r.left!.n) : [];
  return <CodeBlock caption={caption} lines={lines} numbers={numbers} mark={mark} removed={removed} copyable={false} />;
}
