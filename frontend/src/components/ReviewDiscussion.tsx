import { useRef, useState } from "react";
import { MessagesSquare, X } from "lucide-react";
import { get, post } from "../api";
import { Inline, Loading, Problem } from "./ui";

interface Turn { id: string; question: string; answer: string }
interface Response { turns: Turn[] }

/** 只在小题反馈后开放。浏览器原生对话框处理焦点、Esc 和背景隔离。 */
export default function ReviewDiscussion({ id }: { id: number }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [turns, setTurns] = useState<Turn[] | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const token = useRef("");
  const url = `/api/review/${id}/discussion`;
  const read = async () => {
    setError("");
    try { setTurns((await get<Response>(url)).turns); }
    catch (err) { setError((err as Error).message); }
  };
  const open = () => {
    dialog.current?.showModal();
    if (!turns) void read();
  };
  const send = async () => {
    if (busy || !turns || !draft.trim()) return;
    if (!token.current) token.current = Array.from(crypto.getRandomValues(new Uint32Array(4))).join("-");
    setBusy(true);
    setError("");
    try {
      const response = await post<Response>(url, { requestId: token.current, text: draft.trim() });
      setTurns(response.turns);
      setDraft("");
      token.current = "";
      requestAnimationFrame(() => dialog.current?.querySelector(".review-turn:last-child")?.scrollIntoView({ block: "nearest" }));
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return <>
    <button type="button" className="btn" onClick={open}><MessagesSquare size={16} aria-hidden="true" />就这道复习题提问</button>
    <dialog ref={dialog} className="review-dialog" aria-label="本题答疑">
      <div className="review-dialog-head"><h2>本题答疑</h2>
        <button type="button" className="icon-btn" aria-label="关闭本题答疑" onClick={() => dialog.current?.close()}><X size={20} /></button>
      </div>
      <div className="review-dialog-body" aria-live="polite">
        {turns === null && !error && <Loading what="正在取本题问答" />}
        {turns?.length === 0 && <p className="note">可以问这段代码哪里出错，也可以问为什么这样改。</p>}
        {turns?.map(t => <div key={t.id} className="review-turn">
          <p><strong>你</strong>{t.question}</p><p><strong>智能体</strong><Inline text={t.answer} /></p>
        </div>)}
        {error && <Problem message={error} onRetry={turns === null ? read : send} />}
      </div>
      <form onSubmit={e => { e.preventDefault(); void send(); }}>
        <textarea aria-label="你的复习问题" className="field" rows={3} maxLength={2000} value={draft}
          disabled={busy} onChange={e => { token.current = ""; setDraft(e.target.value); }} placeholder="写下你的问题…" />
        <button type="submit" className="btn btn-primary" disabled={busy || !turns || !draft.trim()}>{busy ? "正在回答…" : "发送"}</button>
      </form>
    </dialog>
  </>;
}
