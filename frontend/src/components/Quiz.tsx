// 小题：出现在对话流里，是智能体的一条消息。
// 顺序：题干 → 代码 → 把握程度（必须先选）→ 判断 → 改代码并运行检查 → 结果。
import { useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Layout } from "../lib/hooks";
import type { QuizView } from "../types";
import CodeEditor from "./CodeEditor";
import { CodeBlock } from "./ui";

const LEVELS = ["没把握", "一半", "很有把握"];

function changedCount(a: string[], b: string[]): number {
  // 粗略数一下改了几行，只用来在按钮旁提前提醒；算不算数以后端为准
  let same = 0;
  const used = new Set<number>();
  for (const line of a) {
    const j = b.findIndex((x, k) => !used.has(k) && x.trimEnd() === line.trimEnd());
    if (j >= 0) {
      used.add(j);
      same++;
    }
  }
  return Math.max(a.length, b.length) - same;
}

interface Props {
  quiz: QuizView;
  layout: Layout;
  onChange: (next: QuizView) => void;
}

export default function Quiz({ quiz, layout, onChange }: Props) {
  const shownAt = useRef(Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(quiz.draft ?? quiz.code.join("\n"));
  const [choice, setChoice] = useState<number | null>(null);
  const [typing, setTyping] = useState(false); // 手机上默认用选择题，可以切到自己改

  const call = async (name: string, fn: () => Promise<QuizView>) => {
    setBusy(name);
    setError(null);
    try {
      const next = await fn();
      onChange(next);
      if (next.draft !== undefined) setDraft(next.draft);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const hasChoices = (quiz.choices?.length ?? 0) > 0;
  const useChoices = quiz.state === "edit" && layout === "phone" && hasChoices && !typing;
  const changed = useMemo(() => changedCount(quiz.code, draft.split("\n")), [quiz.code, draft]);
  const done = quiz.state === "done";
  const result = quiz.result;

  return (
    <div className="quiz">
      <p>{quiz.stem}</p>

      {quiz.state === "edit" && !useChoices ? (
        <div className="code quiz-editor">
          <div className="code-cap">
            <span>{quiz.caption}，整段都可以改</span>
          </div>
          <CodeEditor value={draft} onChange={setDraft} label={`${quiz.caption}，可以编辑`} disabled={busy === "run"} />
        </div>
      ) : done && result?.finalCode ? (
        <CodeBlock caption="你改好的代码" lines={result.finalCode} copyable={false} />
      ) : (
        <CodeBlock caption={quiz.caption} lines={quiz.code} copyable={false} />
      )}

      {/* 把握程度：每道小题都问，判断之前必须先选 */}
      {!done && (quiz.state === "confidence" || quiz.state === "judge") && (
        <div className="quiz-row" role="group" aria-label="把握程度">
          <span className="quiz-label">把握</span>
          {LEVELS.map((level) => (
            <button key={level} type="button" className="btn" aria-pressed={quiz.confidence === level}
              disabled={quiz.state !== "confidence" || busy !== null}
              onClick={() => call("confidence", () => api.quizConfidence(quiz.attemptId, level, Date.now() - shownAt.current))}>
              {level}
            </button>
          ))}
        </div>
      )}

      {!done && (quiz.state === "confidence" || quiz.state === "judge") && (
        <div className="quiz-row" role="group" aria-label="判断">
          <span className="quiz-label">判断</span>
          <button type="button" className="btn" disabled={quiz.state !== "judge" || busy !== null}
            onClick={() => call("judge", () => api.quizJudge(quiz.attemptId, false))}>
            对
          </button>
          <button type="button" className="btn" disabled={quiz.state !== "judge" || busy !== null}
            onClick={() => call("judge", () => api.quizJudge(quiz.attemptId, true))}>
            有错
          </button>
          {quiz.state === "confidence" && <span className="note">先选把握程度</span>}
        </div>
      )}

      {quiz.state === "edit" && useChoices && (
        <div className="quiz-choices" role="radiogroup" aria-label="选一种改法">
          {quiz.choices!.map((c) => (
            <button key={c.index} type="button" role="radio" aria-checked={choice === c.index}
              className={"btn quiz-choice" + (/[;(){}=]/.test(c.label) ? " mono" : "")}
              aria-pressed={choice === c.index} disabled={busy !== null} onClick={() => setChoice(c.index)}>
              {c.label}
            </button>
          ))}
        </div>
      )}

      {quiz.state === "edit" && (
        <>
          {quiz.message && (
            <p className="quiz-msg" role="status">
              {quiz.message.text}
              {quiz.message.kind === "fail" && quiz.triesLeft === 1 && "还可以再试一次。"}
              {quiz.message.kind === "compile" && "这次不算机会。"}
            </p>
          )}
          <div className="quiz-row">
            <button type="button" className="btn btn-primary" disabled={busy !== null || (useChoices && choice === null)}
              onClick={() => call("run", () => api.quizRun(quiz.attemptId, useChoices ? { choice: choice! } : { code: draft }))}>
              {busy === "run" ? "正在运行" : "运行检查"}
            </button>
            {!useChoices && changed > 3 && <span className="note">只需要改一两处</span>}
            {layout === "phone" && hasChoices && (
              <button type="button" className="btn-text" onClick={() => setTyping(!typing)}>
                {typing ? "改成选一种改法" : "我想自己改代码"}
              </button>
            )}
          </div>
        </>
      )}

      {done && result && (
        <div className="quiz-result">
          <p className="quiz-headline">
            <span className={result.outcome === "right" ? "cell cell-pass" : result.outcome === "wrong" ? "cell cell-fail" : "cell cell-none"} aria-hidden="true" />
            {result.headline}
          </p>
          {result.lastRun && <p className="note">最后一次运行：{result.lastRun}</p>}
          {result.fix && (
            <CodeBlock caption={result.bugLine ? `问题在第 ${result.bugLine} 行，应该这样改` : "应该这样改"}
              lines={result.fix.lines} mark={result.fix.mark} copyable={false} />
          )}
          {result.explanation && <p>{result.explanation}</p>}
        </div>
      )}

      {error && <p className="error-text" role="alert">{error}</p>}

      {!done && (
        <div className="quiz-skip">
          <button type="button" className="btn-text" disabled={busy !== null}
            onClick={() => call("skip", () => api.quizSkip(quiz.attemptId))}>
            先跳过
          </button>
        </div>
      )}
    </div>
  );
}
