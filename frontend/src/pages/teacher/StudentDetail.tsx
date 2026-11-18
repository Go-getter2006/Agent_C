// 学生明细：知识点和技能（v2 带区间）、复习、卡点、提交、求助、小题。只有登录的这门课；
// 数据结构这边另有“C 语言基础”（只有掌握程度，不含 C 语言的答题记录）和还没有证据时的准备度。
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Copy, FileDown, Waypoints } from "lucide-react";
import { get } from "../../api";
import { Card, Cells, CodeBlock, Empty, LevelLegend, Loading, Problem, ResultPill, levelClass } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime, futureDay, shortTime } from "../../lib/time";
import { Code, HELP, OUTCOME, REASON, RESOLUTION, TeacherPage, endReason, type SkillRef } from "./common";

interface Detail {
  student: { userId: string; name: string; class: { key: string; name: string } };
  knowledge: { code: string; name: string; level: string; p: number; v1: number; v1Level: string }[];
  skills: (SkillRef & {
    p: number | null; level: string; yes: number; no: number; v2: number | null; v2Level: string; v2Interval: [number, number] | null;
    halfLife: number | null; discounted: number; graph: number | null; bigGap: boolean;
  })[];
  submissions: {
    submissionId: number; problemId: number; title: string; attemptNo: number; result: string; resultText: string;
    grid: boolean[] | null; time: string | null;
    labels: { code: string; name: string; skill: string; line: number | null; confidence: number; source: string }[];
  }[];
  moreSubmissions: number;
  stuck: (SkillRef & { nth: number; stubborn: boolean; resolution: string; problem: string; problemId: number | null; error: string; errorName: string; time: string; passedAfter: boolean | null; givenCode: string })[];
  rounds: { problem: string; started: string; status: string; endReason: string; maxHelp: string; messages: number; quiz: boolean }[];
  quizzes: (SkillRef & { type: string; reason: string; state: string; confidence: string; outcome: string; correct: boolean | null; time: string })[];
  rootCauses: { skill: SkillRef; root: SkillRef; p: number }[];
  reviews: { code: string; name: string; kp: string; origin: string; state: string; halfLife: number; retention: number; reps: number; lapses: number; streak: number; due: string }[];
  copies: { problem: string; problemId: number; submissionId: number; matchedName: string; matchedUserId: string | null; share: number | null; time: string | null }[];
  readiness: { code: string; name: string; p: number }[];
  course: "C" | "D";
  foundation: { code: string; name: string; level: string; p: number | null; supports: { code: string; name: string }[] }[];
  graphUpdatedAt: string | null;
}

function LevelPill({ level }: { level: string }) {
  return <span className={`lvpill ${levelClass(level)}`}><i />{level}</span>;
}

/** 区间条：浅色是 80% 区间，圆点是估计值。 */
function Interval({ p, range }: { p: number | null; range: [number, number] | null }) {
  if (p === null) return <span className="muted">—</span>;
  return (
    <span className="interval" role="img" aria-label={`估计 ${p.toFixed(2)}${range ? `，80% 区间 ${range[0].toFixed(2)} 到 ${range[1].toFixed(2)}` : ""}`}>
      <span className="interval-track">
        {range && <span className="interval-band" style={{ left: `${range[0] * 100}%`, width: `${(range[1] - range[0]) * 100}%` }} />}
        <span className="interval-mid" style={{ left: "60%" }} />
        <span className="interval-dot" style={{ left: `${p * 100}%` }} />
      </span>
      <span className="num">{p.toFixed(2)}</span>
    </span>
  );
}

