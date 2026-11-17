// 知识图谱页：上面一排筛选，中间是图（也可以切成表），点一个知识点在右边看详情。
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, BookOpen, CircleAlert, Link2, Network, Route, Search, Table2, X } from "lucide-react";
import { get } from "../../api";
import { StackedBar } from "../../components/charts";
import KnowledgeGraph, { type KMEdge, type KMNode, type KMTopic } from "../../components/KnowledgeGraph";
import { Card, LEVELS, Loading, Problem, Segmented, levelClass } from "../../components/ui";
import { useEscape, useLayout, useLoad } from "../../lib/hooks";
import { Code, type ClassInfo } from "./common";

interface MapData {
  class: { key: string; name: string; course: string };
  courses: { code: "C" | "D"; name: string }[];
  topics: KMTopic[];
  nodes: KMNode[];
  edges: KMEdge[];
  levels: string[];
  status: string;
  students: { userId: string; name: string }[];
}

interface KpDetail {
  code: string; name: string; course: string; topicName: string;
  skills: { code: string; name: string; kp: string; dist: number[] }[];
  weak: { userId: string; name: string; level: string; p: number; predicted: boolean }[];
  errors: { code: string; name: string; count: number }[];
  problems: { problemId: number; title: string; brief: string; primary: boolean }[];
  prereqs: { code: string; name: string; course: string; cross: boolean }[];
  next: { code: string; name: string; course: string; cross: boolean }[];
  levels: string[];
}

const LEVEL_COLORS = ["var(--lv-mastered)", "var(--lv-basic)", "var(--lv-weak)", "var(--lv-none)"];

