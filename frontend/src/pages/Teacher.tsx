// 教师端：班级概况、学生、卡点、图模型（试验中）、数据源。只看自己名下的班。
import { useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useParams } from "react-router-dom";
import { get, post } from "../api";
import { Shell, type NavItem } from "../components/Shell";
import { Cells, CodeBlock, Icon, Loading, Problem } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { deadline, fullTime, shortTime } from "../lib/time";
import { useSession } from "../session";

const NAV: NavItem[] = [
  { to: "/teacher/class", label: "班级", icon: <Icon.bars /> },
  { to: "/teacher/students", label: "学生", icon: <Icon.people /> },
  { to: "/teacher/stuck", label: "卡点", icon: <Icon.pin /> },
  { to: "/teacher/graph", label: "模型", icon: <Icon.graph /> },
  { to: "/teacher/sources", label: "数据", icon: <Icon.plug /> },
];

interface ClassInfo { key: string; name: string; server: string; students: number }
interface SkillRef { code: string; name: string; kp: string }

const CLASS_KEY = "pda.teacher.class";
const RESOLUTION: Record<string, string> = {
  pending: "还在处理", recalled: "自己回忆出来", blank_filled: "挖空补上", example_given: "给了示例代码", exercise: "小练习",
};
const END_REASON = (r: { status: string; endReason: string }) => (r.status === "open" ? "进行中" : r.endReason || "已结束");
const HELP: Record<string, string> = { NA: "没有求助", H0: "反问", H1: "指出位置", H2: "讲原理", H3: "给了示例代码" };
const OUTCOME: Record<string, string> = {
  right: "改对了", right_judge: "判断对", right_second: "第二次改对", right_bypass: "绕开原错误改对",
  wrong_fix: "两次没改对", wrong_judge: "判断错", skipped: "跳过", "": "没做完",
};
const REASON: Record<string, string> = { after_pass: "通过后", recall: "回忆题", revisit: "回访题" };

function useClasses() {
  const list = useLoad(() => get<{ classes: ClassInfo[]; name: string; role: string }>("/api/teacher/classes"), []);
  const [picked, setPicked] = useState<string | null>(() => window.localStorage.getItem(CLASS_KEY));
  const classes = list.data?.classes ?? [];
  const current = classes.find((c) => c.key === picked) ?? classes[0] ?? null;
  const pick = (key: string) => {
    setPicked(key);
    window.localStorage.setItem(CLASS_KEY, key);
  };
  return { classes, current, pick, loading: list.loading, error: list.error, role: list.data?.role ?? "teacher" };
}

function Page({ title, children, withClass = true }: { title: string; children: (cls: ClassInfo | null) => ReactNode; withClass?: boolean }) {
  const { me, logout } = useSession();
  const { classes, current, pick, loading, error } = useClasses();
  return (
    <Shell nav={NAV}>
      <main className="page">
        <div className="page-head">
          <div className="t-head">
            <h1>{title}</h1>
            {withClass && classes.length > 0 && (
              <>
                <label className="sr-only" htmlFor="class-pick">班级</label>
                <select id="class-pick" className="field" value={current?.key ?? ""} onChange={(e) => pick(e.target.value)}>
                  {classes.map((c) => <option key={c.key} value={c.key}>{c.name}（{c.students} 人）</option>)}
                </select>
              </>
            )}
          </div>
          <span className="note">
            {me?.name}　<button type="button" className="btn-text" style={{ minHeight: 0 }} onClick={() => void logout()}>退出登录</button>
          </span>
        </div>
        <div className="page-body">
          {loading && <Loading />}
          {error && <Problem message={error} />}
          {!loading && !error && withClass && !current && (
            <p className="note">你名下还没有同步到班级。第一次登录后，系统会去 XDOJ 同步你的班级和名单，一般几分钟内完成；在“数据”页可以看进度。</p>
          )}
          {!loading && (!withClass || current) && children(current)}
        </div>
      </main>
    </Shell>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="t-section">
      <h2>{title}</h2>
      {note && <p className="note">{note}</p>}
      {children}
    </section>
  );
}

