// 作业选择器、题目行、右栏“这道题的情况”。
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useEscape } from "../lib/hooks";
import { deadline, shortTime } from "../lib/time";
import type { Homework, ProblemDetail, ProblemRow } from "../types";
import { Cells, Icon, Meter, resultLine } from "./ui";

export function homeworkSummary(hw: Homework): string {
  return `已通过 ${hw.passed} 题，共 ${hw.total} 题，${deadline(hw.endTime, hw.ended)}`;
}

/** 作业选择器：只列出老师布置给这个学生班级的作业。 */
export function HomeworkPicker({ homeworks, current, onPick }: { homeworks: Homework[]; current: Homework | null; onPick: (key: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEscape(() => setOpen(false), open);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (!current) {
    return (
      <div className="picker">
        <span className="picker-label">作业</span>
        <span className="note">老师还没有给你的班级布置作业</span>
      </div>
    );
  }
  return (
    <div className="picker" ref={box}>
      <span className="picker-label" id="picker-label">作业</span>
      <button type="button" className="picker-btn" aria-haspopup="listbox" aria-expanded={open}
        aria-labelledby="picker-label picker-current" onClick={() => setOpen(!open)}>
        <span id="picker-current">{current.title}</span>
        <Icon.chevron />
      </button>
      <span className="note">{homeworkSummary(current)}</span>
      {open && (
        <div className="picker-menu" role="listbox" aria-labelledby="picker-label">
          {homeworks.map((hw) => (
            <button key={hw.key} type="button" role="option" aria-selected={hw.key === current.key} className="picker-option"
              onClick={() => { setOpen(false); onPick(hw.key); }}>
              <strong>{hw.title}</strong>
              <small>{homeworkSummary(hw)}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 题目列表。只显示题目名，不显示题号。 */
export function ProblemList({ problems, currentKey, dueRevisits, onNavigate }: { problems: ProblemRow[]; currentKey: string | null; dueRevisits: number; onNavigate?: () => void }) {
  return (
    <nav className="plist" aria-label="题目">
      {problems.map((p) => (
        <Link key={p.key} to={`/chat/${p.key}`} className="prow" aria-current={p.key === currentKey ? "true" : undefined} onClick={onNavigate}>
          <span className="prow-top">
            <span className="prow-title">{p.title}</span>
            <span className="prow-time">{p.last ? shortTime(p.last.time) : ""}</span>
          </span>
          {p.last ? (
            <span className="prow-bottom">
              <span className="prow-state">{resultLine(p.last)}</span>
              {p.last.grid ? <Cells grid={p.last.grid} /> : null}
            </span>
          ) : (
            <span className="prow-state">还没提交</span>
          )}
        </Link>
      ))}
      <Link to="/chat/general" className="prow" aria-current={currentKey === "general" ? "true" : undefined} onClick={onNavigate}>
        <span className="prow-top">
          <span className="prow-title">其他问题</span>
        </span>
        <span className="prow-state">
          {dueRevisits > 0 ? `有 ${dueRevisits} 道回访小题可以做` : "概念、题意，或者不确定是哪道题"}
        </span>
      </Link>
    </nav>
  );
}

/** 右栏：这道题的提交、这道题用到的知识点。 */
export function SidePanel({ detail, onDiff, onAsk }: { detail: ProblemDetail; onDiff: (a: string, b: string) => void; onAsk: (submissionKey: string) => void }) {
  const subs = detail.submissions;
  return (
    <div className="side">
      <section>
        <h2>这道题的提交</h2>
        {subs.length === 0 && <p className="note">还没提交</p>}
        {subs.slice(0, 5).map((sub, i) => (
          <div key={sub.key} className="side-sub">
            <div className="side-row">
              <span>
                {resultLine(sub)}
                {i > 0 && sub.passed !== null && sub.result !== "AC" ? `，过 ${sub.passed} 组` : ""}
              </span>
              <span className="side-time">{shortTime(sub.time)}</span>
            </div>
            {i === 0 && sub.grid && (
              <div className="side-row">
                <Cells grid={sub.grid} />
                {sub.result !== "AC" && sub.result !== "PENDING" && (
                  <button type="button" className="btn-text side-link" onClick={() => onAsk(sub.key)}>问问为什么错</button>
                )}
              </div>
            )}
            {i === 0 && !sub.grid && sub.result === "CE" && (
              <button type="button" className="btn-text side-link" onClick={() => onAsk(sub.key)}>问问为什么错</button>
            )}
          </div>
        ))}
        {subs.length > 5 && <p className="note">更早的 {subs.length - 5} 次在“提交”页里</p>}
        {subs.length >= 2 && (
          <button type="button" className="btn-text side-link" onClick={() => onDiff(subs[1].key, subs[0].key)}>
            看第 {subs[1].attemptNo} 次到第 {subs[0].attemptNo} 次改了什么
          </button>
        )}
      </section>
      <section>
        <h2>这道题用到的知识点</h2>
        {detail.knowledge.length === 0 && <p className="note">这道题还在整理中</p>}
        {detail.knowledge.map((k) => (
          <div key={k.name} className="side-row">
            <span>{k.name}</span>
            <Meter level={k.level} segments={k.segments} />
          </div>
        ))}
      </section>
    </div>
  );
}
