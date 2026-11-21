// 研究 · 模型检验：掌握程度算法 v1、v2、图模型（试验中）和多维融合在同一批预测上谁更准；v2 去掉智能体评估的消融；
// 融合权重、可复查的先修技能、两种估计的分歧、前置关系的描述统计。
// 智能体评估的一致率在“理解评估”，对话行为和表现在“求助效果”。
import { useState } from "react";
import { Link } from "react-router-dom";
import { FlaskConical } from "lucide-react";
import { get } from "../../api";
import { LineChart } from "../../components/charts";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime } from "../../lib/time";
import { Code, pct, useClasses, type ClassInfo, type SkillRef } from "./common";

interface GraphData {
  version: string; prereqStatus: string;
  data: { students: number; evidence: number; posteriors: number; predictions: number; updatedAt: string | null; repeatedStudents: number };
  params: Record<string, number>;
  gaps: { userId: string; name: string; skill: SkillRef; v2: number; graph: number; level: string; yes: number; no: number }[];
  gapTotal: number;
  failureRows: { userId: string; name: string; skill: SkillRef; failures: number; updated: boolean; root: SkillRef | null; p: number | null }[];
  failureTotal: number;
  commonRoots: (SkillRef & { students: number })[];
  edges: { parent: string; child: string; parentName: string; childName: string; n: number; withParent: number | null; withoutParent: number | null; nWith: number; nWithout: number; doubtful: boolean }[];
  weekly: {
    weekStart: string; n: number; simpleAccuracy: number; v2Accuracy: number; graphAccuracy: number; simpleBrier: number; v2Brier: number; graphBrier: number;
    nFused: number; fusedBrier: number | null; v2BaseBrier: number | null; v2SameBrier: number | null;
    nScore?: number; scoreBrier?: number | null; scoreFixedBrier?: number | null;
  }[];
  predictionHistory?: { version: string; weekly: GraphData["weekly"]; legacyGraph: boolean }[];
  fusionWeekly?: GraphData["weekly"];
  fusion: { learned: boolean; n: number; minSamples: number; updatedAt: string | null; weights: { dim: string; name: string; w: number; default: number }[] };
  masteryParams?: {
    version: string; learned: boolean; n: number; fittedAt: string; minOpportunities: number; reason: string | null;
    h0: number; g: number; h0Prior: number; gPrior: number; nllPrior: number | null; nllFit: number | null;
    keys: { key: string; name: string; opportunities: number; scale: number }[];
    history: { version: string; learned: boolean; n: number }[];
  } | null;
}

/** 几周合起来的 Brier：按每周的预测条数加权。 */
function pooled(weeks: GraphData["weekly"], key: "fusedBrier" | "v2BaseBrier" | "v2SameBrier"): number | null {
  const rows = weeks.filter((w) => w.nFused > 0 && w[key] !== null);
  const n = rows.reduce((s, w) => s + w.nFused, 0);
  return n ? rows.reduce((s, w) => s + (w[key] as number) * w.nFused, 0) / n : null;
}

function FusionStats({ d }: { d: GraphData }) {
  const weeks = d.fusionWeekly ?? d.weekly;
  const n = weeks.reduce((s, w) => s + w.nFused, 0);
  const stats = [
    { name: "多维融合", v: pooled(weeks, "fusedBrier") },
    { name: "v2（同一批预测）", v: pooled(weeks, "v2SameBrier") },
    { name: "v2 去掉智能体评估（消融）", v: pooled(weeks, "v2BaseBrier") },
  ];
  return (
    <div className="stats">
      {stats.map((s) => (
        <div key={s.name} className="card stat">
          <span className="stat-label">{s.name}</span>
          <div className="stat-row"><span className="stat-value num">{s.v === null ? "—" : s.v.toFixed(3)}</span></div>
          <span className="stat-foot">平均 Brier 分数，越小越准（{n} 次预测）</span>
        </div>
      ))}
    </div>
  );
}

