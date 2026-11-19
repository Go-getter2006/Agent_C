// 基础组件：测试点格子、结果标签、掌握档位、代码块、抽屉、指标卡、加载和出错。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, CircleCheck, CircleX, Copy, FileCode2, Hourglass, RotateCcw, TrendingDown, TrendingUp, X, type LucideIcon } from "lucide-react";
import { useEscape } from "../lib/hooks";
import type { SubmissionView } from "../types";

// ---------- 测试点格子 ----------
export function gridLabel(grid: boolean[]): string {
  const ok = grid.filter(Boolean).length;
  return ok === grid.length ? `${grid.length} 组全过` : `${grid.length} 组过 ${ok} 组`;
}

/** 每个测试点一个方格：实心绿 = 过，空心红框 = 没过。格子上不带任何测试数据，悬停也没有。 */
export function Cells({ grid, large = false }: { grid: boolean[]; large?: boolean }) {
  return (
    <span className={large ? "cells cells-lg" : "cells"} role="img" aria-label={gridLabel(grid)}>
      {grid.map((ok, i) => (
        <span key={i} className={ok ? "cell cell-pass" : "cell cell-fail"} />
      ))}
    </span>
  );
}

/** “第 3 次，答案错误”；withCount 时加上“，5 组过 4 组”。 */
export function resultLine(sub: SubmissionView, withCount = false): string {
  let text = `第 ${sub.attemptNo} 次，${sub.resultText}`;
  if (withCount && sub.grid && sub.result !== "AC") text += `，${gridLabel(sub.grid)}`;
  return text;
}

/** 结果标签：通过是绿色，评测中是灰色，其余都是红色。图标和文字一起出现，不只靠颜色。 */
export function ResultPill({ sub, short = false }: { sub: SubmissionView; short?: boolean }) {
  if (sub.result === "AC") {
    return <span className="pill pill-pass"><CircleCheck aria-hidden="true" />{short ? "通过" : sub.resultText}</span>;
  }
  if (sub.result === "PENDING") {
    return <span className="pill"><Hourglass aria-hidden="true" />{sub.resultText}</span>;
  }
  return <span className="pill pill-fail"><CircleX aria-hidden="true" />{sub.resultText}</span>;
}

// ---------- 掌握档位：三段横条，永远带文字 ----------
const LEVEL_CLASS: Record<string, string> = { 已掌握: "lv-mastered", 基本掌握: "lv-basic", 待加强: "lv-weak", 还没练到: "lv-none" };
export const levelClass = (level: string) => LEVEL_CLASS[level] ?? "lv-none";
export const LEVELS = ["已掌握", "基本掌握", "待加强", "还没练到"];

export function Meter({ level, segments, label }: { level: string; segments: number; label?: string }) {
  const aria = segments === 0 ? "还没练到" : `三档里的第 ${["一", "二", "三"][segments - 1]}档`;
  return (
    <span className={`meter ${levelClass(level)}`}>
      <span className="meter-bars" role="img" aria-label={aria}>
        {[1, 2, 3].map((i) => (
          <span key={i} className={i <= segments ? "on" : undefined} />
        ))}
      </span>
      {label ?? level}
    </span>
  );
}

export function LevelLegend({ levels = LEVELS }: { levels?: string[] }) {
  return (
    <div className="legend" aria-hidden="true">
      {levels.map((l) => (
        <span key={l}><i className={`swatch sw-${levelClass(l).slice(3)}`} />{l}</span>
      ))}
    </div>
  );
}

// ---------- 行内代码 ----------
export function Inline({ text }: { text: string }) {
  const parts = text.split(/(`[^`\n]+`)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.length > 2 && part.startsWith("`") && part.endsWith("`") ? <code key={i}>{part.slice(1, -1)}</code> : part,
      )}
    </>
  );
}

// ---------- 带行号的代码块 ----------
interface CodeBlockProps {
  caption: string;
  start?: number;
  lines: string[];
  mark?: number[];
  removed?: number[];
  numbers?: (number | null)[]; // 每一行显示的行号；差异视图里有的行没有行号
  onCopy?: () => void;
  copyable?: boolean;
}

export function CodeBlock({ caption, start = 1, lines, mark = [], removed = [], numbers, onCopy, copyable = true }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true);
      timer.current = window.setTimeout(() => setCopied(false), 1600);
      onCopy?.();
    } catch {
      /* 浏览器不让复制时不做任何事 */
    }
  };

  return (
    <div className="code">
      <div className="code-cap">
        <span><FileCode2 aria-hidden="true" />{caption}</span>
        {copyable && (
          <button type="button" className="code-copy" onClick={copy} aria-label={copied ? "已复制" : "复制代码"} title={copied ? "已复制" : "复制代码"}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
          </button>
        )}
      </div>
      <div className="code-body" tabIndex={0} role="group" aria-label={caption}>
        <div className="code-lines">
          {lines.map((text, i) => {
            const no = numbers ? numbers[i] : start + i;
            const cls = no !== null && mark.includes(no) ? " marked" : no !== null && removed.includes(no) ? " removed" : "";
            return (
              <div key={i} className={"code-line" + cls}>
                <span className="code-no">{no ?? ""}</span>
                <span className="code-text">
                  {text.trim() === "______" ? (
                    <>
                      {text.slice(0, text.indexOf("_"))}
                      <span className="code-blank" aria-label="这一行空着">&nbsp;</span>
                    </>
                  ) : (
                    text || " "
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ---------- 抽屉 ----------
export function Drawer({ side, title, onClose, children, dark = false }: { side: "left" | "right"; title: string; onClose: () => void; children: ReactNode; dark?: boolean }) {
  useEscape(onClose);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button")?.focus();
  }, []);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div ref={ref} className={`drawer drawer-${side}${dark ? " drawer-dark" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head">
          <span>{title}</span>
          <button type="button" className="icon-btn" onClick={onClose} aria-label={`关闭${title}`}>
            <X size={20} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </>
  );
}

// ---------- 加载、出错、空状态 ----------
export function Loading({ what = "正在加载", lines = 3 }: { what?: string; lines?: number }) {
  // 很短的请求不画占位条，避免在正常切页时闪一下。
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), 240);
    return () => window.clearTimeout(timer);
  }, []);
  if (!visible) return <span className="sr-only" role="status">{what}</span>;
  return (
    <div className="loading" role="status" aria-label={what}>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton" style={{ width: `${[92, 76, 58, 84, 66][i % 5]}%` }} />
      ))}
      <span className="sr-only">{what}</span>
    </div>
  );
}

