// 学情页：一页四块。全部用列表和档位条；不显示概率、排名、细技能。
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Shell } from "../components/Shell";
import { Loading, Meter, Problem } from "../components/ui";
import { useLoad } from "../lib/hooks";
import { deadline, shortTime } from "../lib/time";
import { useSession } from "../session";

const STATUS_CELL: Record<string, string> = { 已改正: "cell cell-pass", 改正后又出现: "cell cell-fail", 还没解决: "cell cell-fail" };

export default function Progress() {
  const { me, logout } = useSession();
  const navigate = useNavigate();
  const progress = useLoad(() => api.progress(), [], 60000);
  const [starting, setStarting] = useState<number | null>(null);
  const [quizError, setQuizError] = useState<string | null>(null);
  const p = progress.data;

  const startQuiz = async (id: number) => {
    setStarting(id);
    setQuizError(null);
    try {
      await api.startRevisit(id);
      navigate("/chat/general");
    } catch (err) {
      setQuizError((err as Error).message);
    } finally {
      setStarting(null);
    }
  };
  const practice = (key: string) => {
    void api.event("practice", key);
    navigate(`/chat/${key}`);
  };

  return (
    <Shell>
      <main className="page">
        <div className="page-head">
          <h1>{me?.name ? `${me.name}的学情` : "学情"}</h1>
          <button type="button" className="btn-text" onClick={() => void logout()}>退出登录</button>
        </div>
        <div className="page-body">
          {progress.loading && !p && <Loading what="正在整理学情" />}
          {progress.error && <Problem message={progress.error} onRetry={progress.reload} />}
          {p && (
            <div className="progress-grid">
              <div>
                <section className="block" aria-labelledby="b-next">
                  <h2 id="b-next">接下来做什么</h2>
                  {p.next.length === 0 && <p className="note">目前没有特别要补的，继续做作业里的题就好。</p>}
                  {quizError && <p className="error-text" role="alert">{quizError}</p>}
                  <div>
                    {p.next.map((item, i) => (
                      <div key={i} className="next-item">
                        {item.kind === "quiz" ? (
                          <>
                            <span className="row-title">{item.title}</span>
                            <span className="note">{item.reason}</span>
                            <button type="button" className="btn" disabled={starting !== null} onClick={() => void startQuiz(item.revisitId)}>
                              {starting === item.revisitId ? "正在出题" : "现在做这道小题"}
                            </button>
                          </>
                        ) : (
                          <>
                            <span className="row-title">{item.problem.title}<span className="note" style={{ fontWeight: 400 }}>（{item.problem.brief}）</span></span>
                            <span className="note">{item.reason}</span>
                            <button type="button" className="btn" onClick={() => practice(item.problem.key)}>打开这道题</button>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </section>

                <section className="block" aria-labelledby="b-kp">
                  <h2 id="b-kp">知识点掌握</h2>
                  {p.knowledge.length === 0 && <p className="note">还没有可以统计的题目。</p>}
                  {p.knowledge.map((group) => (
                    <div key={group.topic}>
                      <h3>{group.topic}</h3>
                      {group.items.map((kp) => (
                        <div key={kp.name} className="kp-row">
                          <span className="row-title">{kp.name}</span>
                          <Meter level={kp.level} segments={kp.segments} />
                          <span className="row-sub">
                            <span>{kp.basis}</span>
                            {kp.practice && (
                              <button type="button" className="btn-text" onClick={() => practice(kp.practice!.key)}
                                aria-label={`去练一题：${kp.practice.title}`}>
                                去练一题：{kp.practice.title}
                              </button>
                            )}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </section>
              </div>

              <div>
                <section className="block" aria-labelledby="b-err">
                  <h2 id="b-err">我的常见错误</h2>
                  <p className="note">最近 30 天</p>
                  {p.errors.length === 0 && <p className="note">最近 30 天没有记录到错误。</p>}
                  <ul className="rows">
                    {p.errors.map((e) => (
                      <li key={e.name}>
                        <div className="row-main">
                          <span className="row-title">{e.name}</span>
                          <span className="status-tag"><span className={STATUS_CELL[e.status] ?? "cell cell-none"} aria-hidden="true" />{e.status}</span>
                        </div>
                        <span className="row-sub">
                          <span>
                            {e.count} 次，最近一次在
                            <Link to={`/chat/${e.last.key}`}>{e.last.title}</Link>（{e.last.brief}），{shortTime(e.lastTime)}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>

                <section className="block" aria-labelledby="b-act">
                  <h2 id="b-act">做题情况</h2>
                  <ul className="rows">
                    {p.activity.homeworks.map((hw) => (
                      <li key={hw.key}>
                        <div className="row-main">
                          <span className="row-title">{hw.title}</span>
                          <span className="pcells" role="img" aria-label={`${hw.total} 题通过 ${hw.passed} 题`}>
                            {Array.from({ length: hw.total }, (_, i) => <span key={i} className={i < hw.passed ? "cell cell-pass" : "cell cell-none"} />)}
                          </span>
                        </div>
                        <span className="row-sub"><span>已通过 {hw.passed} 题，共 {hw.total} 题，{deadline(hw.endTime, hw.ended)}</span></span>
                      </li>
                    ))}
                  </ul>
                  <dl className="facts">
                    <dt>做过的题</dt><dd>{p.activity.tried} 题，其中 {p.activity.passed} 题已通过</dd>
                    <dt>第一次提交就通过</dt>
                    <dd>{p.activity.firstTryRate === null ? "还没有数据" : `${p.activity.firstTryPassed} 题，占 ${p.activity.firstTryRate}%`}</dd>
                    <dt>平均每题提交</dt><dd>{p.activity.avgSubmissions === null ? "还没有数据" : `${p.activity.avgSubmissions} 次`}</dd>
                  </dl>
                  <table className="table" style={{ maxWidth: 360 }}>
                    <thead><tr><th scope="col">按周</th><th scope="col" className="num">提交次数</th><th scope="col" className="num">新通过的题</th></tr></thead>
                    <tbody>
                      {p.activity.weeks.map((w) => (
                        <tr key={w.label}><td>{w.label}</td><td className="num">{w.submissions}</td><td className="num">{w.passed}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              </div>
            </div>
          )}
        </div>
      </main>
    </Shell>
  );
}
