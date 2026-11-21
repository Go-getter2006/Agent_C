// 作业选择器、题目列表、右栏“这道题”。
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useSlider } from "./Slider";
import { Link } from "react-router-dom";
import { Check, ChevronDown, ChevronUp, ChevronsUpDown, Circle, CircleCheck, CircleX, FileText, GitCompare, History, Hourglass, Layers, MessageCircle } from "lucide-react";
import { api } from "../api";
import { useEscape, useLoad } from "../lib/hooks";
import { deadline, fullTime, shortTime } from "../lib/time";
import { sortProblems } from "../lib/problems";
import { mathPieces, statementBlocks } from "../lib/statement";
import type { Homework, ProblemDetail, ProblemRow, SubmissionChange } from "../types";
import { Cells, CodeBlock, Loading, Meter, Problem } from "./ui";

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
  if (p.last && ["PENDING", "UNKNOWN"].includes(p.last.result)) return <span className="prow-icon"><Hourglass size={16} aria-hidden="true" /></span>;
  if (p.last) return <span className="prow-icon bad"><CircleX size={16} aria-hidden="true" /></span>;
  return <span className="prow-icon"><Circle size={16} aria-hidden="true" /></span>;
}

/** 题目列表。只显示题目名，不显示题号。 */
export function ProblemList({ problems, currentKey, onNavigate, unread }: {
  unread?: Record<string, number>; problems: ProblemRow[]; currentKey: string | null; onNavigate?: () => void;
}) {
  const box = useRef<HTMLElement>(null);
  const ordered = sortProblems(problems);
  const slider = useSlider("plist", box, '.prow[aria-current="true"]', `${currentKey}:${ordered.map(p => p.key).join(",")}`);
  return (
    <>
      <nav className="plist" aria-label="题目" ref={box}>
        <span className={`${slider.className} prow-slider`} style={slider.style} aria-hidden="true" />
        <div className="plist-head">
          <span>题目</span>
          <span className="num">{problems.filter((p) => p.passed).length}/{problems.length} 通过</span>
        </div>
        {ordered.map((p) => (
          <Link key={p.key} to={`/homework/${p.key}`} className="prow" aria-current={p.key === currentKey ? "true" : undefined} onClick={onNavigate}>
            <RowIcon p={p} />
            <span className="prow-main">
              <span className="prow-top">
                <span className="prow-title">{p.title}{!!unread?.[p.key] && <i className="failure-dot" role="img" aria-label="未读提醒" />}</span>
                <span className="prow-time">{p.last ? shortTime(p.last.time) : ""}</span>
              </span>
              {p.last ? (
                <span className="prow-bottom">
                  <span className="prow-state">第 {p.last.attemptNo} 次，{p.last.resultText}</span>
                </span>
              ) : (
                <span className="prow-state">还没提交</span>
              )}
            </span>
          </Link>
        ))}
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

/** 正文里的 [图N] 换成图。 */
type Render = (tex: string, display: boolean) => string;
let mathModule: Promise<Render> | null = null;
/** KaTeX 只在题面里真有公式时才加载。 */
function loadMath(): Promise<Render> {
  mathModule ??= import("../lib/math").then((m) => m.renderMath);
  return mathModule;
}

/** 一段题面文字：公式用 KaTeX 排，别的原样（换行照旧）。KaTeX 还没加载好时先显示原文。 */
function MathText({ text }: { text: string }) {
  const pieces = useMemo(() => mathPieces(text), [text]);
  const hasMath = pieces.some((p) => p.kind === "math");
  const [render, setRender] = useState<Render | null>(null);
  useEffect(() => {
    if (!hasMath) return;
    let alive = true;
    loadMath().then((r) => { if (alive) setRender(() => r); }).catch(() => undefined);
    return () => { alive = false; };
  }, [hasMath]);
  if (!hasMath) return <span>{text}</span>;
  return (
    <span>
      {pieces.map((p, i) => p.kind === "text" ? <Fragment key={i}>{p.text}</Fragment>
        : render ? <span key={i} className={p.display ? "stmt-math stmt-math-block" : "stmt-math"} dangerouslySetInnerHTML={{ __html: render(p.tex, p.display) }} />
          : <span key={i} className="stmt-math-raw">{p.tex}</span>)}
    </span>
  );
}

function WithFigures({ text, byN, sample = false }: { text: string; byN: Map<number, Figure>; sample?: boolean }) {
  const parts = text.split(/\[图(\d+)\]/);
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 0) {
          if (sample) return <Fragment key={i}>{part}</Fragment>;
          // 图是单独一块：紧挨着图的那个换行去掉，免得多出空行
          let s = part;
          if (i > 0) s = s.replace(/^\n/, "");
          if (i < parts.length - 1) s = s.replace(/\n$/, "");
          return s ? <MathText key={i} text={s} /> : null;
        }
        const f = byN.get(Number(part));
        return f ? <FigureImg key={i} f={f} /> : <Fragment key={i}>（题面图片未取得）</Fragment>;
      })}
    </>
  );
}

