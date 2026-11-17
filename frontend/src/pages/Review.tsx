// 复习页：卡过的地方，在快忘的时候做一道小题回忆一下。
// 学生看到的是“记得多牢”的文字和三段条、下次什么时候、还差几次巩固；不显示概率和半衰期。
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Award, CircleCheck, Clock, Play, RotateCcw } from "lucide-react";
import { api } from "../api";
import Quiz from "../components/Quiz";
import { StudentShell } from "../components/Shell";
import { Card, Empty, Facts, Loading, Meter, Problem } from "../components/ui";
import { useLayout, useLoad } from "../lib/hooks";
import { futureDay } from "../lib/time";
import { useStatus } from "../session";
import type { ReviewCurrent, ReviewItemView } from "../types";

/** 记得多牢：三段条用掌握档位的颜色（牢 = 深蓝，模糊 = 浅蓝，快忘了 = 红），旁边一定有文字。 */
function Memory({ item }: { item: ReviewItemView }) {
  const s = item.memory.segments;
  return <Meter level={s >= 3 ? "已掌握" : s === 2 ? "基本掌握" : "待加强"} segments={s} label={item.memory.text} />;
}

function Streak({ item }: { item: ReviewItemView }) {
  const need = item.streak + item.toGraduate;
  return (
    <span className="streak" aria-label={item.state === "graduated" ? "已经巩固" : `连续答对 ${item.streak} 次，再答对 ${item.toGraduate} 次就算巩固`}>
      {Array.from({ length: need }, (_, i) => <i key={i} className={i < item.streak ? "on" : undefined} />)}
    </span>
  );
}

function Origin({ item }: { item: ReviewItemView }) {
  if (!item.origin) return <span className="note">{item.why}</span>;
  return (
    <span className="note">
      {item.why}，在 <Link to={`/chat/${item.origin.key}`} className="link">{item.origin.title}</Link>
    </span>
  );
}

export default function Review() {
  const layout = useLayout();
  const data = useLoad(() => api.review(), [], 60000);
  const { reload: reloadStatus } = useStatus();
  const [active, setActive] = useState<ReviewCurrent | null>(null);
  const [starting, setStarting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const d = data.data;

  // 上次出了题没做完：接着做
  useEffect(() => {
    if (d?.current && (!active || active.itemId !== d.current.itemId) && active?.quiz.state !== "done") setActive(d.current);
  }, [d?.current]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async (id: number) => {
    setStarting(id);
    setError(null);
    try {
      setActive(await api.startReview(id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(null);
    }
  };

  const limitReached = d ? d.doneToday >= d.dailyLimit : false;
  const due = d?.due ?? [];
  const next = due.find((x) => x.id !== active?.itemId) ?? null;
  const activeItem = active ? due.find((x) => x.id === active.itemId) ?? d?.upcoming.find((x) => x.id === active.itemId) ?? null : null;

  return (
    <StudentShell>
      <main className="page">
        <div className="page-inner">
          <div className="page-head">
            <div>
              <h1>复习</h1>
              <p className="page-sub">卡过的地方，过几天用一道小题回忆一下。记住了，下次间隔就拉长；连续答对三次就算巩固。</p>
            </div>
          </div>

          {data.loading && !d && <div className="card card-body"><Loading what="正在取复习计划" /></div>}
          {data.error && <Problem message={data.error} onRetry={data.reload} />}

          {d && (
            <div className="stack">
              <Facts label="复习情况" items={[
                { label: "今天该复习", value: `${due.length} 项`, note: limitReached ? "今天已经做了两道，剩下的明天再来" : `今天还能做 ${Math.max(0, d.dailyLimit - d.doneToday)} 道，每天最多两道` },
                { label: "正在复习", value: `${d.active} 项`, note: "按忘得快慢安排间隔" },
                { label: "已经巩固", value: `${d.graduated.length} 项`, note: "连续答对三次、间隔够长" },
              ]} />

              <div className="grid-main">
                <div className="stack">
                  {active && (
                    <Card title={activeItem ? `复习：${activeItem.title}` : "复习"} sub={activeItem?.detail} className="review-card"
                      action={activeItem ? <Memory item={activeItem} /> : null}>
                      <p className="review-lead">{active.lead}</p>
                      <Quiz quiz={active.quiz} layout={layout}
                        onChange={(q) => {
                          setActive({ ...active, quiz: q });
                          if (q.state === "done") {
                            data.reload();
                            reloadStatus();
                          }
                        }} />
                      {active.quiz.state === "done" && (
                        <div className="review-after">
                          <span className="note">这一项的下次复习时间已经按这次的结果调整好了。</span>
                          <button type="button" className="btn" onClick={() => setActive(null)}>
                            {next && !limitReached ? "做下一项" : "好了"}
                          </button>
                        </div>
                      )}
                    </Card>
                  )}

                  {!active && next && (
                    <Card title="现在该复习的" className="review-card">
                      <div className="review-due">
                        <span className="review-due-icon"><RotateCcw size={20} aria-hidden="true" /></span>
                        <div className="review-due-main">
                          <strong>{next.title}</strong>
                          <span>{next.detail}</span>
                          <Origin item={next} />
                        </div>
                        <Memory item={next} />
                      </div>
                      {error && <p className="error-text" role="alert">{error}</p>}
                      <div className="review-actions">
                        <button type="button" className="btn btn-primary" disabled={starting !== null || limitReached} onClick={() => void start(next.id)}>
                          <Play size={15} aria-hidden="true" />{starting === next.id ? "正在出题" : "开始复习"}
                        </button>
                        <span className="note">{limitReached ? "今天已经做了两道，剩下的明天再来。" : "一道小题，一两分钟。题目是按你当时的代码和错误出的。"}</span>
                      </div>
                    </Card>
                  )}

                  {!active && !next && (
                    <div className="card">
                      <Empty icon={CircleCheck} title="今天没有要复习的">
                        {d.upcoming.length ? `下一项在${futureDay(d.upcoming[0].due)}。` : "卡住过的地方会自动加进来。"}
                      </Empty>
                    </div>
                  )}

                  {due.length > 1 && (
                    <Card title="今天还有" sub="按记得的牢不牢排，最快忘的在前面">
                      <ul className="review-list">
                        {due.filter((x) => x.id !== active?.itemId && x.id !== next?.id).map((x) => (
                          <li key={x.id}>
                            <div className="review-row-main"><strong>{x.title}</strong><span className="note">{x.detail}</span></div>
                            <Memory item={x} />
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                </div>

                <div className="stack">
                  <Card title="接下来" sub="到了日子才会提醒，不用记">
                    {d.upcoming.length === 0 ? <p className="note">没有排着的复习。</p> : (
                      <ul className="review-list">
                        {d.upcoming.map((x) => (
                          <li key={x.id}>
                            <span className="review-date"><Clock size={14} aria-hidden="true" />{futureDay(x.due)}</span>
                            <div className="review-row-main">
                              <strong>{x.title}</strong>
                              <Origin item={x} />
                            </div>
                            <Streak item={x} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>
                  {d.graduated.length > 0 && (
                    <Card title="已经巩固">
                      <ul className="review-list">
                        {d.graduated.map((x) => (
                          <li key={x.id}>
                            <div className="review-row-main"><strong>{x.title}</strong><span className="note">{x.detail}</span></div>
                            <span className="pill pill-pass"><Award aria-hidden="true" />巩固了</span>
                          </li>
                        ))}
                      </ul>
                    </Card>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </StudentShell>
  );
}
