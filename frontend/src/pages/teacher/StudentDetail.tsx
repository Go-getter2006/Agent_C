// 学生明细：这个学生卡在哪、怎么帮。分五个页签（docs/教师端结构.md）：
//   概况：几个数、知识点表（数据结构另有 C 语言基础和准备度）
//   求助：卡点、求助回合、理解评估（附原话）、对话行为
//   小题和复习：判断、定位、修改三维和作答依据、小题记录、复习安排
//   提交：提交记录、雷同的通过
//   模型细节（研究用）：多维画像的数值、技能的 v2 估计和区间、v1、图模型、根因；数据结构的联合状态
// 只有登录的这门课；数据结构这边不出现 C 语言的答题记录。
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Copy, FileDown, FlaskConical, Quote, Waypoints } from "lucide-react";
import { get } from "../../api";
import { Card, Cells, CodeBlock, Empty, Facts, LevelLegend, Loading, Problem, ResultPill, Tabs, levelClass } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime, futureDay, shortTime } from "../../lib/time";
import { Code, endReason, HELP, OUTCOME, REASON, RESOLUTION, ScoreBadge, TeacherPage, useClasses, type SkillRef } from "./common";
import { DiagnosisCard, type DiagnosisRow } from "./Diagnosis";

interface Detail {
  assignmentMastery: { score: number | null; quality: number | null; coveredTopics: number; scoredTopics: number; totalTopics: number; coveredProblems: number; totalProblems: number };
  student: { userId: string; name: string; class: { key: string; name: string } };
  facts: { score: number | null; passed: number; total: number; avgToPass: number | null; weak: number; stuck: number; lastActive: string | null };
  knowledge: { code: string; name: string; level: string; p: number; score: number; text: string; v1: number | null; v1Level: string | null }[];
  skills: (SkillRef & {
    p: number | null; level: string; v1: number | null; v1Level: string | null; yes: number; no: number; v2: number | null; v2Level: string; v2Interval: [number, number] | null;
    halfLife: number | null; discounted: number; graph: number | null; bigGap: boolean;
  })[];
  submissions: {
    submissionId: number; problemId: number; title: string; attemptNo: number; result: string; resultText: string;
    grid: boolean[] | null; time: string | null;
    labels: { code: string; name: string; skill: string; line: number | null; confidence: number; source: string }[];
  }[];
  moreSubmissions: number;
  stuck: (SkillRef & { kpCode: string; nth: number; stubborn: boolean; resolution: string; problem: string; problemId: number | null; error: string; errorName: string; time: string; passedAfter: boolean | null; triesAfter: number | null; givenCode: string })[];
  rounds: { problem: string; started: string; status: string; endReason: string; maxHelp: string; messages: number; quiz: boolean }[];
  quizzes: (SkillRef & { type: string; reason: string; state: string; confidence: string; outcome: string; correct: boolean | null; time: string })[];
  rootCauses: { skill: SkillRef; root: SkillRef; p: number }[];
  reviews: { code: string; name: string; kp: string; origin: string; state: string; halfLife: number; retention: number; reps: number; lapses: number; streak: number; due: string }[];
  copies: { problem: string; problemId: number; submissionId: number; matchedName: string; matchedUserId: string | null; share: number | null; time: string | null }[];
  readiness: { code: string; name: string; p: number }[];
  course: "C" | "D";
  foundation: { code: string; name: string; level: string; p: number | null; supports: { code: string; name: string }[] }[];
  joint: { code: string; name: string; pD: number | null; pC: number | null; eta: number | null; p: number | null; level: string; predicted: boolean }[];
  profile: ProfileRow[];
  assessments: Assessment[];
  dialogue: { total: number; constructive: number | null; answerSeeking: number | null; acts: { code: string; name: string; n: number }[] };
  graphUpdatedAt: string | null;
  diagnosis?: DiagnosisRow[];
}

interface ProfileRow {
  code: string; name: string; level: string; perf: number | null; perfLower: number | null; understanding: number | null;
  nAssess: number; independence: number | null; debugging: number | null; nEpisodes: number; retention: number | null;
  calibration: number | null; bias: number | null;
  prereq: number | null; graph: number | null; fused: number | null; flags: { code: string; text: string }[];
}

interface Assessment {
  id: number; skill: string; skillName: string; kp: string; level: number | null; levelName: string; rawLevel: number | null;
  ruleLevel: number | null; confidence: number; evidence: string[]; misconception: string; adjusted: string; source: string;
  problem: string; time: string; basis: "chat" | "code"; basisName: string;
}

