// 模型对比：掌握程度算法 v1、v2、图模型（试验中）和多维融合在同一批预测上谁更准；智能体评估的消融和一致率；
// 融合学到的维度权重；对话行为和表现的关系；根因推断、差别大的地方、先修边的描述统计。
import { Link } from "react-router-dom";
import { FlaskConical } from "lucide-react";
import { get } from "../../api";
import { LineChart } from "../../components/charts";
import { Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime } from "../../lib/time";
import { Code, pct, type ClassInfo, type SkillRef } from "./common";

interface GraphData {
  version: string; prereqStatus: string;
  data: { students: number; evidence: number; posteriors: number; predictions: number; updatedAt: string | null };
  params: Record<string, number>;
  gaps: { userId: string; name: string; skill: SkillRef; simple: number; graph: number; level: string }[];
  rootCauses: { userId: string; name: string; skill: SkillRef; root: SkillRef; p: number }[];
  commonRoots: (SkillRef & { students: number })[];
  edges: { parent: string; child: string; parentName: string; childName: string; n: number; withParent: number | null; withoutParent: number | null; nWith: number; nWithout: number; doubtful: boolean }[];
  weekly: {
    weekStart: string; n: number; simpleAccuracy: number; v2Accuracy: number; graphAccuracy: number; simpleBrier: number; v2Brier: number; graphBrier: number;
    nFused: number; fusedBrier: number | null; v2BaseBrier: number | null; v2SameBrier: number | null;
  }[];
  fusion: { learned: boolean; n: number; minSamples: number; updatedAt: string | null; weights: { dim: string; name: string; w: number; default: number }[] };
  agreement: { n: number; exact: number | null; kappa: number | null; within1: number | null };
  dialogue: { students: number; constructive: number | null; answerSeeking: number | null };
}

/** 几周合起来的 Brier：按每周的预测条数加权。 */
function pooled(weeks: GraphData["weekly"], key: "fusedBrier" | "v2BaseBrier" | "v2SameBrier"): number | null {
  const rows = weeks.filter((w) => w.nFused > 0 && w[key] !== null);
  const n = rows.reduce((s, w) => s + w.nFused, 0);
  return n ? rows.reduce((s, w) => s + (w[key] as number) * w.nFused, 0) / n : null;
}

function Fusion({ d }: { d: GraphData }) {
  const n = d.weekly.reduce((s, w) => s + w.nFused, 0);
  const stats = [
    { name: "多维融合", v: pooled(d.weekly, "fusedBrier") },
    { name: "v2（同一批预测）", v: pooled(d.weekly, "v2SameBrier") },
    { name: "v2 去掉智能体评估（消融）", v: pooled(d.weekly, "v2BaseBrier") },
  ];
  const maxW = Math.max(1, ...d.fusion.weights.map((x) => Math.abs(x.w)));
  const corr = (x: number | null) => (x === null ? "样本不够" : x.toFixed(2));
  return (
    <>
      <div className="stats">
        {stats.map((s) => (
          <div key={s.name} className="card stat">
            <span className="stat-label">{s.name}</span>
            <div className="stat-row"><span className="stat-value num">{s.v === null ? "—" : s.v.toFixed(3)}</span></div>
            <span className="stat-foot">平均 Brier 分数，越小越准（{n} 次预测）</span>
          </div>
        ))}
      </div>
      <div className="grid-2">
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
        <div className="stack">
          <Card title="智能体评估和规则的一致率" sub="同一个回合，大模型读学生原话评的“理解”和只看事实的规则给的等级">
            {d.agreement.n === 0 ? <p className="note">还没有两者都给了等级的回合。</p> : (
              <p className="num">
                {d.agreement.n} 个回合 · 完全一致 {pct(d.agreement.exact)} · 相差不超过一档 {pct(d.agreement.within1)} · Cohen's κ {d.agreement.kappa === null ? "—" : d.agreement.kappa.toFixed(2)}
              </p>
            )}
            <p className="note">规则看不出“能迁移”，也看不出说法本身错没错；一致率太低时说明要检查评估的提示词。</p>
          </Card>
          <Card title="对话行为和做题表现" sub={`对话至少 5 条、做过至少 3 道题的 ${d.dialogue.students} 名学生；和一次通过率的相关系数（描述性）`}>
            <p className="num">建构性提问占比：{corr(d.dialogue.constructive)} · 索要答案占比：{corr(d.dialogue.answerSeeking)}</p>
            <p className="note">StudyChat（McNichols 等 2026）发现概念性提问和成绩正相关、索要答案负相关；这里用本班的数据看是不是一样。</p>
          </Card>
        </div>
      </div>
    </>
  );
}

