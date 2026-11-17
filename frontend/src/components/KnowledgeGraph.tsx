// 知识图谱画布：C 语言一张图、数据结构一张图，中间连着跨课程的先修关系。
// 排法：一章一行（从上到下是课本的顺序），同一章的知识点从左到右；C 语言在上，数据结构在下。
// 交互：拖动平移，Ctrl + 滚轮或按钮缩放，滚轮上下移动；悬停一个点，它的先修和后继亮起来，其他变淡；点一下打开右边的详情。
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";

export interface KMNode {
  code: string;
  name: string;
  course: "C" | "D";
  topic: string;
  topicName: string;
  skills: number;
  covered: boolean;
  dist: number[];
  mean: number | null;
  withEvidence: number;
  readiness: number | null;
  student?: { level: string; p: number; predicted: boolean };
}

export interface KMEdge {
  from: string;
  to: string;
  n: number;
  cross: boolean;
}

export interface KMTopic {
  code: string;
  name: string;
  course: "C" | "D";
}

const LABEL_W = 128;
const CHIP_W = 116;
const CHIP_H = 46;
const GAP_X = 10;
const ROW_H = 64;
const HEAD_H = 60;
const BAND_GAP = 56;
const PAD = 16;

const LEVEL_COLORS = ["var(--lv-mastered)", "var(--lv-basic)", "var(--lv-weak)", "var(--lv-none)"];
// 箭头颜色和边的样式对应：普通、跨课程、指向它的先修、它通向的后继
const ARROWS: Record<string, string> = { base: "#b4bac3", cross: "#eb6834", in: "#2a78d6", out: "#111318" };
const LEVEL_CLS: Record<string, string> = { 已掌握: "mastered", 基本掌握: "basic", 待加强: "weak", 还没练到: "none" };

interface Placed extends KMNode {
  x: number;
  y: number;
}

interface Layout {
  nodes: Placed[];
  byCode: Map<string, Placed>;
  rows: { topic: KMTopic; y: number }[];
  bands: { course: "C" | "D"; y: number; height: number; count: number }[];
  width: number;
  height: number;
}

function layout(nodes: KMNode[], topics: KMTopic[], courses: ("C" | "D")[]): Layout {
  const placed: Placed[] = [];
  const rows: Layout["rows"] = [];
  const bands: Layout["bands"] = [];
  let y = PAD;
  let maxCols = 1;
  for (const course of courses) {
    const ts = topics.filter((t) => t.course === course);
    const start = y;
    y += HEAD_H;
    let count = 0;
    for (const t of ts) {
      const mine = nodes.filter((n) => n.topic === t.code).sort((a, b) => (a.code < b.code ? -1 : 1));
      if (mine.length === 0) continue;
      rows.push({ topic: t, y });
      mine.forEach((n, i) => placed.push({ ...n, x: PAD + LABEL_W + i * (CHIP_W + GAP_X), y }));
      maxCols = Math.max(maxCols, mine.length);
      count += mine.length;
      y += ROW_H;
    }
    bands.push({ course, y: start, height: y - start, count });
    y += BAND_GAP;
  }
  const width = PAD * 2 + LABEL_W + maxCols * (CHIP_W + GAP_X) - GAP_X;
  return { nodes: placed, byCode: new Map(placed.map((n) => [n.code, n])), rows, bands, width, height: y - BAND_GAP + PAD };
}