function FigureImg({ f }: { f: Figure }) {
  return <img className="stmt-fig" src={f.url} alt={`示意图 ${f.n}`} loading="lazy"
    width={f.width} height={f.height} style={{ aspectRatio: `${f.width} / ${f.height}` }} />;
}

/** 右栏最上面：题目内容，直接显示全文。文字整理不出来时直接显示题面 PDF 的各页。 */
function Statement({ text, figures, pages, onDocument }: { text: string; figures: Figure[]; pages: string[]; onDocument?: () => void }) {
  const byN = new Map(figures.map((f) => [f.n, f]));
  // 文字里提到了的图放在提到的位置，没提到的放在最后
  const mentioned = new Set(Array.from(text.matchAll(/\[图(\d+)\]/g), (x) => Number(x[1])));
  return (
    <section className="side-card" aria-labelledby="side-stmt">
      <div className="side-statement-head"><h2 id="side-stmt"><FileText size={16} aria-hidden="true" />题目</h2>
        {onDocument && <button type="button" className="btn btn-sm" onClick={onDocument}>原始 PDF</button>}
      </div>
      {!text.trim() && pages.length === 0 && <p className="note">题面正在整理，也可以查看原始 PDF。</p>}
      {text.trim() && !pages.length ? (
        <div className="stmt">
          {statementBlocks(text).map((b, i) =>
            b.kind === "h" ? <h3 key={i} className="stmt-h">{b.text}</h3>
              : b.kind === "sample" ? <pre key={i} className="stmt-sample"><WithFigures text={b.text} byN={byN} sample /></pre>
                : <p key={i} className="stmt-p"><WithFigures text={b.text} byN={byN} /></p>)}
          {figures.filter((f) => !mentioned.has(f.n)).map((f) => <FigureImg key={`f${f.n}`} f={f} />)}
        </div>
      ) : (
        <div className="stmt-pages">
          {pages.map((url, i) => <img key={url} src={url} alt={`题目第 ${i + 1} 页`} loading="lazy" />)}
        </div>
      )}
    </section>
  );
}

/** 一次提交改了什么，一句话（代码和完整对照按需打开）。 */
export function changeSummary(change: SubmissionChange | undefined): string | null {
  if (!change || !change.available) return null;
  const where = change.lines.map((l) => (l.from === l.to ? `第 ${l.from} 行` : `第 ${l.from}~${l.to} 行`)).join("、");
  const parts = [change.changed === 0 ? "代码没改" : change.big ? `大幅改动 ${change.changed} 行` : `改了 ${change.changed} 行${where ? `（${where}）` : ""}`];
  if (change.fixed.length) parts.push(`修好了：${change.fixed.join("、")}`);
  if (change.still.length) parts.push(`还在：${change.still.join("、")}`);
  if (change.new.length) parts.push(`新出现：${change.new.join("、")}`);
  return parts.join("；");
}