type Tab = "overview" | "help" | "quiz" | "subs" | "model";

const DIMS: { key: keyof ProfileRow; name: string; tip: string }[] = [
  { key: "perf", name: "做题", tip: "掌握程度算法 v2：评测结果、提交次数、求助级别、雷同" },
  { key: "understanding", name: "理解", tip: "智能体按量规评 0~3 档：问过问题的看他自己说的话；没问过的看他在 OJ 上怎么改的代码" },
  { key: "independence", name: "独立", tip: "通过之前要了多少帮助：一次做对 1，给了示例代码才改对 0" },
  { key: "debugging", name: "改错", tip: "只看 OJ 上的提交：没过的题交了几次改对、同一个错误是不是反复出现、是不是急着重交" },
  { key: "retention", name: "保持", tip: "复习时还记得多少（半衰期回归）" },
  { key: "calibration", name: "自知", tip: "小题前选的把握和实际对错的吻合程度" },
  { key: "prereq", name: "前置知识", tip: "学它之前要先会的那些知识点掌握得怎么样（知识图上的前置关系）" },
  { key: "fused", name: "综合", tip: "几个维度按学到的权重融合（见研究 · 模型检验）" },
];

/** 一格：细条加数值。没有数据时是一条短横线。 */
function DimCell({ v }: { v: number | null }) {
  if (v === null) return <span className="muted">—</span>;
  return (
    <span className="dimcell">
      <span className="dimcell-track" aria-hidden="true"><span style={{ width: `${Math.round(v * 100)}%` }} /></span>
      <span className="num">{v.toFixed(2)}</span>
    </span>
  );
}

const LEVEL_PILL: Record<number, string> = { 0: "pill pill-fail", 1: "pill pill-warn", 2: "pill pill-pass", 3: "pill pill-pass" };

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

