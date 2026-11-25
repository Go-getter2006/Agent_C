import { useEffect, useState } from "react";
import { get } from "../../api";
import { LineChart } from "../../components/charts";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import type { GraphData } from "./Models";

type ModelKey = "v2" | "simple" | "graph";
const MODELS = [
  { key: "v2" as const, name: "证据加权模型（v2）", color: "var(--series-1)" },
  { key: "simple" as const, name: "简单累计模型（v1）", color: "var(--series-2)" },
  { key: "graph" as const, name: "先修关系模型", color: "var(--series-3)" },
];
interface Stats {
  n: number; students: number; passed: number;
  v2Brier: number | null; simpleBrier: number | null; graphBrier: number | null;
  v2Accuracy: number | null; simpleAccuracy: number | null; graphAccuracy: number | null;
  halfBrier: number | null; historyBrier: number | null;
}
export interface ComparisonReport {
  version: string; legacyGraph: boolean; from: string; to: string; today: string; grouping: "day" | "week";
  summary: Stats;
  periods: (Stats & Omit<GraphData["weekly"][number], "weekStart"> & { date: string })[];
  versions: { version: string; n: number; first: string; last: string }[];
  differences: { delta: number; interval: [number, number] | null }[];
  calibration: { model: ModelKey; lower: number; upper: number; n: number; predicted: number | null; observed: number | null }[];
  provenance: { retrospective: number; legacy: number };
}
const decimal = (value: number | null) => value === null ? "—" : value.toFixed(3);
const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toFixed(1)}%`;

export default function ModelComparison({ classKey, tick, currentVersion, onReport }: {
  classKey: string; tick: number; currentVersion: string; onReport: (report: ComparisonReport | null) => void;
}) {
  const [range, setRange] = useState("14");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [version, setVersion] = useState(currentVersion);
  const [calibrationModel, setCalibrationModel] = useState<ModelKey>("graph");
  const invalid = range === "custom" && (!start || !end || start > end);
  const query = new URLSearchParams({ version, days: range === "custom" ? "14" : range });
  if (range === "custom" && !invalid) { query.set("start", start); query.set("end", end); }
  const result = useLoad(() => invalid ? Promise.resolve(null) : get<ComparisonReport>(
    `/api/teacher/classes/${classKey}/model-comparison?${query}`), [classKey, tick, query.toString(), invalid]);
  const report = result.data;
  useEffect(() => { onReport(invalid || result.loading ? null : report); }, [report, invalid, result.loading, onReport]);
  const names = MODELS.map(model => model.key === "graph" && report?.legacyGraph
    ? { ...model, name: "先修关系模型（历史掌握估计）" } : model);
  const periods = report?.periods ?? [];
  const overlap = periods.filter(p => p.n > 0 && p.v2Accuracy === p.simpleAccuracy && p.v2Accuracy === p.graphAccuracy).length;
  const present = periods.filter(p => p.n > 0).length;
  const options = [{ version: currentVersion }, ...(report?.versions ?? []).filter(v => v.version !== currentVersion)];

  return <section className="stack" aria-labelledby="prediction-heading">
    <div>
      <h2 id="prediction-heading" className="sec-head">哪个模型更能预测首交通过</h2>
      <p className="note">每条记录是一名学生第一次提交一道新题，实际通过记为 1，未通过记为 0。</p>
    </div>
    <div className="model-filters">
      <label>预测记录版本<select className="field" aria-label="预测对照版本" value={version} onChange={e => setVersion(e.target.value)}>
        {options.map(v => <option key={v.version} value={v.version}>{v.version === currentVersion ? "当前版本" : "历史版本"} · {v.version || "未记录版本"}</option>)}
      </select></label>
      <label>时间范围<select className="field" aria-label="时间范围" value={range} onChange={e => {
        if (e.target.value === "custom" && report) { setStart(report.from); setEnd(report.to); }
        setRange(e.target.value);
      }}><option value="14">近两周</option><option value="30">近一个月</option><option value="0">全部记录</option><option value="custom">自定义日期</option></select></label>
      {range === "custom" && <>
        <label>开始日期<input className="field" aria-label="开始日期" type="date" value={start} max={end || undefined} onChange={e => setStart(e.target.value)} /></label>
        <label>结束日期<input className="field" aria-label="结束日期" type="date" value={end} min={start || undefined} onChange={e => setEnd(e.target.value)} /></label>
      </>}
      {!invalid && <a className="btn" href={`/api/teacher/export/models?key=${encodeURIComponent(classKey)}&${query}`} download>导出所选结果</a>}
    </div>
    {invalid && <p role="alert">请选择完整的起止日期，开始日期应早于或等于结束日期。</p>}
    {result.error && <Problem message={result.error} onRetry={result.reload} />}
    {result.loading && <Loading what="正在汇总所选时间的预测记录" lines={2} />}
    {report && !invalid && !result.loading && <>
      <p className="model-scope">{report.from} 至 {report.to}（北京时间，含首尾两天） · {report.summary.students} 名学生 · {report.summary.n} 次预测 · 实际首交通过 {report.summary.passed} 次</p>
      {report.legacyGraph && <p className="note">该历史版本的图模型列保存掌握估计。查看这些记录时，应保留这一口径差异。</p>}
      {!report.summary.n && <Empty title="所选时间内没有这个版本的预测记录">
        {report.versions.find(v => v.version === version) && <span>该版本有记录的日期为 {report.versions.find(v => v.version === version)!.first} 至 {report.versions.find(v => v.version === version)!.last}，可调整日期查看。</span>}
      </Empty>}
      <div className="stats model-stats">
        {names.map(model => <div key={model.key} className="card stat">
          <span className="stat-label"><i className="swatch" style={{ background: model.color }} />{model.name}</span>
          <div className="stat-row"><span className="stat-value num">{decimal(report.summary[`${model.key}Brier`])}</span></div>
          <span className="stat-foot">平均概率误差（Brier），越低越好</span>
          <span className="note">判断正确 {percent(report.summary[`${model.key}Accuracy`])}</span>
        </div>)}
      </div>
      <details className="model-explanation"><summary>这两个指标怎么看</summary>
        <p>概率误差为每条记录的（预测概率 − 实际结果）²，再取平均。预测通过概率为 80%，实际通过，误差为 0.04；实际未通过，误差为 0.64。</p>
        <p>判断正确比例按 50% 为界，将概率转为“预计通过”或“预计未通过”，再统计与实际结果一致的比例。两种模型都预计通过，即使分别给出 51% 和 95%，这项判断仍相同。</p>
      </details>
      <div className="grid-2">
        <Card title="预测概率偏差有多大" sub="纵轴为平均 Brier，越低越好。点击图例可单独查看模型，数据点支持触摸或方向键查看数值。">
          <LineChart interactive label="各模型概率误差" labels={periods.map(p => p.date.slice(5).replace("-", "/"))} min={0}
            max={Math.max(.3, ...periods.flatMap(p => [p.v2Brier ?? 0, p.simpleBrier ?? 0, p.graphBrier ?? 0]))} format={v => v.toFixed(3)}
            series={names.map(model => ({ ...model, values: periods.map(p => p[`${model.key}Brier`]) }))} />
        </Card>
        <Card title="通过或未通过，判断对了多少" sub="纵轴为判断正确比例，越高越好。不同线型和同心数据点用来辨认重合曲线。">
          <LineChart interactive label="各模型判断正确比例" labels={periods.map(p => p.date.slice(5).replace("-", "/"))} min={0} max={100} unit="%" format={v => v.toFixed(1)}
            series={names.map(model => ({ ...model, values: periods.map(p => p[`${model.key}Accuracy`] === null ? null : p[`${model.key}Accuracy`]! * 100) }))} />
          {overlap > 0 && <p className="card-foot">{overlap === present ? "有数据的日期中，三个模型的判断正确比例完全相同，曲线重合。" : `有 ${overlap} 个时间段的判断正确比例完全相同，部分曲线重合。`} 相同的正确比例仍可能对应不同的概率误差。</p>}
        </Card>
      </div>
      <Card title="简单参照和模型差异" sub="下列结果使用上面同一批预测。区间按学生整组重抽 1000 次计算，保留每名学生的全部记录。" bodyClass="">
        <div className="table-scroll"><table className="table"><thead><tr><th scope="col">参照方法</th><th scope="col" className="num">平均 Brier</th><th scope="col">怎么算</th></tr></thead>
          <tbody><tr><td>每次都预测 50%</td><td className="num">{decimal(report.summary.halfBrier)}</td><td>对通过和未通过均给出一半概率。</td></tr>
            <tr><td>此前记录的通过率</td><td className="num">{decimal(report.summary.historyBrier)}</td><td>每次只用此前同班级范围、同版本的记录，按（此前通过次数 + 1）÷（此前次数 + 2）预测。同时间发生的记录整组更新。</td></tr></tbody></table></div>
        <div className="table-scroll"><table className="table"><thead><tr><th scope="col">比较</th><th scope="col" className="num">Brier 差值</th><th scope="col">95% 重抽样区间</th><th scope="col">如何理解</th></tr></thead>
          <tbody>{report.differences.map((difference, i) => <tr key={i}>
            <td>{i === 0 ? "证据加权 − 简单累计" : "先修关系 − 证据加权"}</td><td className="num">{difference.delta.toFixed(4)}</td>
            <td>{difference.interval ? `${difference.interval[0].toFixed(4)} 至 ${difference.interval[1].toFixed(4)}` : "学生数不足以估计区间"}</td>
            <td>{!difference.interval ? "继续积累独立学生的记录" : difference.interval[0] <= 0 && difference.interval[1] >= 0 ? "区间跨过 0，差异方向尚不稳定" : difference.interval[1] < 0 ? "前者的误差较低" : "前者的误差较高"}</td>
          </tr>)}</tbody></table></div>
        <p className="card-foot">差值小于 0 表示前者误差较低。区间描述这批学生和题目中的差异，独立学生较少时应谨慎解释。学生数、题目范围和概率校准情况应与结果一起报告。</p>
      </Card>
      <Card title="模型是否过于自信" sub="按预测概率分组，对照平均预测和实际首交通过率。预测明显高于实际时，表示这一组可能过于乐观。" action={
        <label>查看模型<select className="field" aria-label="概率校准模型" value={calibrationModel} onChange={e => setCalibrationModel(e.target.value as ModelKey)}>{names.map(model => <option key={model.key} value={model.key}>{model.name}</option>)}</select></label>
      } bodyClass="">
        <div className="table-scroll"><table className="table"><thead><tr><th scope="col">预测概率范围</th><th scope="col" className="num">记录数</th><th scope="col" className="num">平均预测通过率</th><th scope="col" className="num">实际通过率</th></tr></thead>
          <tbody>{report.calibration.filter(bin => bin.model === calibrationModel).map(bin => <tr key={bin.lower}>
            <td>{Math.round(bin.lower * 100)}% 至 {Math.round(bin.upper * 100)}%{bin.upper === 1 ? "（含 100%）" : "（不含上限）"}</td>
            <td className="num">{bin.n}</td><td className="num">{percent(bin.predicted)}</td><td className="num">{percent(bin.observed)}</td>
          </tr>)}</tbody></table></div>
        <p className="card-foot">分组记录少时先作描述。此表用于检查现有概率，为后续校准提供依据。</p>
      </Card>
      <details className="model-explanation"><summary>{report.grouping === "day" ? "每天" : "每周"}的预测明细</summary>
        <div className="table-scroll"><table className="table"><thead><tr><th scope="col">日期{report.grouping === "week" ? "（周一开始）" : ""}</th><th scope="col" className="num">预测次数</th><th scope="col" className="num">实际通过</th>{names.map(model => <th key={model.key} scope="col">{model.name}<br />Brier / 判断正确</th>)}</tr></thead>
          <tbody>{periods.map(period => <tr key={period.date}><td>{period.date}</td><td className="num">{period.n}</td><td className="num">{period.passed}</td>{names.map(model => <td key={model.key} className="num">{decimal(period[`${model.key}Brier`])} / {percent(period[`${model.key}Accuracy`])}</td>)}</tr>)}</tbody></table></div>
      </details>
      <p className="note">所选范围中，{report.provenance.retrospective} 条记录按历史时点回放计算，{report.provenance.legacy} 条记录缺少完整版本追踪。日期筛选按首次提交发生时间进行。缺少记录的日期保留为空，曲线在该处断开。</p>
    </>}
  </section>;
}
