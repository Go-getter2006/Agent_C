// 复习提升：和知识问答一样，左边是列表，右边是对话。
// - 左边：今天这道（有备好的题时）、复习安排、做完的复习题（最近的在上，每道是一段对话）。
// - 右边是一段对话：智能体先说为什么复习这里，接着是这道小题（在对话里独立作答），做完给反馈，
//   之后可以在下面就这道题提问。反馈之前输入框不能用（不提前给对错、错误行和改法）。
// - 中途离开，回来还是同一道、同一步；做完了点“下一题”接着做；题还在准备：说一声，好了自己出现（不用点）。
// - 复习安排里每一项都能点“去复习”：新开一段对话，智能体先说“正在出题”，题好了就在对话里作答（不等到期）。
import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowUp, CalendarClock, CircleCheck, List } from "lucide-react";
import { api } from "../api";
import Attempt from "../components/Attempt";
import Quiz from "../components/Quiz";
import { StudentShell } from "../components/Shell";
import { AgentMark } from "../components/Thread";
import { Card, Drawer, Empty, Inline, Loading, Problem } from "../components/ui";
import { useLayout, useLoad, type Layout } from "../lib/hooks";
import { dayLabel, futureDay } from "../lib/time";
import { useStatus } from "../session";
import type { QuizView, ReviewAttemptRow, ReviewItemView, ReviewPage, ReviewTurn } from "../types";

function Plan({ items, onGo }: { items: ReviewItemView[]; onGo: (id: number) => void }) {
  return (
    <ul className="review-list">
      {items.map((item) => (
        <li key={item.id}>
          <span className="review-date">{item.dueNow ? "今天" : futureDay(item.due)}</span>
          <div className="review-row-main"><strong>{item.title}</strong><span className="note">{item.why}</span></div>
          <button type="button" className="btn btn-sm review-go" onClick={() => onGo(item.id)}>
            {item.pending ? "继续" : "去复习"}
          </button>
        </li>
      ))}
    </ul>
  );
}

interface Requested { itemId: number; attemptId: number; title: string; done: boolean }

