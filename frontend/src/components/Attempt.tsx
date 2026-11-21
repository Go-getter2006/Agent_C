// 独立作答（界面改造方案第 4 节）：先判断 → 判断有问题的选位置、修改；判断满足要求的智能体追问一句 → 反馈。
// - 第一步“满足要求”“存在问题”同样分量，确认前可以改选；确认以后锁定。把握可选，放在确认按钮旁边；
// - 判断满足要求：智能体追问“你确定吗”（正确和有错的代码问法一样），坚持就出反馈，改判就去选位置、修改
//   （protocol 2 的旧题判断满足要求的选依据）；
// - 学生自己点行号选位置（一行或连续几行），选中的行才变黄；
// - 判断有问题的再修改：可以运行公开样例、看提示，也可以原样提交；
// - 反馈之前不提示对错。完成后在复习页就这道题答疑。
// 用在复习提升（整页，题干、代码、作答在一条阅读轴线上）和对话里通过后出的小题（compact）。
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Braces, CircleCheck, CircleX, FileCode2, Lightbulb, Play } from "lucide-react";
import { api } from "../api";
import type { QuizView } from "../types";
import { highlightC } from "../lib/cSyntax";
import { CodeBlock, Loading, SynLine } from "./ui";
import Recap from "./Recap";

const CodeEditor = lazy(() => import("./CodeEditor"));
const LEVELS = ["没把握", "一半", "很有把握"];

interface Props {
  quiz: QuizView;
  onChange: (next: QuizView) => void;
  compact?: boolean;
  onNext?: () => void;          // 复习提升：做完以后“下一题”
  nextLabel?: string;
  showRecap?: boolean;          // 反馈之后的回顾放在这里（复习提升另起一条消息，传 false）
}

/** 判断“满足要求”以后，智能体的追问和他的回答（像对话里的一问一答）。 */
function Challenge({ text, reply, busy, onReply }: {
  text: string; reply: "kept" | "flipped" | null; busy: boolean; onReply?: (keep: boolean) => void;
}) {
  return (
    <div className="attempt-challenge">
      <p className="attempt-challenge-me">满足要求。</p>
      <p className="attempt-challenge-q">
        <span className="brand-mark" aria-hidden="true"><Braces size={13} strokeWidth={2.4} /></span>
        <span>{text}</span>
      </p>
      {reply ? (
        <p className="attempt-challenge-a">{reply === "kept" ? "我确定，满足要求。" : "我改判：存在问题。"}</p>
      ) : onReply && (
        <div className="attempt-choices" role="group" aria-label="回答追问">
          <button type="button" className="attempt-choice" disabled={busy} onClick={() => onReply(true)}>我确定，满足要求</button>
          <button type="button" className="attempt-choice" disabled={busy} onClick={() => onReply(false)}>我改判：存在问题</button>
        </div>
      )}
    </div>
  );
}

function where(lines: number[]): string {
  if (!lines.length) return "";
  return lines.length === 1 ? `第 ${lines[0]} 行` : `第 ${lines[0]}～${lines[lines.length - 1]} 行`;
}

