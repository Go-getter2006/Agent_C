// 提交记录：这个班在 XDOJ 上的每一次提交，最近的在前。按学生、题目、结果筛；点一条就地展开：
// 左边他的代码（标出程序标的出错行）、标的错误；右边每组测试点的输入、标准输出、他的输出
// （没过的展开、高亮第一处不一样的行；通过的收起）。只给老师看，学生端看不到测试点。
import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, CircleCheck, CircleX, FileInput, FileOutput } from "lucide-react";
import { get } from "../../api";
import { Card, Cells, CodeBlock, Empty, Loading, Problem, ResultPill } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { fullTime } from "../../lib/time";
import { StaffLink, useClasses, type ClassInfo } from "./common";

interface Row {
  key: string; userId: string; name: string; problemId: number; title: string; attemptNo: number;
  result: string; resultText: string; grid: boolean[] | null; time: string | null;
  labels: { name: string; skill: string; line: number | null }[];
}
interface Page {
  items: Row[]; total: number; page: number; pageSize: number;
  students: { userId: string; name: string }[];
  problems: { problemId: number; title: string }[];
  results: { key: string; label: string }[];
}
interface Case {
  no: number; result: string; passed: boolean | null; timeMs: number | null;
  input: string | null; expected: string | null; output: string | null;
  firstDiffLine: number | null; truncated: boolean; binary: boolean;
}
interface Detail {
  lines: string[]; marks: number[]; compileOutput: string;
  labels: { name: string; skill: string; line: number | null }[];
  cases: Case[];
}