// ------------------------------------------------------------ 概况
function OverviewTab({ d }: { d: Detail }) {
  const flags = useMemo(() => Object.fromEntries(d.profile.map((p) => [p.code, p.flags])), [d.profile]);
  const f = d.facts;
  const coverage = d.assignmentMastery;
  return (
    <div className="stack">
      <Facts label="这个学生" items={[
        { label: "作业综合分", value: f.score === null ? "—" : <ScoreBadge score={f.score} />, note: `已练 ${coverage.coveredProblems}/${coverage.totalProblems} 题，涉及 ${coverage.coveredTopics}/${coverage.totalTopics} 个知识点；有证据的知识点均分 ${coverage.quality ?? "—"}` },
        { label: "已通过", value: `${f.passed} / ${f.total} 题`, note: "这个班布置的题" },
        { label: "平均几次改对", value: f.avgToPass === null ? "—" : `${f.avgToPass} 次`, note: "通过的题平均交到第几次" },
        { label: "待加强", value: `${f.weak} 个知识点` },
        { label: "最近提交", value: f.lastActive ? shortTime(f.lastActive) : "还没交过" },
      ]} />
      <Card title="知识点" sub="掌握分 0~100：一次做对、交几次改对、还没过，和对话里评的理解都算在里面" bodyClass="">
        {d.knowledge.length === 0 ? <Empty title="还没有练过的知识点" /> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">知识点</th><th scope="col">档位</th><th scope="col" className="num">掌握分</th><th scope="col">怎么样</th><th scope="col">提示</th></tr></thead>
              <tbody>
                {d.knowledge.map((k) => (
                  <tr key={k.code}>
                    <td className="nowrap"><span className="cell-title">{k.name}</span></td>
                    <td><LevelPill level={k.level} /></td>
                    <td className="num"><ScoreBadge score={k.score} /></td>
                    <td className="kp-text">{k.text}</td>
                    <td>{(flags[k.code] ?? []).map((x) => <span key={x.code} className="pill pill-warn flag-pill">{x.text}</span>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {d.course === "D" && (
        <div className="grid-2">
          <Card title="C 语言基础" sub="数据结构要用到的 C 语言知识点在 C 语言课上的掌握程度（不含 C 语言的答题记录）">
            {d.foundation.every((x) => x.p === null) ? <p className="note">在 C 语言里还没有这方面的证据。</p> : (
              <ul className="rv-list">
                {d.foundation.filter((x) => x.p !== null).sort((a, b) => (a.p ?? 0) - (b.p ?? 0)).slice(0, 10).map((x) => (
                  <li key={x.code}>
                    <div className="rv-top"><span>{x.name}</span><LevelPill level={x.level} /></div>
                    <span className="note">支撑：{x.supports.map((y) => y.name).join("、")}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="还没有证据的知识点" sub="按 C 语言里的前置知识推算的准备度，只作参考">
            {d.readiness.length === 0 ? <p className="note">没有。</p> : (
              <ul className="ready-list">
                {d.readiness.slice(0, 10).map((r) => (
                  <li key={r.code}>
                    <span>{r.name}</span>
                    <span className="ready-bar" aria-hidden="true"><span style={{ width: `${r.p * 100}%` }} /></span>
                    <span className="num">{r.p.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------ 求助
function HelpTab({ d }: { d: Detail }) {
  const given = d.stuck.find((sp) => sp.givenCode);
  return (
    <div className="stack">
      <Card title="卡点" sub="对话里卡住的地方：在哪道题、怎么解决的、之后过没过" bodyClass="">
        {d.stuck.length === 0 ? <Empty title="没有记录到卡点" /> : (
          <div className="table-scroll">
            <table className="table">
              <thead><tr><th scope="col">知识点 · 技能</th><th scope="col">第几次</th><th scope="col">在哪道题</th><th scope="col">错误</th><th scope="col">怎么解决的</th><th scope="col">之后</th><th scope="col">时间</th></tr></thead>
              <tbody>
                {d.stuck.map((sp, i) => (
                  <tr key={i}>
                    <td><span className="cell-title">{sp.kp}</span><span className="cell-sub">{sp.name}</span></td>
                    <td className="nowrap">第 {sp.nth} 次{sp.stubborn && <> <span className="pill pill-warn">顽固</span></>}</td>
                    <td>{sp.problem || <span className="muted">没绑定题</span>}</td>
                    <td>{sp.errorName}</td>
                    <td>{RESOLUTION[sp.resolution] ?? sp.resolution}</td>
                    <td>{sp.passedAfter === true ? <span className="pill pill-pass">通过了{sp.triesAfter ? ` · 又交 ${sp.triesAfter} 次` : ""}</span> : sp.passedAfter === false ? "还没通过" : <span className="muted">—</span>}</td>
                    <td className="nowrap muted">{fullTime(sp.time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {given && (
          <div className="card-body">
            <CodeBlock caption={`最近一次给的示例代码（${given.name}）`} lines={given.givenCode.split("\n")} copyable={false} />
          </div>
        )}
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
        <Card title="对话行为" sub="概念提问、带着想法问、解释自己的想法是建构性的；索要答案相反">
          {d.dialogue.total === 0 ? <p className="note">还没有对话。</p> : (
            <>
              <p className="note num">
                共 {d.dialogue.total} 条 · 建构性 {Math.round((d.dialogue.constructive ?? 0) * 100)}% · 索要答案 {Math.round((d.dialogue.answerSeeking ?? 0) * 100)}%
              </p>
              <ul className="act-list">
                {d.dialogue.acts.map((a) => (
                  <li key={a.code}>
                    <span>{a.name}</span>
                    <span className="dimcell-track" aria-hidden="true"><span style={{ width: `${Math.round((a.n / d.dialogue.total) * 100)}%` }} /></span>
                    <span className="num">{a.n}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
      <Card title="理解评估" sub="问过问题的看他在对话里说的话，没问过的看他在 OJ 上怎么改的代码；结论附原话或改动的代码行">
        {d.assessments.length === 0 ? <p className="note">还没有评过：问过智能体问题，或者有题没过、后来自己改对了，就会自动评。</p> : (
          <ul className="assess-list">
            {d.assessments.map((a) => (
              <li key={a.id}>
                <div className="rv-top">
                  <span><strong>{a.skillName}</strong> <span className="note">{a.problem ? `「${a.problem}」` : a.kp}</span></span>
                  <span className={a.level !== null ? LEVEL_PILL[a.level] : "pill"}>{a.levelName}</span>
                </div>
                <span className="note">{a.basisName}</span>
                {a.evidence.map((q, i) => a.basis === "code"
                  ? <code key={i} className="assess-code">{q}</code>
                  : <span key={i} className="assess-quote"><Quote size={12} aria-hidden="true" />{q}</span>)}
                {a.misconception && <span className="note">看出来的错误想法：{a.misconception}</span>}
                <span className="note">
                  {a.source === "llm" ? `大模型评的，把握 ${a.confidence.toFixed(2)}` : "规则评的"}
                  {a.ruleLevel !== null && a.source === "llm" ? ` · 规则给 ${a.ruleLevel} 档` : ""}
                  {a.adjusted ? ` · 从 ${a.rawLevel} 档下调：${a.adjusted}` : ""} · {shortTime(a.time)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------ 小题和复习
function QuizTab({ d }: { d: Detail }) {
  return (
    <div className="stack">
      <DiagnosisCard rows={d.diagnosis ?? []} />
      <div className="grid-2">
        <Card title="小题记录" bodyClass="">
          {d.quizzes.length === 0 ? <Empty title="还没有做过小题" /> : (
            <table className="table">
              <thead><tr><th scope="col">技能</th><th scope="col">什么时候出的</th><th scope="col">结果</th></tr></thead>
              <tbody>
                {d.quizzes.map((q, i) => (
                  <tr key={i}>
                    <td className="cell-title">{q.name}</td>
                    <td>{REASON[q.reason] ?? q.reason} · {shortTime(q.time)}</td>
                    <td>{q.correct === true ? <span className="pill pill-pass">{OUTCOME[q.outcome] ?? q.outcome}</span> : q.correct === false ? <span className="pill pill-fail">{OUTCOME[q.outcome] ?? q.outcome}</span> : OUTCOME[q.outcome] ?? q.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="复习安排" sub="间隔重复：记得牢的程度降到 75% 时到期">
          {d.reviews.length === 0 ? <p className="note">没有在复习的项。</p> : (
            <ul className="rv-list">
              {d.reviews.map((r) => (
                <li key={r.code}>
                  <div className="rv-top">
                    <strong>{r.name}</strong>
                    <span className={r.state === "graduated" ? "pill pill-pass" : "pill"}>{r.state === "graduated" ? "已巩固" : `下次 ${futureDay(r.due)}`}</span>
                  </div>
                  <span className="note">{r.origin} · 现在记得 {Math.round(r.retention * 100)}% · 复习 {r.reps} 次 · 忘了 {r.lapses} 次</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ 提交
function SubsTab({ d }: { d: Detail }) {
  return (
    <div className="stack">
      {d.copies.length > 0 && (
        <Card title="雷同的通过" sub="和班里更早的通过代码规范化后一样、写法少见、时间挨得近">
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
      <Card title="提交记录" sub={d.moreSubmissions ? `只列最近 80 条，更早的还有 ${d.moreSubmissions} 条` : undefined} bodyClass="">
        {d.submissions.length === 0 ? <Empty title="还没有提交" /> : (
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
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------ 模型细节（研究用）
function ModelTab({ d }: { d: Detail }) {
  const [allSkills, setAllSkills] = useState(false);
  const skills = useMemo(() => d.skills.filter((k) => allSkills || k.yes + k.no > 0), [d.skills, allSkills]);
  return (
    <div className="stack">
      <div className="trial">
        <FlaskConical size={18} aria-hidden="true" />
        <p>研究用：模型内部的估计。上课看“概况”就够了；几个模型谁更准，见研究 · 模型检验。</p>
      </div>
      <Card title="多维掌握画像" sub="每个知识点从几个方面看，再按学到的权重融合。0~1，越大越好；“—”是还没有这方面的数据" bodyClass="">
        {d.profile.length === 0 ? <Empty title="还没有可以画像的知识点" /> : (
          <div className="table-scroll">
            <table className="table table-dims">
              <thead>
                <tr>
                  <th scope="col">知识点</th><th scope="col">档位</th>
                  {DIMS.map((x) => <th key={x.key} scope="col" title={x.tip}>{x.name}</th>)}
                </tr>
              </thead>
              <tbody>
                {d.profile.map((k) => (
                  <tr key={k.code}>
                    <td><span className="cell-title">{k.name}</span> <Code>{k.code}</Code></td>
                    <td><LevelPill level={k.level} /></td>
                    {DIMS.map((x) => <td key={x.key}><DimCell v={k[x.key] as number | null} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {d.course === "D" && d.joint.length > 0 && (
        <Card title="联合状态" sub="数据结构每个知识点：自己的证据、C 语言基础给的先验，和两者各占多少（η）。做的题越多，η 越小" bodyClass="">
          <div className="table-scroll">
            <table className="table table-dims">
              <thead><tr><th scope="col">知识点</th><th scope="col">档位</th><th scope="col">数据结构自己的证据</th><th scope="col">C 语言基础</th><th scope="col" className="num">η</th><th scope="col">合起来</th></tr></thead>
              <tbody>
                {d.joint.map((j) => (
                  <tr key={j.code}>
                    <td><span className="cell-title">{j.name}</span> <Code>{j.code}</Code></td>
                    <td><LevelPill level={j.predicted ? "还没练到" : j.level} /></td>
                    <td><DimCell v={j.pD} /></td>
                    <td><DimCell v={j.pC} /></td>
                    <td className="num">{j.eta === null ? "—" : j.eta.toFixed(2)}</td>
                    <td><DimCell v={j.p} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      <Card title="技能明细" sub="v2 带 80% 区间（竖线是“已掌握”要求的下限）；v1、图模型作对照，和 v2 相差 0.3 以上的标出来"
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
                  <td className="num">{k.v1 === null ? "—" : k.v1.toFixed(2)}</td>
                  <td className="num">{k.graph === null ? "—" : k.graph.toFixed(2)}</td>
                  <td className="num nowrap">{k.yes.toFixed(1)} / {k.no.toFixed(1)}</td>
                  <td>{k.bigGap && <span className="pill pill-warn">差别大</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {d.rootCauses.length > 0 && (
        <Card title="根因推断" sub="图模型（试验中）认为：这些技能反复失败，真正没掌握的可能是它的前置知识">
          <ul className="rv-list">
            {d.rootCauses.map((r, i) => (
              <li key={i}><div className="rv-top"><span>{r.skill.name} ← <strong>{r.root.name}</strong></span><span className="num">{r.p.toFixed(2)}</span></div></li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

const TABS: Tab[] = ["overview", "help", "quiz", "subs", "model"];

export default function StudentDetail() {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const cls = params.get("classKey");
  const data = useLoad(() => get<Detail>(`/api/teacher/students/${id}${cls ? `?classKey=${encodeURIComponent(cls)}` : ""}`), [id, cls, tick], 0, false, `t:student:${id}:${cls ?? ""}`, String(tick));
  const d = data.data;
  const tab: Tab = TABS.includes(params.get("tab") as Tab) ? (params.get("tab") as Tab) : "overview";
  const setTab = (v: Tab) => {
    const next = new URLSearchParams(params);
    if (v === "overview") next.delete("tab");
    else next.set("tab", v);
    setParams(next, { replace: true });
  };

  return (
    <TeacherPage title={d ? d.student.name : "学生明细"} withClass={false}
      sub={d ? <>{d.student.userId} · {d.student.class.name}</> : undefined}
      crumb={<Link to="/teacher/students" className="crumb"><ArrowLeft size={15} aria-hidden="true" />学生列表</Link>}
      tools={d ? (
        <>
          <Link to={`/teacher/map?student=${d.student.userId}`} className="btn btn-sm"><Waypoints size={15} aria-hidden="true" />在知识图谱里看</Link>
          <Link to={`/teacher/logs?ids=${d.student.userId}`} className="btn btn-sm"><FileDown size={15} aria-hidden="true" />申请日志</Link>
        </>
      ) : null}>
      {() => (
        <>
          {data.error && !d && <Problem message={data.error} onRetry={data.reload} />}
          {!d && !data.error && <div className="card card-body"><Loading what="正在取这个学生的明细" lines={6} /></div>}
          {d && (
            <div className="stack">
              <Tabs label="学生明细" value={tab} onChange={setTab} options={[
                { value: "overview", label: "概况" },
                { value: "help", label: "求助", count: d.stuck.length + d.rounds.length },
                { value: "quiz", label: "小题和复习", count: d.quizzes.length },
                { value: "subs", label: "提交", count: d.submissions.length + d.moreSubmissions },
                { value: "model", label: "模型细节" },
              ]} />
              <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
                {tab === "overview" && <OverviewTab d={d} />}
                {tab === "help" && <HelpTab d={d} />}
                {tab === "quiz" && <QuizTab d={d} />}
                {tab === "subs" && <SubsTab d={d} />}
                {tab === "model" && <ModelTab d={d} />}
              </div>
            </div>
          )}
        </>
      )}
    </TeacherPage>
  );
}
