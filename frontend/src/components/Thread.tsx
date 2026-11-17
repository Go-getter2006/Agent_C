// 一条对话线程：消息、正在输入、求助按钮、输入栏。
// 学生只看到自然的回复、代码块和“正在输入”；没有步骤条、标签、分数。
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import type { Layout } from "../lib/hooks";
import { dayLabel, sameDay } from "../lib/time";
import type { HelpAction, Message, QuizView } from "../types";
import Quiz from "./Quiz";
import { CodeBlock, Inline, Loading, Problem } from "./ui";

const ACTION_LABELS: Record<HelpAction, string> = { think: "我想自己想想", hint: "再给点提示", tell: "直接讲给我" };
export const ASK_WHY = "这次提交为什么错了？";

interface Payload {
  text?: string;
  action?: HelpAction;
  submissionKey?: string;
}

interface Props {
  threadKey: string;
  layout: Layout;
  emptyText: string;
  askKey?: string | null;      // 从“问问为什么错”进来：自动问这次提交
  onAsked?: () => void;
  onActivity?: () => void;     // 有新消息、小题有进展：让外面刷新列表和右栏
}

function looksLikeCode(text: string): boolean {
  return text.includes("\n") && /[;{}]|#include/.test(text);
}

export default function Thread({ threadKey, layout, emptyText, askKey, onAsked, onActivity }: Props) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<Payload | null>(null);   // 正在发的那一条
  const [failed, setFailed] = useState<{ payload: Payload; message: string } | null>(null);
  const [text, setText] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const keyRef = useRef(threadKey);
  keyRef.current = threadKey;
  const busy = pending !== null;

  const load = useCallback((silent: boolean) => {
    const key = keyRef.current;
    api.thread(key)
      .then((r) => {
        if (key !== keyRef.current) return;
        setMessages((old) => (silent && old && old.length > r.messages.length ? old : r.messages));
        setLoadError(null);
      })
      .catch((err: Error) => {
        if (key === keyRef.current && !silent) setLoadError(err.message);
      });
  }, []);

  useEffect(() => {
    setMessages(null);
    setLoadError(null);
    setPending(null);
    setFailed(null);
    setText("");
    load(false);
  }, [threadKey, load]);

  // 定时看一眼：题目通过后，智能体可能会追加一道小题
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !busy) load(true);
    }, 20000);
    return () => window.clearInterval(timer);
  }, [busy, load]);

  const send = useCallback(async (payload: Payload) => {
    const key = keyRef.current;
    setPending(payload);
    setFailed(null);
    try {
      const r = await api.send(key, payload);
      if (key !== keyRef.current) return;
      setMessages(r.messages);
      onActivity?.();
    } catch (err) {
      if (key === keyRef.current) setFailed({ payload, message: (err as Error).message });
    } finally {
      if (key === keyRef.current) setPending(null);
    }
  }, [onActivity]);

  // 从“问问为什么错”进来
  useEffect(() => {
    if (askKey && messages !== null && !busy) {
      onAsked?.();
      void send({ text: ASK_WHY, submissionKey: askKey });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [askKey, messages === null]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages?.length, pending, failed, threadKey]);

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
    void send({ text: value });
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
      <div className="chat-scroll" ref={scroller}>
        {messages === null && !loadError && <Loading what="正在取对话" />}
        {loadError && <Problem message={loadError} onRetry={() => load(false)} />}
        {messages !== null && (
          <div className="chat-list" aria-live="polite">
            {list.length === 0 && !shown && <p className="chat-empty">{emptyText}</p>}
            {list.map((m, i) => (
              <MessageItem key={m.id} message={m} layout={layout} disabled={busy}
                daySep={i === 0 ? (sameDay(m.time, new Date().toISOString()) ? null : dayLabel(m.time)) : sameDay(m.time, list[i - 1].time) ? null : dayLabel(m.time)}
                onAction={(action) => void send({ action })} onQuiz={(q) => updateQuiz(m.id, q)}
                onCopy={() => void api.event("copy_code", threadKey)} />
            ))}
            {shown && (
              <div className="msg msg-student">
                <span className="msg-who">我</span>
                <div className="msg-body">
                  {pendingText.split("\n").filter((p) => p.trim()).slice(0, 12).map((p, i) => <p key={i}>{p}</p>)}
                </div>
              </div>
            )}
            {pending && (
              <div className="msg msg-agent">
                <span className="msg-who">智能体</span>
                <div className="msg-body">
                  <p className="typing" role="status">正在输入<i /><i /><i /></p>
                </div>
              </div>
            )}
            {failed && !pending && (
              <div className="msg msg-agent">
                <span className="msg-who">智能体</span>
                <div className="msg-body">
                  <p role="alert">{failed.message.includes("没发出去") ? failed.message : `这条没发出去：${failed.message}`}</p>
                  <div className="msg-actions">
                    <button type="button" className="btn" onClick={() => void send(failed.payload)}>重新发送</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="sr-only" htmlFor="composer-input">输入消息</label>
        <textarea id="composer-input" ref={input} rows={1} value={text} placeholder="说说你的想法，或贴一段代码"
          className={looksLikeCode(text) ? "has-code" : undefined} onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown} spellCheck={false} />
        <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>发送</button>
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
  return (
    <>
      {daySep && <div className="day-sep" role="separator">{daySep}</div>}
      <div className={mine ? "msg msg-student" : "msg msg-agent"}>
        <span className="msg-who">{mine ? "我" : "智能体"}</span>
        <div className="msg-body">
          {message.blocks.map((b, i) =>
            b.t === "p" ? (
              <p key={i}><Inline text={b.text} /></p>
            ) : (
              <CodeBlock key={i} caption={b.caption} start={b.start} lines={b.lines} mark={b.mark}
                copyable={!mine} onCopy={onCopy} />
            ),
          )}
          {message.quiz && <Quiz quiz={message.quiz} layout={layout} onChange={onQuiz} />}
          {message.actions.length > 0 && (
            <div className="msg-actions">
              {message.actions.map((a) => (
                <button key={a} type="button" className="btn" disabled={disabled} onClick={() => onAction(a)}>
                  {ACTION_LABELS[a]}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
