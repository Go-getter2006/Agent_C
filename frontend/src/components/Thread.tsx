// 一条对话线程：消息、正在输入、求助按钮、输入栏。
// 学生只看到自然的回复、代码块和“正在输入”；没有步骤条、标签、分数。
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Braces, Brain, Lightbulb, MessageCircle, RotateCcw, X, type LucideIcon } from "lucide-react";
import { api } from "../api";
import type { Layout } from "../lib/hooks";
import { readDraft, readScroll, saveScroll, writeDraft } from "../lib/memory";
import { dayLabel, sameDay } from "../lib/time";
import type { HelpAction, Message, QuizView } from "../types";
import { useSession, useStudentCache } from "../session";
import CourseScene from "./CourseScene";
import Attempt from "./Attempt";
import FlowChart from "./FlowChart";
import Quiz from "./Quiz";
import { CodeBlock, Inline, Loading, Problem } from "./ui";

const ACTION_LABELS: Record<HelpAction, string> = { think: "我想自己想想", hint: "再给点提示", tell: "直接讲给我" };
const ACTION_ICONS: Record<HelpAction, LucideIcon> = { think: Brain, hint: Lightbulb, tell: MessageCircle };


function EmptyScene() {
  const { me } = useSession();
  return <CourseScene course={me?.course === "D" ? "D" : "C"} variant="mini" className="chat-empty-scene" />;
}

export function AgentMark() {
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
  emptyReady?: boolean;
  waitingContext?: boolean;
  latestNotice?: number;
  onNoticeRead?: () => void;
  context?: ThreadContext | null;
  contextOffer?: { label: string; onPick: () => void } | null;   // 还没带上时：可以点一下带上
  onThread?: (key: string) => void;   // 新建问答发出第一条以后，有了自己的编号
  onActivity?: () => void;     // 有新消息、小题有进展：让外面刷新列表和右栏
}

function looksLikeCode(text: string): boolean {
  return text.includes("\n") && /[;{}]|#include/.test(text);
}