/** 展开在这次提交下面的代码（不另开窗口）。 */
function SourceInline({ sub, onAsk }: { sub: ProblemDetail["submissions"][number]; onAsk: () => void }) {
  const code = useLoad(() => api.source(sub.key), [sub.key]);
  return (
    <div className="srow-code" id={`code-${sub.key}`}>
      {code.loading && !code.data && <Loading what="正在取代码" lines={2} />}
      {code.error && <Problem message={code.error} onRetry={code.reload} />}
      {code.data && <CodeBlock caption={`第 ${sub.attemptNo} 次提交的代码`} lines={code.data.lines} />}
      <button type="button" className="btn btn-sm" onClick={onAsk}><MessageCircle size={15} aria-hidden="true" />就这一版问</button>
    </div>
  );
}

/** 本题的一次提交：第几次、时间、结果、测试点方块、通过组数、看代码（就地展开）。方块旁边一定有文字。 */
function SubmissionRow({ sub, change, open, onToggle, onAsk }: {
  sub: ProblemDetail["submissions"][number]; change?: SubmissionChange; open: boolean; onToggle: () => void; onAsk: () => void;
}) {
  const ok = sub.result === "AC";
  const wait = sub.result === "PENDING" || sub.result === "UNKNOWN";
  const passedCount = sub.grid ? sub.grid.filter(Boolean).length : 0;
  const summary = changeSummary(change);
  return (
    <div className={open ? "srow srow-open" : "srow"}>
      <span className="srow-when"><strong>第 {sub.attemptNo} 次</strong><small>{fullTime(sub.time)}</small></span>
      <span className={ok ? "srow-result ok" : wait ? "srow-result" : "srow-result bad"}>{sub.resultText}</span>
      <span className="srow-cells">
        {sub.grid ? <Cells grid={sub.grid} /> : <span className="note">{sub.result === "PENDING" ? "正在评测" : sub.result === "CE" ? "编译错误，没运行测试点" : "测试点结果正在取"}</span>}
      </span>
      <span className="srow-count num">{sub.grid ? `${passedCount}/${sub.grid.length} 通过` : ""}</span>
      <button type="button" className="btn btn-sm" aria-expanded={open} aria-controls={`code-${sub.key}`} onClick={onToggle}>
        {open ? "收起代码" : "看代码"}{open ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
      </button>
      {summary && <p className="srow-change">{summary}</p>}
      {open && <SourceInline sub={sub} onAsk={onAsk} />}
    </div>
  );
}

/** 右栏：题面、本题的每一次提交（最近的在上，都直接列出来；看代码在那一行下面展开）、用到的知识点。 */
export function SidePanel({ detail, onDiff, onDocument, onAsk }: {
  detail: ProblemDetail; onDiff: (a: string, b: string) => void; onDocument?: () => void; onAsk: (key: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => setOpen(null), [detail.key]);
  const toggle = (key: string) => {
    if (open !== key) void api.event("open_code", key);
    setOpen(open === key ? null : key);
  };
  const subs = detail.submissions;
  return (
    <div className="side">
      <Statement text={detail.statement ?? ""} figures={detail.figures ?? []} pages={detail.pages ?? []} onDocument={onDocument} />
      <section className="side-card" aria-labelledby="side-subs">
        <div className="side-statement-head">
          <h2 id="side-subs"><History size={16} aria-hidden="true" />本题提交</h2>
          {subs.length > 0 && <span className="note num">共 {subs.length} 次</span>}
        </div>
        {subs.length === 0 && <p className="note">还没提交。在 XDOJ 上交一次，这里几秒钟后就能看到。</p>}
        <div className="srows">
          {subs.map((sub, i) => (
            <SubmissionRow key={sub.key} sub={sub} change={subs[i + 1] ? detail.changes?.[sub.key] : undefined}
              open={open === sub.key} onToggle={() => toggle(sub.key)} onAsk={() => onAsk(sub.key)} />
          ))}
        </div>
        {subs.length > 1 && (
          <button type="button" className="btn btn-sm srow-compare" onClick={() => onDiff(subs[1].key, subs[0].key)}>
            <GitCompare size={15} aria-hidden="true" />比较最近两次修改
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