const Code = ({ children }: { children: ReactNode }) => <span className="code-cell">{children}</span>;

// ---------------------------------------------------------------- 班级概况
interface Overview {
  class: { key: string; name: string; students: number; activeThisWeek: number };
  levels: string[];
  knowledge: { code: string; name: string; topic: string; counts: number[] }[];
  errors: { code: string; name: string; students: number; count: number }[];
  overconfident: (SkillRef & { n: number; wrong: number })[];
  homeworks: { title: string; contestId: number; problems: number; allPassed: number; started: number; endTime: string | null; ended: boolean }[];
}

function ClassOverview({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<Overview>(`/api/teacher/classes/${cls.key}/overview`), [cls.key]);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <Loading what="正在汇总全班的情况" />;
  const total = d.class.students || 1;
  let topic = "";
  return (
    <div className="t-sections">
      <p>{d.class.students} 名学生，最近 7 天有 {d.class.activeThisWeek} 人交过题。</p>
      <Section title="每个知识点的掌握分布" note="按简单算法的档位统计。只列这个班布置过的题涉及到的知识点。">
        <div className="legend">
          {d.levels.map((level, i) => <span key={level}><i className={`d${i}`} />{level}</span>)}
        </div>
        <div className="t-scroll">
          <table className="table dense">
            <thead>
              <tr>
                <th scope="col">知识点</th><th scope="col" style={{ width: "36%" }}>分布</th>
                {d.levels.map((level) => <th key={level} scope="col" className="num">{level}</th>)}
              </tr>
            </thead>
            <tbody>
              {d.knowledge.map((k) => {
                const head = k.topic !== topic ? ((topic = k.topic), <tr key={`t-${k.topic}`} className="topic-row"><td colSpan={6}>{k.topic}</td></tr>) : null;
                return [
                  head,
                  <tr key={k.code}>
                    <td className="strong">{k.name} <Code>{k.code}</Code></td>
                    <td>
                      <div className="dist" role="img" aria-label={d.levels.map((l, i) => `${l} ${k.counts[i]} 人`).join("，")}>
                        {k.counts.map((n, i) => n > 0 && <span key={i} className={`d${i}`} style={{ width: `${(100 * n) / total}%` }} />)}
                      </div>
                    </td>
                    {k.counts.map((n, i) => <td key={i} className="num">{n}</td>)}
                  </tr>,
                ];
              })}
            </tbody>
          </table>
        </div>
      </Section>
      <div className="t-cols">
        <Section title="全班最常见的错误" note="最近 30 天，按出现的提交次数排序。">
          {d.errors.length === 0 ? <p className="note">最近 30 天没有标注到错误。</p> : (
            <table className="table dense">
              <thead><tr><th scope="col">错误</th><th scope="col" className="num">人数</th><th scope="col" className="num">次数</th></tr></thead>
              <tbody>
                {d.errors.map((e) => (
                  <tr key={e.code}><td className="strong">{e.name} <Code>{e.code}</Code></td><td className="num">{e.students}</td><td className="num">{e.count}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
        <Section title="自以为会的技能" note="小题里选了“很有把握”却答错的。数据少的时候只作参考。">
          {d.overconfident.length === 0 ? <p className="note">目前没有。</p> : (
            <table className="table dense">
              <thead><tr><th scope="col">技能</th><th scope="col" className="num">很有把握的作答</th><th scope="col" className="num">其中答错</th></tr></thead>
              <tbody>
                {d.overconfident.map((o) => (
                  <tr key={o.code}><td className="strong">{o.name} <Code>{o.code}</Code></td><td className="num">{o.n}</td><td className="num">{o.wrong}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
      <Section title="作业完成情况">
        <div className="t-scroll">
          <table className="table dense">
            <thead>
              <tr><th scope="col">作业</th><th scope="col" className="num">题数</th><th scope="col" className="num">开始做的人</th><th scope="col" className="num">全部通过的人</th><th scope="col">截止</th></tr>
            </thead>
            <tbody>
              {d.homeworks.map((h) => (
                <tr key={h.contestId}>
                  <td className="strong">{h.title}</td><td className="num">{h.problems}</td><td className="num">{h.started}</td>
                  <td className="num">{h.allPassed}</td><td>{deadline(h.endTime, h.ended)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- 学生列表
interface StudentRow {
  userId: string; name: string; tried: number; passed: number; submissions: number; firstTryRate: number | null;
  weak: number; mastered: number; stuck: number; stubborn: number; rounds: number; lastActive: string | null;
}
type SortKey = keyof StudentRow;

function Students({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<{ total: number; students: StudentRow[] }>(`/api/teacher/classes/${cls.key}/students`), [cls.key]);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "userId", dir: 1 });
  const rows = useMemo(() => {
    const list = [...(data.data?.students ?? [])];
    list.sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (x === y) return a.userId < b.userId ? -1 : 1;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * sort.dir;
    });
    return list;
  }, [data.data, sort]);
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!data.data) return <Loading what="正在取学生名单" />;
  const columns: [SortKey, string, boolean][] = [
    ["userId", "学号", false], ["name", "姓名", false], ["tried", "做过的题", true], ["passed", "已通过", true],
    ["submissions", "提交次数", true], ["firstTryRate", "一次通过率", true], ["weak", "待加强的知识点", true],
    ["stuck", "卡点", true], ["rounds", "求助回合", true], ["lastActive", "最近提交", false],
  ];
  return (
    <div className="t-sections">
      <p className="note">这个班布置过 {data.data.total} 道题。点表头可以排序，点姓名看这个学生的明细。</p>
      <div className="t-scroll">
        <table className="table dense">
          <thead>
            <tr>
              {columns.map(([key, label, num]) => (
                <th key={key} scope="col" className={num ? "num" : undefined}
                  aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
                  <button type="button" onClick={() => setSort({ key, dir: sort.key === key ? (sort.dir === 1 ? -1 : 1) : num ? -1 : 1 })}>
                    {label}{sort.key === key ? (sort.dir === 1 ? "（升序）" : "（降序）") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.userId}>
                <td className="nowrap">{r.userId}</td>
                <td className="strong"><Link to={`/teacher/students/${r.userId}`}>{r.name || "（没有姓名）"}</Link></td>
                <td className="num">{r.tried}</td><td className="num">{r.passed}</td><td className="num">{r.submissions}</td>
                <td className="num">{r.firstTryRate === null ? "" : `${r.firstTryRate}%`}</td>
                <td className="num">{r.weak}</td>
                <td className="num">{r.stuck}{r.stubborn > 0 ? `（顽固 ${r.stubborn}）` : ""}</td>
                <td className="num">{r.rounds}</td>
                <td className="nowrap">{r.lastActive ? fullTime(r.lastActive) : "还没提交"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 学生明细
interface Detail {
  student: { userId: string; name: string; class: { key: string; name: string } };
  knowledge: { code: string; name: string; level: string; p: number }[];
  skills: (SkillRef & { p: number | null; level: string; yes: number; no: number; graph: number | null; bigGap: boolean })[];
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
}

function StudentDetail() {
  const { id } = useParams();
  const data = useLoad(() => get<Detail>(`/api/teacher/students/${id}`), [id]);
  const d = data.data;
  return (
    <Page title={d ? `${d.student.name}（${d.student.userId}）` : "学生明细"} withClass={false}>
      {() => (
        <div className="t-sections">
          <Link to="/teacher/students" className="crumb"><Icon.back />回到学生列表</Link>
          {data.error && <Problem message={data.error} onRetry={data.reload} />}
          {!d && !data.error && <Loading what="正在取这个学生的明细" />}
          {d && (
            <>
              <p className="note">{d.student.class.name}</p>
              <Section title="卡点">
                {d.stuck.length === 0 ? <p className="note">没有记录到卡点。</p> : (
                  <div className="t-scroll">
                    <table className="table dense">
                      <thead><tr><th scope="col">技能</th><th scope="col">第几次</th><th scope="col">在哪道题</th><th scope="col">怎么解决的</th><th scope="col">之后通过了吗</th><th scope="col">时间</th></tr></thead>
                      <tbody>
                        {d.stuck.map((sp, i) => (
                          <tr key={i}>
                            <td className="strong">{sp.name} <Code>{sp.code}</Code></td>
                            <td className="nowrap">第 {sp.nth} 次{sp.stubborn && <> <span className="flag">顽固</span></>}</td>
                            <td>{sp.problem}{sp.problemId ? <> <Code>{sp.problemId}</Code></> : null}</td>
                            <td>{RESOLUTION[sp.resolution] ?? sp.resolution}</td>
                            <td>{sp.passedAfter === true ? "通过了" : sp.passedAfter === false ? "没有" : "还没有"}</td>
                            <td className="nowrap">{fullTime(sp.time)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {d.stuck.filter((sp) => sp.givenCode).slice(0, 1).map((sp, i) => (
                  <CodeBlock key={i} caption={`最近一次给他的示例代码（${sp.name}）`} lines={sp.givenCode.split("\n")} copyable={false} />
                ))}
              </Section>
              {d.rootCauses.length > 0 && (
                <Section title="根因推断" note="图模型（试验中）认为：这些技能反复失败，真正没掌握的可能是它的先修技能。">
                  <table className="table dense">
                    <thead><tr><th scope="col">反复失败的技能</th><th scope="col">可能的根因</th><th scope="col" className="num">图模型估计</th></tr></thead>
                    <tbody>
                      {d.rootCauses.map((r, i) => (
                        <tr key={i}><td>{r.skill.name} <Code>{r.skill.code}</Code></td><td className="strong">{r.root.name} <Code>{r.root.code}</Code></td><td className="num">{r.p.toFixed(2)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </Section>
              )}
              <Section title="技能明细" note="简单算法是学生端和智能体用的；图模型是试验中的对照。两者相差 0.3 以上的标出来。">
                <div className="t-scroll">
                  <table className="table dense">
                    <thead>
                      <tr><th scope="col">技能</th><th scope="col">所属知识点</th><th scope="col">档位</th><th scope="col" className="num">简单算法</th><th scope="col" className="num">图模型</th><th scope="col" className="num">“会”的证据</th><th scope="col" className="num">“不会”的证据</th><th scope="col" /></tr>
                    </thead>
                    <tbody>
                      {d.skills.map((k) => (
                        <tr key={k.code}>
                          <td className="strong">{k.name} <Code>{k.code}</Code></td><td>{k.kp}</td><td className="nowrap">{k.level}</td>
                          <td className="num">{k.p === null ? "" : k.p.toFixed(2)}</td><td className="num">{k.graph === null ? "" : k.graph.toFixed(2)}</td>
                          <td className="num">{k.yes.toFixed(2)}</td><td className="num">{k.no.toFixed(2)}</td>
                          <td>{k.bigGap && <span className="flag">差别大</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
              <Section title="提交记录" note={d.moreSubmissions ? `只列最近 80 条，更早的还有 ${d.moreSubmissions} 条。` : undefined}>
                <div className="t-scroll">
                  <table className="table dense">
                    <thead><tr><th scope="col">题目</th><th scope="col">提交</th><th scope="col">结果</th><th scope="col">测试点</th><th scope="col">错误标注</th><th scope="col">时间</th></tr></thead>
                    <tbody>
                      {d.submissions.map((x) => (
                        <tr key={x.submissionId}>
                          <td className="strong">{x.title} <Code>{x.problemId}</Code></td>
                          <td className="nowrap">第 {x.attemptNo} 次</td><td className="nowrap">{x.resultText}</td>
                          <td>{x.grid ? <Cells grid={x.grid} /> : null}</td>
                          <td>
                            {x.labels.map((lab, i) => (
                              <div key={i}>{lab.name} <Code>{lab.code}{lab.line ? `，第 ${lab.line} 行` : ""}，{lab.source === "rule" ? "规则" : lab.source === "llm" ? "大模型" : "预置"} {lab.confidence.toFixed(2)}</Code></div>
                            ))}
                          </td>
                          <td className="nowrap">{fullTime(x.time)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
              <div className="t-cols">
                <Section title="求助回合">
                  {d.rounds.length === 0 ? <p className="note">还没有问过问题。</p> : (
                    <table className="table dense">
                      <thead><tr><th scope="col">题目</th><th scope="col">最多帮到</th><th scope="col">怎么结束的</th><th scope="col">时间</th></tr></thead>
                      <tbody>
                        {d.rounds.map((r, i) => (
                          <tr key={i}><td className="strong">{r.problem}</td><td>{HELP[r.maxHelp] ?? r.maxHelp}</td><td>{END_REASON(r)}{r.quiz ? "，出了小题" : ""}</td><td className="nowrap">{shortTime(r.started)}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Section>
                <Section title="小题">
                  {d.quizzes.length === 0 ? <p className="note">还没有做过小题。</p> : (
                    <table className="table dense">
                      <thead><tr><th scope="col">技能</th><th scope="col">什么时候出的</th><th scope="col">把握</th><th scope="col">结果</th></tr></thead>
                      <tbody>
                        {d.quizzes.map((q, i) => (
                          <tr key={i}><td className="strong">{q.name}</td><td>{REASON[q.reason] ?? q.reason}，{shortTime(q.time)}</td><td>{q.confidence}</td><td>{OUTCOME[q.outcome] ?? q.outcome}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Section>
              </div>
            </>
          )}
        </div>
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- 卡点名单
interface StuckData {
  rows: { userId: string; name: string; skill: SkillRef; times: number; stubborn: boolean; lastTime: string; lastProblem: string; resolution: string; passedAfter: boolean | null; errorName: string; error: string }[];
  onceOnly: number;
  common: (SkillRef & { students: number })[];
}

function Stuck({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<StuckData>(`/api/teacher/classes/${cls.key}/stuck`), [cls.key]);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <Loading what="正在取卡点名单" />;
  return (
    <div className="t-sections">
      <Section title="反复卡住的学生" note="同一个技能卡住两次及以上。第三次起标为顽固卡点，建议当面讲。">
        {d.rows.length === 0 ? (
          <p className="note">目前没有人在同一个技能上卡住两次。{d.onceOnly > 0 ? `只卡住过一次的有 ${d.onceOnly} 人次。` : ""}</p>
        ) : (
          <div className="t-scroll">
            <table className="table dense">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col" className="num">次数</th><th scope="col">最近一次在哪道题</th><th scope="col">怎么解决的</th><th scope="col">时间</th></tr></thead>
              <tbody>
                {d.rows.map((r, i) => (
                  <tr key={i}>
                    <td className="strong"><Link to={`/teacher/students/${r.userId}`}>{r.name}</Link></td>
                    <td>{r.skill.name} <Code>{r.skill.code}</Code> {r.stubborn && <span className="flag">顽固</span>}</td>
                    <td className="num">{r.times}</td><td>{r.lastProblem}</td>
                    <td>{RESOLUTION[r.resolution] ?? r.resolution}</td><td className="nowrap">{fullTime(r.lastTime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.rows.length > 0 && d.onceOnly > 0 && <p className="note">另外，只卡住过一次的有 {d.onceOnly} 人次，没有列出。</p>}
      </Section>
      <Section title="卡住人数最多的技能">
        {d.common.length === 0 ? <p className="note">还没有记录到卡点。</p> : (
          <table className="table dense" style={{ maxWidth: 640 }}>
            <thead><tr><th scope="col">技能</th><th scope="col">所属知识点</th><th scope="col" className="num">人数</th></tr></thead>
            <tbody>
              {d.common.map((c) => <tr key={c.code}><td className="strong">{c.name} <Code>{c.code}</Code></td><td>{c.kp}</td><td className="num">{c.students}</td></tr>)}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- 图模型
interface GraphData {
  version: string; prereqStatus: string;
  data: { students: number; evidence: number; posteriors: number; predictions: number; updatedAt: string | null };
  params: Record<string, number>;
  gaps: { userId: string; name: string; skill: SkillRef; simple: number; graph: number; level: string }[];
  rootCauses: { userId: string; name: string; skill: SkillRef; root: SkillRef; p: number }[];
  commonRoots: (SkillRef & { students: number })[];
  edges: { parent: string; child: string; parentName: string; childName: string; n: number; withParent: number | null; withoutParent: number | null; nWith: number; nWithout: number; doubtful: boolean }[];
  weekly: { weekStart: string; n: number; simpleAccuracy: number; graphAccuracy: number; simpleBrier: number; graphBrier: number }[];
}

const pct = (x: number | null) => (x === null ? "没有数据" : `${Math.round(x * 100)}%`);

function Graph({ cls }: { cls: ClassInfo }) {
  const data = useLoad(() => get<GraphData>(`/api/teacher/classes/${cls.key}/graph`), [cls.key]);
  const d = data.data;
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <Loading what="正在取图模型的结果" />;
  return (
    <div className="t-sections">
      <p className="trial">
        {"试验中。图模型和简单算法用同一批证据同步运行，结果只在这里显示，学生端和智能体都不用它。"}
        {`现在用了 ${d.data.students} 名学生的 ${d.data.evidence} 条证据，`}
        {d.data.updatedAt ? `上次全图更新在${fullTime(d.data.updatedAt)}。` : "还没有做过全图更新。"}
        {`先修边：${d.prereqStatus}。参数是经验值（先修影响 ${d.params.gamma}，碰巧做对 ${d.params.guess}，失误 ${d.params.slip}），还没有用数据估计。`}
      </p>
      <Section title="两个模型谁猜得准" note="每次学生第一次交一道新题之前，两个模型各自预测他能不能一次做对；这里按周统计。预测条数少的周只作参考。">
        {d.weekly.length === 0 ? <p className="note">还没有可以统计的预测。</p> : (
          <table className="table dense" style={{ maxWidth: 760 }}>
            <thead>
              <tr><th scope="col">从哪一周</th><th scope="col" className="num">预测条数</th><th scope="col" className="num">简单算法猜对</th><th scope="col" className="num">图模型猜对</th><th scope="col" className="num">简单算法 Brier</th><th scope="col" className="num">图模型 Brier</th></tr>
            </thead>
            <tbody>
              {d.weekly.map((w) => (
                <tr key={w.weekStart}><td>{w.weekStart} 起</td><td className="num">{w.n}</td><td className="num">{pct(w.simpleAccuracy)}</td><td className="num">{pct(w.graphAccuracy)}</td><td className="num">{w.simpleBrier.toFixed(3)}</td><td className="num">{w.graphBrier.toFixed(3)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="note">Brier 分数是预测概率和实际结果之差的平方的平均，越小越好。</p>
      </Section>
      <div className="t-cols">
        <Section title="全班共同的根因" note="学生反复失败时，图模型认为真正没掌握的先修技能；这里按涉及的学生人数排。">
          {d.commonRoots.length === 0 ? <p className="note">目前没有推断出共同的根因。</p> : (
            <table className="table dense">
              <thead><tr><th scope="col">先修技能</th><th scope="col" className="num">学生人数</th></tr></thead>
              <tbody>{d.commonRoots.map((c) => <tr key={c.code}><td className="strong">{c.name} <Code>{c.code}</Code></td><td className="num">{c.students}</td></tr>)}</tbody>
            </table>
          )}
        </Section>
        <Section title="逐个学生的根因推断">
          {d.rootCauses.length === 0 ? <p className="note">目前没有。</p> : (
            <table className="table dense">
              <thead><tr><th scope="col">学生</th><th scope="col">反复失败的技能</th><th scope="col">可能的根因</th></tr></thead>
              <tbody>
                {d.rootCauses.map((r, i) => (
                  <tr key={i}><td className="strong"><Link to={`/teacher/students/${r.userId}`}>{r.name}</Link></td><td>{r.skill.name}</td><td>{r.root.name} <Code>{r.p.toFixed(2)}</Code></td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
      <Section title="两个模型差别大的地方" note="同一个学生同一个技能，两个模型的估计相差 0.3 以上。">
        {d.gaps.length === 0 ? <p className="note">目前没有差别大的。</p> : (
          <div className="t-scroll">
            <table className="table dense">
              <thead><tr><th scope="col">学生</th><th scope="col">技能</th><th scope="col">学生端档位</th><th scope="col" className="num">简单算法</th><th scope="col" className="num">图模型</th></tr></thead>
              <tbody>
                {d.gaps.map((gap, i) => (
                  <tr key={i}><td className="strong"><Link to={`/teacher/students/${gap.userId}`}>{gap.name}</Link></td><td>{gap.skill.name} <Code>{gap.skill.code}</Code></td><td>{gap.level}</td><td className="num">{gap.simple.toFixed(2)}</td><td className="num">{gap.graph.toFixed(2)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="先修边" note="描述性统计，不是边的强度估计：两头都有数据的学生里，先修会和不会时，后继技能“会”的比例。先修会不会没有差别的边标出来，供修正知识点图参考。">
        <div className="t-scroll">
          <table className="table dense">
            <thead><tr><th scope="col">先修技能</th><th scope="col">后继技能</th><th scope="col" className="num">学生数</th><th scope="col" className="num">先修会时</th><th scope="col" className="num">先修不会时</th><th scope="col" /></tr></thead>
            <tbody>
              {d.edges.map((e) => (
                <tr key={e.parent + e.child}>
                  <td>{e.parentName} <Code>{e.parent}</Code></td><td>{e.childName} <Code>{e.child}</Code></td><td className="num">{e.n}</td>
                  <td className="num">{pct(e.withParent)}{e.nWith ? `（${e.nWith} 人）` : ""}</td>
                  <td className="num">{pct(e.withoutParent)}{e.nWithout ? `（${e.nWithout} 人）` : ""}</td>
                  <td>{e.doubtful && <span className="flag">可能不成立</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- 数据源
interface SourceRow {
  id: number; server: string; account: string; enabled: boolean; fromEnv: boolean; demo: boolean; classes: number;
  lastLogin: string | null; lastPoll: string | null; lastMeta: string | null; pollFailures: number; submissions: number;
  pendingDetails: number; backlog: Record<string, number>; requestOkRate: number | null;
}
interface SourcesData {
  sources: SourceRow[]; interval: number;
  candidates: { kind: string; name: string; description: string; nearest: string[]; time: string }[];
  libraries: { errors: number; skills: number; knowledge: number; errorVersion: string; taxonomyVersion: string };
}

function Sources() {
  const data = useLoad(() => get<SourcesData>("/api/teacher/sources"), [], 30000);
  const [note, setNote] = useState<{ id: number; text: string; bad: boolean } | null>(null);
  const d = data.data;
  const sync = async (id: number) => {
    try {
      await post(`/api/teacher/sources/${id}/sync`);
      setNote({ id, text: "已经通知同步程序，名单和作业会在几分钟内更新。", bad: false });
    } catch (err) {
      setNote({ id, text: (err as Error).message, bad: true });
    }
  };
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <Loading what="正在取数据源的状态" />;
  return (
    <div className="t-sections">
      <p className="note">系统对 XDOJ 只读：只访问写死的十几个查询接口，所有请求排队发送，两次请求至少相隔 {d.interval} 秒。</p>
      {d.sources.length === 0 && <p className="note">还没有数据源。</p>}
      {d.sources.map((src) => {
        const backlog = Object.values(src.backlog).reduce((a, b) => a + b, 0);
        return (
          <Section key={src.id} title={`${src.server}，账号 ${src.account}`}>
            <dl className="facts">
              <dt>状态</dt>
              <dd>{src.demo ? "演示数据，不连 XDOJ" : !src.enabled ? "没有启用" : src.pollFailures >= 3 ? `连不上，已经连续失败 ${src.pollFailures} 次` : "正常"}</dd>
              <dt>名下的班级</dt><dd>{src.classes} 个</dd>
              <dt>已同步的提交</dt><dd>{src.submissions} 条{src.pendingDetails > 0 ? `，其中 ${src.pendingDetails} 条的详情还在取` : ""}</dd>
              <dt>排队中的任务</dt><dd>{backlog} 个</dd>
              <dt>上次查到新提交</dt><dd>{src.lastPoll ? fullTime(src.lastPoll) : "还没有"}</dd>
              <dt>上次同步名单和作业</dt><dd>{src.lastMeta ? fullTime(src.lastMeta) : "还没有"}</dd>
              <dt>最近请求的成功率</dt><dd>{src.requestOkRate === null ? "还没有请求" : `${src.requestOkRate}%`}</dd>
              <dt>密码</dt><dd>{src.demo ? "不需要" : src.fromEnv ? "来自服务器的环境变量，不在库里" : "加密保存在服务器上"}</dd>
            </dl>
            <div>
              <button type="button" className="btn" disabled={!src.enabled} onClick={() => void sync(src.id)}>立即同步名单和作业</button>
            </div>
            {note?.id === src.id && <p className={note.bad ? "error-text" : "note"} role="status">{note.text}</p>}
          </Section>
        );
      })}
      <Section title="标注用的表">
        <dl className="facts">
          <dt>知识点</dt><dd>{d.libraries.knowledge} 个</dd>
          <dt>技能</dt><dd>{d.libraries.skills} 条，第 {d.libraries.taxonomyVersion} 版（骨架初稿）</dd>
          <dt>错误类型</dt><dd>{d.libraries.errors} 种，第 {d.libraries.errorVersion} 版</dd>
        </dl>
      </Section>
      {d.candidates.length > 0 && (
        <Section title="候选的新错误类型" note="自动标注时库里没有合适编码的情况。怎么入库还没有定，这里只列出来。">
          <table className="table dense">
            <thead><tr><th scope="col">候选名称</th><th scope="col">描述</th><th scope="col">最接近的现有编码</th><th scope="col">时间</th></tr></thead>
            <tbody>{d.candidates.map((c, i) => <tr key={i}><td className="strong">{c.name}</td><td>{c.description}</td><td><Code>{c.nearest.join("、")}</Code></td><td className="nowrap">{shortTime(c.time)}</td></tr>)}</tbody>
          </table>
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 路由
export default function Teacher() {
  return (
    <Routes>
      <Route path="class" element={<Page title="班级概况">{(cls) => cls && <ClassOverview cls={cls} />}</Page>} />
      <Route path="students" element={<Page title="学生">{(cls) => cls && <Students cls={cls} />}</Page>} />
      <Route path="students/:id" element={<StudentDetail />} />
      <Route path="stuck" element={<Page title="卡点">{(cls) => cls && <Stuck cls={cls} />}</Page>} />
      <Route path="graph" element={<Page title="图模型">{(cls) => cls && <Graph cls={cls} />}</Page>} />
      <Route path="sources" element={<Page title="数据源" withClass={false}>{() => <Sources />}</Page>} />
      <Route path="*" element={<Navigate to="/teacher/class" replace />} />
    </Routes>
  );
}
