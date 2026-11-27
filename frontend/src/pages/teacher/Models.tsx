// 研究 · 模型检验：掌握程度算法 v1、v2、先修关系模型的诊断线索和多维融合在同一批预测上谁更准；v2 去掉智能体评估的消融；
// 融合权重、可复查的先修技能、两种估计的分歧、前置关系的描述统计。
// 智能体评估的一致率在“理解评估”，对话行为和表现在“求助效果”。
import ExamReplay from "./ExamReplay";
import ModelComparison, { type ComparisonReport } from "./ModelComparison";
import ModelGuide from "./ModelGuide";
import ModelNavigation, { type ModelSection } from "./ModelNavigation";
import { useState } from "react";
import { FlaskConical } from "lucide-react";
import { get } from "../../api";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime } from "../../lib/time";
import { StaffLink, Code, pct, useClasses, type ClassInfo, type SkillRef } from "./common";

export interface GraphData {
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
    weekStart: string; n: number; simpleAccuracy: number | null; v2Accuracy: number | null; graphAccuracy: number | null; simpleBrier: number | null; v2Brier: number | null; graphBrier: number | null;
    nFused: number; fusedBrier: number | null; v2BaseBrier: number | null; v2SameBrier: number | null;
    nScore?: number; scoreBrier?: number | null; scoreFixedBrier?: number | null;
  }[];
  replayCoverage?: { legacy: number; noHistoricalLabel: number; noArchivedWeights: number };
  predictionHistory?: { version: string; weekly: GraphData["weekly"]; legacyGraph: boolean }[];
  fusionWeekly?: GraphData["weekly"];
  fusion: { learned: boolean; n: number; minSamples: number; updatedAt: string | null; weights: { dim: string; name: string; w: number; default: number }[] };
  masteryParams?: {
    version: string; learned: boolean; n: number; fittedAt: string; minOpportunities: number; reason: string | null;
    h0: number; g: number; h0Prior: number; gPrior: number; nllPrior: number | null; nllFit: number | null;
    keys: { key: string; name: string; opportunities: number; scale: number }[];
    history: { version: string; learned: boolean; n: number }[];
  } | null;
  scoringRules: { model: string; mastered: number; basic: number; copyFactor: number; secondaryTested: number;
    secondaryIncidental: number; resolvedFailure: number; repeatedFailure: number };
  scoreBreakdown: { userId: string; name: string; score: number | null; quality: number | null;
    coveredProblems: number; totalProblems: number; coveredTopics: number; totalTopics: number; scoredTopics: number }[];
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
    { name: "证据加权模型（同一批预测）", v: pooled(weeks, "v2SameBrier") },
    { name: "证据加权模型去掉理解评估", v: pooled(weeks, "v2BaseBrier") },
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

