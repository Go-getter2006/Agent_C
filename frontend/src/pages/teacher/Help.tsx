// 研究 · 求助效果（app/stuckstats.py、app/helpeffect.py，定义见 docs/研究数据与指标.md）：分级帮助有没有用。
// 先看用了多少，再比反复没过时来问的和没来问的，然后是每次帮助之后、按知识点。只给比例（几比几）；
// 谁卡在哪个知识点（上课用）在知识图谱的知识点详情里。
import { CircleCheck } from "lucide-react";
import { get } from "../../api";
import { Card, Empty, Facts, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { StaffLink, useClasses, type ClassInfo } from "./common";

type R = { n: number; d: number } | null;

interface KpRow {
  code: string; name: string; students: number; episodes: number; repeat: number; stubborn: number;
  recall: R; passed: R; transfer: R; silent: number; silentPassed: R;
}

interface HelpNextRow {
  level: string; name: string; n: number; resubmitted: R; nextPass: R; noSubmission: number; noSubmissionStudents: number; unjudged: number; nextStatus: Record<"passed" | "present" | "absent" | "unknown", R>; passed: R;
  medianMinutes: number | null; medianTries: number | null;
}

interface Group { cases: number; students: number; passed: R; medianTries: number | null }
interface Corr { n: number; r: number | null; low: number | null; high: number | null; p: number | null }

interface HelpData {
  rows: KpRow[]; silentFails: number; version: string;
  usage: { students: number; used: number; askedHomework: number; stuck: number; silent: number };
  askVsSilent: { version: string; threshold: number; asked: Group; silent: Group };
  helpNext: { version: string; rows: HelpNextRow[] };
  totals: { students: number; stuck: number; episodes: number; recall: R; passed: R; transfer: R; silent: number };
  dialogue: { version: string; students: number; constructive: Corr; answerSeeking: Corr };
}

const frac = (r: R) => (r ? `${r.n}/${r.d}` : "—");
const pct = (r: R) => (r && r.d ? `${Math.round((100 * r.n) / r.d)}%` : "");
/** 相关系数：r、95% 区间、p；区间跨过 0 就是看不出有没有关系。 */
function CorrText({ c }: { c: Corr }) {
  if (c.r === null || c.low === null || c.high === null) return <>算不了（{c.n} 人）</>;
  const unclear = c.low <= 0 && c.high >= 0;
  return (
    <>
      <b>{c.r.toFixed(2)}</b>
      <span className="note">（95% 区间 {c.low.toFixed(2)} 到 {c.high.toFixed(2)}，p = {c.p === null ? "—" : c.p < 0.001 ? "< 0.001" : c.p.toFixed(3)}）</span>
      {unclear && <span className="note"> · 区间跨过 0，看不出有没有关系</span>}
    </>
  );
}

function Ratio({ r }: { r: R }) {
  return <>{frac(r)}{r && r.d > 0 && <span className="note"> {pct(r)}</span>}</>;
}

export default function Help({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const data = useLoad(() => get<HelpData>(`/api/teacher/classes/${cls.key}/help`), [cls.key, tick], 0, false, `t:help:${cls.key}`, String(tick));
  const d = data.data;
  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在汇总" lines={5} /></div>;
  const u = d.usage;
  const t = d.totals;
  const av = d.askVsSilent;
  const stuckRows = d.rows.filter((r) => r.episodes > 0);
  const silentRows = d.rows.filter((r) => r.silent > 0).sort((a, b) => b.silent - a.silent);
  const of = (n: number) => `${n}/${u.students} 人`;

  return (
    <div className="stack">
      <Facts label="用了多少" items={[
        { label: "用过智能体", value: of(u.used), note: "在任何栏目发过消息" },
        { label: "在作业题上求助", value: of(u.askedHomework), note: "通过后的回顾不算" },
        { label: "求助时卡住过", value: `${u.stuck} 人 · ${t.episodes} 次`, note: "智能体记下的卡点" },
        { label: "反复没过、没来问", value: of(u.silent), note: `一道题没过的提交 ≥ ${d.silentFails} 次` },
      ]} />

      <Card title="反复没过的题：来问的和没来问的" sub={`一道题没通过的提交 ≥ ${av.threshold} 次的（学生, 题）；分子 / 分母`} bodyClass="">
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th scope="col"></th><th scope="col" className="num">题次</th><th scope="col" className="num">学生</th>
                <th scope="col" className="num">后来通过</th><th scope="col" className="num">通过前一共交了几次</th>
              </tr>
            </thead>
            <tbody>
              {([["来问过", av.asked], ["没来问", av.silent]] as const).map(([name, g]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{g.cases || "—"}</td>
                  <td className="num">{g.students || "—"}</td>
                  <td className="num"><Ratio r={g.passed} /></td>
                  <td className="num">{g.medianTries === null ? "—" : `${g.medianTries} 次`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="card-foot">
          来问过：就这道题在作业引导里开过求助回合。次数是中位数。描述性的，不是帮助的效果：来问的往往是更难的题、更想做出来的学生。指标版本 {av.version}。
        </p>
      </Card>

      <Card title="每次帮助之后" sub="作业引导里每次帮助以后，这道题的下一次提交；分子 / 分母" bodyClass="">
        {d.helpNext.rows.length === 0 ? (
          <Empty icon={CircleCheck} title="还没有帮助记录" />
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">帮助</th><th scope="col" className="num">次数</th><th scope="col" className="num">再交了</th>
                  <th scope="col" className="num">下一次就通过</th>{["再次识别到", "未再识别到", "尚未再交", "待评测"].map(x => <th scope="col" className="num" key={x}>{x}</th>)}
                  <th scope="col" className="num">之后通过</th><th scope="col" className="num">隔多久再交</th>
                  <th scope="col" className="num">到通过交了几次</th>
                </tr>
              </thead>
              <tbody>
                {d.helpNext.rows.map((r) => (
                  <tr key={r.level} className={r.level === "all" ? "row-total" : undefined}>
                    <td>{r.name}</td>
                    <td className="num">{r.n}</td>
                    <td className="num"><Ratio r={r.resubmitted} /></td>
                    <td className="num"><Ratio r={r.nextPass} /></td>
                    {(["present", "absent"] as const).map(k => <td className="num" key={k}><Ratio r={r.nextStatus[k]} /></td>)}
                    <td className="num">{r.noSubmission} 次 · {r.noSubmissionStudents} 人</td><td className="num">{r.unjudged}</td>
                    <td className="num"><Ratio r={r.passed} /></td>
                    <td className="num">{r.medianMinutes === null ? "—" : `${r.medianMinutes} 分钟`}</td>
                    <td className="num">{r.medianTries === null ? "—" : `${r.medianTries} 次`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.helpNext.rows.filter(r => r.level === "all" && (r.nextStatus.unknown?.n ?? 0) > 0).map(r => {
          const comparable = (r.nextStatus.present?.n ?? 0) + (r.nextStatus.absent?.n ?? 0);
          return <p key={r.level} className="card-foot">{comparable + (r.nextStatus.unknown?.n ?? 0)} 次仍未通过，其中 {comparable} 次有足够标注可比较。</p>;
        })}
        <p className="card-foot">
          两次提交之间给了几次帮助的，算最后那一次。四种结果的分母均为已有评测结果的下一次提交。只有具体且可信的错误标注才参与比较。
          时间和次数是中位数。帮助级别是按学生当时的情况选的，级别之间不能直接比效果。指标版本 {d.helpNext.version}。
        </p>
      </Card>

      <div className="grid-2">
        <Card title="求助时卡住的知识点" sub="分子 / 分母；谁卡在这里，在知识图谱里点这个知识点看" bodyClass="">
          {stuckRows.length === 0 ? <Empty icon={CircleCheck} title="还没有人在求助时卡住" /> : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">知识点</th><th scope="col" className="num">卡住</th><th scope="col" className="num">反复</th>
                    <th scope="col" className="num">第二次自己想起来</th><th scope="col" className="num">卡后通过</th>
                    <th scope="col" className="num">之后新题一次通过</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="row-total">
                    <td>全部</td><td className="num">{t.stuck} 人 · {t.episodes} 次</td><td className="num"></td>
                    <td className="num"><Ratio r={t.recall} /></td><td className="num"><Ratio r={t.passed} /></td>
                    <td className="num"><Ratio r={t.transfer} /></td>
                  </tr>
                  {stuckRows.map((r) => (
                    <tr key={r.code}>
                      <td><StaffLink className="link" to={`/map?kp=${r.code}`}>{r.name}</StaffLink></td>
                      <td className="num">{r.students} 人 · {r.episodes} 次</td>
                      <td className="num">{r.repeat || "—"}{r.stubborn ? <span className="note"> 顽固 {r.stubborn}</span> : null}</td>
                      <td className="num"><Ratio r={r.recall} /></td>
                      <td className="num"><Ratio r={r.passed} /></td>
                      <td className="num"><Ratio r={r.transfer} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="card-foot">
            反复：卡住两个回合以上的人；顽固：第三次卡在同一技能的人。第二次自己想起来：智能体先请他回忆时自己说出来。
            卡后通过：卡住时那道题后来通过。之后新题一次通过：第一次卡在这里以后才开始做的同知识点题，第一次提交就通过。指标版本 {d.version}。
          </p>
        </Card>

        <Card title="反复没过、没来问的知识点" sub="主知识点是它的题上；分子 / 分母" bodyClass="">
          {silentRows.length === 0 ? <Empty icon={CircleCheck} title="没有反复没过又没来问的" /> : (
            <div className="table-scroll">
              <table className="table">
                <thead><tr><th scope="col">知识点</th><th scope="col" className="num">没来问</th><th scope="col" className="num">后来通过</th></tr></thead>
                <tbody>
                  {silentRows.map((r) => (
                    <tr key={r.code}>
                      <td><StaffLink className="link" to={`/map?kp=${r.code}`}>{r.name}</StaffLink></td>
                      <td className="num">{r.silent} 人</td>
                      <td className="num"><Ratio r={r.silentPassed} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="card-foot">没来问：一道题没过的提交不少于 {d.silentFails} 次，没就这道题求助（在这个知识点上求助时卡住过的不算在这里）。后来通过按（学生, 题）数。</p>
        </Card>
      </div>

      <Card title="对话行为和做题表现" sub={`对话至少 5 条、通过至少 3 道题的学生 ${d.dialogue.students} 人；和“平均几次改对”的相关系数（负数表示次数少）`}>
        <p className="num">建构性提问占比：<CorrText c={d.dialogue.constructive} /></p>
        <p className="num">索要答案占比：<CorrText c={d.dialogue.answerSeeking} /></p>
        <p className="note">人数不设门槛；人少时区间很宽。区间用 Fisher z 变换算，少于 4 人算不了。描述性的，不是因果。StudyChat（McNichols 等 2026）发现概念性提问和成绩正相关、索要答案负相关。指标版本 {d.dialogue.version}。</p>
      </Card>
    </div>
  );
}
