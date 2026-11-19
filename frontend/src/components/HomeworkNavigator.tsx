import { useLayoutEffect, useRef, type MouseEvent } from "react";
import { ArrowLeft, ChevronRight, FileText } from "lucide-react";
import type { Homework, ProblemRow } from "../types";
import { homeworkSummary, ProblemList } from "./Lists";

interface Props {
  unread?: { problems: Record<string, number>; homeworks: string[] };
  homeworks: Homework[];
  current: Homework | null;
  problems: ProblemRow[];
  currentKey: string | null;
  entered: boolean;
  loading: boolean;
  ready: boolean;
  error: string | null;
  onEnter: (key: string) => void;
  onBack: () => void;
  onNavigate?: () => void;
}

/** 两层导航常驻同一左栏；只平移轨道，不重建整个工作台。 */
export default function HomeworkNavigator(p: Props) {
  const root = useRef<HTMLDivElement>(null);
  const returnButton = useRef<HTMLButtonElement>(null);
  const lastEntry = useRef<HTMLButtonElement | null>(null);
  const pointerNavigation = useRef(false);
  const previous = useRef(p.entered);
  useLayoutEffect(() => {
    if (previous.current !== p.entered) {
      if (!pointerNavigation.current) {
        if (p.entered) returnButton.current?.focus({ preventScroll: true });
        else lastEntry.current?.focus({ preventScroll: true });
      }
      previous.current = p.entered;
    }
  }, [p.entered]);
  const motion = (e: MouseEvent) => {
    pointerNavigation.current = e.detail > 0;
    root.current?.setAttribute("data-motion", e.detail > 0 ? "slide" : "none");
  };
  return (
    <div className="homework-nav" ref={root} data-level={p.entered ? "problems" : "homeworks"}>
      <div className="homework-nav-track">
        <section className="homework-nav-pane" aria-label="全部作业" aria-hidden={p.entered} {...(p.entered ? { inert: "" } : {})}>
          <header className="homework-index-head"><h2>全部作业</h2><span className="note num">{p.homeworks.length} 次</span></header>
          <nav className="homework-index" aria-label="选择作业">
            {p.homeworks.map(hw => (
              <button type="button" key={hw.key} className="homework-entry" aria-label={hw.title + (p.unread?.homeworks.includes(hw.key) ? "，有未读提醒" : "")}
                aria-describedby={`summary-${hw.key}`} onClick={e => { motion(e); lastEntry.current = e.currentTarget; p.onEnter(hw.key); }}>
                <FileText size={18} aria-hidden="true" />
                <span className="homework-entry-main"><strong>{hw.title}{p.unread?.homeworks.includes(hw.key) && <i className="failure-dot" aria-hidden="true" />}</strong>
                  <span id={`summary-${hw.key}`} className="note">{homeworkSummary(hw)}</span>
                  <span className="bar bar-pass" aria-hidden="true"><span style={{ width: `${hw.total ? hw.passed / hw.total * 100 : 0}%` }} /></span>
                </span><ChevronRight size={18} aria-hidden="true" />
              </button>
            ))}
            {!p.homeworks.length && <p className="plist-empty" role="status">{p.loading ? "正在取作业…" : p.error ?? "老师还没有给你的班级布置作业"}</p>}
          </nav>
        </section>
        <section className="homework-nav-pane" aria-label="本次作业题目" aria-hidden={!p.entered} {...(!p.entered ? { inert: "" } : {})}>
          <header className="homework-detail-head">
            <button ref={returnButton} type="button" className="homework-back" aria-label="返回全部作业" onClick={e => { motion(e); p.onBack(); }}><ArrowLeft size={21} aria-hidden="true" />全部作业</button>
            <h2>{p.current?.title ?? "作业题目"}</h2>
            {p.current && <p className="note">{homeworkSummary(p.current)}</p>}
          </header>
          {p.error && <p className="plist-empty" role="status">{p.error}</p>}
          {!p.ready && !p.error && <p className="plist-empty" role="status">正在取这次作业的题目…</p>}
          {p.ready && p.problems.length === 0 && <p className="plist-empty">这次作业里还没有题目</p>}
          <div className="problem-list-content" aria-busy={!p.ready} {...(!p.ready ? { inert: "" } : {})}>
            <ProblemList unread={p.unread?.problems} problems={p.ready ? p.problems : []} currentKey={p.currentKey} onNavigate={p.onNavigate} />
          </div>
        </section>
      </div>
    </div>
  );
}
