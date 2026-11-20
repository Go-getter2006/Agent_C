// 知识图谱：哪些知识点没学好，谁卡在这里。上面一排筛选，中间是图（也可以切成表，表能按待加强、卡住、没来问排），
// 点一个知识点在右边看详情：全班、卡在这里的学生、没来问的、每个学生、小题三维、技能、常见错误、练习题、前置和后续。
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowRight, ArrowUpDown, BookOpen, CircleAlert, Link2, Network, Search, Table2, X } from "lucide-react";
import { get } from "../../api";
import { StackedBar } from "../../components/charts";
import KnowledgeGraph, { hasData, type KMEdge, type KMNode, type KMTopic } from "../../components/KnowledgeGraph";
import { Card, LEVELS, Loading, Problem, Segmented, levelClass } from "../../components/ui";
import { useEscape, useLayout, useLoad } from "../../lib/hooks";
import { Code, RESOLUTION, ScoreBadge, useClasses, type ClassInfo } from "./common";

interface MapData {
  class: { key: string; name: string; course: "C" | "D" };
  courses: { code: "C" | "D"; name: string }[];
  topics: KMTopic[];
  nodes: KMNode[];
  edges: KMEdge[];
  levels: string[];
  status: string;
  students: { userId: string; name: string }[];
}

interface StuckStudent {
  userId: string; name: string; times: number; rounds: number; stubborn: boolean; skills: string[];
  lastTime: string; lastProblem: string; resolution: string; passed: boolean | null; triesAfter: number | null;
}
interface SilentStudent { userId: string; name: string; problems: { title: string; problemId: number; fails: number }[] }

interface KpDetail {
  code: string; name: string; course: string; topicName: string; foundation: boolean;
  stuck: { students: StuckStudent[]; silent: SilentStudent[]; silentFails: number };
  skills: { code: string; name: string; kp: string; dist: number[] }[];
  weak: { userId: string; name: string; level: string; p: number; predicted: boolean }[];
  summary: string;   // 全班一句话
  // 小题首次独立作答的判断、定位、修改（全班、每个学生）；颜色仍表示原来的掌握档位
  diagnosis: { judge: Ratio; locate: Ratio; edit: Ratio; shown: number; why: Why } | null;
  studentDims: Record<string, { judge: Ratio; locate: Ratio; edit: Ratio; shown: number; why: Why }>;
  students: { userId: string; name: string; level: string; p: number | null; score: number | null; predicted: boolean; text: string }[];
  errors: { code: string; name: string; count: number }[];
  problems: { problemId: number; title: string; brief: string; primary: boolean }[];
  prereqs: { code: string; name: string; course: string; cross: boolean }[];
  next: { code: string; name: string; course: string; cross: boolean }[];
  levels: string[];
}

const LEVEL_COLORS = ["var(--lv-mastered)", "var(--lv-basic)", "var(--lv-weak)", "var(--lv-none)"];
type Ratio = { n: number; d: number } | null;
type Why = Partial<Record<"judgeBug" | "judgeOk" | "locate" | "edit", string>>;
/** 有数写几比几；没有数时写为什么（还没进到这一步）。 */
const frac = (r: Ratio, why?: string) => (r ? `${r.n}/${r.d}` : why ?? "—");

