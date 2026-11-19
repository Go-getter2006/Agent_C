// 教师端：知识点 × 判断、定位、修改（界面改造方案第 5 节，图 9）。左边几比几，右边最近一次作答的证据链。
// 定位、修改的分母只算实际进入这一步的次数，不能读成对所有题的能力估计；另列应答机会、提示后完成、跳过等。
import { useState } from "react";
import { CodeBlock, Empty } from "../../components/ui";
import { fullTime } from "../../lib/time";

type R = { n: number; d: number } | null;

export interface DiagnosisRow {
  code: string;
  name: string;
  judge: R; judgeBug: R; judgeOk: R; locate: R; edit: R;
  counts: { shown: number; locateEntered: number; editEntered: number; afterHelp: number; skipped: number; open: number; legacy: number };
  latest: {
    title: string; time: string; legacy: boolean; hints: number; sampleRuns: number; order: string[];
    steps: { label: string; value: string; note: string }[];
    code: { start: number; lines: string[]; picked: number[]; bug: number[] };
  } | null;
}

const frac = (r: R) => (r ? `${r.n}/${r.d}` : "尚无记录");
const ORDER: Record<string, string> = { judge: "判断", locate: "选行", sample: "运行公开样例", hint: "看提示", submit: "提交修改", feedback: "反馈" };

export function DiagnosisCard({ rows }: { rows: DiagnosisRow[] }) {
  const [picked, setPicked] = useState<string | null>(rows[0]?.code ?? null);
  const cur = rows.find((r) => r.code === picked) ?? null;
  if (rows.length === 0) {
    return <section className="card"><Empty title="还没有小题作答记录">学生在复习提升里做的小题，会按判断、定位、修改分开统计。</Empty></section>;
  }
  return (
    <section className="card diag" aria-labelledby="diag-t">
      <div className="diag-split">
        <div className="diag-col">
          <div className="dims-head"><h2 id="diag-t">知识点 × 能力</h2><span className="note">首次独立成功 / 有效机会</span></div>
          <div className="table-scroll">
            <table className="dims-table">
              <thead><tr><th scope="col">知识点</th><th scope="col">判断</th><th scope="col">定位</th><th scope="col">修改</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.code} className={r.code === picked ? "picked" : undefined}>
                    <th scope="row"><button type="button" aria-pressed={r.code === picked} onClick={() => setPicked(r.code)}>{r.name}</button></th>
                    <td className="num">{frac(r.judge)}</td>
                    <td className="num">{frac(r.locate)}</td>
                    <td className="num">{frac(r.edit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cur && (
            <div className="diag-split-note">
              <h3>{cur.name} · 判断拆分</h3>
              <p>错题识别 {frac(cur.judgeBug)}　正确代码识别 {frac(cur.judgeOk)}</p>
              <p className="note">
                学生看过 {cur.counts.shown} 道 · 进入定位 {cur.counts.locateEntered} 次 · 进入修改 {cur.counts.editEntered} 次 ·
                提示或运行后才改对 {cur.counts.afterHelp} 次 · 跳过 {cur.counts.skipped} · 没做完 {cur.counts.open}
                {cur.counts.legacy ? ` · 旧版记录 ${cur.counts.legacy} 次（未测定位）` : ""}
              </p>
            </div>
          )}
        </div>
        <div className="diag-col">
          <div className="dims-head"><h2>最近一次作答</h2>{cur && <span className="pill">{cur.name}</span>}</div>
          {cur?.latest ? (
            <div className="diag-evidence">
              <h3>{cur.latest.title}</h3>
              <span className="note">{fullTime(cur.latest.time)}</span>
              <ol className="diag-steps">
                {cur.latest.steps.map((s) => (
                  <li key={s.label}><strong>{s.label}</strong><span>{s.value}</span>{s.note && <small>{s.note}</small>}</li>
                ))}
              </ol>
              <CodeBlock caption={cur.latest.code.picked.length ? "原代码 · 学生所选行" : "原代码"} start={cur.latest.code.start}
                lines={cur.latest.code.lines} mark={cur.latest.code.picked} copyable={false} />
              {cur.latest.order.length > 0 && (
                <p className="note">顺序：{cur.latest.order.map((k) => ORDER[k] ?? k).join(" → ")}</p>
              )}
            </div>
          ) : <p className="note">这个知识点还没有判断过的小题。</p>}
        </div>
      </div>
    </section>
  );
}