/** 可以点行号选行的代码：一行或连续几行。Shift 点另一行选一段；键盘上 Tab 到行号，回车或空格选。 */
function SelectableCode({ caption, lines, start, selected, onSelect, disabled }: {
  caption: string; lines: string[]; start: number; selected: number[]; onSelect: (next: number[]) => void; disabled: boolean;
}) {
  const anchor = useRef<number | null>(null);
  const colored = useMemo(() => highlightC(lines), [lines]);
  const pick = (no: number, extend: boolean) => {
    if (disabled) return;
    if (extend && anchor.current !== null) {
      const [a, b] = [Math.min(anchor.current, no), Math.max(anchor.current, no)];
      onSelect(Array.from({ length: b - a + 1 }, (_, i) => a + i));
      return;
    }
    if (selected.length === 1 && selected[0] === no) {
      anchor.current = null;
      onSelect([]);
      return;
    }
    anchor.current = no;
    onSelect([no]);
  };
  return (
    <div className="code code-pick">
      <div className="code-cap"><span><FileCode2 aria-hidden="true" />{caption}</span></div>
      <div className="code-body" role="group" aria-label={`${caption}，点行号选行`}>
        <div className="code-lines">
          {lines.map((text, i) => {
            const no = start + i;
            const on = selected.includes(no);
            return (
              <div key={i} className={on ? "code-line picked" : "code-line"}>
                <button type="button" className="code-no code-no-btn" aria-pressed={on} disabled={disabled}
                  aria-label={`第 ${no} 行${on ? "，已选" : ""}`}
                  onClick={(e: MouseEvent) => pick(no, e.shiftKey)}
                  onKeyDown={(e: KeyboardEvent) => { if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); pick(no, true); } }}>
                  {no}
                </button>
                <span className="code-text">{text ? <SynLine toks={colored[i]} /> : " "}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function Attempt({ quiz, onChange, compact = false, onNext, nextLabel = "下一题", showRecap = true }: Props) {
  const shownAt = useRef(Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [says, setSays] = useState<"ok" | "bug" | null>(quiz.judged);
  const [confidence, setConfidence] = useState<string | null>(quiz.confidence);
  const [picked, setPicked] = useState<number[]>(quiz.lines ?? []);
  const [draft, setDraft] = useState(quiz.draft ?? quiz.code.join("\n"));
  const start = quiz.startLine ?? 1;
  const state = quiz.state;
  const result = quiz.result;

  const call = async (name: string, fn: () => Promise<QuizView>) => {
    setBusy(name);
    setError(null);
    try {
      const next = await fn();
      onChange(next);
      if (next.draft !== undefined && name !== "sample" && name !== "submit") setDraft(next.draft);
    } catch (err) {
      setError((err as Error).message);   // 失败后选择和改好的代码都留着
    } finally {
      setBusy(null);
    }
  };

  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (state === "done" || !container.current) return;
    let inView = false;
    let sent = false;
    const seen = () => {
      if (inView && !document.hidden && !sent) {
        sent = true;
        void api.quizViewed(quiz.attemptId).catch(() => { sent = false; });
      }
    };
    const observer = new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; seen(); });
    observer.observe(container.current);
    document.addEventListener("visibilitychange", seen);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", seen); };
  }, [quiz.attemptId, state === "done"]);

  return (
    <div ref={container} className={compact ? "attempt attempt-compact" : "attempt"}>

      {state === "done" && result ? (
        <h2 className="attempt-title">{result.headline}</h2>
      ) : (
        <h2 className="attempt-title attempt-stem">{quiz.stem}</h2>
      )}
      {state === "done" && <p className="attempt-sub">{quiz.stem}</p>}

      {state === "locate" ? (
        <SelectableCode caption={quiz.caption} lines={quiz.code} start={start} selected={picked} onSelect={setPicked} disabled={busy !== null} />
      ) : state === "edit" ? (
        <div className="code quiz-editor">
          <div className="code-cap"><span><FileCode2 aria-hidden="true" />{quiz.caption} · 修改稿</span></div>
          <Suspense fallback={<div className="card-body"><Loading what="正在打开编辑器" lines={3} /></div>}>
            <CodeEditor value={draft} onChange={setDraft} label={`${quiz.caption}，可以编辑`} disabled={busy !== null} start={start} />
          </Suspense>
        </div>
      ) : state === "done" && result ? (
        <CodeBlock caption={result.finalCode ? "你改好的代码" : "你提交时的代码"} lines={result.finalCode ?? quiz.code} start={start}
          mark={result.lines ?? []} copyable={false} />
      ) : (
        <CodeBlock caption={quiz.caption} lines={quiz.code} start={start} mark={quiz.mark ?? []} copyable={false} />
      )}

      {state === "judge" && (
        <div className="attempt-ask">
          <h3>这段代码满足题目要求吗？</h3>
          <div className="attempt-choices" role="radiogroup" aria-label="你的判断">
            {(["ok", "bug"] as const).map((v) => (
              <button key={v} type="button" role="radio" aria-checked={says === v} className="attempt-choice"
                disabled={busy !== null} onClick={() => setSays(v)}>
                {v === "ok" ? "满足要求" : "存在问题"}
              </button>
            ))}
          </div>
          <div className="attempt-confirm">
            <button type="button" className="btn btn-primary" disabled={!says || busy !== null}
              onClick={() => call("judge", () => api.quizJudge(quiz.attemptId, says === "bug", confidence, Date.now() - shownAt.current))}>
              {busy === "judge" ? "正在保存" : "确认判断"}
            </button>
            <span className="attempt-confidence" role="group" aria-label="把握（可选）">
              <span className="note">把握（可选）</span>
              {LEVELS.map((l) => (
                <button key={l} type="button" className="chip-sm" aria-pressed={confidence === l} disabled={busy !== null}
                  onClick={() => setConfidence(confidence === l ? null : l)}>{l}</button>
              ))}
            </span>
          </div>
          {!says && <p className="note">先选一项，再确认。确认以后不能改。</p>}
        </div>
      )}

      {quiz.challenge && state !== "done" && (
        <Challenge text={quiz.challenge.text} reply={quiz.challenge.reply} busy={busy !== null}
          onReply={state === "challenge" ? (keep) => call("challenge", () => api.quizChallenge(quiz.attemptId, keep)) : undefined} />
      )}

      {state === "locate" && (
        <div className="attempt-ask">
          <h3>{(quiz.current ?? quiz.judged) === "bug" ? "问题在哪几行？" : "哪些行支持你的判断？"}</h3>
          <p className={picked.length ? "attempt-picked" : "attempt-picked empty"} role="status">
            {picked.length ? `已选${where(picked)}` : "点行号选一行；按住 Shift 再点一行，选连续的几行"}
          </p>
          <div className="attempt-confirm">
            <button type="button" className="btn btn-primary" disabled={!picked.length || busy !== null}
              onClick={() => call("locate", () => api.quizLocate(quiz.attemptId, picked))}>
              {busy === "locate" ? "正在保存" : (quiz.current ?? quiz.judged) === "bug" ? "确认位置" : "确认依据"}
            </button>
          </div>
        </div>
      )}

      {state === "edit" && (
        <div className="attempt-ask">
          <h3>修改代码，让它满足题目要求。</h3>
          {quiz.hints && quiz.hints.length > 0 && (
            <ul className="attempt-hints">{quiz.hints.map((h, i) => <li key={i}><Lightbulb size={15} aria-hidden="true" />{h}</li>)}</ul>
          )}
          {quiz.message && (
            <p className={quiz.message.kind === "fail" ? "quiz-msg quiz-msg-fail" : "quiz-msg"} role="status">
              {quiz.message.text}{quiz.message.kind === "fail" && quiz.triesLeft === 1 ? "还可以再试一次。" : ""}
            </p>
          )}
          <div className="attempt-confirm">
            {quiz.hasSample && (
              <button type="button" className="btn" disabled={busy !== null} onClick={() => call("sample", () => api.quizSample(quiz.attemptId, draft))}>
                <Play size={15} aria-hidden="true" />{busy === "sample" ? "正在运行" : "运行公开样例"}
              </button>
            )}
            <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => call("submit", () => api.quizSubmit(quiz.attemptId, draft))}>
              {busy === "submit" ? "正在判分" : "提交修改"}
            </button>
            {quiz.moreHints && (
              <button type="button" className="link" disabled={busy !== null} onClick={() => call("hint", () => api.quizHint(quiz.attemptId))}>
                <Lightbulb size={15} aria-hidden="true" />看提示
              </button>
            )}
          </div>
          <p className="note">觉得原来的代码已经满足要求，也可以原样提交。</p>
        </div>
      )}

      {state === "done" && result && result.outcome !== "skipped" && (
        <div className="attempt-feedback">
          <p className={result.outcome === "right" ? "attempt-verdict ok" : "attempt-verdict bad"}>
            {result.outcome === "right" ? <CircleCheck size={18} aria-hidden="true" /> : <CircleX size={18} aria-hidden="true" />}
            {quiz.judged === "ok" ? "你选了「满足要求」" : "你选了「存在问题」"}
            {result.challenge === "kept" ? "，追问以后仍坚持" : result.challenge === "flipped" ? "，追问以后改判为「存在问题」" : ""}
            {result.lines?.length ? `，并指出${where(result.lines)}` : ""}。
            {result.explanation ? ` ${result.explanation}` : ""}
          </p>

          {result.fix && (
            <CodeBlock caption={result.bugLines?.length ? `问题在${where(result.bugLines)}，可以这样改` : "可以这样改"}
              lines={result.fix.lines} mark={result.fix.mark} start={result.fix.start ?? start} copyable={false} />
          )}
          {showRecap && <Recap attemptId={quiz.attemptId} />}
          {onNext && (
            <div className="attempt-confirm">
              <button type="button" className="btn btn-primary" onClick={onNext}>{nextLabel}</button>
            </div>
          )}
        </div>
      )}
      {state === "done" && result?.outcome === "skipped" && onNext && <button className="btn btn-primary" type="button" onClick={onNext}>{nextLabel}</button>}

      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  );
}
