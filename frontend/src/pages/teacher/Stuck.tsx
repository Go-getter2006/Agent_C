// 卡点：反复卡住的学生、卡住人数最多的技能。
import { Link } from "react-router-dom";
import { CircleCheck } from "lucide-react";
import { get } from "../../api";
import { BarList } from "../../components/charts";
import { Avatar, Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime } from "../../lib/time";
import { Code, RESOLUTION, type ClassInfo, type SkillRef } from "./common";

interface StuckData {
  rows: { userId: string; name: string; skill: SkillRef; times: number; stubborn: boolean; lastTime: string; lastProblem: string; resolution: string; passedAfter: boolean | null; errorName: string; error: string }[];
  onceOnly: number;
  common: (SkillRef & { students: number })[];
}

export default function Stuck({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<StuckData>(`/api/teacher/classes/${cls.key}/stuck`), [cls.key], 60000);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取卡点名单" lines={5} /></div>;
  return (
    <div className="grid-main">
      <Card title="反复卡住的学生" sub="同一个技能卡住两次及以上；第三次起标为顽固卡点，建议当面讲" bodyClass="">
        {d.rows.length === 0 ? (
          <Empty icon={CircleCheck} title="目前没有人在同一个技能上卡住两次">
            {d.onceOnly > 0 ? `只卡住过一次的有 ${d.onceOnly} 人次，没有列出。` : undefined}
          </Empty>
        ) : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col" className="num">次数</th><th scope="col">最近一次在哪道题</th><th scope="col">怎么解决的</th><th scope="col">时间</th></tr></thead>
              <tbody>
                {d.rows.map((r, i) => (
                  <tr key={i}>
                    <td><Link to={`/teacher/students/${r.userId}`} className="who"><Avatar name={r.name} /><span className="cell-title">{r.name}</span></Link></td>
                    <td><span className="cell-title">{r.skill.name}</span><span className="cell-sub"><Code>{r.skill.code}</Code> {r.stubborn && <span className="pill pill-warn">顽固</span>}</span></td>
                    <td className="num">{r.times}</td><td>{r.lastProblem}</td>
                    <td>{RESOLUTION[r.resolution] ?? r.resolution}</td><td className="nowrap muted">{fullTime(r.lastTime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.rows.length > 0 && d.onceOnly > 0 && <p className="card-foot">另外，只卡住过一次的有 {d.onceOnly} 人次，没有列出。</p>}
      </Card>
      <Card title="卡住人数最多的技能" sub="每个技能有几个人卡住过">
        {d.common.length === 0 ? <Empty icon={CircleCheck} title="还没有记录到卡点" /> : (
          <BarList unit=" 人" rows={d.common.map((c) => ({ key: c.code, label: <>{c.name} <Code>{c.code}</Code></>, value: c.students, sub: c.kp }))} />
        )}
      </Card>
    </div>
  );
}
