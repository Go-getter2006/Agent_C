// 全部提交记录（从“我的学情”进来）：一道题一张卡片，最近交过的题在上，卡片里是这道题的每一次提交。
// 想问某一次提交：点“就这一版问”，回到作业引导里那道题，下一条消息带着这次提交（不替学生自动发问）。
import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, ChevronDown, ChevronUp, CircleCheck, Code2, Inbox, MessageCircle } from "lucide-react";
import { ApiError, api } from "../api";
import DiffOverlay from "../components/Diff";
import { ChangeNote, HomeworkPicker, homeworkSummary } from "../components/Lists";
import { StudentShell } from "../components/Shell";
import { Cells, CodeBlock, Empty, Loading, Problem, ResultPill } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { fullTime } from "../lib/time";
import { useHomeworks } from "../session";
import type { ProblemRow, SubmissionChange, SubmissionView } from "../types";

type Row = ProblemRow & { submissions: SubmissionView[]; changes: Record<string, SubmissionChange> };

export default function Submissions() {
  const { homeworks, current, pick, loading } = useHomeworks();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const data = useLoad(() => (current ? api.homeworkSubmissions(current.key) : Promise.resolve(null)), [current?.key], 15000, false, current ? `s:submissions:${current.key}` : undefined);
  const fresh = !!data.data && data.data.homework.key === current?.key;
  const [open, setOpen] = useState<string | null>(null);
  const [source, setSource] = useState<{ key: string; lines?: string[]; error?: string } | null>(null);

  const toggleSource = (sub: SubmissionView) => {
    if (open === sub.key) return setOpen(null);
    setOpen(sub.key);
    void api.event("open_submission", sub.key);
  };

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setSource({ key: open });
    const load = async () => {
      try {
        const r = await api.source(open);
        if (!cancelled) setSource({ key: open, lines: r.lines });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 409) timer = setTimeout(load, 2500);
        else setSource({ key: open, error: (err as Error).message });
      }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [open]);

  const diff = search.get("diff")?.split("~") ?? null; // 题目~旧~新
  const openDiff = (problemKey: string, a: string, b: string) => {
    void api.event("open_diff", problemKey);
    setSearch({ diff: `${problemKey}~${a}~${b}` });
  };

  const problems: Row[] = data.data?.problems ?? [];
  const latestTime = (p: Row) => Math.max(...p.submissions.map((s) => Date.parse(s.time ?? "") || 0));
  const tried = problems.filter((p) => p.submissions.length > 0).sort((a, b) => latestTime(b) - latestTime(a));
  const untried = problems.filter((p) => p.submissions.length === 0);
  const total = tried.reduce((n, p) => n + p.submissions.length, 0);

  const ops = (p: Row, sub: SubmissionView) => (
    <span className="sub-ops">
      <Link className="btn btn-sm btn-ghost" to={`/homework/${p.key}?sub=${sub.key}`}>
        <MessageCircle size={15} aria-hidden="true" />就这一版问
      </Link>
      <button type="button" className="btn btn-sm btn-ghost" aria-expanded={open === sub.key} onClick={() => void toggleSource(sub)}>
        <Code2 size={15} aria-hidden="true" />{open === sub.key ? "收起代码" : "看代码"}
        {open === sub.key ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </button>
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
              <Link to="/progress" className="link back-link"><ArrowLeft size={15} aria-hidden="true" />我的学情</Link>
              <h1>全部提交记录</h1>
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

          <div className={fresh ? "stack fade-swap" : "stack fade-swap is-stale"}>
            {tried.map((p) => (
              <section key={p.key} className="card sub-card" aria-labelledby={`sub-${p.key}`}>
                <div className="sub-card-head">
                  <div className="sub-card-title">
                    <h2 id={`sub-${p.key}`}>{p.title}</h2>
                    <span className="note">{p.brief}</span>
                  </div>
                  <div className="sub-card-tools">
                    {p.passed ? <span className="pill pill-pass"><CircleCheck aria-hidden="true" />已通过</span> : <span className="pill">还没通过</span>}
                    <Link to={`/homework/${p.key}`} className="btn btn-sm">回到这道题</Link>
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
                        {ops(p, sub)}
                      </div>
                      {i < p.submissions.length - 1 && (
                        <div className="sub-change">
                          <ChangeNote change={p.changes?.[sub.key]} onDiff={() => openDiff(p.key, p.submissions[i + 1].key, sub.key)} />
                        </div>
                      )}
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
                      <Link className="btn btn-sm btn-ghost" to={`/homework/${p.key}`}>去问问题</Link>
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