export default function Models({ cls }: { cls: ClassInfo }) {
  const { classes, tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const data = useLoad(() => get<GraphData>(`/api/teacher/classes/${cls.key}/graph`), [cls.key, tick], 0, false, `t:models:${cls.key}`, String(tick));
  const [comparison, setComparison] = useState<ComparisonReport | null>(null);
  const [examTimes, setExamTimes] = useState<Record<string, string>>({});
  const [section, setSection] = useState<ModelSection>("models-overview");
  const examClasses = cls.members ? classes.filter(c => cls.members?.includes(c.key)) : [cls];
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取模型的结果" lines={5} /></div>;
  const rootEmpty = "暂未记录到至少两次尚无改正证据的失败";
  const filtered = {
    ...d,
    weekly: (comparison?.periods ?? []).filter(period => period.n > 0).map(period => ({ ...period, weekStart: period.date })),
    fusionWeekly: (comparison?.periods ?? []).filter(period => period.n > 0).map(period => ({ ...period, weekStart: period.date })),
  };

  return (
    <div className="stack">
      <div className="trial">
        <FlaskConical size={18} aria-hidden="true" />
        <p>
          学生掌握分按自身证据计算；先修关系模型在开发者端做研究对照。
          现在用了 {d.data.students} 名学生的 {d.data.evidence} 条证据、{d.data.predictions} 次累计预测。
          {d.data.updatedAt ? `图模型 ${d.version} 最近一次更新于 ${fullTime(d.data.updatedAt)}。` : `图模型 ${d.version} 正在等待后台更新。`}
          前置关系：{d.prereqStatus}。先修关系模型仍使用经验参数，计算方法见下方说明。
        </p>
      </div>

      <ModelNavigation value={section} onChange={setSection} />

      <section className="stack model-panel" role="tabpanel" id="panel-models-overview"
        aria-labelledby="tab-models-overview" hidden={section !== "models-overview"}>
        <Card title="模型分别负责什么" sub="当前计分、研究对照与试验模型集中列在这里。" bodyClass="">
          <div className="table-scroll"><table className="table model-registry">
            <thead><tr><th scope="col">模型</th><th scope="col">使用状态</th><th scope="col">依据</th><th scope="col">主要用途</th></tr></thead>
            <tbody>
              <tr><td className="cell-title">证据加权模型（v2）</td><td><span className="pill">{d.scoringRules.model === "v2" ? "当前计分" : "研究对照"}</span></td><td>做题、改错、帮助记录、理解评估和时间衰减</td><td>掌握分按自身证据计算；研究时另检验带先修起点的预测概率</td></tr>
              <tr><td className="cell-title">简单累计模型（v1）</td><td><span className="pill">{d.scoringRules.model === "v1" ? "当前计分" : "研究对照"}</span></td><td>固定事件权重与固定遗忘系数</td><td>与证据加权模型比较的历史基线</td></tr>
              <tr><td className="cell-title">先修关系模型</td><td><span className="pill">研究对照</span></td><td>作答证据、技能之间的先修关系</td><td>研究预测效果，查找需要复核的先修技能</td></tr>
              <tr><td className="cell-title">多维融合模型</td><td><span className="pill">研究对照</span></td><td>做题、理解、独立、改错、保持、自知和前置知识</td><td>检验组合不同证据能否改善后续表现的预测</td></tr>
            </tbody>
          </table></div>
        </Card>
        <ModelGuide d={d} />
      </section>

      {/* 保持组件挂载，切换栏目不重复请求，也保留日期、版本和考试时间。 */}
      <section className="stack model-panel" role="tabpanel" id="panel-models-validation"
        aria-labelledby="tab-models-validation" hidden={section !== "models-validation"}>
      <ModelComparison key={cls.key} classKey={cls.key} tick={tick} currentVersion={d.version} onReport={setComparison} />
      </section>

      <section className="stack model-panel" role="tabpanel" id="panel-models-parameters"
        aria-labelledby="tab-models-parameters" hidden={section !== "models-parameters"}>
      <p className="model-scope">{comparison ? `预测检验范围：${comparison.from} 至 ${comparison.to}。` : "预测检验范围正在读取。"} 日期与版本在“预测检验”中选择；下方参数为当前生效版本。</p>

      <h2 className="sec-head">掌握分的参数<span>每周学一版，下一周都用它；和经验值比之后的预测</span></h2>
      <MasteryParams d={filtered} />

      <h2 className="sec-head">多维融合<span>每个维度的权重按数据学，每天夜里更新</span></h2>
      {d.replayCoverage && <p className="note">夜间预测按历史记录回算。{d.replayCoverage.legacy} 条旧记录缺少版本追踪，{d.replayCoverage.noHistoricalLabel} 条缺当时的题目标注，{d.replayCoverage.noArchivedWeights} 条缺当时的融合权重；后两项可重叠。</p>}
      <ExamReplay key={cls.key} classKey={cls.key} classes={examClasses} times={examTimes}
        onTimeChange={(classKey, time) => setExamTimes(current => ({ ...current, [classKey]: time }))} />
      <FusionStats d={filtered} />
      <div className="sec-narrow">
        <FusionWeights d={d} />
      </div>
      </section>

      <section className="stack model-panel" role="tabpanel" id="panel-models-prerequisites"
        aria-labelledby="tab-models-prerequisites" hidden={section !== "models-prerequisites"}>

      <h2 className="sec-head">先修关系模型的诊断线索<span>可复查的先修技能、模型分歧、前置关系统计</span></h2>
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
                    <td><StaffLink className="link" to={`/students/${r.userId}`}>{r.name}</StaffLink></td>
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

      <Card title="证据加权与先修关系模型的分歧" sub="同一技能有直接证据，当前证据加权模型估计与最近一次图模型估计相差至少 0.3" bodyClass="">
        {d.gaps.length === 0 ? <Empty title={d.data.updatedAt ? "暂无符合条件的分歧" : "当前版本的图模型还在更新"} /> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col">技能档位</th><th scope="col" className="num">证据加权概率</th><th scope="col" className="num">先修关系概率</th><th scope="col" className="num">正面 / 负面证据</th></tr></thead>
              <tbody>
                {d.gaps.map((gap, i) => (
                  <tr key={i}><td><StaffLink className="link" to={`/students/${gap.userId}`}>{gap.name}</StaffLink></td><td>{gap.skill.name} <Code>{gap.skill.code}</Code></td><td>{gap.level}</td><td className="num">{gap.v2.toFixed(2)}</td><td className="num">{gap.graph.toFixed(2)}</td><td className="num">{gap.yes.toFixed(2)} / {gap.no.toFixed(2)}</td></tr>
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
      </section>
    </div>
  );
}