/** 输入、输出：和代码块一个样子（标题条、行号），mark 那一行高亮。 */
function IoBlock({ caption, text, mark, icon: Icon, note }: {
  caption: string; text: string | null; mark?: number | null; icon: typeof FileInput; note?: string;
}) {
  const lines = text === null ? [] : text.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n");
  return (
    <div className="code io-block">
      <div className="code-cap"><span><Icon aria-hidden="true" />{caption}</span>{note && <span className="note">{note}</span>}</div>
      {text === null ? <p className="io-empty">没取到</p> : text === "" ? <p className="io-empty">（空）</p> : (
        <div className="code-body" tabIndex={0} role="group" aria-label={caption}>
          <div className="code-lines">
            {lines.map((line, i) => (
              <div key={i} className={mark === i + 1 ? "code-line marked" : "code-line"}>
                <span className="code-no">{i + 1}</span>
                <span className="code-text">{line || " "}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CaseCard({ c }: { c: Case }) {
  const failed = c.passed === false;
  const [open, setOpen] = useState(failed);
  const head = (
    <>
      {failed ? <CircleX size={15} aria-hidden="true" /> : <CircleCheck size={15} aria-hidden="true" />}
      <strong>第 {c.no} 组</strong>
      <span>{c.result || (c.passed ? "通过" : "")}</span>
      {c.timeMs !== null && <span className="note">{c.timeMs} ms</span>}
      {failed && c.firstDiffLine && <span className="note">第 {c.firstDiffLine} 行开始不一样</span>}
    </>
  );
  return (
    <section className={failed ? "tcase tcase-fail" : "tcase"}>
      <button type="button" className="tcase-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={15} aria-hidden="true" /> : <ChevronRight size={15} aria-hidden="true" />}{head}
      </button>
      {open && (c.binary ? <p className="note">这组数据不是文字，不显示。</p> : (
        <div className="tcase-body">
          <IoBlock caption="输入" text={c.input} icon={FileInput} />
          <IoBlock caption="标准输出" text={c.expected} icon={FileOutput} mark={failed ? c.firstDiffLine : null} />
          {c.passed ? <p className="note">他的输出和标准输出一样。</p> : (
            <IoBlock caption="他的输出" text={c.output} icon={FileOutput} mark={c.firstDiffLine}
              note={c.truncated ? "太长，截断了" : undefined} />
          )}
        </div>
      ))}
    </section>
  );
}

function SubmissionDetail({ skey }: { skey: string }) {
  const data = useLoad(() => get<Detail>(`/api/teacher/submissions/${skey}`), [skey], 0, false, `t:sub:${skey}`);
  if (data.error) return <Problem message={data.error} onRetry={data.reload} />;
  const d = data.data;
  if (!d) return <Loading what="正在取代码" lines={3} />;
  return (
    <div className="tsub-detail">
      <div className="tsub-left">
        {d.labels.length > 0 && (
          <ul className="tsub-labels">
            {d.labels.map((l, i) => <li key={i}><strong>{l.name}</strong>{l.skill && <span className="note"> · {l.skill}</span>}{l.line ? <span className="note"> · 第 {l.line} 行</span> : null}</li>)}
          </ul>
        )}
        {d.lines.length > 0 ? <CodeBlock caption="他的代码" lines={d.lines} mark={d.marks} /> : <p className="note">代码还没取到。</p>}
      </div>
      <div className="tsub-right" aria-label="测试点">
        {d.compileOutput && <pre className="tsub-compile">{d.compileOutput}</pre>}
        {d.cases.map((c) => <CaseCard key={c.no} c={c} />)}
        {d.cases.length === 0 && !d.compileOutput && <p className="note">还没有测试点的结果。</p>}
      </div>
    </div>
  );
}

export default function Submissions({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();
  const [student, setStudent] = useState("");
  const [problem, setProblem] = useState("");
  const [result, setResult] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const query = new URLSearchParams({ ...(student && { student }), ...(problem && { problem }), ...(result && { result }), page: String(page) });
  const data = useLoad(() => get<Page>(`/api/teacher/classes/${cls.key}/submissions?${query}`),
    [cls.key, student, problem, result, page, tick], 0, true, `t:subs:${cls.key}:${query}`, String(tick));
  const d = data.data;
  const pick = (set: (v: string) => void) => (v: string) => { set(v); setPage(1); setOpen(null); };
  if (data.error && !d) return <Problem message={data.error} onRetry={data.reload} />;
  if (!d) return <div className="card card-body"><Loading what="正在取提交记录" lines={6} /></div>;
  const pages = Math.max(1, Math.ceil(d.total / d.pageSize));

  return (
    <Card bodyClass="" className="table-card">
      <div className="table-tools">
        <select className="field" value={student} onChange={(e) => pick(setStudent)(e.target.value)} aria-label="按学生看">
          <option value="">全部学生</option>
          {d.students.map((s) => <option key={s.userId} value={s.userId}>{s.name}（{s.userId}）</option>)}
        </select>
        <select className="field" value={problem} onChange={(e) => pick(setProblem)(e.target.value)} aria-label="按题目看">
          <option value="">全部题目</option>
          {d.problems.map((p) => <option key={p.problemId} value={p.problemId}>{p.title || p.problemId}</option>)}
        </select>
        <select className="field" value={result} onChange={(e) => pick(setResult)(e.target.value)} aria-label="按结果看">
          <option value="">全部结果</option>
          {d.results.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select>
        <span className="note">{d.total} 次提交</span>
      </div>
      {d.items.length === 0 ? <div className="card-body"><Empty title="没有符合的提交" /></div> : (
        <div className="table-scroll">
          <table className="table tsub-table">
            <thead>
              <tr><th scope="col"><span className="sr-only">展开</span></th><th scope="col">时间</th><th scope="col">学生</th><th scope="col">题目</th>
                <th scope="col">结果</th><th scope="col">测试点</th><th scope="col">程序标的错误</th></tr>
            </thead>
            <tbody>
              {d.items.map((x) => {
                const expanded = open === x.key;
                return (
                  <Fragment key={x.key}>
                    <tr className={expanded ? "tsub-row open" : "tsub-row"}>
                      <td>
                        <button type="button" className="btn btn-sm btn-ghost" aria-expanded={expanded} aria-label={expanded ? "收起" : "看代码"}
                          onClick={() => setOpen(expanded ? null : x.key)}>
                          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        </button>
                      </td>
                      <td className="note">{x.time ? fullTime(x.time) : ""}</td>
                      <td><StaffLink className="link" to={`/students/${x.userId}`}>{x.name || x.userId}</StaffLink></td>
                      <td>{x.title || x.problemId}<span className="note"> · 第 {x.attemptNo} 次</span></td>
                      <td><ResultPill sub={{ key: x.key, attemptNo: x.attemptNo, result: x.result, resultText: x.resultText, passed: null, total: null, grid: x.grid, time: x.time }} short /></td>
                      <td>{x.grid ? <Cells grid={x.grid} /> : null}</td>
                      <td>{x.labels.map((l) => l.name).join("、")}</td>
                    </tr>
                    {expanded && (
                      <tr className="tsub-expand"><td colSpan={7}><SubmissionDetail skey={x.key} /></td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="pager">
          <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => { setPage(page - 1); setOpen(null); }}>上一页</button>
          <span className="note">第 {page} / {pages} 页</span>
          <button type="button" className="btn btn-sm" disabled={page >= pages} onClick={() => { setPage(page + 1); setOpen(null); }}>下一页</button>
        </div>
      )}
    </Card>
  );
}