function Panel({ cls, node, studentName, chain, onChain, onSelect, onClose }: {
  cls: ClassInfo; node: KMNode; studentName: string | null; chain: boolean; onChain: (v: boolean) => void;
  onSelect: (code: string) => void; onClose: () => void;
}) {
  const detail = useLoad(() => get<KpDetail>(`/api/teacher/classes/${cls.key}/knowledge-map/${node.code}`), [cls.key, node.code]);
  const d = detail.data;
  const total = node.dist.reduce((a, b) => a + b, 0);
  return (
    <aside className="kg-panel pop" aria-label={`知识点：${node.name}`}>
      <div className="kg-panel-head">
        <div>
          <span className="eyebrow">{node.course === "C" ? "C 语言" : "数据结构"} · {node.topicName}</span>
          <h2>{node.name}</h2>
          <Code>{node.code}</Code> <span className="note">· {node.skills} 条技能</span>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭详情"><X size={18} /></button>
      </div>
      <div className="kg-panel-body">
        {studentName && node.student && (
          <div className="kg-panel-student">
            <span className="note">{studentName}</span>
            <span className={`lvpill ${levelClass(node.student.level)}`}><i />{node.student.predicted ? "还没练到（推算）" : node.student.level}</span>
            <span className="num">{node.student.p.toFixed(2)}</span>
          </div>
        )}
        {node.course === "C" ? (
          node.covered ? (
            <section>
              <h3>全班分布</h3>
              <StackedBar counts={node.dist} colors={LEVEL_COLORS} names={LEVELS} total={total} />
              <div className="kg-dist-legend">
                {LEVELS.map((l, i) => (
                  <span key={l}><i className="swatch" style={{ background: LEVEL_COLORS[i] }} />{l}<b className="num">{node.dist[i]}</b></span>
                ))}
              </div>
              <p className="note">{node.withEvidence} 人有证据{node.mean !== null ? ` · 全班平均估计 ${node.mean.toFixed(2)}` : ""}</p>
            </section>
          ) : <p className="note">这个班的作业还没涉及这个知识点。</p>
        ) : (
          <section>
            <h3>准备度</h3>
            <p className="note">数据结构还没开课。全班平均准备度 {node.readiness === null ? "没有数据" : node.readiness.toFixed(2)}，是按 C 语言里的先修知识点推算的。</p>
          </section>
        )}

        <section>
          <label className="check">
            <input type="checkbox" checked={chain} onChange={(e) => onChain(e.target.checked)} />
            <Route size={15} aria-hidden="true" />在图上高亮整条先修链
          </label>
        </section>

        {detail.loading && !d && <Loading lines={4} />}
        {detail.error && <Problem message={detail.error} onRetry={detail.reload} />}
        {d && (
          <>
            {(d.prereqs.length > 0 || d.next.length > 0) && (
              <section>
                <h3>先修和后继</h3>
                <div className="kg-links">
                  {d.prereqs.map((p) => (
                    <button key={p.code} type="button" className={p.cross ? "kg-link kg-link-cross" : "kg-link"} onClick={() => onSelect(p.code)}>
                      <span className="eyebrow">先修{p.cross ? " · 跨课程" : ""}</span>{p.name}
                    </button>
                  ))}
                  {d.next.map((p) => (
                    <button key={p.code} type="button" className={p.cross ? "kg-link kg-link-cross" : "kg-link"} onClick={() => onSelect(p.code)}>
                      <span className="eyebrow">后继{p.cross ? " · 跨课程" : ""}</span>{p.name}<ArrowRight size={13} aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </section>
            )}
            {d.weak.length > 0 && (
              <section>
                <h3>需要留意的学生</h3>
                <ul className="kg-weak">
                  {d.weak.slice(0, 8).map((w) => (
                    <li key={w.userId}>
                      <Link to={`/teacher/students/${w.userId}`} className="link">{w.name}</Link>
                      <span className="note">{w.predicted ? "推算" : w.level} · {w.p.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {node.course === "C" && (
              <section>
                <h3>技能</h3>
                <ul className="kg-skills">
                  {d.skills.map((s) => (
                    <li key={s.code}>
                      <span>{s.name} <Code>{s.code}</Code></span>
                      <StackedBar counts={s.dist} colors={LEVEL_COLORS} names={LEVELS} thin />
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {d.errors.length > 0 && (
              <section>
                <h3>相关的错误</h3>
                <ul className="kg-errors">
                  {d.errors.map((e) => (
                    <li key={e.code}><span><CircleAlert size={13} aria-hidden="true" />{e.name} <Code>{e.code}</Code></span><span className="num">{e.count} 次</span></li>
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
          </>
        )}
      </div>
    </aside>
  );
}

export default function KnowledgeMap({ cls }: { cls: ClassInfo }) {
  const layoutKind = useLayout();
  const [params, setParams] = useSearchParams();
  const student = params.get("student") ?? "";
  const data = useLoad(() => get<MapData>(`/api/teacher/classes/${cls.key}/knowledge-map${student ? `?student=${student}` : ""}`), [cls.key, student]);
  const [course, setCourse] = useState<"all" | "C" | "D">(() => (window.innerWidth < 768 ? "C" : "all"));
  const [view, setView] = useState<"graph" | "table">("graph");
  const [showCross, setShowCross] = useState(true);
  const [chain, setChain] = useState(false);
  const [selected, setSelected] = useState<string | null>(params.get("kp"));
  const [search, setSearch] = useState("");
  const d = data.data;

  useEffect(() => setChain(false), [selected]);
  useEscape(() => setSelected(null), selected !== null);

  const courses = useMemo<("C" | "D")[]>(() => (course === "all" ? ["C", "D"] : [course]), [course]);
  const nodes = useMemo(() => (d ? d.nodes.filter((n) => courses.includes(n.course)) : []), [d, courses]);
  const courseNames = useMemo(() => Object.fromEntries((d?.courses ?? []).map((c) => [c.code, c.name])), [d]);
  const node = d?.nodes.find((n) => n.code === selected) ?? null;
  const studentName = d?.students.find((s) => s.userId === student)?.name ?? null;
  const cross = d ? d.edges.filter((e) => e.cross).length : 0;

  const setStudent = (id: string) => {
    const next = new URLSearchParams(params);
    if (id) next.set("student", id);
    else next.delete("student");
    setParams(next, { replace: true });
  };

  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在画知识图谱" lines={6} /></div>;

  return (
    <div className="kg-page">
      <div className="kg-toolbar card">
        <Segmented label="课程" value={course} onChange={(v) => { setCourse(v); setSelected(null); }}
          options={[{ value: "all", label: "两门课" }, { value: "C", label: "C 语言" }, { value: "D", label: "数据结构" }]} />
        <label className="kg-color">
          <span className="eyebrow">着色</span>
          <select className="field" value={student} onChange={(e) => setStudent(e.target.value)}>
            <option value="">全班的分布</option>
            {d.students.map((s) => <option key={s.userId} value={s.userId}>{s.name}（{s.userId}）</option>)}
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
        <label className="check">
          <input type="checkbox" checked={showCross} onChange={(e) => setShowCross(e.target.checked)} />
          <Link2 size={15} aria-hidden="true" />跨课程联系
        </label>
        <Segmented label="显示方式" value={view} onChange={setView}
          options={[{ value: "graph", label: <><Network size={15} aria-hidden="true" />图</> }, { value: "table", label: <><Table2 size={15} aria-hidden="true" />表</> }]} />
      </div>

      <div className="kg-legend legend">
        {student ? (
          <>
            {LEVELS.map((l) => <span key={l}><i className={`swatch sw-${levelClass(l).slice(3)}`} />{l}</span>)}
            <span><i className="swatch kg-sw-ds" />虚线框：数据结构，底下的条是推算的准备度</span>
          </>
        ) : (
          <>
            <span>每个知识点底下的条是全班的分布：</span>
            {LEVELS.map((l) => <span key={l}><i className={`swatch sw-${levelClass(l).slice(3)}`} />{l}</span>)}
            <span><b className="kg-badge-sample num">2</b>待加强的人数</span>
            <span><i className="swatch kg-sw-uncovered" />作业还没涉及</span>
          </>
        )}
        <span><i className="kg-line kg-line-cross" />跨课程的先修</span>
        <span><i className="kg-line kg-line-in" />先修</span>
        <span><i className="kg-line kg-line-out" />后继</span>
        <span className="kg-counts note num">{nodes.length} 个知识点 · {d.edges.length} 条先修，其中 {cross} 条跨课程 · 图：{d.status}</span>
      </div>

      {view === "graph" ? (
        <div className="kg-stage card">
          <KnowledgeGraph nodes={nodes} edges={d.edges} topics={d.topics} courses={courses} courseNames={courseNames}
            showCross={showCross} chain={chain} selected={selected} search={search} mode={student ? "student" : "class"}
            onSelect={setSelected} fitKey={`${course}-${layoutKind}`} />
          {node && courses.includes(node.course) && (
            <Panel cls={cls} node={node} studentName={studentName} chain={chain} onChain={setChain}
              onSelect={setSelected} onClose={() => setSelected(null)} />
          )}
          <p className="kg-hint note">拖动可以移动，Ctrl + 滚轮缩放。点一个知识点看详情。</p>
        </div>
      ) : (
        <Card bodyClass="" className="table-card">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">知识点</th><th scope="col">章</th>
                  {student ? <th scope="col">{studentName}</th> : LEVELS.map((l) => <th key={l} scope="col" className="num">{l}</th>)}
                  <th scope="col" className="num">{student ? "估计" : "平均估计 / 准备度"}</th>
                  <th scope="col">先修</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <tr key={n.code}>
                    <td><button type="button" className="link" onClick={() => { setView("graph"); setSelected(n.code); }}>{n.name}</button> <Code>{n.code}</Code></td>
                    <td className="muted nowrap">{n.topicName}</td>
                    {student ? (
                      <td>{n.student ? <span className={`lvpill ${levelClass(n.student.level)}`}><i />{n.student.predicted ? "推算" : n.student.level}</span> : "—"}</td>
                    ) : n.dist.map((v, i) => <td key={i} className="num">{n.course === "D" ? "" : v}</td>)}
                    <td className="num">{student ? (n.student ? n.student.p.toFixed(2) : "—") : n.course === "D" ? (n.readiness?.toFixed(2) ?? "—") : (n.mean?.toFixed(2) ?? "—")}</td>
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
