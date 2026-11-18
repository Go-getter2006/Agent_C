// 一条对话线程：消息、正在输入、求助按钮、输入栏。
// 学生只看到自然的回复、代码块和“正在输入”；没有步骤条、标签、分数。
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Braces, Brain, Lightbulb, MessageCircle, RotateCcw, X, type LucideIcon } from "lucide-react";
import { api } from "../api";
import type { Layout } from "../lib/hooks";
import { readDraft, readScroll, saveScroll, writeDraft } from "../lib/memory";
import { dayLabel, sameDay } from "../lib/time";
import type { HelpAction, Message, QuizView } from "../types";
import { useSession } from "../session";
import CourseScene from "./CourseScene";
import Attempt from "./Attempt";
import Quiz from "./Quiz";
import { CodeBlock, Inline, Loading, Problem } from "./ui";

const ACTION_LABELS: Record<HelpAction, string> = { think: "我想自己想想", hint: "再给点提示", tell: "直接讲给我" };
const ACTION_ICONS: Record<HelpAction, LucideIcon> = { think: Brain, hint: Lightbulb, tell: MessageCircle };

// 对话空着的时候：首页那个环的小图（C 语言是内存格子围成的 C，数据结构是链表和树），跟着光标轻轻转
function EmptyScene() {
  const { me } = useSession();
  return <CourseScene course={me?.course === "D" ? "D" : "C"} variant="mini" className="chat-empty-scene" />;
}

function AgentMark() {
  return <span className="brand-mark" aria-hidden="true"><Braces size={15} strokeWidth={2.4} /></span>;
}
interface Payload {
  text?: string;
  action?: HelpAction;
  submissionKey?: string;
  problemKey?: string;
}

/** 输入框上面的一枚标签：这条消息带着什么一起问（结合哪道题、针对哪次提交）。可以去掉。 */
export interface ThreadContext {
  label: string;
  problemKey?: string;
  submissionKey?: string;
  onClear: () => void;
}

interface Props {
  threadKey: string;
  layout: Layout;
  emptyTitle: string;
  emptyText: string;
  placeholder?: string;
  welcomeKey?: string | null;  // 未通过的最新提交：让智能体主动提问。
  context?: ThreadContext | null;
  contextOffer?: { label: string; onPick: () => void } | null;   // 还没带上时：可以点一下带上
  onThread?: (key: string) => void;   // 新建问答发出第一条以后，有了自己的编号
  onActivity?: () => void;     // 有新消息、小题有进展：让外面刷新列表和右栏
}

function looksLikeCode(text: string): boolean {
  return text.includes("\n") && /[;{}]|#include/.test(text);
}

