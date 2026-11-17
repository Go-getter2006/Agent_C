// 提交页：一道题一张卡片，卡片里是这道题的每一次提交。
import { Fragment, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronUp, CircleCheck, Code2, GitCompare, Inbox, MessageCircle } from "lucide-react";
import { api } from "../api";
import DiffOverlay from "../components/Diff";
import { HomeworkPicker, homeworkSummary } from "../components/Lists";
import { StudentShell } from "../components/Shell";
import { Cells, CodeBlock, Empty, Loading, Problem, ResultPill } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { fullTime } from "../lib/time";
import { useHomeworks } from "../session";
import type { ProblemRow, SubmissionView } from "../types";

type Row = ProblemRow & { submissions: SubmissionView[] };

export default function Submissions() {
  const { homeworks, current, pick, loading } = useHomeworks();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const data = useLoad(() => (current ? api.homeworkSubmissions(current.key) : Promise.resolve(null)), [current?.key], 20000);
  const [open, setOpen] = useState<string | null>(null);
  const [source, setSource] = useState<{ key: string; lines?: string[]; error?: string } | null>(null);

  const toggleSource = async (sub: SubmissionView) => {
    if (open === sub.key) return setOpen(null);
    setOpen(sub.key);
    setSource({ key: sub.key });
    void api.event("open_submission", sub.key);
    try {
      const r = await api.source(sub.key);
      setSource({ key: sub.key, lines: r.lines });
    } catch (err) {
      setSource({ key: sub.key, error: (err as Error).message });
    }
  };

  const diff = search.get("diff")?.split("~") ?? null; // 题目~旧~新
  const openDiff = (problemKey: string, a: string, b: string) => {
    void api.event("open_diff", problemKey);
    setSearch({ diff: `${problemKey}~${a}~${b}` });
  };

  const problems: Row[] = data.data?.problems ?? [];
  const tried = problems.filter((p) => p.submissions.length > 0);
  const untried = problems.filter((p) => p.submissions.length === 0);
  const total = tried.reduce((n, p) => n + p.submissions.length, 0);

  const ops = (p: Row, sub: SubmissionView, i: number) => (
    <span className="sub-ops">
      {sub.result !== "AC" && sub.result !== "PENDING" && (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => navigate(`/chat/${p.key}?ask=${sub.key}`)}>
          <MessageCircle size={15} aria-hidden="true" />问问为什么错
        </button>
      )}
      <button type="button" className="btn btn-sm btn-ghost" aria-expanded={open === sub.key} onClick={() => void toggleSource(sub)}>
        <Code2 size={15} aria-hidden="true" />{open === sub.key ? "收起代码" : "看代码"}
        {open === sub.key ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </button>
      {i < p.submissions.length - 1 && (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => openDiff(p.key, p.submissions[i + 1].key, sub.key)}>
          <GitCompare size={15} aria-hidden="true" />和上一次比
        </button>
      )}
    </span>
  );
  const sourceView = (sub: SubmissionView) =>
    open === sub.key && source?.key === sub.key ? (
      <div className="sub-source">
        {source.error ? <Problem message={source.error} /> : source.lines ? (
          <CodeBlock caption={`第 ${sub.attemptNo} 次提交的代码`} lines={source.lines} />
        ) : <Loading what="正在取代码" lines={2} />}
      </div>
    ) : null;

  return (
    <StudentShell>
      <main className="page">
        <div className="page-inner">
          <div className="page-head">
            <div>
              <h1>提交记录</h1>
              <p className="page-sub">{current ? `${homeworkSummary(current)}${total ? `，一共交了 ${total} 次` : ""}` : "按作业看每一次提交"}</p>
            </div>
            <div className="page-tools subs-picker">
              <HomeworkPicker homeworks={homeworks} current={current} onPick={pick} compact />
            </div>
          </div>

          {(loading || (data.loading && !data.data)) && <div className="card card-body"><Loading what="正在取提交记录" /></div>}
          {data.error && <Problem message={data.error} onRetry={data.reload} />}
          {data.data && tried.length === 0 && (
            <div className="card">
              <Empty icon={Inbox} title="这个作业还没有提交过">在 XDOJ 上交一次，这里几秒钟后就能看到。</Empty>
            </div>
          )}

          <div className="stack">
            {tried.map((p) => (
              <section key={p.key} className="card sub-card" aria-labelledby={`sub-${p.key}`}>
                <div className="sub-card-head">
                  <div className="sub-card-title">
                    <h2 id={`sub-${p.key}`}>{p.title}</h2>
                    <span className="note">{p.brief}</span>
                  </div>
                  <div className="sub-card-tools">
                    {p.passed ? <span className="pill pill-pass"><CircleCheck aria-hidden="true" />已通过</span> : <span className="pill">还没通过</span>}
                    <Link to={`/chat/${p.key}`} className="btn btn-sm">去对话</Link>
                  </div>
                </div>
                <div className="sub-rows" role="list">
                  {p.submissions.map((sub, i) => (
                    <Fragment key={sub.key}>
                      <div className="sub-row" role="listitem">
                        <span className="sub-no num">第 {sub.attemptNo} 次</span>
                        <span className="sub-result"><ResultPill sub={sub} /></span>
                        <span className="sub-cells">
                          {sub.grid ? <Cells grid={sub.grid} /> : <span className="note">{sub.result === "CE" ? "没有运行" : ""}</span>}
                        </span>
                        <span className="sub-time note">{fullTime(sub.time)}</span>
                        {ops(p, sub, i)}
                      </div>
                      {sourceView(sub)}
                    </Fragment>
                  ))}
                </div>
              </section>
            ))}

            {untried.length > 0 && (
              <section className="card" aria-labelledby="untried">
                <div className="card-head"><h2 id="untried">还没提交的题</h2></div>
                <ul className="simple-rows">
                  {untried.map((p) => (
                    <li key={p.key}>
                      <span>
                        <strong>{p.title}</strong>
                        <span className="note">{p.brief}</span>
                      </span>
                      <Link className="btn btn-sm btn-ghost" to={`/chat/${p.key}`}>去问问题</Link>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </div>
      </main>
      {diff && diff.length === 3 && (
        <DiffOverlay problemKey={diff[0]} a={diff[1]} b={diff[2]} onClose={() => (window.history.length > 1 ? navigate(-1) : setSearch({}))} />
      )}
    </StudentShell>
  );
}