function edgePath(a: Placed, b: Placed): string {
  if (a.y === b.y) {
    const adjacent = Math.abs(a.x - b.x) <= CHIP_W + GAP_X + 1;
    if (adjacent) {
      const [l, r] = a.x < b.x ? [a, b] : [b, a];
      const yy = a.y + CHIP_H / 2;
      return a.x < b.x ? `M${l.x + CHIP_W},${yy}L${r.x - 3},${yy}` : `M${r.x},${yy}L${l.x + CHIP_W + 3},${yy}`;
    }
    const ax = a.x + CHIP_W / 2, bx = b.x + CHIP_W / 2;
    const lift = Math.min(26, 10 + Math.abs(bx - ax) / 18);
    return `M${ax},${a.y}C${ax},${a.y - lift} ${bx},${b.y - lift} ${bx},${b.y - 3}`;
  }
  if (b.y > a.y) {
    const ax = a.x + CHIP_W / 2, ay = a.y + CHIP_H, bx = b.x + CHIP_W / 2, by = b.y;
    const dy = (by - ay) * 0.5;
    return `M${ax},${ay}C${ax},${ay + dy} ${bx},${by - dy} ${bx},${by - 3}`;
  }
  const ax = a.x + CHIP_W / 2, ay = a.y, bx = b.x + CHIP_W / 2, by = b.y + CHIP_H;
  const dy = (ay - by) * 0.5;
  return `M${ax},${ay}C${ax},${ay - dy} ${bx},${by + dy} ${bx},${by + 3}`;
}

function closure(start: string, edges: KMEdge[], dir: "up" | "down"): Set<string> {
  const out = new Set<string>();
  const stack = [start];
  while (stack.length) {
    const cur = stack.pop()!;
    for (const e of edges) {
      const [from, to] = dir === "up" ? [e.to, e.from] : [e.from, e.to];
      if (from === cur && !out.has(to)) {
        out.add(to);
        stack.push(to);
      }
    }
  }
  return out;
}

interface Props {
  nodes: KMNode[];
  edges: KMEdge[];
  topics: KMTopic[];
  courses: ("C" | "D")[];
  courseNames: Record<string, string>;
  showCross: boolean;
  chain: boolean;
  selected: string | null;
  search: string;
  mode: "class" | "student";
  onSelect: (code: string | null) => void;
  fitKey: string; // 变了就重新适应宽度
}

