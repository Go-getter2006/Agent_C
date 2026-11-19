// 复习提升（界面改造方案第 4 节）：进来就是一道备好的题，在这里独立作答。
// - 撤下原题、聊天和提交侧栏：题干、代码、作答在同一条阅读轴线上；
// - 中途离开，回来还是同一道、同一步；做完给完整的反馈，有下一道就接着做；
// - 今天没有可做的题：给最近一次的反馈和接下来的安排；题还在准备：说一声，过一会儿自己出现（不用点）。
// 到期的项数、备好的题、今天做完的分开说。
import { useState } from "react";
import { CircleCheck } from "lucide-react";
import { api } from "../api";
import Quiz from "../components/Quiz";
import ReviewDiscussion from "../components/ReviewDiscussion";
import Attempt from "../components/Attempt";
import { StudentShell } from "../components/Shell";
import { Card, Empty, Loading, Meter, Problem } from "../components/ui";
import { useLoad, useLayout } from "../lib/hooks";
import { futureDay, shortTime } from "../lib/time";
import { useStatus } from "../session";
import type { QuizView, ReviewItemView } from "../types";

function Plan({ items }: { items: ReviewItemView[] }) {
  return (
    <ul className="review-list">
      {items.map((item) => (
        <li key={item.id}>
          <span className="review-date">{item.dueNow ? "今天" : futureDay(item.due)}</span>
          <div className="review-row-main"><strong>{item.title}</strong><span className="note">{item.why}</span></div>
          <Meter level={item.memory.segments >= 3 ? "已掌握" : item.memory.segments === 2 ? "基本掌握" : "待加强"}
            segments={item.memory.segments} label={item.memory.text} />
        </li>
      ))}
    </ul>
  );
}

export default function Review() {
  const layout = useLayout();
  const data = useLoad(() => api.review(), [], 30000);
  const { reload: reloadStatus } = useStatus();
  const [shown, setShown] = useState<QuizView | null>(null);   // 正在做的这道（做的过程中不被定时刷新冲掉）
  const d = data.data;
  const [dismissed, setDismissed] = useState<number | undefined>();
  const current = d?.current?.quiz.attemptId === dismissed ? null : d?.current ?? null;
  // 做完的那道一直留着反馈，等他点“下一题”；定时刷新备好了下一道也不自己换掉
  const quiz = shown && (shown.state === "done" || shown.attemptId === current?.quiz.attemptId) ? shown : current?.quiz ?? shown;
  const finished = quiz?.state === "done";
  const waiting = !current && !!d && d.due.length > 0 && d.doneToday < d.dailyLimit;

  const update = (q: QuizView) => { setShown(q); if (q.state === "done") reloadStatus(); };
  const next = () => {
    setDismissed(quiz?.attemptId);
    setShown(null);
    data.reload();
    reloadStatus();
  };

  return (
    <StudentShell>
      <main className="page">
        <div className="page-inner review-axis">
          {data.loading && !d && <Loading what="正在准备复习题" />}
          {data.error && <Problem message={data.error} onRetry={data.reload} />}

          {quiz && (
            <section aria-label="当前小题">
              {current?.lead && !finished && <p className="review-lead">{current.lead}</p>}
              {quiz.protocol === 2 ? <Attempt key={quiz.attemptId} quiz={quiz} onChange={update}
                onNext={finished ? next : undefined} nextLabel={(d?.due.length ?? 0) > 1 ? "下一题" : "看复习安排"} /> : <>
                <Quiz key={quiz.attemptId} quiz={quiz} layout={layout} onChange={update} />
                {finished && <div className="attempt-confirm">
                  {quiz.result?.outcome !== "skipped" && <ReviewDiscussion id={quiz.attemptId} />}
                  <button className="btn" type="button" onClick={next}>继续</button>
                </div>}
              </>}
            </section>
          )}

          {d && !quiz && (
            <div className="stack">
              <div className="page-head">
                <div>
                  <h1>复习提升</h1>
                  <p className="page-sub">
                    到期 {d.due.length} 项 · 今天做完 {d.doneToday} 道（每天最多 {d.dailyLimit} 道）
                  </p>
                </div>
              </div>
              {waiting && (
                <div className="card card-body review-waiting" role="status">
                  <strong>题目正在准备</strong>
                  <span className="note">好了会自己出现，不用刷新。</span>
                </div>
              )}
              {d.last ? (
                <Card title="最近一次反馈" sub={`${d.last.title} · ${shortTime(d.last.time)}`}>
                  <p className="attempt-title attempt-title-sm">{d.last.headline}</p>
                  <p>{d.last.explanation}</p>
                  <ReviewDiscussion key={d.last.attemptId} id={d.last.attemptId} />
                </Card>
              ) : !waiting && (
                <div className="card">
                  <Empty icon={CircleCheck} title="今天没有要做的题">做错过、改过几次才通过的地方，快忘的时候会出现在这里。</Empty>
                </div>
              )}
              <Card title="接下来">
                {d.upcoming.length ? <Plan items={d.upcoming} /> : <p className="note">暂时没有别的安排。</p>}
              </Card>
              {d.graduated.length > 0 && (
                <Card title="已经巩固">
                  <ul className="review-list">
                    {d.graduated.map((item) => <li key={item.id}><strong>{item.title}</strong><span className="pill pill-pass">已巩固</span></li>)}
                  </ul>
                </Card>
              )}
            </div>
          )}
        </div>
      </main>
    </StudentShell>
  );
}