export function Problem({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="problem" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          <RotateCcw size={14} aria-hidden="true" />再试一次
        </button>
      )}
    </div>
  );
}

export function Empty({ icon: Icon, title, children }: { icon?: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {Icon && <span className="empty-icon"><Icon size={18} aria-hidden="true" /></span>}
      <strong>{title}</strong>
      {children && <span className="note">{children}</span>}
    </div>
  );
}

// ---------- 卡片、指标卡 ----------
export function Card({ title, sub, action, children, className = "", bodyClass = "card-body", id }: {
  title?: ReactNode; sub?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; id?: string;
}) {
  return (
    <section className={`card ${className}`} aria-labelledby={id && title ? `${id}-t` : undefined}>
      {(title || action) && (
        <div className="card-head">
          <div>
            {title && <h2 id={id ? `${id}-t` : undefined}>{title}</h2>}
            {sub && <p className="card-sub">{sub}</p>}
          </div>
          {action}
        </div>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  );
}

/** 指标卡：一个数，加上和上周比的变化。变化的颜色只表示好坏方向，旁边一定有箭头和文字。 */
export function Stat({ icon: Icon, label, value, unit, delta, deltaText, goodWhenUp = true, foot, children }: {
  icon?: LucideIcon; label: string; value: ReactNode; unit?: string; delta?: number | null; deltaText?: string;
  goodWhenUp?: boolean; foot?: ReactNode; children?: ReactNode;
}) {
  let deltaEl: ReactNode = null;
  if (delta !== undefined && delta !== null) {
    const up = delta > 0;
    const cls = delta === 0 ? "delta" : up === goodWhenUp ? "delta delta-up" : "delta delta-down";
    deltaEl = (
      <span className={cls}>
        {delta > 0 ? <TrendingUp aria-hidden="true" /> : delta < 0 ? <TrendingDown aria-hidden="true" /> : null}
        {delta > 0 ? "+" : ""}{delta}{deltaText ?? ""}
      </span>
    );
  }
  return (
    <div className="card stat">
      <span className="stat-label">{Icon && <Icon aria-hidden="true" />}{label}</span>
      <div className="stat-row">
        <span className="stat-value num">{value}{unit && <small>{unit}</small>}</span>
        {deltaEl}
      </div>
      {children}
      {foot && <span className="stat-foot">{foot}</span>}
    </div>
  );
}

/** 一排事实：小标签、一个不太大的数、一句说明。学生端用它代替大数字卡片。 */
export function Facts({ items, label }: { items: { label: string; value: ReactNode; note?: ReactNode }[]; label: string }) {
  return (
    <dl className="card facts-strip" aria-label={label}>
      {items.map((it) => (
        <div key={it.label} className="fact">
          <dt>{it.label}</dt>
          <dd className="fact-value num">{it.value}</dd>
          {it.note && <dd className="fact-note">{it.note}</dd>}
        </div>
      ))}
    </dl>
  );
}

/** 页签：一页里互斥的几块内容。选中的那个有下划线；count 是旁边的小数字（0 不显示）。 */
export function Tabs<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode; count?: number }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" id={`tab-${o.value}`} aria-selected={value === o.value}
          aria-controls={`panel-${o.value}`} onClick={() => onChange(o.value)}>
          {o.label}{o.count ? <span className="tab-count num">{o.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** 分段按钮：几个互斥的选项。 */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** 头像：名字的最后一个字。 */
export function Avatar({ name, dark = false }: { name: string; dark?: boolean }) {
  const ch = name.trim().slice(-1) || "?";
  return <span className={dark ? "avatar avatar-dark" : "avatar"} aria-hidden="true">{ch}</span>;
}
