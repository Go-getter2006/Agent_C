// 手写的 SVG 图表：柱状图、折线图、分布条、横向条。
// 规则：只有一根纵轴；柱子细、顶端 4px 圆角、贴着基线；线宽 2px；网格是很淡的实线；两个系列以上一定有图例；
// 数值和标签用文字色，不用系列色；每个图都能悬停看数。
import { useMemo, useState, type PointerEvent, type ReactNode } from "react";
import { useWidth } from "../lib/hooks";

export interface Series {
  name: string;
  color: string; // CSS 颜色或变量
}

/** 好看的刻度间隔：1、2、5（不是整数时也可以 2.5）乘 10 的幂，大约分成 target 段。 */
function niceStep(range: number, target = 4, integer = false): number {
  if (range <= 0) return 1;
  const raw = range / target;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / p;
  const pick = (integer ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10]).find((c) => n <= c) ?? 10;
  const step = pick * p;
  return integer ? Math.max(1, Math.round(step)) : step;
}

function scale(min: number, maxValue: number, fixedMax: number | undefined, integer: boolean): { max: number; ticks: number[] } {
  const top = fixedMax ?? maxValue;
  const step = niceStep(top - min, 4, integer);
  const max = fixedMax ?? Math.max(min + step, Math.ceil((maxValue - min) / step - 1e-9) * step + min);
  const ticks: number[] = [];
  for (let v = min; v <= max + step * 1e-6; v += step) ticks.push(Number(v.toFixed(6)));
  return { max, ticks };
}

const fmtNum = (v: number) => (Number.isInteger(v) ? String(v) : String(Number(v.toFixed(2))));

/** 柱子：顶端圆角，底端是直的（贴着基线）。 */
function barPath(x: number, y: number, w: number, h: number, r = 4): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <div className="legend chart-legend">
      {series.map((s) => (
        <span key={s.name}><i className="swatch" style={{ background: s.color }} />{s.name}</span>
      ))}
    </div>
  );
}

interface Tip {
  x: number;
  y: number;
  content: ReactNode;
}

function Tooltip({ tip, width }: { tip: Tip | null; width: number }) {
  if (!tip) return null;
  const left = Math.min(Math.max(tip.x, 8), Math.max(8, width - 168));
  return (
    <div className="tip" style={{ left, top: Math.max(0, tip.y - 8), transform: "translateY(-100%)" }}>
      {tip.content}
    </div>
  );
}

// ---------------------------------------------------------------- 柱状图（可以分组）
interface ColumnProps {
  labels: string[];
  series: Series[];
  values: number[][]; // values[系列][类别]
  height?: number;
  unit?: string;
  label: string; // 给读屏的整体说明
  emphasizeLast?: boolean;
}