function Panel({ cls, node, home, studentId, studentName, onSelect, onClose }: {
  cls: ClassInfo; node: KMNode; home: "C" | "D"; studentId: string; studentName: string | null;
  onSelect: (code: string) => void; onClose: () => void;
}) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const detail = useLoad(() => get<KpDetail>(`/api/teacher/classes/${cls.key}/knowledge-map/${node.code}`), [cls.key, node.code, tick], 0, false, `t:node:${cls.key}:${node.code}`, String(tick));
  const d = detail.data;
  const total = node.dist.reduce((a, b) => a + b, 0);
  const foundation = node.course !== home;
  return (
    <aside className="kg-panel pop" aria-label={`知识点：${node.name}`}>
      <div className="kg-panel-head">
        <div>
          <span className="eyebrow">{foundation ? "C 语言基础" : node.course === "C" ? "C 语言" : "数据结构"} · {node.topicName}</span>
          <h2>{node.name}</h2>
          <Code>{node.code}</Code> <span className="note">· {node.skills} 条技能</span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭详情"><X size={18} /></button>
      </div>
      <div className="kg-panel-body">
        {studentName && node.student && (
          <section className="kg-panel-me">
            <div className="kg-panel-student">
              <span className="note">{studentName}</span>
              <span className={`lvpill ${levelClass(node.student.level)}`}><i />{node.student.predicted ? "还没练到（推算）" : node.student.level}</span>
              {node.student.score !== null ? <ScoreBadge score={node.student.score} /> : <span className="num">{node.student.p.toFixed(2)}</span>}
            </div>
            {node.student.text && <p className="kg-panel-text">{node.student.text}</p>}
          </section>
        )}
        <section>
          <h3>{foundation ? "在 C 语言里的掌握程度" : "全班"}</h3>
          {hasData(node) ? (
            <>
              <StackedBar counts={node.dist} colors={LEVEL_COLORS} names={LEVELS} total={total} />
              <div className="kg-dist-legend">
                {LEVELS.map((l, i) => (
                  <span key={l}><i className="swatch" style={{ background: LEVEL_COLORS[i] }} />{l}<b className="num">{node.dist[i]}</b></span>
                ))}
              </div>
              {d?.summary && <p className="kg-panel-text">{d.summary}</p>}
              {foundation && <p className="note">来自学生在 C 语言课上的练习，这里只看掌握程度，不列答题记录。</p>}
            </>
          ) : node.course === "D" && !foundation ? (
            <p className="note">还没有人有证据。全班平均准备度 {node.readiness === null ? "没有数据" : node.readiness.toFixed(2)}，是按 C 语言里的前置知识推算的。</p>
          ) : <p className="note">{foundation ? "学生在 C 语言里还没有这方面的证据。" : "这个班的作业还没涉及这个知识点。"}</p>}
        </section>

        {detail.loading && !d && <Loading lines={4} />}
        {detail.error && !d && <Problem message={detail.error} onRetry={detail.reload} />}
        {d && (
          <>
            {(d.stuck.students.length > 0 || d.stuck.silent.length > 0) && (
              <section>
                <h3>卡在这里</h3>
                {d.stuck.students.length > 0 && (
                  <ul className="kg-people">
                    {d.stuck.students.map((w) => (
                      <li key={w.userId} className={w.userId === studentId ? "kg-people-me" : undefined}>
                        <div className="kg-people-top">
                          <Link to={`/teacher/students/${w.userId}?tab=help`} className="link">{w.name}</Link>
                          <span className="note num">卡住 {w.rounds} 次</span>
                          {w.stubborn && <span className="pill pill-warn">顽固</span>}
                          {w.passed === true ? <span className="pill pill-pass">后来通过</span> : w.passed === false ? <span className="pill pill-fail">还没通过</span> : null}
                        </div>
                        <p className="kg-people-text">
                          最近一次{w.lastProblem ? `在「${w.lastProblem}」` : "没绑定题"}，{RESOLUTION[w.resolution] ?? w.resolution}；{w.skills.join("、")}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
                {d.stuck.silent.length > 0 && (
                  <>
                    <h4 className="kg-subhead">没来问（一道题没过的提交 ≥ {d.stuck.silentFails} 次，没求助）</h4>
                    <ul className="kg-people">
                      {d.stuck.silent.map((w) => (
                        <li key={w.userId} className={w.userId === studentId ? "kg-people-me" : undefined}>
                          <div className="kg-people-top"><Link to={`/teacher/students/${w.userId}?tab=subs`} className="link">{w.name}</Link></div>
                          <p className="kg-people-text">{w.problems.map((p) => `「${p.title || p.problemId}」没过 ${p.fails} 次`).join("、")}</p>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
            )}
            {d.students.length > 0 && (
              <section>
                <h3>每个学生</h3>
                <ul className="kg-people">
                  {d.students.map((w) => (
                    <li key={w.userId} className={w.userId === studentId ? "kg-people-me" : undefined}>
                      <div className="kg-people-top">
                        <Link to={`/teacher/students/${w.userId}`} className="link">{w.name}</Link>
                        <span className={`lvpill ${levelClass(w.level)}`}><i />{w.predicted ? "推算" : w.level}</span>
                        {w.score !== null ? <ScoreBadge score={w.score} /> : w.p !== null && <span className="note num">{w.p.toFixed(2)}</span>}
                      </div>
                      {w.text && <p className="kg-people-text">{w.text}</p>}
                      {d.studentDims?.[w.userId] && (
                        <p className="kg-people-text num">
                          小题：判断 {frac(d.studentDims[w.userId].judge)} · 定位 {frac(d.studentDims[w.userId].locate, d.studentDims[w.userId].why?.locate)} · 修改 {frac(d.studentDims[w.userId].edit, d.studentDims[w.userId].why?.edit)}
                          {" "}<Link to={`/teacher/students/${w.userId}?tab=quiz`} className="link">作答依据</Link>
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {d.diagnosis && d.diagnosis.shown > 0 && (
              <section>
                <h3>小题：判断、定位、修改</h3>
                <dl className="kg-dims">
                  <div><dt>判断</dt><dd className="num">{frac(d.diagnosis.judge)}</dd></div>
                  <div><dt>定位</dt><dd className="num">{frac(d.diagnosis.locate, d.diagnosis.why?.locate)}</dd></div>
                  <div><dt>修改</dt><dd className="num">{frac(d.diagnosis.edit, d.diagnosis.why?.edit)}</dd></div>
                </dl>
                <p className="note">全班首次独立作答；定位、修改的分母只算实际进入这一步的次数，修改算看提示之前交的第一稿</p>
              </section>
            )}
            {(hasData(node) || foundation) && (
              <section>
                <h3>技能</h3>
                <ul className="kg-skills">
                  {d.skills.map((s) => (
                    <li key={s.code}>
                      <span>{s.name}</span>
                      <StackedBar counts={s.dist} colors={LEVEL_COLORS} names={LEVELS} thin />
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {d.errors.length > 0 && (
              <section>
                <h3>常见的错误</h3>
                <ul className="kg-errors">
                  {d.errors.map((e) => (
                    <li key={e.code}><span><CircleAlert size={13} aria-hidden="true" />{e.name}</span><span className="num">{e.count} 次</span></li>
                  ))}
                </ul>
              </section>
            )}
            {d.problems.length > 0 && (
              <section>
                <h3>练这个知识点的题</h3>
                <ul className="kg-errors">
                  {d.problems.map((p) => (
                    <li key={p.problemId}><span><BookOpen size={13} aria-hidden="true" />{p.title} <Code>{p.problemId}</Code></span>{p.primary && <span className="pill">主要</span>}</li>
                  ))}
                </ul>
              </section>
            )}
            {(d.prereqs.length > 0 || d.next.length > 0) && (
              <section>
                <h3>前置知识和后续知识</h3>
                <div className="kg-links">
                  {d.prereqs.map((p) => (
                    <button key={p.code} type="button" className={p.cross ? "kg-link kg-link-cross" : "kg-link"} onClick={() => onSelect(p.code)}>
                      <span className="eyebrow">前置{p.cross ? " · 跨课程" : ""}</span>{p.name}
                    </button>
                  ))}
                  {d.next.map((p) => (
                    <button key={p.code} type="button" className={p.cross ? "kg-link kg-link-cross" : "kg-link"} onClick={() => onSelect(p.code)}>
                      <span className="eyebrow">后续{p.cross ? " · 跨课程" : ""}</span>{p.name}<ArrowRight size={13} aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

export default function KnowledgeMap({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();   // 数据变了才重取（common.tsx 的 useVersionTicks）
  const layoutKind = useLayout();
  const [params, setParams] = useSearchParams();
  const student = params.get("student") ?? "";
  const data = useLoad(() => get<MapData>(`/api/teacher/classes/${cls.key}/knowledge-map${student ? `?student=${student}` : ""}`), [cls.key, student, tick], 0, false, `t:map:${cls.key}:${student ?? ""}`, String(tick));
  const [scope, setScope] = useState<"home" | "all">("home");
  const view: "graph" | "table" = params.get("view") === "table" ? "table" : "graph";
  const sort = (["weak", "stuck", "silent"] as const).find((x) => x === params.get("sort")) ?? "topic";
  const [onlyCovered, setOnlyCovered] = useState(true);
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const setView = (v: "graph" | "table") => setParam("view", v === "table" ? "table" : "");
  const [showCross, setShowCross] = useState(true);
  const [selected, setSelected] = useState<string | null>(params.get("kp"));
  const [search, setSearch] = useState("");
  const [topic, setTopic] = useState("");
  const [shape, setShape] = useState({ hidden: 0, isolated: 0 });
  const onLayout = useCallback((v: { hidden: number; isolated: number }) => setShape(v), []);
  const d = data.data;

  useEscape(() => setSelected(null), selected !== null);

  const home: "C" | "D" = d?.class.course === "D" ? "D" : "C";
  const multi = (d?.courses.length ?? 0) > 1; // 数据结构的班：可以带上 C 语言基础
  const courses = useMemo<("C" | "D")[]>(() => (multi && scope === "all" ? ["C", "D"] : [home]), [multi, scope, home]);
  const nodes = useMemo(() => (d ? d.nodes.filter((n) => courses.includes(n.course)) : []), [d, courses]);
  const courseNames = useMemo(() => Object.fromEntries((d?.courses ?? []).map((c) => [c.code, c.name])), [d]);
  const node = d?.nodes.find((n) => n.code === selected) ?? null;
  const studentName = d?.students.find((s) => s.userId === student)?.name ?? null;
  // 只数这次看得到的边（只看数据结构时，跨课程的边不算）
  const shownEdges = useMemo(() => {
    const codes = new Set(nodes.map((n) => n.code));
    return (d?.edges ?? []).filter((e) => codes.has(e.from) && codes.has(e.to));
  }, [d, nodes]);
  const cross = shownEdges.filter((e) => e.cross).length;

  const setStudent = (id: string) => setParam("student", id);
  // 表：默认只看作业涉及的知识点；可以按待加强、卡住、没来问的人数排
  const anyCovered = nodes.some((n) => n.covered);
  const rows = useMemo(() => {
    const list = nodes.filter((n) => (!topic || n.topic === topic)
      && (!onlyCovered || !anyCovered || n.covered || (n.stuck ?? 0) + (n.silent ?? 0) > 0));
    const key = (n: KMNode) => (sort === "weak" ? n.dist[2] : sort === "stuck" ? n.stuck ?? 0 : sort === "silent" ? n.silent ?? 0 : 0);
    return sort === "topic" ? list : [...list].sort((a, b) => key(b) - key(a) || a.code.localeCompare(b.code));
  }, [nodes, onlyCovered, anyCovered, sort, topic]);

  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在画知识图谱" lines={6} /></div>;

  return (
    <div className="kg-page">
      <div className="kg-toolbar card">
        {multi && (
          <Segmented label="范围" value={scope} onChange={(v) => { setScope(v); setSelected(null); setTopic(""); }}
            options={[{ value: "home", label: "数据结构" }, { value: "all", label: "加上 C 语言基础" }]} />
        )}
        <label className="kg-color">
          <span className="eyebrow">着色</span>
          <select className="field" value={student} onChange={(e) => setStudent(e.target.value)}>
            <option value="">全班的分布</option>
            {d.students.map((s) => <option key={s.userId} value={s.userId}>{s.name}（{s.userId}）</option>)}
          </select>
        </label>
        <label className="kg-color kg-topic-pick">
          <span className="eyebrow">章</span>
          <select className="field" value={topic} onChange={(e) => setTopic(e.target.value)}>
            <option value="">全部</option>
            {d.topics.filter((t) => courses.includes(t.course)).map((t) => <option key={t.code} value={t.code}>{t.code} {t.name}</option>)}
          </select>
        </label>
        <label className="search kg-search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">找知识点</span>
          <input className="field" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="找知识点"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const hit = nodes.find((n) => n.name.includes(search.trim()));
                if (hit) setSelected(hit.code);
              }
            }} />
        </label>
        {multi && scope === "all" && (
          <label className="check">
            <input type="checkbox" checked={showCross} onChange={(e) => setShowCross(e.target.checked)} />
            <Link2 size={15} aria-hidden="true" />跨课程联系
          </label>
        )}
        <Segmented label="显示方式" value={view} onChange={setView}
          options={[{ value: "graph", label: <><Network size={15} aria-hidden="true" />图</> }, { value: "table", label: <><Table2 size={15} aria-hidden="true" />表</> }]} />
      </div>

      <nav className="kg-chapters" aria-label="章节导航">
        <button type="button" className="btn btn-sm" aria-pressed={!topic} onClick={() => { setTopic(""); setSelected(null); }}>课程概览</button>
        {d.topics.filter(t => courses.includes(t.course)).map(t => <button key={t.code} type="button"
          className="btn btn-sm" aria-pressed={topic === t.code} onClick={() => { setTopic(t.code); setSelected(null); }}>
          {t.name}<span className="note num">{nodes.filter(n => n.topic === t.code).length}</span>
        </button>)}
      </nav>
      <div className="kg-legend legend">
        {student ? (
          <>
            {LEVELS.map((l) => <span key={l}><i className={`swatch sw-${levelClass(l).slice(3)}`} />{l}</span>)}
            {home === "D" && <span><i className="swatch kg-sw-ds" />底下的细条：还没有证据时按 C 语言推算的准备度</span>}
          </>
        ) : (
          <>
            <span>{view === "graph" ? "每个知识点底下的条是全班的分布：" : "全班的分布："}</span>
            {LEVELS.map((l) => <span key={l}><i className={`swatch sw-${levelClass(l).slice(3)}`} />{l}</span>)}
            {view === "graph" && <span><b className="kg-badge-sample num">2</b>待加强的人数</span>}
            {view === "graph" && <span><i className="swatch kg-sw-uncovered" />作业还没涉及</span>}
          </>
        )}
        {view === "graph" && multi && scope === "all" && <span><i className="kg-line kg-line-cross" />跨课程的前置关系</span>}
        {view === "graph" && <span><i className="kg-line kg-line-in" />前置</span>}
        {view === "graph" && <span><i className="kg-line kg-line-out" />后续</span>}
        <span className="kg-counts note num">{nodes.length} 个知识点 · {shownEdges.length} 条前置{cross > 0 ? `，其中 ${cross} 条跨课程` : ""}{view === "graph" && shape.hidden > 0 ? `（${shape.hidden} 条能由别的路径推出来，图上没画）` : ""} · 图：{d.status}</span>
      </div>

      {view === "graph" ? (
        <div className="kg-stage card">
          <KnowledgeGraph nodes={nodes} edges={d.edges} topics={d.topics} courses={courses} courseNames={courseNames} home={home}
            showCross={showCross} selected={selected} search={search} topic={topic} mode={student ? "student" : "class"}
            onSelect={setSelected} onLayout={onLayout} fitKey={`${scope}-${layoutKind}-${showCross}`} />
          {node && courses.includes(node.course) && (
            <Panel cls={cls} node={node} home={home} studentId={student} studentName={studentName}
              onSelect={setSelected} onClose={() => setSelected(null)} />
          )}
          <p className="kg-hint note">从左到右是学习的先后。点一个知识点：整条前置链和后续链亮起来，右边看详情。拖动移动，Ctrl + 滚轮缩放。</p>
        </div>
      ) : (
        <Card bodyClass="" className="table-card">
          <div className="table-tools">
            {anyCovered && (
              <label className="check">
                <input type="checkbox" checked={onlyCovered} onChange={(e) => setOnlyCovered(e.target.checked)} />
                只看作业涉及的
              </label>
            )}
            <span className="note">{rows.length} 个知识点</span>
          </div>
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">知识点</th><th scope="col">章</th>
                  {student ? (
                    <><th scope="col">{studentName}</th><th scope="col" className="num">掌握分</th></>
                  ) : (
                    <>
                      <th scope="col">全班</th>
                      {([["weak", "待加强"], ["stuck", "卡住过"], ["silent", "没来问"]] as const).map(([k, label]) => (
                        <th key={k} scope="col" className="num" aria-sort={sort === k ? "descending" : undefined}>
                          <button type="button" onClick={() => setParam("sort", sort === k ? "" : k)}>
                            {label}{sort === k ? <ArrowDown aria-hidden="true" /> : <ArrowUpDown aria-hidden="true" className="sort-idle" />}
                          </button>
                        </th>
                      ))}
                      <th scope="col" className="num">平均掌握分</th>
                    </>
                  )}
                  <th scope="col">前置</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((n) => (
                  <tr key={n.code}>
                    <td><button type="button" className="link" onClick={() => { setView("graph"); setSelected(n.code); }}>{n.name}</button></td>
                    <td className="muted nowrap">{n.topicName}</td>
                    {student ? (
                      <>
                        <td>{n.student ? <span className={`lvpill ${levelClass(n.student.level)}`}><i />{n.student.predicted ? "推算" : n.student.level}</span> : "—"}</td>
                        <td className="num">{n.student?.score != null ? <ScoreBadge score={n.student.score} /> : "—"}</td>
                      </>
                    ) : (
                      <>
                        <td className="kg-table-dist">{hasData(n) ? <StackedBar counts={n.dist} colors={LEVEL_COLORS} names={LEVELS} thin /> : <span className="muted">{n.course === "D" && n.readiness !== null ? `准备度 ${n.readiness.toFixed(2)}` : "—"}</span>}</td>
                        <td className="num">{n.dist[2] ? <span className="pill pill-fail">{n.dist[2]}</span> : <span className="muted">0</span>}</td>
                        <td className="num">{n.stuck ? <span className="pill pill-warn">{n.stuck}</span> : <span className="muted">0</span>}</td>
                        <td className="num">{n.silent ? <span className="pill">{n.silent}</span> : <span className="muted">0</span>}</td>
                        <td className="num">{n.score !== null ? <ScoreBadge score={n.score} /> : "—"}</td>
                      </>
                    )}
                    <td className="muted">{d.edges.filter((e) => e.to === n.code).map((e) => d.nodes.find((x) => x.code === e.from)?.name).join("、")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
