// 解题流程图（后端 chat/plan.py 的 flow 块）。
// 第一次：从“开始”到“提交”的竖排流程图，每一步一张卡片（标题、要做什么、关键知识点），可以选卡在哪几步。
// 之后每条引导消息带一条简短的步骤条：做完的打勾，正在讲的那一步高亮。
import { useState } from "react";
import { Check, ChevronRight, Flag, Play } from "lucide-react";
import type { FlowStep } from "../types";

interface Props {
  steps: FlowStep[];
  pick: boolean;
  current: number | null;
  done: number[];
  live: boolean;                 // 最新一条、还能选
  disabled: boolean;
  onSend: (text: string) => void;
}

export function pickText(sel: number[]): string {
  return `我卡在第 ${[...sel].sort((a, b) => a - b).map((i) => i + 1).join("、")} 步`;
}

export default function FlowChart({ steps, pick, current, done, live, disabled, onSend }: Props) {
  const [sel, setSel] = useState<number[]>([]);
  if (!pick) return <FlowStrip steps={steps} current={current} done={done} />;
  const choosing = live && !disabled;
  const toggle = (i: number) => setSel((old) => (old.includes(i) ? old.filter((x) => x !== i) : [...old, i]));
  return (
    <div className="flow" role="group" aria-label="解题流程图">
      <div className="flow-cap"><Play aria-hidden="true" />开始</div>
      <ol className="flow-steps">
        {steps.map((st, i) => {
          const on = sel.includes(i);
          const body = (
            <>
              <span className="flow-no" aria-hidden="true">{i + 1}</span>
              <span className="flow-main">
                <strong className="flow-title">{st.title}</strong>
                {st.how && <span className="flow-how">{st.how}</span>}
                {st.kp && st.kp !== st.title && <span className="flow-kp">关键 · {st.kp}</span>}
              </span>
              {choosing && <span className="flow-box" aria-hidden="true">{on && <Check strokeWidth={3} />}</span>}
            </>
          );
          return (
            <li key={i} className={on ? "flow-step is-picked" : "flow-step"}>
              {choosing ? (
                <button type="button" className="flow-card" aria-pressed={on} onClick={() => toggle(i)}
                  aria-label={`第 ${i + 1} 步 ${st.title}${st.how ? `：${st.how}` : ""}`}>
                  {body}
                </button>
              ) : <div className="flow-card">{body}</div>}
            </li>
          );
        })}
      </ol>
      <div className="flow-cap flow-cap-end"><Flag aria-hidden="true" />提交</div>
      {choosing && (
        <div className="flow-actions">
          <button type="button" className="btn btn-primary" disabled={!sel.length} onClick={() => onSend(pickText(sel))}>
            {sel.length ? pickText(sel) : "选出卡住的步骤"}
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => onSend("每一步都不会")}>每一步都不会</button>
        </div>
      )}
    </div>
  );
}

function FlowStrip({ steps, current, done }: { steps: FlowStep[]; current: number | null; done: number[] }) {
  return (
    <ol className="flow-strip" aria-label="解题步骤">
      {steps.map((st, i) => {
        const finished = done.includes(i);
        const now = current === i;
        return (
          <li key={i} className={now ? "is-current" : finished ? "is-done" : undefined}
            aria-current={now ? "step" : undefined}>
            {i > 0 && <ChevronRight className="flow-strip-arrow" aria-hidden="true" />}
            <span className="flow-pill">
              <span className="flow-no" aria-hidden="true">{finished ? <Check strokeWidth={3} /> : i + 1}</span>
              <span>{st.title}</span>
              <span className="sr-only">{finished ? "（想清楚了）" : now ? "（正在讲）" : ""}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