export function ColumnChart({ labels, series, values, height = 200, unit = "", label, emphasizeLast = false }: ColumnProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const padL = 32, padR = 4, padT = 8, padB = 26;
  const flat = values.flat();
  const { max, ticks: tickVals } = scale(0, Math.max(1, ...flat), undefined, flat.every((v) => Number.isInteger(v)));
  const plotW = Math.max(0, width - padL - padR);
  const plotH = height - padT - padB;
  const band = labels.length ? plotW / labels.length : 0;
  const groupGap = 2;
  const barW = Math.min(24, Math.max(4, (band * 0.62 - groupGap * (series.length - 1)) / series.length));
  const groupW = barW * series.length + groupGap * (series.length - 1);
  const y = (v: number) => padT + plotH - (v / max) * plotH;

  const tip: Tip | null = hover === null ? null : {
    x: padL + band * hover + band / 2 - 70,
    y: Math.min(...values.map((s) => y(s[hover]))),
    content: (
      <>
        <b>{labels[hover]}</b>
        {series.map((s, i) => (
          <div key={s.name} className="tip-row">
            <span><i className="swatch" style={{ background: s.color }} />{s.name}</span>
            <b className="num">{fmtNum(values[i][hover])}{unit}</b>
          </div>
        ))}
      </>
    ),
  };

  return (
    <div className="chart" ref={ref}>
      {series.length > 1 && <Legend series={series} />}
      {width > 0 && (
        <div className="chart-plot">
          <svg width={width} height={height} role="img" aria-label={label}>
            {tickVals.map((t) => (
              <g key={t}>
                <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} className={t === 0 ? "axis-base" : "grid-line"} />
                <text x={padL - 8} y={y(t)} dy="0.32em" textAnchor="end" className="axis-text">{fmtNum(t)}</text>
              </g>
            ))}
            {labels.map((lab, i) => {
              const x0 = padL + band * i + (band - groupW) / 2;
              const dim = hover !== null && hover !== i;
              return (
                <g key={lab} opacity={dim ? 0.45 : 1}>
                  {series.map((s, k) => {
                    const v = values[k][i];
                    const h = plotH - (y(v) - padT);
                    return <path key={s.name} d={barPath(x0 + k * (barW + groupGap), y(v), barW, h)} fill={s.color}
                      opacity={emphasizeLast && i !== labels.length - 1 && hover === null ? 0.78 : 1} />;
                  })}
                  <text x={padL + band * i + band / 2} y={height - 8} textAnchor="middle"
                    className={emphasizeLast && i === labels.length - 1 ? "axis-text axis-strong" : "axis-text"}>{lab}</text>
                  <rect x={padL + band * i} y={padT} width={band} height={plotH + padB} fill="transparent"
                    onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
                </g>
              );
            })}
          </svg>
          <Tooltip tip={tip} width={width} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 折线图
interface LineProps {
  labels: string[];
  series: (Series & { values: (number | null)[] })[];
  height?: number;
  min?: number;
  max?: number;
  unit?: string;
  label: string;
  format?: (v: number) => string;
  lowerIsBetter?: boolean;
  interactive?: boolean;
}

export function LineChart({ labels, series, height = 200, min = 0, max: maxIn, unit = "", label, format = fmtNum, interactive = false }: LineProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const shown = series.filter(s => !interactive || !hidden.includes(s.name));
  const padL = 40, padR = 12, padT = 10, padB = 26;
  const all = shown.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const { max, ticks: tickVals } = useMemo(() => scale(min, Math.max(min + 1e-9, ...all), maxIn, false), [min, maxIn, all.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const plotW = Math.max(0, width - padL - padR);
  const plotH = height - padT - padB;
  const step = labels.length > 1 ? plotW / (labels.length - 1) : 0;
  const x = (i: number) => padL + (labels.length === 1 ? plotW / 2 : step * i);
  const y = (v: number) => padT + plotH - ((v - min) / (max - min)) * plotH;

  const inspectPoint = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const index = labels.length === 1 ? 0 : Math.round((event.clientX - rect.left - padL) / (step || 1));
    setHover(labels.length ? Math.min(labels.length - 1, Math.max(0, index)) : null);
  };

  const path = (vals: (number | null)[]) => {
    let d = "";
    let pen = false;
    vals.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i)},${y(v)}`;
      pen = true;
    });
    return d;
  };

  const tip: Tip | null = hover === null || !labels.length || !shown.length ? null : {
    x: x(hover) - 70,
    y: Math.min(...shown.map((s) => (s.values[hover] === null ? padT + plotH : y(s.values[hover]!)))),
    content: (
      <>
        <b>{labels[hover]}</b>
        {shown.map((s) => (
          <div key={s.name} className="tip-row">
            <span><i className="swatch" style={{ background: s.color }} />{s.name}</span>
            <b className="num">{s.values[hover] === null ? "没有数据" : `${format(s.values[hover]!)}${unit}`}</b>
          </div>
        ))}
      </>
    ),
  };

  return (
    <div className="chart" ref={ref}>
      {series.length > 1 && (interactive ? <div className="legend chart-legend" role="group" aria-label="选择显示的模型">
        {series.map((s, i) => <button type="button" key={s.name} className="chart-toggle" aria-pressed={!hidden.includes(s.name)}
          onClick={() => setHidden(current => current.includes(s.name) ? current.filter(name => name !== s.name) : [...current, s.name])}>
          <svg width="24" height="12" aria-hidden="true"><line x1="0" y1="6" x2="24" y2="6" stroke={s.color} strokeWidth="2"
            strokeDasharray={[undefined, "8 5", "2 5"][i % 3]} /></svg>{s.name}
        </button>)}
      </div> : <Legend series={series} />)}
      {width > 0 && (
        <div className="chart-plot">
          <svg width={width} height={height} role="img" aria-label={label} tabIndex={interactive ? 0 : undefined}
            onFocus={() => { if (interactive && labels.length) setHover(0); }}
            onKeyDown={e => {
              if (interactive && labels.length && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
                e.preventDefault();
                setHover(current => Math.max(0, Math.min(labels.length - 1, (current ?? 0) + (e.key === "ArrowRight" ? 1 : -1))));
              }
            }}
            onMouseLeave={() => setHover(null)}
            onPointerDown={inspectPoint} onPointerMove={inspectPoint}>
            {tickVals.map((t) => (
              <g key={t}>
                <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} className={t === min ? "axis-base" : "grid-line"} />
                <text x={padL - 8} y={y(t)} dy="0.32em" textAnchor="end" className="axis-text">{format(t)}</text>
              </g>
            ))}
            {labels.map((lab, i) => (!interactive || i === 0 || i === labels.length - 1
              || (i % Math.max(1, Math.ceil(64 / (step || 1))) === 0 && x(labels.length - 1) - x(i) >= 60)) && (
              <text key={lab + i} x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"} className="axis-text">
                {lab}
              </text>
            ))}
            {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={padT + plotH} className="crosshair" />}
            {shown.map((s) => (
              <g key={s.name}>
                <path d={path(s.values)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                  strokeDasharray={interactive ? [undefined, "8 5", "2 5"][series.indexOf(s) % 3] : undefined} />
                {s.values.map((v, i) => v !== null && (interactive || hover === i || s.values.filter((u) => u !== null).length === 1) && (
                  <circle key={i} cx={x(i)} cy={y(v)} r={interactive ? 6 - series.indexOf(s) % 3 * 1.5 : 4.5}
                    fill={interactive ? "var(--surface)" : s.color} stroke={interactive ? s.color : "var(--surface)"} strokeWidth={2} />
                ))}
              </g>
            ))}
          </svg>
          <Tooltip tip={tip} width={width} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 分布条（横向堆叠）
export function StackedBar({ counts, colors, names, total, thin = false }: {
  counts: number[]; colors: string[]; names: string[]; total?: number; thin?: boolean;
}) {
  const sum = total ?? counts.reduce((a, b) => a + b, 0);
  const label = names.map((n, i) => `${n} ${counts[i]} 人`).join("，");
  return (
    <div className={thin ? "stack-bar stack-bar-thin" : "stack-bar"} role="img" aria-label={label} title={label}>
      {counts.map((n, i) => n > 0 && (
        <span key={i} style={{ flexGrow: n, flexBasis: 0, background: colors[i] }} />
      ))}
      {sum === 0 && <span style={{ flexGrow: 1, background: "var(--lv-none)" }} />}
    </div>
  );
}

// ---------------------------------------------------------------- 横向条列表
export function BarList({ rows, color = "var(--series-1)", unit = "" }: {
  rows: { key: string; label: ReactNode; value: number; sub?: ReactNode }[]; color?: string; unit?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="barlist">
      {rows.map((r) => (
        <li key={r.key}>
          <div className="barlist-top">
            <span className="barlist-label">{r.label}</span>
            <span className="barlist-value num">{r.value}{unit}</span>
          </div>
          <div className="barlist-track" aria-hidden="true">
            <span style={{ width: `${(100 * r.value) / max}%`, background: color }} />
          </div>
          {r.sub && <span className="barlist-sub">{r.sub}</span>}
        </li>
      ))}
    </ul>
  );
}