export default function Thread({ threadKey, layout, emptyTitle, emptyText, placeholder, welcomeKey, context, contextOffer,
  onThread, onActivity }: Props) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [stale, setStale] = useState(false);   // 换了题、新的对话还没取到：先留着上一份（变淡），不闪一下
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<Payload | null>(null);   // 正在发的那一条
  const [failed, setFailed] = useState<{ payload: Payload; message: string } | null>(null);
  const [text, setTextRaw] = useState(() => readDraft(threadKey));
  const setText = useCallback((value: string) => {
    setTextRaw(value);
    writeDraft(keyRef.current, value);   // 没发出去的草稿按对话记在本机，换栏目再回来还在
  }, []);
  const scroller = useRef<HTMLDivElement>(null);
  const restoreScroll = useRef<number | null>(readScroll(threadKey));
  const created = useRef<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const keyRef = useRef(threadKey);
  keyRef.current = threadKey;
  const busy = pending !== null;
  const loadSeq = useRef(0);
  const interacting = useRef(false);
  const welcomed = useRef(new Set<string>());
  interacting.current = busy || !!text.trim();

  const load = useCallback((silent: boolean) => {
    const key = keyRef.current;
    const seq = ++loadSeq.current;
    api.thread(key)
      .then((r) => {
        if (key !== keyRef.current || seq !== loadSeq.current) return;
        setMessages((old) => (silent && old && old.length > r.messages.length ? old : r.messages));
        setStale(false);
        setLoadError(null);
      })
      .catch((err: Error) => {
        if (key === keyRef.current && !silent) setLoadError(err.message);
      });
  }, []);

  useEffect(() => {
    if (created.current === threadKey) {   // 新建的问答刚有了编号：消息已经在了，不重新取、不闪
      created.current = null;
      return;
    }
    setStale(true);
    setLoadError(null);
    setPending(null);
    setFailed(null);
    setTextRaw(readDraft(threadKey));
    restoreScroll.current = readScroll(threadKey);
    load(false);
  }, [threadKey, load]);

  // 定时看一眼（自动的）：提交没过时智能体可能主动先说一句；题目通过后可能追加一道小题
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !busy) load(true);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  useEffect(() => {
    if (!welcomeKey || !threadKey.startsWith("p") || messages === null || stale || interacting.current) return;
    const token = `${threadKey}:${welcomeKey}`;
    if (welcomed.current.has(token)) return;
    welcomed.current.add(token);
    const seq = ++loadSeq.current;
    void api.welcome(threadKey).then(r => {
      if (threadKey !== keyRef.current || seq !== loadSeq.current || interacting.current) return;
      setMessages(old => old && old.length > r.messages.length ? old : r.messages);
    }).catch(() => { welcomed.current.delete(token); });
  }, [welcomeKey, threadKey, messages, stale]); // 首次请求失败时，下次正常轮询可以自动重试。
  // 复习小题不再插进对话：在“复习提升”里做（以前对话里出过的题照样能看、能接着做）

  const send = useCallback(async (payload: Payload) => {
    const key = keyRef.current;
    setPending(payload);
    setFailed(null);
    let still = true;   // 发出去以后还在这个对话里（中途换了别的对话，结果就不管了）
    try {
      const r = await api.send(key, payload);
      still = key === keyRef.current;
      if (!still) return;
      loadSeq.current += 1;
      setMessages(r.messages);
      if (r.thread && r.thread !== key) {   // 新建问答：第一条发出去以后有了编号
        writeDraft(key, "");
        created.current = r.thread;
        keyRef.current = r.thread;
        onThread?.(r.thread);
      }
      onActivity?.();
    } catch (err) {
      still = key === keyRef.current;
      if (still) setFailed({ payload, message: (err as Error).message });
    } finally {
      if (still) setPending(null);
    }
  }, [onActivity, onThread]);

  // 换了对话以后第一次取到消息：回到上次看到的位置；以后有新消息就到最下面
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || messages === null || stale) return;
    if (restoreScroll.current !== null) {
      el.scrollTop = restoreScroll.current;
      restoreScroll.current = null;
      return;
    }
    el.scrollTop = el.scrollHeight;
  }, [messages?.length, pending, failed, threadKey, stale]);

  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight + 2, 180) + "px";
  }, [text]);

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    void send({ text: value, problemKey: context?.problemKey, submissionKey: context?.submissionKey });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // 回车发送；Shift+回车换行；输入法选字时的回车不算
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const updateQuiz = (id: number, next: QuizView) => {
    setMessages((old) => old && old.map((m) => (m.id === id ? { ...m, quiz: next } : m)));
    if (next.state === "done") onActivity?.();
  };

  const shown: Payload | null = pending ?? failed?.payload ?? null;
  const pendingText = shown ? (shown.action ? ACTION_LABELS[shown.action] : shown.text ?? "") : "";
  const list = messages ?? [];

  return (
    <>
      <div className="chat-scroll" ref={scroller} onScroll={(e) => saveScroll(threadKey, e.currentTarget.scrollTop)}>
        {messages === null && !loadError && <div className="chat-list"><Loading what="正在取对话" /></div>}
        {loadError && <div className="chat-list"><Problem message={loadError} onRetry={() => load(false)} /></div>}
        {messages !== null && (
          <div className={stale ? "chat-list fade-swap is-stale" : "chat-list fade-swap"} aria-live="polite" aria-busy={stale}>
            {list.length === 0 && !shown && (
              <div className="chat-empty">
                <EmptyScene />
                <strong>{emptyTitle}</strong>
                <span>{emptyText}</span>
              </div>
            )}
            {list.map((m, i) => (
              <MessageItem key={m.id} message={m} layout={layout} disabled={busy}
                daySep={i === 0 ? (sameDay(m.time, new Date().toISOString()) ? null : dayLabel(m.time)) : sameDay(m.time, list[i - 1].time) ? null : dayLabel(m.time)}
                onAction={(action) => void send({ action })} onQuiz={(q) => updateQuiz(m.id, q)}
                onCopy={() => void api.event("copy_code", threadKey)} />
            ))}
            {shown && (
              <div className="msg msg-student">
                <div className="msg-body">
                  <div className={looksLikeCode(pendingText) ? "bubble bubble-code" : "bubble"}>
                    {looksLikeCode(pendingText) ? pendingText : pendingText.split("\n").filter((p) => p.trim()).slice(0, 12).map((p, i) => <p key={i}>{p}</p>)}
                  </div>
                </div>
              </div>
            )}
            {pending && (
              <div className="msg msg-agent">
                <AgentMark />
                <div className="msg-body">
                  <span className="msg-meta"><strong>智能体</strong></span>
                  <span className="typing" role="status" aria-label="正在输入"><i /><i /><i /></span>
                </div>
              </div>
            )}
            {failed && !pending && (
              <div className="msg msg-agent">
                <AgentMark />
                <div className="msg-body">
                  <span className="msg-meta"><strong>智能体</strong></span>
                  <p role="alert">{failed.message.includes("没发出去") ? failed.message : `这条没发出去：${failed.message}`}</p>
                  <div className="chips">
                    <button type="button" className="chip" onClick={() => void send(failed.payload)}>
                      <RotateCcw aria-hidden="true" />重新发送
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {(context || contextOffer) && (
          <div className="composer-context">
            {context ? (
              <span className="context-chip">
                {context.label}
                <button type="button" onClick={context.onClear} aria-label={`不带上：${context.label}`}><X size={14} aria-hidden="true" /></button>
              </span>
            ) : contextOffer && (
              <button type="button" className="context-offer" onClick={contextOffer.onPick}>{contextOffer.label}</button>
            )}
          </div>
        )}
        <div className="composer-box">
          <label className="sr-only" htmlFor="composer-input">输入消息</label>
          <textarea id="composer-input" ref={input} rows={1} value={text} placeholder={placeholder ?? "说说你的想法，或贴一段代码"}
            className={looksLikeCode(text) ? "has-code" : undefined} onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown} spellCheck={false} />
          <button type="submit" className="send" disabled={busy || !text.trim()} aria-label="发送">
            <ArrowUp size={20} strokeWidth={2.2} />
          </button>
        </div>
        <p className="composer-hint">回车发送，Shift + 回车换行。</p>
      </form>
    </>
  );
}

interface ItemProps {
  message: Message;
  layout: Layout;
  disabled: boolean;
  daySep: string | null;
  onAction: (action: HelpAction) => void;
  onQuiz: (quiz: QuizView) => void;
  onCopy: () => void;
}

function MessageItem({ message, layout, disabled, daySep, onAction, onQuiz, onCopy }: ItemProps) {
  const mine = message.role === "student";
  if (mine) {
    const text = message.blocks.filter((b) => b.t === "p").map((b) => (b as { text: string }).text).join("\n");
    const code = looksLikeCode(text);
    return (
      <>
        {daySep && <div className="day-sep" role="separator">{daySep}</div>}
        <div className="msg msg-student">
          <div className="msg-body">
            <div className={code ? "bubble bubble-code" : "bubble"}>
              {code ? text : message.blocks.map((b, i) =>
                b.t === "p" ? <p key={i}><Inline text={b.text} /></p> : <CodeBlock key={i} caption={b.caption} start={b.start} lines={b.lines} mark={b.mark} copyable={false} />,
              )}
            </div>
          </div>
        </div>
      </>
    );
  }
  return (
    <>
      {daySep && <div className="day-sep" role="separator">{daySep}</div>}
      <div className="msg msg-agent">
        <AgentMark />
        <div className="msg-body">
          <span className="msg-meta"><strong>智能体</strong></span>
          {message.blocks.map((b, i) =>
            b.t === "p" ? (
              <p key={i}><Inline text={b.text} /></p>
            ) : (
              <CodeBlock key={i} caption={b.caption} start={b.start} lines={b.lines} mark={b.mark} onCopy={onCopy} />
            ),
          )}
          {message.quiz && (message.quiz.protocol === 2
            ? <Attempt quiz={message.quiz} onChange={onQuiz} compact />
            : <Quiz quiz={message.quiz} layout={layout} onChange={onQuiz} />)}
          {message.actions.length > 0 && (
            <div className="chips" role="group" aria-label="接下来想怎么做">
              {message.actions.map((a) => {
                const Icon = ACTION_ICONS[a];
                return (
                  <button key={a} type="button" className="chip" disabled={disabled} onClick={() => onAction(a)}>
                    <Icon aria-hidden="true" />{ACTION_LABELS[a]}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
