import { useState } from "react";
import { post } from "../../api";
import { Card } from "../../components/ui";
import type { ClassInfo } from "./common";

type Report = {
  classKey: string; className: string;
  students: number; pairedStudents: number; pairedOpportunities: number; missingQuiz: number; missingDialogue: number;
  computedAt: string; cutoffExclusive: string; examTime: string; scores: number[];
  firstSubmission: { passed: number; passedNoHelpCheckedClear: number; passedSimilarityUnknown: number };
  differences: { delta: number; interval: [number, number] | null }[];
  rows: unknown[]; exclusions: Record<string, unknown[]>; noEvidenceStudents: string[];
};
const groups = ["只用 OJ", "OJ＋对话", "OJ＋对话＋复习小题"];

type Batch = { computedAt: string; classReports: Report[] };
const beijingTime = (time: string) => new Date(time).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });

function ClassReport({ report }: { report: Report }) {
  return (
    <div>
      <p>考试开始 {beijingTime(report.examTime)}，证据截止 {beijingTime(report.cutoffExclusive)}。
        {report.students} 名学生，{report.rows.length} 条学生与技能估计，{report.noEvidenceStudents.length} 人尚无可用证据。</p>
      <h3>三种证据组合预测平时新题首交</h3>
      <p>配对 {report.pairedStudents} 人、{report.pairedOpportunities} 次首交；全班中，{report.missingDialogue} 人在配对时没有该技能的对话证据，{report.missingQuiz} 人没有小题证据。</p>
      <p>首交通过 {report.firstSubmission.passed} 次，其中未观察到系统内求助且已检查无雷同标记 {report.firstSubmission.passedNoHelpCheckedClear} 次，雷同状态未知 {report.firstSubmission.passedSimilarityUnknown} 次。</p>
      <p>首交前暂无题目标注 {report.exclusions.noLabelAtSubmission?.length ?? 0} 次，评测结果未定 {report.exclusions.unjudged?.length ?? 0} 次，截至所选时间尚未取到结果 {report.exclusions.resultUnavailableAtCutoff?.length ?? 0} 次。</p>
      {report.scores.length > 0 && <div className="table-scroll"><table className="table"><thead><tr><th>证据</th><th>Brier（越低越好）</th><th>相对上一组的差值及 95% 区间</th></tr></thead>
        <tbody>{report.scores.map((score, i) => {
          const diff = report.differences[i-1];
          return <tr key={groups[i]}><td>{groups[i]}</td><td>{score.toFixed(3)}</td>
            <td>{diff ? diff.delta.toFixed(3) + (diff.interval ? "（" + diff.interval.map(x => x.toFixed(3)).join(" 至 ") + "）" : "（学生不足两人）") : "—"}</td></tr>;
        })}</tbody></table></div>}
    </div>
  );
}

export default function ExamReplay({ classKey, classes, times, onTimeChange }: {
  classKey: string;
  classes: ClassInfo[];
  times: Record<string, string>;
  onTimeChange: (classKey: string, time: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [batch, setBatch] = useState<Batch | null>(null);
  const ready = classes.length > 0 && classes.every(c => times[c.key]);
  async function calculate(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError("");
    try {
      const examTimes = Object.fromEntries(classes.map(c => [c.key, times[c.key] + ":00+08:00"]));
      setBatch(await post<Batch>(`/api/teacher/classes/${classKey}/exam-replay`, { examTimes }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "考前数据读取失败");
    } finally {
      setBusy(false);
    }
  }
  function download(report: Batch) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `考前技能估计-${classKey}-${report.computedAt.slice(0, 19).replace(/:/g, "-")}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <Card title="考前技能估计" sub="按班填写考试开始时间（北京时间）。如果考试还没开始，证据只算到当前时刻。">
    <form className="stack" onSubmit={calculate}>
      <div className="grid-2">
        {classes.map(c => (
          <label className="label" key={c.key}>{c.name} · 考试开始时间
            <input className="field" required type="datetime-local" disabled={busy} value={times[c.key] ?? ""}
              onChange={e => { onTimeChange(c.key, e.target.value); setBatch(null); setError(""); }} />
          </label>
        ))}
      </div>
      <div><button className="btn" disabled={!ready || busy}>{busy ? "正在回算…" : "查看考前估计"}</button></div>
    </form>
    {error && <p role="alert">{error}</p>}
    {batch && <div className="stack">
      <div><button className="btn" onClick={() => download(batch)}>导出逐技能估计与配对明细（JSON）</button></div>
      {batch.classReports.length === 1 ? <ClassReport report={batch.classReports[0]} />
        : batch.classReports.map((report, index) => (
          <details key={report.classKey} open={index === 0}>
            <summary>{report.className} · 考试开始 {beijingTime(report.examTime)}</summary>
            <ClassReport report={report} />
          </details>
        ))}
    </div>}
  </Card>;
}
