// 研究 · 求助效果（app/stuckstats.py，定义见 docs/研究数据与指标.md）：分级帮助有没有用。
// 只给比例（几比几）；谁卡在哪个知识点（上课用）在知识图谱的知识点详情里。
import { Link } from "react-router-dom";
import { CircleCheck } from "lucide-react";
import { get } from "../../api";
import { Card, Empty, Facts, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import type { ClassInfo } from "./common";

type R = { n: number; d: number } | null;

interface KpRow {
  code: string; name: string; students: number; episodes: number; repeat: number; stubborn: number;
  recall: R; passed: R; transfer: R; silent: number;
}

interface HelpData {
  rows: KpRow[]; silentFails: number; version: string;
  totals: { students: number; stuck: number; episodes: number; recall: R; passed: R; transfer: R; silent: number };
  dialogue: { students: number; constructive: number | null; answerSeeking: number | null };
}

const frac = (r: R) => (r ? `${r.n}/${r.d}` : "—");
const corr = (x: number | null) => (x === null ? "样本不够" : x.toFixed(2));

export default function Help({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<HelpData>(`/api/teacher/classes/${cls.key}/help`), [cls.key], 120000, false, `t:help:${cls.key}`);
  const d = data.data;
  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在汇总" lines={5} /></div>;
  const t = d.totals;
  return (
    <div className="stack">
      <Facts label="全班" items={[
        { label: "卡住过", value: `${t.stuck} 人 · ${t.episodes} 次`, note: `全班 ${t.students} 人` },
        { label: "第二次自己想起来", value: frac(t.recall), note: "同一技能第二次卡住时" },
        { label: "卡后通过", value: frac(t.passed), note: "卡住时那道题后来通过" },
        { label: "之后新题一次通过", value: frac(t.transfer), note: "同知识点、之后才开始做的题" },
        { label: "没来问", value: `${t.silent} 人`, note: `一道题没过的提交 ≥ ${d.silentFails} 次` },
      ]} />

      <Card title="按知识点" sub="分子 / 分母；谁卡在这里，在知识图谱里点这个知识点看" bodyClass="">
        {d.rows.length === 0 ? (
          <Empty icon={CircleCheck} title="还没有卡点">对话里卡住、或一道题反复没过又没来问的，会按知识点列在这里。</Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">知识点</th><th scope="col" className="num">卡住</th><th scope="col" className="num">反复</th>
                  <th scope="col" className="num">顽固</th><th scope="col" className="num">第二次自己想起来</th>
                  <th scope="col" className="num">卡后通过</th><th scope="col" className="num">之后新题一次通过</th><th scope="col" className="num">没来问</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.code}>
                    <td><Link className="link" to={`/teacher/map?kp=${r.code}`}>{r.name}</Link></td>
                    <td className="num">{r.students ? `${r.students} 人 · ${r.episodes} 次` : "—"}</td>
                    <td className="num">{r.repeat || "—"}</td>
                    <td className="num">{r.stubborn || "—"}</td>
                    <td className="num">{frac(r.recall)}</td>
                    <td className="num">{frac(r.passed)}</td>
                    <td className="num">{frac(r.transfer)}</td>
                    <td className="num">{r.silent ? `${r.silent} 人` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="card-foot">
          反复：卡住两个回合以上的人。顽固：第三次卡在同一技能的人。第二次自己想起来：智能体先请他回忆时自己说出来。
          卡后通过：卡住时那道题后来在 XDOJ 上通过。之后新题一次通过：第一次卡在这里以后才开始做的同知识点题，第一次提交就通过。
          没来问：一道题没过的提交不少于 {d.silentFails} 次，没就这道题求助。指标版本 {d.version}。
        </p>
      </Card>

      <Card title="对话行为和做题表现" sub={`对话至少 5 条、通过至少 3 道题的 ${d.dialogue.students} 名学生；和“平均几次改对”的相关系数（负数表示次数少）`}>
        <p className="num">建构性提问占比：{corr(d.dialogue.constructive)} · 索要答案占比：{corr(d.dialogue.answerSeeking)}</p>
        <p className="note">描述性的，不是因果。StudyChat（McNichols 等 2026）发现概念性提问和成绩正相关、索要答案负相关。</p>
      </Card>
    </div>
  );
}
