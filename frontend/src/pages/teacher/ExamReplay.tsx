import { useState } from "react";
import { get } from "../../api";
import { Card } from "../../components/ui";

type Report = {
  students: number; pairedStudents: number; pairedOpportunities: number; missingQuiz: number; missingDialogue: number;
  computedAt: string; cutoffExclusive: string; examTime: string; scores: number[];
  firstSubmission: { passed: number; passedNoHelpCheckedClear: number; passedSimilarityUnknown: number };
  differences: { delta: number; interval: [number, number] | null }[];
  rows: unknown[]; exclusions: Record<string, unknown[]>; noEvidenceStudents: string[];
};
const groups = ["只用 OJ", "OJ＋对话", "OJ＋对话＋复习小题"];

export default function ExamReplay({ classKey }: { classKey: string }) {
  const [time, setTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  async function calculate(event: React.FormEvent) {
    event.preventDefault();
    if (!time || busy) return;
    setBusy(true);
    setError("");
    try {
      const exam = encodeURIComponent(time + ":00+08:00");
      setReport(await get<Report>(`/api/teacher/classes/${classKey}/exam-replay?exam=${exam}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "考前数据读取失败");
    } finally {
      setBusy(false);
    }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `考前技能估计-${classKey}-${time.replace(":", "-")}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <Card title="考前技能估计" sub="选择外部考试开始时间，按当时可用的记录回算。未来考试只算到当前时刻。">
    <form className="actions" onSubmit={calculate}>
      <label>考试开始时间（北京时间） <input required type="datetime-local" disabled={busy} value={time}
        onChange={e => { setTime(e.target.value); setReport(null); setError(""); }} /></label>
      <button className="btn" disabled={!time || busy}>{busy ? "正在回算…" : "查看考前估计"}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {report && <div>
      <p>证据截止 {new Date(report.cutoffExclusive).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（北京时间）。
        {report.students} 名学生，{report.rows.length} 条学生与技能估计，{report.noEvidenceStudents.length} 人尚无可用证据。</p>
      <button className="btn" onClick={download}>导出逐技能估计与配对明细（JSON）</button>
      <h3>三种证据组合预测平时新题首交</h3>
      <p>配对 {report.pairedStudents} 人、{report.pairedOpportunities} 次首交；全班中，{report.missingDialogue} 人在配对时没有该技能的对话证据，{report.missingQuiz} 人没有小题证据。</p>
      <p>首交通过 {report.firstSubmission.passed} 次，其中未观察到系统内求助且已检查无雷同标记 {report.firstSubmission.passedNoHelpCheckedClear} 次，雷同状态未知 {report.firstSubmission.passedSimilarityUnknown} 次。</p>
      <p>首交前暂无题目标注 {report.exclusions.noLabelAtSubmission?.length ?? 0} 次，评测结果未定 {report.exclusions.unjudged?.length ?? 0} 次，截至所选时间尚未取到结果 {report.exclusions.resultUnavailableAtCutoff?.length ?? 0} 次。</p>
      {report.scores.length > 0 && <table className="table"><thead><tr><th>证据</th><th>Brier（越低越好）</th><th>相对上一组的差值及 95% 区间</th></tr></thead>
        <tbody>{report.scores.map((score, i) => {
          const diff = report.differences[i-1];
          return <tr key={groups[i]}><td>{groups[i]}</td><td>{score.toFixed(3)}</td>
            <td>{diff ? diff.delta.toFixed(3) + (diff.interval ? "（" + diff.interval.map(x => x.toFixed(3)).join(" 至 ") + "）" : "（学生不足两人）") : "—"}</td></tr>;
        })}</tbody></table>}
    </div>}
  </Card>;
}