const SERIES = [
  { name: "v2（现在用的）", color: "var(--series-1)" },
  { name: "v1（原来的简单算法）", color: "var(--series-2)" },
  { name: "图模型（试验中）", color: "var(--series-3)" },
];

export default function Models({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<GraphData>(`/api/teacher/classes/${cls.key}/graph`), [cls.key]);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取模型的结果" lines={5} /></div>;
  const weeks = [...d.weekly].reverse();
  const labels = weeks.map((w) => w.weekStart.slice(5).replace("-", "/"));
  const n = weeks.reduce((s, w) => s + w.n, 0);
  const avg = (key: "simpleBrier" | "v2Brier" | "graphBrier") => (n ? weeks.reduce((s, w) => s + w[key] * w.n, 0) / n : null);

  return (
    <div className="stack">
      <div className="trial">
        <FlaskConical size={18} aria-hidden="true" />
        <p>
          学生端和智能体用的是 <b>v2</b>。v1 和图模型用同一批证据同步运行，结果只在教师端显示。
          现在用了 {d.data.students} 名学生的 {d.data.evidence} 条证据、{d.data.predictions} 次预测；
          {d.data.updatedAt ? `图模型上次全图更新在${fullTime(d.data.updatedAt)}。` : "图模型还没有做过全图更新。"}
          先修边：{d.prereqStatus}。图模型的参数是经验值（先修影响 {d.params.gamma}，碰巧做对 {d.params.guess}，失误 {d.params.slip}），还没有用数据估计。
        </p>
      </div>

      <div className="stats">
        {(["v2Brier", "simpleBrier", "graphBrier"] as const).map((key, i) => {
          const v = avg(key);
          return (
            <div key={key} className="card stat">
              <span className="stat-label"><i className="swatch" style={{ background: SERIES[i].color }} />{SERIES[i].name}</span>
              <div className="stat-row"><span className="stat-value num">{v === null ? "—" : v.toFixed(3)}</span></div>
              <span className="stat-foot">平均 Brier 分数，越小越准（{n} 次预测）</span>
            </div>
          );
        })}
      </div>

      <Fusion d={d} />

      <div className="grid-2">
        <Card title="Brier 分数，按周" sub="学生第一次交一道新题之前，三个模型各自预测他能不能一次做对。越低越好">
          {weeks.length === 0 ? <Empty title="还没有可以统计的预测" /> : (
            <LineChart label="三个模型每周的 Brier 分数" labels={labels} min={0} max={0.3} format={(v) => v.toFixed(2)}
              series={[
                { ...SERIES[0], values: weeks.map((w) => w.v2Brier) },
                { ...SERIES[1], values: weeks.map((w) => w.simpleBrier) },
                { ...SERIES[2], values: weeks.map((w) => w.graphBrier) },
              ]} />
          )}
        </Card>
        <Card title="猜对的比例，按周" sub="把预测概率按 0.5 切成“能 / 不能”以后猜对的比例。预测条数少的周只作参考">
          {weeks.length === 0 ? <Empty title="还没有可以统计的预测" /> : (
            <LineChart label="三个模型每周猜对的比例" labels={labels} min={0} max={100} unit="%"
              series={[
                { ...SERIES[0], values: weeks.map((w) => Math.round(w.v2Accuracy * 100)) },
                { ...SERIES[1], values: weeks.map((w) => Math.round(w.simpleAccuracy * 100)) },
                { ...SERIES[2], values: weeks.map((w) => Math.round(w.graphAccuracy * 100)) },
              ]} />
          )}
        </Card>
      </div>

      <Card title="每周的明细" bodyClass="">
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th scope="col">从哪一周</th><th scope="col" className="num">预测条数</th><th scope="col" className="num">v2 Brier</th><th scope="col" className="num">v1 Brier</th><th scope="col" className="num">图模型 Brier</th><th scope="col" className="num">v2 猜对</th><th scope="col" className="num">v1 猜对</th><th scope="col" className="num">图模型猜对</th></tr>
            </thead>
            <tbody>
              {d.weekly.map((w) => (
                <tr key={w.weekStart}>
                  <td>{w.weekStart} 起</td><td className="num">{w.n}</td>
                  <td className="num">{w.v2Brier.toFixed(3)}</td><td className="num">{w.simpleBrier.toFixed(3)}</td><td className="num">{w.graphBrier.toFixed(3)}</td>
                  <td className="num">{pct(w.v2Accuracy)}</td><td className="num">{pct(w.simpleAccuracy)}</td><td className="num">{pct(w.graphAccuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="card-foot">Brier 分数是预测概率和实际结果（做对 = 1，做错 = 0）之差的平方的平均。全猜 0.5 时是 0.25。</p>
      </Card>

      <div className="grid-2">
        <Card title="全班共同的根因" sub="学生反复失败时，图模型认为真正没掌握的先修技能" bodyClass="">
          {d.commonRoots.length === 0 ? <Empty title="目前没有推断出共同的根因" /> : (
            <table className="table">
              <thead><tr><th scope="col">先修技能</th><th scope="col" className="num">学生人数</th></tr></thead>
              <tbody>{d.commonRoots.map((c) => <tr key={c.code}><td className="cell-title">{c.name} <Code>{c.code}</Code></td><td className="num">{c.students}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
        <Card title="逐个学生的根因推断" bodyClass="">
          {d.rootCauses.length === 0 ? <Empty title="目前没有" /> : (
            <table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col">反复失败的技能</th><th scope="col">可能的根因</th></tr></thead>
              <tbody>
                {d.rootCauses.map((r, i) => (
                  <tr key={i}><td><Link className="link" to={`/teacher/students/${r.userId}`}>{r.name}</Link></td><td>{r.skill.name}</td><td>{r.root.name} <Code>{r.p.toFixed(2)}</Code></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Card title="v1 和图模型差别大的地方" sub="同一个学生同一个技能，两个模型的估计相差 0.3 以上" bodyClass="">
        {d.gaps.length === 0 ? <Empty title="目前没有差别大的" /> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col">学生端档位</th><th scope="col" className="num">v1</th><th scope="col" className="num">图模型</th></tr></thead>
              <tbody>
                {d.gaps.slice(0, 30).map((gap, i) => (
                  <tr key={i}><td><Link className="link" to={`/teacher/students/${gap.userId}`}>{gap.name}</Link></td><td>{gap.skill.name} <Code>{gap.skill.code}</Code></td><td>{gap.level}</td><td className="num">{gap.simple.toFixed(2)}</td><td className="num">{gap.graph.toFixed(2)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.gaps.length > 30 && <p className="card-foot">只列前 30 条，一共 {d.gaps.length} 条。</p>}
      </Card>

      <Card title="先修边" sub="描述性统计，不是边的强度估计：两头都有数据的学生里，先修会和不会时，后继技能“会”的比例。先修会不会没有差别的边标出来，供修正知识点图参考" bodyClass="">
        <div className="table-scroll">
          <table className="table">
            <thead><tr><th scope="col">先修技能</th><th scope="col">后继技能</th><th scope="col" className="num">学生数</th><th scope="col" className="num">先修会时</th><th scope="col" className="num">先修不会时</th><th scope="col"><span className="sr-only">提示</span></th></tr></thead>
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
