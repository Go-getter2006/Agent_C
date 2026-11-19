// 卡点按知识点汇总（app/stuckstats.py）：卡在哪个知识点、卡住以后怎么样了；选一个知识点看技能、学生和没来问的。
import { useState } from "react";
import { Link } from "react-router-dom";
import { CircleCheck } from "lucide-react";
import { get } from "../../api";
import { BarList } from "../../components/charts";
import { Avatar, Card, Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime } from "../../lib/time";
import { Code, RESOLUTION, type ClassInfo } from "./common";

type R = { n: number; d: number } | null;

interface KpRow {
  code: string; name: string;
  students: number; episodes: number; repeat: number; stubborn: number;
  recall: R; passed: R; transfer: R; silent: number; lastTime: string | null;
  skills: { code: string; name: string; students: number; episodes: number }[];
  studentRows: {
    userId: string; name: string; times: number; rounds: number; stubborn: boolean; skills: string[];
    lastTime: string; lastProblem: string; resolution: string; passed: boolean | null; triesAfter: number | null; silent: number;
  }[];
  silentRows: { userId: string; name: string; problems: { title: string; problemId: number; fails: number }[] }[];
}

interface StuckData { rows: KpRow[]; silentFails: number; version: string }

const frac = (r: R) => (r ? `${r.n}/${r.d}` : "—");

function Outcome({ passed, tries }: { passed: boolean | null; tries: number | null }) {
  if (passed === null) return <span className="muted">没绑定题</span>;
  if (passed) return <span className="pill pill-pass">通过{tries ? ` · 又交 ${tries} 次` : ""}</span>;
  return <span>还没通过{tries ? `（又交 ${tries} 次）` : ""}</span>;
}

function Detail({ row, silentFails }: { row: KpRow; silentFails: number }) {
  return (
    <div className="stuck-detail">
      <Card title={`${row.name} · 谁卡在这里`} bodyClass="">
        {row.studentRows.length === 0 ? <p className="card-body note">没有人在对话里卡在这里。</p> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">学生</th><th scope="col" className="num">回合</th><th scope="col">技能</th><th scope="col">最近一次</th><th scope="col">怎么解决的</th><th scope="col">之后</th></tr></thead>
              <tbody>
                {row.studentRows.map((r) => (
                  <tr key={r.userId}>
                    <td><Link to={`/teacher/students/${r.userId}`} className="who"><Avatar name={r.name} /><span className="cell-title">{r.name}</span></Link></td>
                    <td className="num">{r.rounds}{r.stubborn && <> <span className="pill pill-warn">顽固</span></>}</td>
                    <td>{r.skills.join("、")}</td>
                    <td><span className="cell-title">{r.lastProblem || "没绑定题"}</span><span className="cell-sub">{fullTime(r.lastTime)}</span></td>
                    <td>{RESOLUTION[r.resolution] ?? r.resolution}</td>
                    <td><Outcome passed={r.passed} tries={r.triesAfter} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="stack">
        <Card title="涉及的技能">
          {row.skills.length === 0 ? <p className="note">对话里没有卡点。</p> : (
            <BarList unit=" 次" rows={row.skills.map((x) => ({ key: x.code, label: <>{x.name} <Code>{x.code}</Code></>, value: x.episodes, sub: `${x.students} 人` }))} />
          )}
        </Card>
        <Card title="没来问" sub={`一道题没通过的提交不少于 ${silentFails} 次，没就这道题求助`}>
          {row.silentRows.length === 0 ? <p className="note">没有。</p> : (
            <ul className="stuck-silent">
              {row.silentRows.map((r) => (
                <li key={r.userId}>
                  <Link to={`/teacher/students/${r.userId}`} className="who"><Avatar name={r.name} /><span className="cell-title">{r.name}</span></Link>
                  <span className="note">{r.problems.map((p) => `${p.title || p.problemId} ×${p.fails}`).join("、")}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export default function Stuck({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<StuckData>(`/api/teacher/classes/${cls.key}/stuck`), [cls.key], 60000, false, `t:stuck:${cls.key}`);
  const [picked, setPicked] = useState<string | null>(null);
  const d = data.data;
  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在汇总卡点" lines={5} /></div>;
  if (d.rows.length === 0) {
    return <div className="card"><Empty icon={CircleCheck} title="还没有卡点">对话里卡住、或者一道题反复没过又没来问的，会按知识点列在这里。</Empty></div>;
  }
  const cur = d.rows.find((r) => r.code === picked) ?? d.rows[0];
  return (
    <div className="stack">
      <section className="card diag" aria-labelledby="stuck-t">
        <div className="dims-head"><h2 id="stuck-t">按知识点</h2><span className="note">分子 / 分母</span></div>
        <div className="table-scroll">
          <table className="dims-table">
            <thead>
              <tr>
                <th scope="col">知识点</th><th scope="col" className="num">卡住</th><th scope="col" className="num">反复</th>
                <th scope="col" className="num">顽固</th><th scope="col" className="num">第二次自己想起来</th>
                <th scope="col" className="num">卡后通过</th><th scope="col" className="num">之后新题一次通过</th><th scope="col" className="num">没来问</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r) => (
                <tr key={r.code} className={r.code === cur.code ? "picked" : undefined}>
                  <th scope="row"><button type="button" aria-pressed={r.code === cur.code} onClick={() => setPicked(r.code)}>{r.name}</button></th>
                  <td className="num">{r.students ? `${r.students} 人 · ${r.episodes} 次` : "—"}</td>
                  <td className="num">{r.repeat || "—"}</td>
                  <td className="num">{r.stubborn ? <span className="pill pill-warn">{r.stubborn}</span> : "—"}</td>
                  <td className="num">{frac(r.recall)}</td>
                  <td className="num">{frac(r.passed)}</td>
                  <td className="num">{frac(r.transfer)}</td>
                  <td className="num">{r.silent ? `${r.silent} 人` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note stuck-defs">
          反复：卡住两个回合以上的人数。第二次自己想起来：同一技能第二次卡住时自己说出来。卡后通过：卡住时那道题后来通过。
          之后新题一次通过：第一次卡在这里以后才开始做的同知识点题，第一次提交就通过。
        </p>
      </section>
      <Detail row={cur} silentFails={d.silentFails} />
    </div>
  );
}