export default function KnowledgeGraph({ nodes, edges, topics, courses, courseNames, showCross, chain, selected, search, mode, onSelect, fitKey }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean; id: number } | null>(null);
  const suppressClick = useRef(false);

  const L = useMemo(() => layout(nodes, topics, courses), [nodes, topics, courses]);
  const visibleEdges = useMemo(
    () => edges.filter((e) => L.byCode.has(e.from) && L.byCode.has(e.to) && (showCross || !e.cross)),
    [edges, L, showCross],
  );

  // 高亮：悬停优先，其次是选中的点
  const focus = hover ?? selected;
  const lit = useMemo(() => {
    if (!focus) return null;
    const useChain = chain && focus === selected;
    const up = new Set<string>([focus]);
    const down = new Set<string>([focus]);
    if (useChain) {
      closure(focus, visibleEdges, "up").forEach((c) => up.add(c));
      closure(focus, visibleEdges, "down").forEach((c) => down.add(c));
    } else {
      for (const e of visibleEdges) {
        if (e.from === focus) down.add(e.to);
        if (e.to === focus) up.add(e.from);
      }
    }
    const edgeKind = (e: KMEdge): "in" | "out" | null => {
      if (useChain) {
        if (up.has(e.from) && up.has(e.to)) return "in";
        if (down.has(e.from) && down.has(e.to)) return "out";
        return null;
      }
      return e.to === focus ? "in" : e.from === focus ? "out" : null;
    };
    return { nodes: new Set([...up, ...down]), edgeKind };
  }, [focus, chain, selected, visibleEdges]);

  const needle = search.trim();
  const matches = useMemo(() => (needle ? new Set(L.nodes.filter((n) => n.name.includes(needle) || n.code.includes(needle.toUpperCase())).map((n) => n.code)) : null), [needle, L]);

  const fit = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const w = el.clientWidth;
    const minK = w < 768 ? 0.62 : 0.5;
    const k = Math.max(minK, Math.min(1, w / L.width));
    setView({ x: Math.max(0, (w - L.width * k) / 2), y: 0, k });
  }, [L.width]);

  useLayoutEffect(() => {
    fit();
  }, [fit, fitKey]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => fit());
    ro.observe(el);
    return () => ro.disconnect();
  }, [fit]);

  const clampView = useCallback((v: { x: number; y: number; k: number }) => {
    const el = box.current;
    if (!el) return v;
    const w = el.clientWidth, h = el.clientHeight;
    const cw = L.width * v.k, ch = L.height * v.k;
    const x = cw <= w ? Math.max(0, Math.min(w - cw, v.x)) : Math.min(40, Math.max(w - cw - 40, v.x));
    const y = ch <= h ? Math.min(Math.max(v.y, -20), 20) : Math.min(40, Math.max(h - ch - 40, v.y));
    return { x, y, k: v.k };
  }, [L.width, L.height]);

  const zoomAt = useCallback((factor: number, cx?: number, cy?: number) => {
    setView((v) => {
      const el = box.current;
      const px = cx ?? (el ? el.clientWidth / 2 : 0);
      const py = cy ?? (el ? el.clientHeight / 2 : 0);
      const k = Math.min(1.8, Math.max(0.35, v.k * factor));
      const r = k / v.k;
      return clampView({ x: px - (px - v.x) * r, y: py - (py - v.y) * r, k });
    });
  }, [clampView]);

  // 滚轮：Ctrl/⌘ + 滚轮缩放，否则平移
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) {
        zoomAt(Math.exp(-e.deltaY * 0.0022), e.clientX - rect.left, e.clientY - rect.top);
      } else {
        setView((v) => clampView({ ...v, x: v.x - (e.shiftKey ? e.deltaY : e.deltaX), y: v.y - (e.shiftKey ? 0 : e.deltaY) }));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt, clampView]);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false, id: e.pointerId };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) > 5) {
      d.moved = true;
      (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    }
    if (d.moved) setView((v) => clampView({ ...v, x: d.vx + dx, y: d.vy + dy }));
  };
  const onPointerUp = () => {
    if (drag.current?.moved) suppressClick.current = true;
    drag.current = null;
    window.setTimeout(() => (suppressClick.current = false), 0);
  };

  const hovered = hover ? L.byCode.get(hover) : null;

  return (
    <div className={`kg${drag.current?.moved ? " kg-dragging" : ""}`} ref={box}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      onClick={(e) => { if (!suppressClick.current && e.target === e.currentTarget) onSelect(null); }}>
      <div className="kg-canvas" style={{ width: L.width, height: L.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
        onClick={(e) => { if (!suppressClick.current && e.target === e.currentTarget) onSelect(null); }}>
        {L.bands.map((b) => (
          <div key={b.course} className={`kg-band kg-band-${b.course}`} style={{ top: b.y, height: b.height, width: L.width - PAD * 2, left: PAD }}>
            <div className="kg-band-head">
              <strong>{courseNames[b.course]}</strong>
              <span>{b.count} 个知识点{b.course === "D" ? " · 还没开课，按 C 语言的先修推算准备度" : ""}</span>
            </div>
          </div>
        ))}
        {L.rows.map((r) => (
          <div key={r.topic.code} className="kg-topic" style={{ top: r.y, left: PAD + 12, width: LABEL_W - 20, height: CHIP_H }}>
            <span className="kg-topic-code">{r.topic.code}</span>
            <span className="kg-topic-name">{r.topic.name}</span>
          </div>
        ))}
        <svg className="kg-edges" width={L.width} height={L.height} aria-hidden="true">
          <defs>
            {Object.entries(ARROWS).map(([id, color]) => (
              <marker key={id} id={`kg-${id}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0L8,4L0,8z" fill={color} />
              </marker>
            ))}
          </defs>
          {visibleEdges.map((e) => {
            const a = L.byCode.get(e.from)!, b = L.byCode.get(e.to)!;
            const on = lit ? lit.edgeKind(e) : null;
            const dim = lit !== null && !on;
            const kind = on ?? (e.cross ? "cross" : "base");
            const cls = `kg-edge kg-edge-${kind}${dim ? " kg-edge-dim" : ""}`;
            return <path key={`${e.from}-${e.to}`} d={edgePath(a, b)} className={cls} markerEnd={dim ? undefined : `url(#kg-${kind})`} />;
          })}
        </svg>
        {L.nodes.map((n) => {
          const dim = (lit !== null && !lit.nodes.has(n.code)) || (matches !== null && !matches.has(n.code));
          const total = n.dist.reduce((s, v) => s + v, 0) || 1;
          const stu = mode === "student" ? n.student : undefined;
          const lvl = stu ? LEVEL_CLS[stu.level] ?? "none" : "";
          const weak = n.dist[2] ?? 0;
          const ready = n.course === "D" ? (stu ? (stu.predicted ? stu.p : null) : n.readiness) : null;
          const label = stu
            ? `${n.name}，${stu.level}${stu.predicted ? "（推算）" : ""}`
            : n.course === "D"
              ? `${n.name}，还没开课，准备度 ${ready === null ? "没有数据" : ready.toFixed(2)}`
              : n.covered ? `${n.name}，${n.dist.map((v, i) => `${["已掌握", "基本掌握", "待加强", "还没练到"][i]} ${v} 人`).join("，")}` : `${n.name}，作业还没涉及`;
          return (
            <button key={n.code} type="button"
              className={`kg-node${n.course === "D" ? " kg-node-ds" : ""}${!n.covered && n.course === "C" ? " kg-node-uncovered" : ""}${lvl ? ` kg-lv-${lvl}` : ""}${selected === n.code ? " kg-node-selected" : ""}${dim ? " kg-node-dim" : ""}${matches?.has(n.code) ? " kg-node-match" : ""}`}
              style={{ left: n.x, top: n.y, width: CHIP_W, height: CHIP_H }}
              aria-label={label} aria-pressed={selected === n.code}
              onMouseEnter={() => setHover(n.code)} onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(n.code)} onBlur={() => setHover(null)}
              onClick={(e) => { e.stopPropagation(); if (!suppressClick.current) onSelect(selected === n.code ? null : n.code); }}>
              <span className="kg-node-name">{n.name}</span>
              {mode === "class" && n.course === "C" && n.covered && (
                <span className="kg-node-dist" aria-hidden="true">
                  {n.dist.map((v, i) => v > 0 && <span key={i} style={{ flexGrow: v / total, background: LEVEL_COLORS[i] }} />)}
                </span>
              )}
              {ready !== null && (
                <span className="kg-node-ready" aria-hidden="true"><span style={{ width: `${ready * 100}%` }} /></span>
              )}
              {mode === "class" && weak > 0 && <span className="kg-node-badge num" aria-hidden="true">{weak}</span>}
            </button>
          );
        })}
      </div>

      {hovered && (
        <div className="tip kg-tip" style={{
          left: Math.min(view.x + (hovered.x + CHIP_W / 2) * view.k, (box.current?.clientWidth ?? 0) - 120),
          top: view.y + hovered.y * view.k - 8, transform: "translate(-50%, -100%)",
        }}>
          <b>{hovered.name}</b>
          {mode === "student" && hovered.student ? (
            <div className="tip-row"><span>{hovered.student.predicted ? "推算的准备度" : hovered.student.level}</span><b className="num">{hovered.student.p.toFixed(2)}</b></div>
          ) : hovered.course === "D" ? (
            <div className="tip-row"><span>全班平均准备度</span><b className="num">{hovered.readiness === null ? "—" : hovered.readiness.toFixed(2)}</b></div>
          ) : hovered.covered ? (
            ["已掌握", "基本掌握", "待加强", "还没练到"].map((l, i) => (
              <div key={l} className="tip-row"><span><i className="swatch" style={{ background: LEVEL_COLORS[i] }} />{l}</span><b className="num">{hovered.dist[i]} 人</b></div>
            ))
          ) : (
            <div className="tip-row"><span>作业还没涉及</span></div>
          )}
        </div>
      )}

      <div className="kg-zoom" onPointerDown={(e) => e.stopPropagation()}>
        <button type="button" className="icon-btn" onClick={() => zoomAt(1.2)} aria-label="放大"><Plus size={17} /></button>
        <button type="button" className="icon-btn" onClick={() => zoomAt(1 / 1.2)} aria-label="缩小"><Minus size={17} /></button>
        <button type="button" className="icon-btn" onClick={fit} aria-label="适应宽度"><Maximize2 size={16} /></button>
      </div>
    </div>
  );
}