export default function Thread({ threadKey, layout, emptyTitle, emptyText, placeholder, latestNotice, onNoticeRead, context, contextOffer,
  emptyReady = true, waitingContext = false, onThread, onActivity }: Props) {
  const cache = useStudentCache().threads;
  const request = useRef<AbortController | null>(null);
  const [messages, setMessages] = useState<Message[] | null>(() => cache.get(threadKey) ?? null);
  const [owner, setOwner] = useState(threadKey);
  const stale = owner !== threadKey;
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
  const sending = useRef(new Map<string, Payload>());
  const failures = useRef(new Map<string, { payload: Payload; message: string }>());
  const keyRef = useRef(threadKey);
  keyRef.current = threadKey;
  const busy = pending !== null;
  const loadSeq = useRef(0);
  const followBottom = useRef(true);
  const [newBelow, setNewBelow] = useState(false);
  const reading = useRef(new Set<number>());
  const requestedNotice = useRef<number>();

  const load = useCallback((silent: boolean) => {
    const key = keyRef.current;
    if (silent && request.current) return;
    const seq = ++loadSeq.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    api.thread(key, controller.signal)
      .then((r) => {
        if (controller.signal.aborted || key !== keyRef.current || seq !== loadSeq.current) return;
        cache.put(key, r.messages);
        setMessages(r.messages);
        setOwner(key);
        setLoadError(null);
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted && seq === loadSeq.current && key === keyRef.current && (!silent || !cache.get(key))) setLoadError(err.message);
      }).finally(() => { if (request.current === controller) request.current = null; });
  }, []);

  useLayoutEffect(() => {
    if (created.current === threadKey) {   // 新建的问答刚有了编号：消息已经在了，不重新取、不闪
      created.current = null;
      return;
    }
    const cached = cache.get(threadKey);
    setMessages(cached ?? null);
    setOwner(threadKey);
    followBottom.current = true;
    requestedNotice.current = undefined;
    setNewBelow(false);
    setLoadError(null);
    setPending(sending.current.get(threadKey) ?? null);
    setFailed(failures.current.get(threadKey) ?? null);
    setTextRaw(readDraft(threadKey));
    restoreScroll.current = readScroll(threadKey);
    if (!cache.fresh(threadKey)) load(false);
    return () => { request.current?.abort(); loadSeq.current += 1; };
  }, [threadKey, load]);

  // 当前题的提醒和对话，切回标签后也会在下一次轮询时更新。
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !busy) load(true);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  useEffect(() => {
    if (latestNotice && !stale && messages && !busy && requestedNotice.current !== latestNotice
        && latestNotice > (messages[messages.length - 1]?.id ?? 0)) {
      requestedNotice.current = latestNotice;
      load(true);
    }
  }, [latestNotice, threadKey, stale, busy, messages, load]);

  useEffect(() => {
    const root = scroller.current;
    if (!root || stale || !messages?.some(m => m.unreadNotice)) return;
    const observer = new IntersectionObserver(entries => {
      if (document.visibilityState !== "visible" || keyRef.current !== threadKey || document.querySelector('[aria-modal="true"]')) return;
      const ids = entries.filter(e => e.isIntersecting).map(e => Number((e.target as HTMLElement).dataset.notice))
        .filter(id => !reading.current.has(id)).slice(0, 100);
      if (!ids.length) return;
      ids.forEach(id => reading.current.add(id));
      void api.readNotices(threadKey, ids).then(() => {
        if (keyRef.current === threadKey) setMessages(old => old && cache.put(threadKey,
          old.map(m => ids.includes(m.id) ? { ...m, unreadNotice: false } : m)));
        else cache.delete(threadKey);
        onNoticeRead?.();
      }).catch(() => {}).finally(() => ids.forEach(id => reading.current.delete(id)));
    }, { root });
    const observe = () => {
      observer.disconnect();
      root.querySelectorAll("[data-notice]").forEach(el => observer.observe(el));
    };
    observe();
    document.addEventListener("visibilitychange", observe);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", observe); };
  }, [messages, stale, threadKey, onNoticeRead]);

  const send = useCallback(async (payload: Payload) => {
    const key = keyRef.current;
    if (sending.current.has(key)) return;
    followBottom.current = true;
    sending.current.set(key, payload);
    failures.current.delete(key);
    setPending(payload);
    setFailed(null);
    let still = true;   // 发出去以后还在这个对话里（中途换了别的对话，结果就不管了）
    try {
      const r = await api.send(key, payload);
      still = key === keyRef.current;
      cache.delete(key);
      cache.put(r.thread || key, r.messages);
      if (!still) return;
      loadSeq.current += 1;
      setOwner(r.thread || key);
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
      const retry = { payload, message: (err as Error).message };
      failures.current.set(key, retry);
      if (still) setFailed(retry);
    } finally {
      sending.current.delete(key);
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
      followBottom.current = el.scrollHeight - el.clientHeight - el.scrollTop < 48;
      setNewBelow(!followBottom.current && !!messages[messages.length - 1]?.unreadNotice);
      return;
    }
    if (followBottom.current) el.scrollTop = el.scrollHeight;
    else if (messages[messages.length - 1]?.unreadNotice) setNewBelow(true);
  }, [messages?.length, pending, failed, threadKey, stale]);

  useLayoutEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight + 2, 180) + "px";
  }, [text]);

  const submit = () => {
    const value = text.trim();
    if (!value || busy || waitingContext || stale) return;
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
    if (keyRef.current !== threadKey) return;
    setMessages(old => old && cache.put(threadKey, old.map(m => m.id === id ? { ...m, quiz: next } : m)));
    if (next.state === "done") onActivity?.();
  };

  const shown: Payload | null = pending ?? failed?.payload ?? null;
  const pendingText = shown ? (shown.action ? ACTION_LABELS[shown.action] : shown.text ?? "") : "";
  const list = messages ?? [];

  return (
    <>
      <div className="chat-scroll" ref={scroller} onScroll={({ currentTarget: el }) => {
        followBottom.current = el.scrollHeight - el.clientHeight - el.scrollTop < 48;
        if (followBottom.current) setNewBelow(false);
        saveScroll(threadKey, el.scrollTop);
      }}>
        {(stale || messages === null || (!list.length && !emptyReady && !shown)) && !loadError && <div className="chat-list"><Loading what="正在取对话" /></div>}
        {loadError && <div className="chat-list"><Problem message={loadError} onRetry={() => load(false)} /></div>}
        {!stale && messages !== null && (
          <div className="chat-list" aria-live="polite">
            {list.length === 0 && !shown && emptyReady && !loadError && (
              <div className="chat-empty">
                <EmptyScene />
                <strong>{emptyTitle}</strong>
                <span>{emptyText}</span>
              </div>
            )}
            {list.map((m, i) => (
              <MessageItem key={m.id} message={m} layout={layout} disabled={busy}
                live={i === list.length - 1 && !shown} onSend={(value) => void send({ text: value })}
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
      {newBelow && <button type="button" className="new-notice" onClick={() => {
        const el = scroller.current;
        if (el) el.scrollTop = el.scrollHeight;
        setNewBelow(false);
      }}>有新消息 ↓</button>}
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
          <button type="submit" className="send" disabled={busy || stale || waitingContext || !text.trim()} aria-label="发送">
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
  live: boolean;                 // 最新一条（流程图只有这一条能选）
  onAction: (action: HelpAction) => void;
  onSend: (text: string) => void;
  onQuiz: (quiz: QuizView) => void;
  onCopy: () => void;
}

function MessageItem({ message, layout, disabled, daySep, live, onAction, onSend, onQuiz, onCopy }: ItemProps) {
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
                b.t === "p" ? <p key={i}><Inline text={b.text} /></p>
                  : b.t === "code" ? <CodeBlock key={i} caption={b.caption} start={b.start} lines={b.lines} mark={b.mark} copyable={false} /> : null,
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
      <div className="msg msg-agent" data-notice={message.unreadNotice ? message.id : undefined}>
        <AgentMark />
        <div className="msg-body">
          <span className="msg-meta"><strong>智能体</strong></span>
          {message.blocks.map((b, i) =>
            b.t === "p" ? (
              <p key={i}><Inline text={b.text} /></p>
            ) : b.t === "flow" ? (
              <FlowChart key={i} steps={b.steps} pick={b.pick} current={b.current} done={b.done}
                live={live} disabled={disabled} onSend={onSend} />
            ) : (
              <CodeBlock key={i} caption={b.caption} start={b.start} lines={b.lines} mark={b.mark} onCopy={onCopy} />
            ),
          )}
          {message.quiz && ((message.quiz.protocol ?? 1) >= 2
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