function FusionWeights({ d }: { d: GraphData }) {
  const maxW = Math.max(1, ...d.fusion.weights.map((x) => Math.abs(x.w)));
  return (
    <>
      <Card title="多维融合的权重" sub={d.fusion.learned
          ? `按 ${d.fusion.n} 次预测学出来的（往默认值收缩），每天夜里更新${d.fusion.updatedAt ? `，上次在${fullTime(d.fusion.updatedAt)}` : ""}`
          : `预测还不到 ${d.fusion.minSamples} 次（现在 ${d.fusion.n} 次），先用默认权重`} bodyClass="">
          <table className="table">
            <thead><tr><th scope="col">维度</th><th scope="col">现在的权重</th><th scope="col" className="num">默认</th></tr></thead>
            <tbody>
              {d.fusion.weights.map((x) => (
                <tr key={x.dim}>
                  <td>{x.name}</td>
                  <td>
                    <span className="dimcell">
                      <span className="dimcell-track" aria-hidden="true"><span className={x.w < 0 ? "neg" : undefined} style={{ width: `${Math.round((Math.abs(x.w) / maxW) * 100)}%` }} /></span>
                      <span className="num">{x.w.toFixed(2)}</span>
                    </span>
                  </td>
                  <td className="num muted">{x.default.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="card-foot">融合：把有数据的维度取 logit 加权相加再变回概率。哪个维度更能预测“下一道新题能不能一次做对”，学出来的权重就越大；橙色是负的权重（在本班的数据里，这一维越高，下一题反而越难一次做对）。</p>
        </Card>
    </>
  );
}

/** 掌握分的参数（learn.py）：每周学一版、冻结；之后的预测里同时记这一版和经验值算的掌握分，比谁准。 */
function MasteryParams({ d }: { d: GraphData }) {
  const p = d.masteryParams;
  const weeks = d.weekly.filter((w) => (w.nScore ?? 0) > 0);
  const n = weeks.reduce((s, w) => s + (w.nScore ?? 0), 0);
  const pool = (key: "scoreBrier" | "scoreFixedBrier") => {
    const rows = weeks.filter((w) => w[key] !== null && w[key] !== undefined);
    const k = rows.reduce((s, w) => s + (w.nScore ?? 0), 0);
    return k ? rows.reduce((s, w) => s + (w[key] as number) * (w.nScore ?? 0), 0) / k : null;
  };
  const stats = [{ name: "每周学的参数", v: pool("scoreBrier") }, { name: "经验值（对照）", v: pool("scoreFixedBrier") }];
  return (
    <>
      <div className="stats">
        {stats.map((s) => (
          <div key={s.name} className="card stat">
            <span className="stat-label">{s.name}</span>
            <div className="stat-row"><span className="stat-value num">{s.v === null ? "—" : s.v.toFixed(3)}</span></div>
            <span className="stat-foot">掌握分预测下一道新题，平均 Brier（{n} 次预测）</span>
          </div>
        ))}
      </div>
      {p && (
        <div className="sec-narrow">
          <Card title={`这一周的参数 · ${p.version}`} sub={p.learned
              ? `用 ${p.n} 次“新题第一次提交”学的，${fullTime(p.fittedAt)}；经验值是先验，数据少的类别几乎不动`
              : (p.reason ?? `机会不到 ${p.minOpportunities} 次，沿用经验值`)} bodyClass="">
            {p.learned && p.keys.length > 0 && (
              <table className="table">
                <thead><tr><th scope="col">证据</th><th scope="col" className="num">出现在几次机会里</th><th scope="col" className="num">系数（经验值 1）</th></tr></thead>
                <tbody>
                  {p.keys.map((k) => (
                    <tr key={k.key}><td>{k.name}</td><td className="num">{k.opportunities}</td><td className="num">{k.scale.toFixed(2)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="card-foot">
              掌握分 = (1 + 会) / (2 + 会 + 不会)，每条证据乘这一类的系数、按半衰期衰减。半衰期起点 {p.h0} 天（经验值 {p.h0Prior}），
              每多一次隔天的独立成功乘 {p.g}（经验值 {p.gPrior}）。
              {p.nllPrior !== null && p.nllFit !== null ? ` 拟合时平均对数损失 ${p.nllPrior.toFixed(3)} → ${p.nllFit.toFixed(3)}；好不好看上面之后的预测。` : ""}
            </p>
          </Card>
        </div>
      )}
    </>
  );
}

const SERIES = [
  { name: "v2（现在用的）", color: "var(--series-1)" },
  { name: "v1（原来的简单算法）", color: "var(--series-2)" },
  { name: "图模型（试验中）", color: "var(--series-3)" },
];

export default function Models({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const data = useLoad(() => get<GraphData>(`/api/teacher/classes/${cls.key}/graph`), [cls.key, tick], 0, false, `t:models:${cls.key}`, String(tick));
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取模型的结果" lines={5} /></div>;
  const history = d.predictionHistory ?? [];
  const archived = selectedVersion === d.version ? undefined :
    history.find((x) => x.version === selectedVersion) ?? (d.weekly.length ? undefined : history[0]);
  const selection = archived?.version ?? d.version;
  const rows = archived?.weekly ?? d.weekly;
  const weeks = [...rows].reverse();
  const labels = weeks.map((w) => w.weekStart.slice(5).replace("-", "/"));
  const n = weeks.reduce((s, w) => s + w.n, 0);
  const rootEmpty = "暂未记录到至少两次尚无改正证据的失败";
  const noPredictions = "当前版本近六周暂无可统计的首次提交";
  const selectedScope = archived
    ? `历史版本 ${archived.version || "未记录版本"} · 该版最后有预测的六周`
    : `当前版本 ${d.version} · 近六周`;
  const graphSeries = archived?.legacyGraph ? { ...SERIES[2], name: "图模型（当时的掌握估计）" } : SERIES[2];
  const predictionSeries = [SERIES[0], SERIES[1], graphSeries];
  const maxBrier = Math.max(0.3, ...weeks.flatMap(w => [w.simpleBrier, w.v2Brier, w.graphBrier]));
  const avg = (key: "simpleBrier" | "v2Brier" | "graphBrier") => (n ? weeks.reduce((s, w) => s + w[key] * w.n, 0) / n : null);

  return (
    <div className="stack">
      <div className="trial">
        <FlaskConical size={18} aria-hidden="true" />
        <p>
          学生端和智能体用的是 <b>v2</b>。图模型使用有权重的作答证据和先修关系。
          现在用了 {d.data.students} 名学生的 {d.data.evidence} 条证据、{d.data.predictions} 次累计预测。
          {d.data.updatedAt ? `图模型 ${d.version} 最近一次更新于 ${fullTime(d.data.updatedAt)}。` : `图模型 ${d.version} 正在等待后台更新。`}
          前置关系：{d.prereqStatus}。图模型的参数是经验值（先修技能平均掌握的影响 {d.params.gamma}，碰巧做对 {d.params.guess}，失误 {d.params.slip}），还没有用数据估计。
        </p>
      </div>

      <h2 className="sec-head">谁预测得准<span>学生第一次交一道新题之前，预测他能不能一次做对；Brier 分数越小越准</span></h2>
      <div className="sec-head">
        {history.length > 0 && (
          <select className="field" aria-label="预测对照版本" value={selection} onChange={(e) => setSelectedVersion(e.target.value)}>
            <option value={d.version}>当前版本 · {d.version}</option>
            {history.map((x) => <option key={x.version} value={x.version}>历史版本 · {x.version || "未记录版本"}</option>)}
          </select>
        )}
        <span>{selectedScope}{archived && d.weekly.length === 0 ? " · 当前版本暂无可统计的首次提交" : ""}</span>
        {archived?.legacyGraph && <span>旧版图模型以掌握估计作为首次通过预测。</span>}
      </div>
      <div className="stats">
        {(["v2Brier", "simpleBrier", "graphBrier"] as const).map((key, i) => {
          const v = avg(key);
          return (
            <div key={key} className="card stat">
              <span className="stat-label"><i className="swatch" style={{ background: predictionSeries[i].color }} />{predictionSeries[i].name}</span>
              <div className="stat-row"><span className="stat-value num">{v === null ? "—" : v.toFixed(3)}</span></div>
              <span className="stat-foot">平均 Brier 分数，越小越准（{n} 次预测）</span>
            </div>
          );
        })}
      </div>

      <div className="grid-2">
        <Card title="Brier 分数，按周" sub="学生第一次交一道新题之前，三个模型各自预测他能不能一次做对。越低越好">
          {weeks.length === 0 ? <Empty title={noPredictions} /> : (
            <LineChart label="三个模型每周的 Brier 分数" labels={labels} min={0} max={maxBrier} format={(v) => v.toFixed(2)}
              series={[
                { ...SERIES[0], values: weeks.map((w) => w.v2Brier) },
                { ...SERIES[1], values: weeks.map((w) => w.simpleBrier) },
                { ...graphSeries, values: weeks.map((w) => w.graphBrier) },
              ]} />
          )}
        </Card>
        <Card title="猜对的比例，按周" sub="把预测概率按 0.5 切成“能 / 不能”以后猜对的比例。预测条数少的周只作参考">
          {weeks.length === 0 ? <Empty title={noPredictions} /> : (
            <LineChart label="三个模型每周猜对的比例" labels={labels} min={0} max={100} unit="%"
              series={[
                { ...SERIES[0], values: weeks.map((w) => Math.round(w.v2Accuracy * 100)) },
                { ...SERIES[1], values: weeks.map((w) => Math.round(w.simpleAccuracy * 100)) },
                { ...graphSeries, values: weeks.map((w) => Math.round(w.graphAccuracy * 100)) },
              ]} />
          )}
        </Card>
      </div>

      <Card title="每周的明细" sub={selectedScope} bodyClass="">
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th scope="col">从哪一周</th><th scope="col" className="num">预测条数</th><th scope="col" className="num">v2 Brier</th><th scope="col" className="num">v1 Brier</th><th scope="col" className="num">图模型 Brier</th><th scope="col" className="num">v2 猜对</th><th scope="col" className="num">v1 猜对</th><th scope="col" className="num">图模型猜对</th></tr>
            </thead>
            <tbody>
              {rows.map((w) => (
                <tr key={w.weekStart}>
                  <td>{w.weekStart} 起</td><td className="num">{w.n}</td>
                  <td className="num">{w.v2Brier.toFixed(3)}</td><td className="num">{w.simpleBrier.toFixed(3)}</td><td className="num">{w.graphBrier.toFixed(3)}</td>
                  <td className="num">{pct(w.v2Accuracy)}</td><td className="num">{pct(w.simpleAccuracy)}</td><td className="num">{pct(w.graphAccuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="card-foot">三列使用同一版本、同一批记录。{archived?.legacyGraph
          ? "旧版的图模型列直接使用掌握估计计算误差，供历史查看。"
          : "Brier 分数是预估首次通过概率和实际结果（通过 = 1）之差的平方的平均。"}</p>
      </Card>

      <h2 className="sec-head">掌握分的参数<span>每周学一版，下一周都用它；和经验值比之后的预测</span></h2>
      <MasteryParams d={d} />

      <h2 className="sec-head">多维融合<span>每个维度的权重按数据学，每天夜里更新</span></h2>
      <FusionStats d={d} />
      <div className="sec-narrow">
        <FusionWeights d={d} />
      </div>

      <h2 className="sec-head">图模型（试验中）<span>可复查的先修技能、模型分歧、前置关系统计</span></h2>
      <div className="grid-2">
        <Card title="多人需要复查的先修技能" sub="至少两名学生出现同一条线索，按涉及人数汇总" bodyClass="">
          {d.commonRoots.length === 0 ? <Empty title="暂无多人共有的线索" /> : (
            <table className="table">
              <thead><tr><th scope="col">前置技能</th><th scope="col" className="num">学生人数</th></tr></thead>
              <tbody>{d.commonRoots.map((c) => <tr key={c.code}><td className="cell-title">{c.name} <Code>{c.code}</Code></td><td className="num">{c.students}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
        <Card title="反复失败的技能" sub={`${d.data.repeatedStudents} 名学生有至少两次尚无改正证据的同技能失败。先修技能有直接证据且图模型估计低于 0.4 时，列为复查线索。`} bodyClass="">
          {d.failureRows.length === 0 ? <Empty title={rootEmpty} /> : (
            <div className="table-scroll"><table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col" className="num">失败次数</th><th scope="col">先修复查线索</th></tr></thead>
              <tbody>
                {d.failureRows.map((r) => (
                  <tr key={`${r.userId}:${r.skill.code}`}>
                    <td><Link className="link" to={`/teacher/students/${r.userId}`}>{r.name}</Link></td>
                    <td>{r.skill.name}</td><td className="num">{r.failures}</td>
                    <td>{r.root && r.p !== null
                      ? <>{r.root.name} <Code>{r.p.toFixed(2)}</Code></>
                      : <span className="muted">{r.updated ? "证据不足" : "图模型待更新"}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
          {d.failureTotal > 30 && <p className="card-foot">只列前 30 条，一共 {d.failureTotal} 条。</p>}
        </Card>
      </div>

      <Card title="v2 与图模型的分歧" sub="同一技能有直接证据，当前 v2 估计与最近一次图模型估计相差至少 0.3" bodyClass="">
        {d.gaps.length === 0 ? <Empty title={d.data.updatedAt ? "暂无符合条件的分歧" : "当前版本的图模型还在更新"} /> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col">技能档位（v2）</th><th scope="col" className="num">v2</th><th scope="col" className="num">图模型</th><th scope="col" className="num">v2 证据（会 / 不会）</th></tr></thead>
              <tbody>
                {d.gaps.map((gap, i) => (
                  <tr key={i}><td><Link className="link" to={`/teacher/students/${gap.userId}`}>{gap.name}</Link></td><td>{gap.skill.name} <Code>{gap.skill.code}</Code></td><td>{gap.level}</td><td className="num">{gap.v2.toFixed(2)}</td><td className="num">{gap.graph.toFixed(2)}</td><td className="num">{gap.yes.toFixed(2)} / {gap.no.toFixed(2)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.gapTotal > 30 && <p className="card-foot">只列前 30 条，一共 {d.gapTotal} 条。</p>}
        <p className="card-foot">图模型的估计也受相连的先修和后继技能影响。出现较大分歧时，可点开学生查看直接证据。</p>
      </Card>

      <Card title="前置关系" sub="描述性统计，不是边的强度估计：两头都有数据的学生里，前置会和不会时，后续技能“会”的比例。前置会不会没有差别的边标出来，供修正知识点图参考" bodyClass="">
        <div className="table-scroll">
          <table className="table">
            <thead><tr><th scope="col">前置技能</th><th scope="col">后续技能</th><th scope="col" className="num">学生数</th><th scope="col" className="num">前置会时</th><th scope="col" className="num">前置不会时</th><th scope="col"><span className="sr-only">提示</span></th></tr></thead>
            <tbody>
              {d.edges.map((e) => (
                <tr key={e.parent + e.child}>
                  <td>{e.parentName} <Code>{e.parent}</Code></td><td>{e.childName} <Code>{e.child}</Code></td><td className="num">{e.n}</td>
                  <td className="num">{pct(e.withParent)}{e.nWith ? `（${e.nWith} 人）` : ""}</td>
                  <td className="num">{pct(e.withoutParent)}{e.nWithout ? `（${e.nWithout} 人）` : ""}</td>
                  <td>{e.doubtful && <span className="pill pill-warn">可能不成立</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
