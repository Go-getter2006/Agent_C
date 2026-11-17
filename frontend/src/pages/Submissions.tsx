// 提交页：作业选择器加完整的提交表。
import { Fragment, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import DiffOverlay from "../components/Diff";
import { HomeworkPicker } from "../components/Lists";
import { Shell } from "../components/Shell";
import { Cells, CodeBlock, Loading, Problem } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { fullTime } from "../lib/time";
import { useHomeworks } from "../session";
import type { SubmissionView } from "../types";

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

  const problems = data.data?.problems ?? [];
  const tried = problems.filter((p) => p.submissions.length > 0);
  const untried = problems.filter((p) => p.submissions.length === 0);

  const ops = (p: { key: string; submissions: SubmissionView[] }, sub: SubmissionView, i: number) => (
    <span className="sub-ops">
      {sub.result !== "AC" && sub.result !== "PENDING" && (
        <button type="button" className="btn-text" onClick={() => navigate(`/chat/${p.key}?ask=${sub.key}`)}>问问为什么错</button>
      )}
      <button type="button" className="btn-text" aria-expanded={open === sub.key} onClick={() => void toggleSource(sub)}>
        {open === sub.key ? "收起代码" : "看代码"}
      </button>
      {i < p.submissions.length - 1 && (
        <button type="button" className="btn-text" onClick={() => openDiff(p.key, p.submissions[i + 1].key, sub.key)}>和上一次比</button>
      )}
    </span>
  );
  const sourceView = (sub: SubmissionView) =>
    open === sub.key && source?.key === sub.key ? (
      source.error ? <Problem message={source.error} /> : source.lines ? (
        <CodeBlock caption={`第 ${sub.attemptNo} 次提交的代码`} lines={source.lines} />
      ) : <Loading what="正在取代码" />
    ) : null;

  return (
    <Shell>
      <main className="page">
        <div className="page-head">
          <h1 className="sr-only">提交</h1>
          <div className="subs-picker">
            <HomeworkPicker homeworks={homeworks} current={current} onPick={pick} />
          </div>
        </div>
        <div className="page-body">
          {(loading || (data.loading && !data.data)) && <Loading what="正在取提交记录" />}
          {data.error && <Problem message={data.error} onRetry={data.reload} />}
          {data.data && tried.length === 0 && <p className="note">这个作业还没有提交过。在 XDOJ 上交一次，这里几秒钟后就能看到。</p>}

          {tried.length > 0 && (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">题目</th><th scope="col">提交</th><th scope="col">结果</th>
                    <th scope="col">测试点</th><th scope="col">时间</th><th scope="col"><span className="sr-only">操作</span></th>
                  </tr>
                </thead>
                <tbody>
                  {tried.map((p) => p.submissions.map((sub, i) => (
                    <Fragment key={sub.key}>
                      <tr className={i === 0 ? "group-start" : undefined}>
                        <td className="sub-problem">
                          {i === 0 && (
                            <Link to={`/chat/${p.key}`}><strong>{p.title}</strong><small>{p.brief}</small></Link>
                          )}
                        </td>
                        <td className="nowrap">第 {sub.attemptNo} 次</td>
                        <td className="nowrap">{sub.resultText}</td>
                        <td>{sub.grid ? <Cells grid={sub.grid} /> : <span className="note">{sub.result === "CE" ? "没有运行" : ""}</span>}</td>
                        <td className="nowrap note">{fullTime(sub.time)}</td>
                        <td>{ops(p, sub, i)}</td>
                      </tr>
                      {open === sub.key && (
                        <tr className="sub-source"><td /><td colSpan={5}>{sourceView(sub)}</td></tr>
                      )}
                    </Fragment>
                  )))}
                </tbody>
              </table>
            </div>
          )}

          {/* 手机：一道题一块 */}
          <div className="sub-cards">
            {tried.map((p) => (
              <section key={p.key} className="sub-card">
                <Link to={`/chat/${p.key}`} className="sub-card-head"><strong>{p.title}</strong><small>{p.brief}</small></Link>
                {p.submissions.map((sub, i) => (
                  <div key={sub.key} className="sub-item">
                    <div className="row-main">
                      <span>第 {sub.attemptNo} 次，{sub.resultText}</span>
                      {sub.grid && <Cells grid={sub.grid} />}
                    </div>
                    <span className="note">{fullTime(sub.time)}</span>
                    {ops(p, sub, i)}
                    {sourceView(sub)}
                  </div>
                ))}
              </section>
            ))}
          </div>

          {untried.length > 0 && (
            <section className="block" style={{ marginTop: 32 }}>
              <h2>还没提交的题</h2>
              <ul className="rows">
                {untried.map((p) => (
                  <li key={p.key}>
                    <div className="row-main">
                      <span className="row-title">{p.title}</span>
                      <Link className="btn-text" to={`/chat/${p.key}`}>去问问题</Link>
                    </div>
                    <span className="row-sub">{p.brief}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </main>
      {diff && diff.length === 3 && (
        <DiffOverlay problemKey={diff[0]} a={diff[1]} b={diff[2]} onClose={() => (window.history.length > 1 ? navigate(-1) : setSearch({}))} />
      )}
    </Shell>
  );
}
