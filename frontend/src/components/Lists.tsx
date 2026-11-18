// 作业选择器、题目列表、右栏“这道题”。
import { useEffect, useRef, useState } from "react";
import { useSlider } from "./Slider";
import { Link } from "react-router-dom";
import { Check, ChevronDown, ChevronUp, ChevronsUpDown, Circle, CircleCheck, CircleX, ExternalLink, FileText, GitCompare, History, Layers, MessageCircle, MessagesSquare, RotateCcw } from "lucide-react";
import { useEscape } from "../lib/hooks";
import { deadline, shortTime } from "../lib/time";
import type { Homework, ProblemDetail, ProblemRow, SubmissionChange } from "../types";
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
  const box = useRef<HTMLElement>(null);
  const slider = useSlider("plist", box, '.prow[aria-current="true"]', `${currentKey}:${problems.length}`);
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
      <nav className="plist" aria-label="题目" ref={box}>
        <span className={`${slider.className} prow-slider`} style={slider.style} aria-hidden="true" />
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

/** 一次提交改了什么、修好了什么问题。直接显示，不用点进去看差异。 */
export function ChangeNote({ change, onDiff }: { change: SubmissionChange | undefined; onDiff?: () => void }) {
  if (!change) return null;
  if (!change.available) return <p className="chg-note note">代码还在取，稍后显示改了什么</p>;
  const where = change.lines.map((l) => (l.from === l.to ? `第 ${l.from} 行` : `第 ${l.from}~${l.to} 行`)).join("、");
  return (
    <div className="chg">
      <p className="chg-head">
        {change.changed === 0 ? "代码没改，又交了一次" : change.big ? `大幅改动：改了 ${change.changed} 行，像是换了一种写法` : `改了 ${change.changed} 行${where ? `（${where}）` : ""}`}
      </p>
      {(change.fixed.length > 0 || change.passedNow) && (
        <p className="chg-fixed"><CircleCheck size={13} aria-hidden="true" />{change.fixed.length ? `修好了：${change.fixed.join("、")}` : "这次通过了"}</p>
      )}
      {change.still.length > 0 && <p className="chg-still"><CircleX size={13} aria-hidden="true" />还在：{change.still.join("、")}</p>}
      {change.new.length > 0 && <p className="chg-still"><CircleX size={13} aria-hidden="true" />新出现：{change.new.join("、")}</p>}
      {change.excerpt.length > 0 && !change.big && (
        <pre className="chg-code" aria-label="改动的代码">
          {change.excerpt.map((e, i) => (
            <span key={i} className={e.type === "add" ? "chg-add" : "chg-del"}>
              <i>{e.type === "add" ? "+" : "−"}</i><b className="num">{e.n}</b>{e.text}{"\n"}
            </span>
          ))}
        </pre>
      )}
      {onDiff && change.changed > 0 && (
        <button type="button" className="link chg-more" onClick={onDiff}><GitCompare size={13} aria-hidden="true" />看完整的对照</button>
      )}
    </div>
  );
}

/** 题目内容：默认收起，只露前几行。 */
type Figure = ProblemDetail["figures"][number];

/** 题面文字里的 [图N] 换成图；文字里没提到的图放在最后。 */
function StatementBody({ text, figures }: { text: string; figures: Figure[] }) {
  const byN = new Map(figures.map((f) => [f.n, f]));
  const used = new Set<number>();
  const parts = text.split(/\[图(\d+)\]/);
  const img = (f: Figure) => (
    <img key={`fig-${f.n}`} className="stmt-fig" src={f.url} alt={`示意图 ${f.n}`} loading="lazy"
      width={f.width} height={f.height} style={{ aspectRatio: `${f.width} / ${f.height}` }} />
  );
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) {
          // 图是单独一块：紧挨着图的那个换行去掉，免得多出空行
          let s = part;
          if (i > 0) s = s.replace(/^\n/, "");
          if (i < parts.length - 1) s = s.replace(/\n$/, "");
          return s ? <span key={i}>{s}</span> : null;
        }
        const f = byN.get(Number(part));
        if (!f) return null;
        used.add(f.n);
        return img(f);
      })}
      {figures.filter((f) => !used.has(f.n)).map(img)}
    </>
  );
}

function Statement({ text, figures, original }: { text: string; figures: Figure[]; original: string | null }) {
  const [open, setOpen] = useState(false);
  if (!text.trim() && figures.length === 0 && !original) return null;
  const long = text.length > 260 || text.split("\n").length > 8 || figures.length > 0;
  return (
    <section className="side-card" aria-labelledby="side-stmt">
      <div className="side-card-head">
        <h2 id="side-stmt"><FileText size={16} aria-hidden="true" />题目</h2>
        {original && <a className="link side-link" href={original} target="_blank" rel="noreferrer">看原题<ExternalLink size={13} aria-hidden="true" /></a>}
      </div>
      {text.trim() ? (
        <div className={open || !long ? "stmt" : "stmt stmt-clip"}><StatementBody text={text} figures={figures} /></div>
      ) : (
        <p className="note">题目内容还在整理，先看原题。</p>
      )}
      {text.trim() && long && (
        <button type="button" className="link side-link" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <><ChevronUp size={14} aria-hidden="true" />收起</> : <><ChevronDown size={14} aria-hidden="true" />展开全部</>}
        </button>
      )}
    </section>
  );
}

/** 右栏：这道题的提交、这道题用到的知识点。 */
export function SidePanel({ detail, onDiff, onAsk }: { detail: ProblemDetail; onDiff: (a: string, b: string) => void; onAsk: (submissionKey: string) => void }) {
  const subs = detail.submissions;
  const latest = subs[0];
  const canAsk = latest && latest.result !== "AC" && latest.result !== "PENDING";
  return (
    <div className="side">
      <Statement text={detail.statement ?? ""} figures={detail.figures ?? []} original={detail.original ?? null} />
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
            {subs.length > 1 && <ChangeNote change={detail.changes?.[latest.key]} onDiff={() => onDiff(subs[1].key, latest.key)} />}
            {canAsk && (
              <button type="button" className="btn btn-primary" onClick={() => onAsk(latest.key)}>
                <MessageCircle size={16} aria-hidden="true" />问问为什么错
              </button>
            )}
          </div>
        )}
        {subs.length > 1 && (
          <div className="timeline">
            {subs.slice(1, 6).map((sub, i) => (
              <div key={sub.key} className="tl-item">
                <span className={sub.result === "AC" ? "tl-dot ok" : sub.result === "PENDING" ? "tl-dot wait" : "tl-dot"} aria-hidden="true" />
                <span className="tl-main">
                  <span className="tl-top">
                    <span>第 {sub.attemptNo} 次，{sub.resultText}{sub.grid && sub.result !== "AC" ? `，过 ${sub.grid.filter(Boolean).length} 组` : ""}</span>
                    <span className="side-time">{shortTime(sub.time)}</span>
                  </span>
                  {subs[i + 2] && <ChangeNote change={detail.changes?.[sub.key]} onDiff={() => onDiff(subs[i + 2].key, sub.key)} />}
                </span>
              </div>
            ))}
          </div>
        )}
        {subs.length > 6 && <p className="note">更早的 {subs.length - 6} 次在“提交”页里</p>}
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
