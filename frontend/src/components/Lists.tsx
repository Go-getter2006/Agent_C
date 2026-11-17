// 作业选择器、题目列表、右栏“这道题”。
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Check, ChevronsUpDown, Circle, CircleCheck, CircleX, GitCompare, History, Layers, MessageCircle, MessagesSquare, RotateCcw } from "lucide-react";
import { useEscape } from "../lib/hooks";
import { deadline, shortTime } from "../lib/time";
import type { Homework, ProblemDetail, ProblemRow } from "../types";
import { Cells, Meter, ResultPill, gridLabel } from "./ui";

export function homeworkSummary(hw: Homework): string {
  return `已通过 ${hw.passed} 题，共 ${hw.total} 题，${deadline(hw.endTime, hw.ended)}`;
}

/** 作业选择器：只列出老师布置给这个学生班级的作业。 */
export function HomeworkPicker({ homeworks, current, onPick, compact = false }: {
  homeworks: Homework[]; current: Homework | null; onPick: (key: string) => void; compact?: boolean;
}) {
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
      <div className={compact ? "picker picker-compact" : "picker"}>
        <span className="picker-label">作业</span>
        <span className="note">老师还没有给你的班级布置作业</span>
      </div>
    );
  }
  const pct = current.total ? (100 * current.passed) / current.total : 0;
  return (
    <div className={compact ? "picker picker-compact" : "picker"} ref={box}>
      <span className="picker-label" id="picker-label">作业</span>
      <button type="button" className="picker-btn" aria-haspopup="listbox" aria-expanded={open}
        aria-labelledby="picker-label picker-current" onClick={() => setOpen(!open)}>
        <span>
          <strong id="picker-current">{current.title}</strong>
          <small>{homeworkSummary(current)}</small>
          <span className="picker-progress" aria-hidden="true">
            <span className="bar bar-pass"><span style={{ width: `${pct}%` }} /></span>
          </span>
        </span>
        <ChevronsUpDown size={16} aria-hidden="true" />
      </button>
      {open && (
        <div className="picker-menu pop" role="listbox" aria-labelledby="picker-label">
          {homeworks.map((hw) => (
            <button key={hw.key} type="button" role="option" aria-selected={hw.key === current.key} className="picker-option"
              onClick={() => { setOpen(false); onPick(hw.key); }}>
              <span>
                <strong>{hw.title}</strong>
                <small>{homeworkSummary(hw)}</small>
              </span>
              {hw.key === current.key && <Check size={16} aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RowIcon({ p }: { p: ProblemRow }) {
  if (p.passed) return <span className="prow-icon ok"><CircleCheck size={16} aria-hidden="true" /></span>;
  if (p.last) return <span className="prow-icon bad"><CircleX size={16} aria-hidden="true" /></span>;
  return <span className="prow-icon"><Circle size={16} aria-hidden="true" /></span>;
}

/** 题目列表。只显示题目名，不显示题号。 */
export function ProblemList({ problems, currentKey, dueReviews, onNavigate }: {
  problems: ProblemRow[]; currentKey: string | null; dueReviews: number; onNavigate?: () => void;
}) {
  return (
    <>
      {dueReviews > 0 && (
        <Link to="/review" className="review-nudge" onClick={onNavigate}>
          <span className="nudge-icon"><RotateCcw size={16} aria-hidden="true" /></span>
          <span>
            <strong>有 {dueReviews} 项该复习了</strong>
            <small>做一道小题，几分钟就好</small>
          </span>
        </Link>
      )}
      <nav className="plist" aria-label="题目">
        <div className="plist-head">
          <span>题目</span>
          <span className="num">{problems.filter((p) => p.passed).length}/{problems.length} 通过</span>
        </div>
        {problems.map((p) => (
          <Link key={p.key} to={`/chat/${p.key}`} className="prow" aria-current={p.key === currentKey ? "true" : undefined} onClick={onNavigate}>
            <RowIcon p={p} />
            <span className="prow-main">
              <span className="prow-top">
                <span className="prow-title">{p.title}</span>
                <span className="prow-time">{p.last ? shortTime(p.last.time) : ""}</span>
              </span>
              {p.last ? (
                <span className="prow-bottom">
                  <span className="prow-state">第 {p.last.attemptNo} 次，{p.last.resultText}</span>
                  {p.last.grid ? <Cells grid={p.last.grid} /> : null}
                </span>
              ) : (
                <span className="prow-state">还没提交</span>
              )}
            </span>
          </Link>
        ))}
        <div className="plist-sep" />
        <Link to="/chat/general" className="prow" aria-current={currentKey === "general" ? "true" : undefined} onClick={onNavigate}>
          <span className="prow-icon"><MessagesSquare size={16} aria-hidden="true" /></span>
          <span className="prow-main">
            <span className="prow-title">其他问题</span>
            <span className="prow-state">概念、题意，或者不确定是哪道题</span>
          </span>
        </Link>
      </nav>
    </>
  );
}

/** 右栏：这道题的提交、这道题用到的知识点。 */
export function SidePanel({ detail, onDiff, onAsk }: { detail: ProblemDetail; onDiff: (a: string, b: string) => void; onAsk: (submissionKey: string) => void }) {
  const subs = detail.submissions;
  const latest = subs[0];
  const canAsk = latest && latest.result !== "AC" && latest.result !== "PENDING";
  return (
    <div className="side">
      <section className="side-card" aria-labelledby="side-subs">
        <h2 id="side-subs"><History size={16} aria-hidden="true" />这道题的提交</h2>
        {subs.length === 0 && <p className="note">还没提交。在 XDOJ 上交一次，这里几秒钟后就能看到。</p>}
        {latest && (
          <div className="side-latest">
            <div className="side-row">
              <span className="eyebrow">最近一次，第 {latest.attemptNo} 次</span>
              <span className="side-time">{shortTime(latest.time)}</span>
            </div>
            <div className="side-row">
              <ResultPill sub={latest} />
              {latest.grid && <span className="note num">{gridLabel(latest.grid)}</span>}
            </div>
            {latest.grid && <Cells grid={latest.grid} large />}
            {canAsk && (
              <button type="button" className="btn btn-primary" onClick={() => onAsk(latest.key)}>
                <MessageCircle size={16} aria-hidden="true" />问问为什么错
              </button>
            )}
          </div>
        )}
        {subs.length > 1 && (
          <div className="timeline">
            {subs.slice(1, 6).map((sub) => (
              <div key={sub.key} className="tl-item">
                <span className={sub.result === "AC" ? "tl-dot ok" : sub.result === "PENDING" ? "tl-dot wait" : "tl-dot"} aria-hidden="true" />
                <span>第 {sub.attemptNo} 次，{sub.resultText}{sub.grid && sub.result !== "AC" ? `，过 ${sub.grid.filter(Boolean).length} 组` : ""}</span>
                <span className="side-time">{shortTime(sub.time)}</span>
              </div>
            ))}
          </div>
        )}
        {subs.length > 6 && <p className="note">更早的 {subs.length - 6} 次在“提交”页里</p>}
        {subs.length >= 2 && (
          <button type="button" className="link side-link" onClick={() => onDiff(subs[1].key, subs[0].key)}>
            <GitCompare size={15} aria-hidden="true" />看第 {subs[1].attemptNo} 次到第 {subs[0].attemptNo} 次改了什么
          </button>
        )}
      </section>
      <section className="side-card" aria-labelledby="side-kp">
        <h2 id="side-kp"><Layers size={16} aria-hidden="true" />用到的知识点</h2>
        {detail.knowledge.length === 0 && <p className="note">这道题还在整理中</p>}
        <div className="kp-list">
          {detail.knowledge.map((k) => (
            <div key={k.name} className="side-row">
              <span>{k.name}</span>
              <Meter level={k.level} segments={k.segments} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