export default function StudentDetail() {
  const { id } = useParams();
  const data = useLoad(() => get<Detail>(`/api/teacher/students/${id}`), [id]);
  const [allSkills, setAllSkills] = useState(false);
  const d = data.data;
  const skills = useMemo(() => (d ? d.skills.filter((k) => allSkills || k.yes + k.no > 0) : []), [d, allSkills]);

  return (
    <TeacherPage title={d ? d.student.name : "学生明细"} withClass={false}
      sub={d ? <>{d.student.userId} · {d.student.class.name}</> : undefined}
      crumb={<Link to="/teacher/students" className="crumb"><ArrowLeft size={15} aria-hidden="true" />学生列表</Link>}
      tools={d ? (
        <>
          <Link to={`/teacher/map?student=${d.student.userId}`} className="btn btn-sm"><Waypoints size={15} aria-hidden="true" />在知识图谱里看</Link>
          <Link to={`/teacher/logs?ids=${d.student.userId}`} className="btn btn-sm"><FileDown size={15} aria-hidden="true" />申请这个学生的日志</Link>
        </>
      ) : null}>
      {() => (
        <>
          {data.error && <Problem message={data.error} onRetry={data.reload} />}
          {!d && !data.error && <div className="card card-body"><Loading what="正在取这个学生的明细" lines={6} /></div>}
          {d && (
            <div className="stack">
              <div className="grid-main">
                <Card title="知识点" sub="档位来自掌握程度算法 v2（学生端和智能体用的）；括号里是 v1 的档位，作对照" bodyClass="">
                  <div className="table-scroll">
                    <table className="table">
                      <thead><tr><th scope="col">知识点</th><th scope="col">档位</th><th scope="col" className="num">估计</th><th scope="col">v1</th></tr></thead>
                      <tbody>
                        {d.knowledge.map((k) => (
                          <tr key={k.code}>
                            <td><span className="cell-title">{k.name}</span> <Code>{k.code}</Code></td>
                            <td><LevelPill level={k.level} /></td>
                            <td className="num">{k.p.toFixed(2)}</td>
                            <td className="muted nowrap">{k.v1Level}（{k.v1.toFixed(2)}）</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
                <div className="stack">
                  {d.course === "D" && (
                    <Card title="C 语言基础" sub="数据结构要用到的 C 语言知识点，在 C 语言课上掌握得怎么样。只有掌握程度，不含 C 语言的答题记录">
                      {d.foundation.every((f) => f.p === null) ? <p className="note">在 C 语言里还没有这方面的证据。</p> : (
                        <ul className="rv-list">
                          {d.foundation.filter((f) => f.p !== null).sort((a, b) => (a.p ?? 0) - (b.p ?? 0)).slice(0, 10).map((f) => (
                            <li key={f.code}>
                              <div className="rv-top"><span>{f.name} <Code>{f.code}</Code></span><LevelPill level={f.level} /></div>
                              <span className="note">支撑：{f.supports.map((x) => x.name).join("、")}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </Card>
                  )}
                  {d.course === "D" && d.readiness.length > 0 && (
                    <Card title="还没有证据的知识点" sub="按 C 语言里的先修知识点推算的准备度，只作参考">
                      <ul className="ready-list">
                        {d.readiness.slice(0, 10).map((r) => (
                          <li key={r.code}>
                            <span>{r.name} <Code>{r.code}</Code></span>
                            <span className="ready-bar" aria-hidden="true"><span style={{ width: `${r.p * 100}%` }} /></span>
                            <span className="num">{r.p.toFixed(2)}</span>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                  <Card title="复习" sub="间隔重复：记得牢的程度按半衰期算，降到 75% 时到期">
                    {d.reviews.length === 0 ? <p className="note">没有在复习的项。</p> : (
                      <ul className="rv-list">
                        {d.reviews.map((r) => (
                          <li key={r.code}>
                            <div className="rv-top">
                              <strong>{r.name}</strong>
                              <span className={r.state === "graduated" ? "pill pill-pass" : "pill"}>{r.state === "graduated" ? "已巩固" : `下次 ${futureDay(r.due)}`}</span>
                            </div>
                            <span className="note">
                              {r.origin} · 半衰期 {r.halfLife} 天 · 现在记得 {Math.round(r.retention * 100)}% · 复习 {r.reps} 次 · 忘了 {r.lapses} 次 · 连对 {r.streak} 次
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>
                  {d.copies.length > 0 && (
                    <Card title="雷同的通过" sub="和班里更早的通过代码规范化后一样、写法少见、时间挨得近。这类通过的证据按 0.4 计">
                      <ul className="rv-list">
                        {d.copies.map((c, i) => (
                          <li key={i}>
                            <div className="rv-top"><strong><Copy size={14} aria-hidden="true" /> {c.problem}</strong><span className="note">{shortTime(c.time)}</span></div>
                            <span className="note">和{c.matchedName || "一位同学"}更早的通过一样{c.share !== null ? `，班里只有 ${Math.round(c.share * 100)}% 的人这样写` : ""}</span>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                  {d.rootCauses.length > 0 && (
                    <Card title="根因推断" sub="图模型（试验中）认为：这些技能反复失败，真正没掌握的可能是它的先修">
                      <ul className="rv-list">
                        {d.rootCauses.map((r, i) => (
                          <li key={i}><div className="rv-top"><span>{r.skill.name} ← <strong>{r.root.name}</strong></span><span className="num">{r.p.toFixed(2)}</span></div></li>
                        ))}
                      </ul>
                    </Card>
                  )}
                </div>
              </div>

              <Card title="技能明细" sub="v2：可靠性加权、个人化的遗忘、图先验，带 80% 区间（竖线是“已掌握”要求的下限 0.6）。v1 和图模型作对照，相差 0.3 以上的标出来"
                action={<button type="button" className="btn btn-sm" aria-pressed={allSkills} onClick={() => setAllSkills(!allSkills)}>{allSkills ? "只看有证据的" : "显示全部技能"}</button>}
                bodyClass="">
                <div className="card-body-tight"><LevelLegend /></div>
                <div className="table-scroll">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">技能</th><th scope="col">v2 档位</th><th scope="col">v2 估计与区间</th>
                        <th scope="col" className="num" title="这个学生这个技能的证据半衰期">半衰期</th>
                        <th scope="col" className="num" title="因为雷同而打折的通过次数">打折</th>
                        <th scope="col" className="num">v1</th><th scope="col" className="num">图模型</th>
                        <th scope="col" className="num">会 / 不会</th><th scope="col"><span className="sr-only">提示</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {skills.map((k) => (
                        <tr key={k.code}>
                          <td><span className="cell-title">{k.name}</span><span className="cell-sub">{k.kp} · <Code>{k.code}</Code></span></td>
                          <td><LevelPill level={k.v2Level} /></td>
                          <td><Interval p={k.v2} range={k.v2Interval} /></td>
                          <td className="num">{k.halfLife === null ? "—" : `${k.halfLife} 天`}</td>
                          <td className="num">{k.discounted || ""}</td>
                          <td className="num">{k.p === null ? "—" : k.p.toFixed(2)}</td>
                          <td className="num">{k.graph === null ? "—" : k.graph.toFixed(2)}</td>
                          <td className="num nowrap">{k.yes.toFixed(1)} / {k.no.toFixed(1)}</td>
                          <td>{k.bigGap && <span className="pill pill-warn">差别大</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card title="卡点" bodyClass="">
                {d.stuck.length === 0 ? <Empty title="没有记录到卡点" /> : (
                  <div className="table-scroll">
                    <table className="table">
                      <thead><tr><th scope="col">技能</th><th scope="col">第几次</th><th scope="col">在哪道题</th><th scope="col">错误</th><th scope="col">怎么解决的</th><th scope="col">之后通过了吗</th><th scope="col">时间</th></tr></thead>
                      <tbody>
                        {d.stuck.map((sp, i) => (
                          <tr key={i}>
                            <td><span className="cell-title">{sp.name}</span><span className="cell-sub"><Code>{sp.code}</Code></span></td>
                            <td className="nowrap">第 {sp.nth} 次{sp.stubborn && <> <span className="pill pill-warn">顽固</span></>}</td>
                            <td>{sp.problem}{sp.problemId ? <> <Code>{sp.problemId}</Code></> : null}</td>
                            <td>{sp.errorName} <Code>{sp.error}</Code></td>
                            <td>{RESOLUTION[sp.resolution] ?? sp.resolution}</td>
                            <td>{sp.passedAfter === true ? <span className="pill pill-pass">通过了</span> : sp.passedAfter === false ? "没有" : "还没有"}</td>
                            <td className="nowrap muted">{fullTime(sp.time)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {d.stuck.filter((sp) => sp.givenCode).slice(0, 1).map((sp, i) => (
                  <div key={i} className="card-body">
                    <CodeBlock caption={`最近一次给这个学生的示例代码（${sp.name}）`} lines={sp.givenCode.split("\n")} copyable={false} />
                  </div>
                ))}
              </Card>

              <Card title="提交记录" sub={d.moreSubmissions ? `只列最近 80 条，更早的还有 ${d.moreSubmissions} 条` : undefined} bodyClass="">
                <div className="table-scroll">
                  <table className="table">
                    <thead><tr><th scope="col">题目</th><th scope="col">提交</th><th scope="col">结果</th><th scope="col">测试点</th><th scope="col">错误标注</th><th scope="col">时间</th></tr></thead>
                    <tbody>
                      {d.submissions.map((x) => (
                        <tr key={x.submissionId}>
                          <td><span className="cell-title">{x.title}</span> <Code>{x.problemId}</Code></td>
                          <td className="nowrap">第 {x.attemptNo} 次</td>
                          <td><ResultPill sub={{ key: "", attemptNo: x.attemptNo, result: x.result, resultText: x.resultText, passed: null, total: null, grid: x.grid, time: x.time }} /></td>
                          <td>{x.grid ? <Cells grid={x.grid} /> : null}</td>
                          <td>
                            {x.labels.map((lab, i) => (
                              <div key={i} className="label-line">{lab.name} <Code>{lab.code}{lab.line ? ` · 第 ${lab.line} 行` : ""} · {lab.source === "rule" ? "规则" : lab.source === "llm" ? "大模型" : "预置"} {lab.confidence.toFixed(2)}</Code></div>
                            ))}
                          </td>
                          <td className="nowrap muted">{fullTime(x.time)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <div className="grid-2">
                <Card title="求助回合" bodyClass="">
                  {d.rounds.length === 0 ? <Empty title="还没有问过问题" /> : (
                    <table className="table">
                      <thead><tr><th scope="col">题目</th><th scope="col">最多帮到</th><th scope="col">怎么结束的</th><th scope="col">时间</th></tr></thead>
                      <tbody>
                        {d.rounds.map((r, i) => (
                          <tr key={i}><td className="cell-title">{r.problem}</td><td>{HELP[r.maxHelp] ?? r.maxHelp}</td><td>{endReason(r)}{r.quiz ? "，出了小题" : ""}</td><td className="nowrap muted">{shortTime(r.started)}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Card>
                <Card title="小题" bodyClass="">
                  {d.quizzes.length === 0 ? <Empty title="还没有做过小题" /> : (
                    <table className="table">
                      <thead><tr><th scope="col">技能</th><th scope="col">什么时候出的</th><th scope="col">把握</th><th scope="col">结果</th></tr></thead>
                      <tbody>
                        {d.quizzes.map((q, i) => (
                          <tr key={i}>
                            <td className="cell-title">{q.name}</td>
                            <td>{REASON[q.reason] ?? q.reason} · {shortTime(q.time)}</td>
                            <td>{q.confidence || "—"}</td>
                            <td>{q.correct === true ? <span className="pill pill-pass">{OUTCOME[q.outcome] ?? q.outcome}</span> : q.correct === false ? <span className="pill pill-fail">{OUTCOME[q.outcome] ?? q.outcome}</span> : OUTCOME[q.outcome] ?? q.outcome}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Card>
              </div>
            </div>
          )}
        </>
      )}
    </TeacherPage>
  );
}
