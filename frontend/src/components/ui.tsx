// 基础组件：图标、测试点格子、掌握档位、代码块、抽屉。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useEscape } from "../lib/hooks";
import type { SubmissionView } from "../types";

// ---------- 图标：1.8 线宽的描边图标 ----------
function Svg({ children, size = 20, stroke = 1.8 }: { children: ReactNode; size?: number; stroke?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

export const Icon = {
  chat: () => <Svg><path d="M4 5h16v11H9l-5 4z" /></Svg>,
  list: () => <Svg><path d="M5 7h14M5 12h14M5 17h9" /></Svg>,
  bars: () => <Svg><path d="M5 19V10M12 19V5M19 19v-6" /></Svg>,
  people: () => <Svg><path d="M4 19c0-3 2.5-5 5-5s5 2 5 5M9 10a3 3 0 100-6 3 3 0 000 6zM15 14c2.500 0 5 1.500 5 5M16 10a3 3 0 100-6" /></Svg>,
  pin: () => <Svg><path d="M12 21s7-6.200 7-11a7 7 0 10-14 0c0 4.800 7 11 7 11z" /><path d="M12 12.500a2.500 2.500 0 100-5 2.500 2.500 0 000 5z" /></Svg>,
  graph: () => <Svg><path d="M6 7a2 2 0 100-4 2 2 0 000 4zM18 11a2 2 0 100-4 2 2 0 000 4zM8 21a2 2 0 100-4 2 2 0 000 4zM7.500 6.200l8.700 2.200M16.700 10.600L9.200 17.600" /></Svg>,
  plug: () => <Svg><path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0zM12 17v4" /></Svg>,
  chevron: () => <Svg size={16} stroke={2}><path d="M6 9l6 6 6-6" /></Svg>,
  close: () => <Svg><path d="M6 6l12 12M18 6L6 18" /></Svg>,
  copy: () => <Svg size={16}><path d="M9 9h10v11H9zM5 15V4h10" /></Svg>,
  check: () => <Svg size={16} stroke={2}><path d="M5 12.500l4.500 4.500L19 7.500" /></Svg>,
  back: () => <Svg><path d="M15 6l-6 6 6 6" /></Svg>,
};

// ---------- 测试点格子 ----------
export function gridLabel(grid: boolean[]): string {
  const ok = grid.filter(Boolean).length;
  return ok === grid.length ? `${grid.length} 组全过` : `${grid.length} 组过 ${ok} 组`;
}

/** 每个测试点一个方格。格子上不带任何测试数据，悬停也没有。 */
export function Cells({ grid }: { grid: boolean[] }) {
  return (
    <span className="cells" role="img" aria-label={gridLabel(grid)}>
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

// ---------- 掌握档位：三段横条，永远带文字 ----------
export function Meter({ level, segments }: { level: string; segments: number }) {
  const label = segments === 0 ? "还没练到" : `三档里的第 ${["一", "二", "三"][segments - 1]}档`;
  return (
    <span className="meter">
      <span className="meter-bars" role="img" aria-label={label}>
        {[1, 2, 3].map((i) => (
          <span key={i} className={i <= segments ? "on" : undefined} />
        ))}
      </span>
      {level}
    </span>
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
        <span>{caption}</span>
        {copyable && (
          <button type="button" className="code-copy" onClick={copy} aria-label={copied ? "已复制" : "复制代码"}>
            {copied ? <Icon.check /> : <Icon.copy />}
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

// ---------- 抽屉：出现时才有一层遮罩，本身不加阴影 ----------
export function Drawer({ side, title, onClose, children }: { side: "left" | "right"; title: string; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button")?.focus();
  }, []);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div ref={ref} className={`drawer drawer-${side}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="drawer-head">
          <span>{title}</span>
          <button type="button" className="icon-btn" onClick={onClose} aria-label={`关闭${title}`}>
            <Icon.close />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </>
  );
}

export function Loading({ what = "正在加载" }: { what?: string }) {
  return <p className="note" role="status">{what}</p>;
}

export function Problem({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <p className="error-text" role="alert">
      {message}
      {onRetry && (
        <>
          {" "}
          <button type="button" className="btn-text" style={{ minHeight: 0 }} onClick={onRetry}>
            再试一次
          </button>
        </>
      )}
    </p>
  );
}