/** 点了“去复习”：新开一段对话，智能体先说正在出题，题好了就在这里作答。 */
function RequestChat({ itemId, title, layout, onReady, onAsked, onDone }: {
  itemId: number; title: string; layout: Layout; onReady: (quiz: QuizView) => void; onAsked: () => void; onDone: () => void;
}) {
  const [quiz, setQuiz] = useState<QuizView | null>(null);
  const [lead, setLead] = useState<string | undefined>();
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);
  useEffect(() => {
    let alive = true;
    setQuiz(null);
    setError("");
    api.requestReview(itemId)
      .then((r) => { if (!alive) return; setQuiz(r.quiz); setLead(r.lead); onReady(r.quiz); })
      .catch((e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [itemId, round]); // eslint-disable-line react-hooks/exhaustive-deps
  if (quiz) {
    return <ReviewChat quiz={quiz} lead={lead} layout={layout} onChange={(q) => { setQuiz(q); if (q.state === "done") onReady(q); }}
      onNext={onDone} nextLabel="看复习安排" onAsked={onAsked} />;
  }
  return (
    <div className="chat-scroll">
      <div className="chat-list" aria-live="polite">
        <div className="msg msg-student"><div className="msg-body"><div className="bubble"><p>复习「{title}」</p></div></div></div>
        <div className="msg msg-agent">
          <AgentMark />
          <div className="msg-body">
            <span className="msg-meta"><strong>智能体</strong></span>
            {error ? (
              <>
                <p>{error}</p>
                <div className="attempt-confirm"><button type="button" className="btn btn-sm" onClick={() => setRound((n) => n + 1)}>再试一次</button></div>
              </>
            ) : (
              <p className="review-making">正在出题<span className="typing" role="status" aria-label="正在出题"><i /><i /><i /></span></p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 左边的列表。 */
function ReviewList({ d, currentTitle, waiting, history, requested, selected, onOpen }: {
  d: ReviewPage | null; currentTitle: string | null; waiting: boolean; history: ReviewAttemptRow[];
  requested: Requested | null; selected: string; onOpen: (key: string) => void;
}) {
  const requestKey = requested ? `i${requested.itemId}` : "";
  return (
    <div className="topic-pane">
      <header className="topic-head">
        <h2>复习</h2>
        {d && <span className="note num">今天 {d.doneToday}/{d.dailyLimit}</span>}
      </header>
      <nav className="topic-list" aria-label="复习记录">
        {currentTitle && (
          <button type="button" className="topic-item" aria-current={selected === "now" ? "true" : undefined} onClick={() => onOpen("now")}>
            <strong>{currentTitle}</strong><small>今天 · 待完成</small>
          </button>
        )}
        {requested && !requested.done && (   // 点了“去复习”新开的这一段，做完以后就在下面的记录里
          <button type="button" className="topic-item" aria-current={selected === requestKey ? "true" : undefined} onClick={() => onOpen(requestKey)}>
            <strong>{requested.title}</strong><small>今天 · 待完成</small>
          </button>
        )}
        {!currentTitle && waiting && (
          <span className="topic-item topic-wait" role="status"><strong>题目正在准备</strong><small>好了会自己出现</small></span>
        )}
        <button type="button" className="topic-item" aria-current={selected === "plan" ? "true" : undefined} onClick={() => onOpen("plan")}>
          <strong><CalendarClock size={14} aria-hidden="true" /> 复习安排</strong>
          <small>{d ? `到期 ${d.due.length} 项 · 接下来 ${d.upcoming.length} 项` : "　"}</small>
        </button>
        {history.map((a) => (
          <button key={a.attemptId} type="button" className="topic-item"
            aria-current={selected === `a${a.attemptId}` || (selected === requestKey && requested?.attemptId === a.attemptId) ? "true" : undefined}
            onClick={() => onOpen(`a${a.attemptId}`)}>
            <strong>{a.title}</strong>
            <small>{dayLabel(a.time)} · {a.right ? "做对了" : "没做对"}{a.asked ? ` · 问了 ${a.asked} 次` : ""}</small>
          </button>
        ))}
      </nav>
    </div>
  );
}

const newRequestId = () => Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-");

/** 右边的一段对话：为什么复习这里、这道小题（就在对话里作答）、做完以后的一问一答。 */
function ReviewChat({ quiz, lead, layout, onChange, onNext, nextLabel, onAsked }: {
  quiz: QuizView; lead?: string; layout: Layout; onChange: (q: QuizView) => void;
  onNext?: () => void; nextLabel?: string; onAsked?: () => void;
}) {
  const done = quiz.state === "done" && quiz.result?.outcome !== "skipped";
  const [turns, setTurns] = useState<ReviewTurn[]>([]);
  const [text, setText] = useState("");
  const [asking, setAsking] = useState<string | null>(null);
  const [error, setError] = useState("");
  const token = useRef("");
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTurns([]);
    setError("");
    if (!done) return;
    let alive = true;
    api.reviewTurns(quiz.attemptId).then((r) => { if (alive) setTurns(r.turns); }).catch(() => undefined);
    return () => { alive = false; };
  }, [quiz.attemptId, done]);

  useEffect(() => {
    const el = scroller.current;
    if (el && (turns.length || asking)) el.scrollTop = el.scrollHeight;
  }, [turns.length, asking]);

  const send = async () => {
    const q = text.trim();
    if (!done || asking || !q) return;
    if (!token.current) token.current = newRequestId();
    setAsking(q);
    setError("");
    try {
      const r = await api.reviewAsk(quiz.attemptId, q, token.current);
      setTurns(r.turns);
      setText("");
      token.current = "";
      onAsked?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAsking(null);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <>
      <div className="chat-scroll" ref={scroller}>
        <div className="chat-list" aria-live="polite">
          {lead && (
            <div className="msg msg-agent">
              <AgentMark />
              <div className="msg-body"><span className="msg-meta"><strong>智能体</strong></span><p>{lead}</p></div>
            </div>
          )}
          <div className="msg msg-agent msg-wide">
            <AgentMark />
            <div className="msg-body">
              <span className="msg-meta"><strong>智能体</strong></span>
              {quiz.protocol === 2
                ? <Attempt key={quiz.attemptId} quiz={quiz} onChange={onChange} onNext={done || quiz.state === "done" ? onNext : undefined} nextLabel={nextLabel} />
                : <>
                  <Quiz key={quiz.attemptId} quiz={quiz} layout={layout} onChange={onChange} />
                  {quiz.state === "done" && onNext && <div className="attempt-confirm"><button className="btn" type="button" onClick={onNext}>{nextLabel ?? "继续"}</button></div>}
                </>}
            </div>
          </div>
          {turns.map((t) => (
            <Fragment key={t.id}>
              <div className="msg msg-student"><div className="msg-body"><div className="bubble"><p>{t.question}</p></div></div></div>
              <div className="msg msg-agent">
                <AgentMark />
                <div className="msg-body">
                  <span className="msg-meta"><strong>智能体</strong></span>
                  {t.answer.split("\n").filter((p) => p.trim()).map((p, i) => <p key={i}><Inline text={p} /></p>)}
                </div>
              </div>
            </Fragment>
          ))}
          {asking && (
            <>
              <div className="msg msg-student"><div className="msg-body"><div className="bubble"><p>{asking}</p></div></div></div>
              <div className="msg msg-agent">
                <AgentMark />
                <div className="msg-body">
                  <span className="msg-meta"><strong>智能体</strong></span>
                  <span className="typing" role="status" aria-label="正在输入"><i /><i /><i /></span>
                </div>
              </div>
            </>
          )}
          {error && <Problem message={error} onRetry={() => void send()} />}
        </div>
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <div className="composer-box">
          <label className="sr-only" htmlFor="composer-input">就这道题提问</label>
          <textarea id="composer-input" rows={1} value={text} maxLength={2000} disabled={!done || !!asking}
            placeholder={done ? "就这道题问…" : "做完这道题以后可以提问"}
            onChange={(e) => { token.current = ""; setText(e.target.value); }} onKeyDown={onKey} spellCheck={false} />
          <button type="submit" className="send" disabled={!done || !!asking || !text.trim()} aria-label="发送">
            <ArrowUp size={20} strokeWidth={2.2} />
          </button>
        </div>
      </form>
    </>
  );
}

/** 一道做完的复习题：题和反馈，接着可以问。 */
function HistoryChat({ id, layout, onAsked }: { id: number; layout: Layout; onAsked: () => void }) {
  const one = useLoad(() => api.reviewAttempt(id), [id], 0, false, `s:review-attempt:${id}`);
  if (one.error) return <div className="chat-scroll"><div className="chat-list"><Problem message={one.error} onRetry={one.reload} /></div></div>;
  if (!one.data) return <div className="chat-scroll"><div className="chat-list"><Loading what="正在取这道题" /></div></div>;
  return <ReviewChat quiz={one.data.quiz} layout={layout} onChange={() => undefined} onAsked={onAsked} />;
}

/** 复习安排：今天的情况、接下来、已经巩固。 */
function PlanView({ d, waiting, onGo }: { d: ReviewPage; waiting: boolean; onGo: (id: number) => void }) {
  return (
    <div className="chat-scroll">
      <div className="review-plan stack">
        <p className="page-sub">到期 {d.due.length} 项 · 今天做完 {d.doneToday} 道（每天最多 {d.dailyLimit} 道）</p>
        {waiting && (
          <div className="card card-body review-waiting" role="status">
            <strong>题目正在准备</strong>
            <span className="note">好了会自己出现，不用刷新。</span>
          </div>
        )}
        {!waiting && d.due.length === 0 && (
          <div className="card"><Empty icon={CircleCheck} title="今天没有要做的题">需要巩固或重新练习的知识点，会在间隔一段时间后出现在这里。</Empty></div>
        )}
        {d.due.length > 0 && <Card title="待复习"><Plan items={d.due} onGo={onGo} /></Card>}
        <Card title="接下来">
          {d.upcoming.length ? <Plan items={d.upcoming} onGo={onGo} /> : <p className="note">暂时没有别的安排。</p>}
        </Card>
        {d.graduated.length > 0 && (
          <Card title="已经巩固">
            <ul className="review-list">
              {d.graduated.map((item) => <li key={item.id}><strong>{item.title}</strong><span className="pill pill-pass">已巩固</span></li>)}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

export default function Review() {
  const { key: param } = useParams();
  const navigate = useNavigate();
  const layout = useLayout();
  const { reload: reloadStatus } = useStatus();
  const [poll, setPoll] = useState(0);
  const data = useLoad(() => api.review(), [], poll, false, "s:review");
  const history = useLoad(() => api.reviewAttempts(), [], 0, true, "s:review-attempts");
  const [shown, setShown] = useState<QuizView | null>(null);   // 正在做的这道（做的过程中不被刷新冲掉）
  const [dismissed, setDismissed] = useState<number | undefined>();
  const [drawer, setDrawer] = useState(false);
  const [nexting, setNexting] = useState(false);   // 点了“下一题”，新的还没取到
  const [requested, setRequested] = useState<Requested | null>(null);   // 点了“去复习”新开的那一段
  const d = data.data;
  useEffect(() => setNexting(false), [d]);
  const current = d?.current && d.current.quiz.attemptId !== dismissed ? d.current : null;
  // 做完的那道一直留着反馈，等他点“下一题”；刷新时备好了下一道也不自己换掉
  const quiz = shown && (shown.state === "done" || shown.attemptId === current?.quiz.attemptId) ? shown : current?.quiz ?? null;
  const waiting = !current && !!d && d.due.length > 0 && d.doneToday < d.dailyLimit;
  useEffect(() => setPoll(waiting ? 30000 : 0), [waiting]);   // 只在题还在准备时定时看一下

  const currentTitle = quiz ? (d?.due.find((x) => x.id === current?.itemId)?.title ?? "今天的复习") : null;
  const rows = (history.data?.attempts ?? []).filter((a) => a.attemptId !== quiz?.attemptId
    && !(requested && !requested.done && a.attemptId === requested.attemptId));
  const key = param ?? (d ? (quiz ? "now" : "plan") : "");

  // /review：有备好的题就进这道，没有就看复习安排
  useEffect(() => {
    if (!d || param) return;
    navigate(`/review/${quiz ? "now" : "plan"}`, { replace: true });
  }, [d, param]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (next: string) => { setDrawer(false); navigate(`/review/${next}`); };
  const update = (q: QuizView) => {
    setShown(q);
    if (d && current?.quiz.attemptId === q.attemptId) data.setData({ ...d, current: { ...current, quiz: q } });
    if (q.state === "done") { reloadStatus(); history.reload(); }
  };
  const next = () => {
    setDismissed(quiz?.attemptId);
    setShown(null);
    setNexting(true);
    data.reload();
    history.reload();
    reloadStatus();
  };

  const historyId = key.startsWith("a") ? Number(key.slice(1)) : null;
  const requestId = key.startsWith("i") ? Number(key.slice(1)) : null;   // 点了“去复习”的那一项
  const requestTitle = requestId !== null
    ? [...(d?.due ?? []), ...(d?.upcoming ?? [])].find((x) => x.id === requestId)?.title ?? "复习题" : null;
  const historyTitle = rows.find((a) => a.attemptId === historyId)?.title ?? (quiz?.attemptId === historyId ? currentTitle : null);
  const title = key === "now" ? currentTitle ?? "今天的复习" : key === "plan" ? "复习安排"
    : requestTitle ?? historyTitle ?? "复习题";
  const go = (id: number) => open(`i${id}`);
  const pane = <ReviewList d={d ?? null} currentTitle={currentTitle} waiting={waiting} history={rows}
    requested={requested && requested.attemptId !== quiz?.attemptId ? requested : null} selected={key} onOpen={open} />;

  let body;
  if (data.error && !d) body = <div className="chat-scroll"><div className="chat-list"><Problem message={data.error} onRetry={data.reload} /></div></div>;
  else if (!d || (key === "now" && !quiz && nexting)) body = <div className="chat-scroll"><div className="chat-list"><Loading what="正在准备复习题" /></div></div>;
  else if (key === "now" && quiz) {
    body = <ReviewChat quiz={quiz} lead={quiz.state !== "done" ? current?.lead : undefined} layout={layout} onChange={update}
      onNext={next} nextLabel={(d.due.length ?? 0) > 1 ? "下一题" : "看复习安排"} onAsked={history.reload} />;
  } else if (requestId !== null) {
    body = <RequestChat key={requestId} itemId={requestId} title={requestTitle ?? "复习题"} layout={layout}
      onReady={(q) => {
        setRequested({ itemId: requestId, attemptId: q.attemptId, title: requestTitle ?? "复习题", done: q.state === "done" });
        history.reload(); reloadStatus();
      }}
      onAsked={history.reload} onDone={() => { data.reload(); open("plan"); }} />;
  } else if (historyId !== null && quiz?.attemptId === historyId) {
    body = <ReviewChat quiz={quiz} layout={layout} onChange={update} onAsked={history.reload} />;
  } else if (historyId !== null) body = <HistoryChat key={historyId} id={historyId} layout={layout} onAsked={history.reload} />;
  else body = <PlanView d={d} waiting={waiting} onGo={go} />;

  // 点了“下一题”、今天的做完了：回到复习安排
  useEffect(() => {
    if (key === "now" && d && !quiz && !nexting) navigate("/review/plan", { replace: true });
  }, [key, d, quiz, nexting]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <StudentShell>
      <div className="wb wb-knowledge wb-review">
        <aside className="wb-list" aria-label="复习记录">{pane}</aside>
        <main className="wb-chat" id="chat">
          <header className="chat-head" hidden={!key}>
            <div className="chat-head-main"><h1>{title}</h1></div>
            <span className="chat-head-tools">
              <button type="button" className="btn btn-sm only-narrow" onClick={() => setDrawer(true)}>
                <List size={15} aria-hidden="true" />复习记录
              </button>
            </span>
          </header>
          {body}
        </main>
      </div>
      {drawer && <Drawer side="left" title="复习记录" onClose={() => setDrawer(false)}>{pane}</Drawer>}
    </StudentShell>
  );
}
