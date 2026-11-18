// 知识图谱画布：登录的这门课一张图。数据结构的班还可以带上它要用到的 C 语言知识点（“基础”，只有掌握程度），
// 中间连着跨课程的前置关系。
// 排法（lib/layered.ts）：从左到右是学习的先后，前置知识在左、后续知识在右；连线只走列与列之间的空隙，
// 不从知识点底下穿过；能由别的路径推出来的连线不画；和谁都不连的知识点放在下面。两门课一起看时 C 语言在左。
// 交互：拖动平移，Ctrl + 滚轮或按钮缩放，滚轮上下移动；悬停一个点，它直接的前置知识和后续知识亮起来；
// 点一下选中，整条前置链和后续链亮起来、其他退到后面，右边打开详情。位置不随着色、搜索、选中改变。
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import { layeredLayout, type LayoutOpts } from "../lib/layered";

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
  score: number | null; // 全班练过的同学的平均掌握分（0~100）
  foundation?: boolean; // 数据结构的班里带上的 C 语言知识点
  student?: { level: string; p: number; predicted: boolean; score: number | null; text: string };
}

/** 这个知识点在这个班有没有可以显示的分布：作业涉及了，或者有人有证据。 */
export const hasData = (n: KMNode) => n.covered || n.withEvidence > 0;

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

const CHIP_W = 124;
const CHIP_H = 46;
const OPTS: LayoutOpts = { nodeW: CHIP_W, nodeH: CHIP_H, colGap: 76, rowGap: 14, dummyGap: 8, bandGap: 44, padX: 28, top: 64, isoGap: 12 };

const LEVEL_COLORS = ["var(--lv-mastered)", "var(--lv-basic)", "var(--lv-weak)", "var(--lv-none)"];
// 箭头颜色和边的样式对应：普通、跨课程、指向它的前置知识、它通向的后续
const ARROWS: Record<string, string> = { base: "#aab1bb", cross: "#eb6834", in: "#2a78d6", out: "#111318" };
const LEVEL_CLS: Record<string, string> = { 已掌握: "mastered", 基本掌握: "basic", 待加强: "weak", 还没练到: "none" };

function closure(start: string, edges: { from: string; to: string }[], dir: "up" | "down"): Set<string> {
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
  home: "C" | "D"; // 这个班是哪门课的
  showCross: boolean;
  selected: string | null;
  search: string;
  topic: string; // 只看这一章（其他变淡），空是全部
  mode: "class" | "student";
  onSelect: (code: string | null) => void;
  onLayout?: (info: { hidden: number; isolated: number }) => void;
  fitKey: string; // 变了就重新适应宽度
}

export default function KnowledgeGraph({ nodes, edges, topics, courses, courseNames, home, showCross, selected, search, topic, mode, onSelect, onLayout, fitKey }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean; id: number } | null>(null);
  const suppressClick = useRef(false);
  const touch = useRef(false);

  const byCode = useMemo(() => new Map(nodes.map((n) => [n.code, n])), [nodes]);
  const L = useMemo(
    () => layeredLayout(nodes, edges.filter((e) => showCross || !e.cross), courses, OPTS),
    [nodes, edges, courses, showCross],
  );
  useEffect(() => onLayout?.({ hidden: L.hidden.length, isolated: L.isolated.length }), [L, onLayout]);
  const topicName = useMemo(() => new Map(topics.map((t) => [t.code, t.name])), [topics]);

  // 聚焦：悬停时亮起直接的前置（蓝）和后续（黑）；选中时亮起整条链，其他的退到后面
  const focus = hover ?? selected;
  const lit = useMemo(() => {
    if (!focus) return null;
    const whole = focus === selected; // 鼠标停在选中的点上也看整条链
    const up = new Set<string>([focus]);
    const down = new Set<string>([focus]);
    if (whole) {
      closure(focus, L.edges, "up").forEach((c) => up.add(c));
      closure(focus, L.edges, "down").forEach((c) => down.add(c));
    } else {
      for (const e of L.edges) {
        if (e.from === focus) down.add(e.to);
        if (e.to === focus) up.add(e.from);
      }
    }
    const edgeKind = (e: { from: string; to: string }): "in" | "out" | null => {
      if (whole) {
        if (up.has(e.from) && up.has(e.to)) return "in";
        if (down.has(e.from) && down.has(e.to)) return "out";
        return null;
      }
      return e.to === focus ? "in" : e.from === focus ? "out" : null;
    };
    return { nodes: new Set([...up, ...down]), edgeKind };
  }, [focus, selected, L]);

  const needle = search.trim();
  const matches = useMemo(() => {
    if (!needle && !topic) return null;
    return new Set(nodes.filter((n) => (!topic || n.topic === topic) &&
      (!needle || n.name.includes(needle) || n.code.includes(needle.toUpperCase()))).map((n) => n.code));
  }, [needle, topic, nodes]);

  // 亮起的线画在最上面
  const drawn = useMemo(() => {
    const rows = L.edges.map((e) => {
      const on = lit ? lit.edgeKind(e) : null;
      return { e, on, dim: lit !== null && !on };
    });
    return [...rows.filter((x) => !x.on), ...rows.filter((x) => x.on)];
  }, [L, lit]);

  const chapterBounds = useMemo(() => {
    const points = topic ? nodes.filter(n => n.topic === topic).map(n => L.pos.get(n.code)).filter(p => !!p) : [];
    if (!points.length) return null;
    const x = Math.min(...points.map(p => p!.x)), y = Math.min(...points.map(p => p!.y));
    return { x, y, w: Math.max(...points.map(p => p!.x)) - x + CHIP_W, h: Math.max(...points.map(p => p!.y)) - y + CHIP_H };
  }, [topic, nodes, L]);
  const bx = chapterBounds?.x, by = chapterBounds?.y, bw = chapterBounds?.w, bh = chapterBounds?.h;
  const fit = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const w = el.clientWidth;
    if (window.innerWidth >= 768 && bx !== undefined && by !== undefined && bw && bh) {
      const k = Math.max(.5, Math.min(1.35, (w - 64) / bw, (el.clientHeight - 80) / bh));
      setView({ x: (w - bw * k) / 2 - bx * k, y: Math.max(40, (el.clientHeight - bh * k) / 2) - by * k, k });
      return;
    }
    const minK = w < 768 ? 0.62 : 0.5;
    const k = Math.max(minK, Math.min(1, w / L.width));
    setView({ x: Math.max(0, (w - L.width * k) / 2), y: 0, k });
  }, [L.width, bx, by, bw, bh]);

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

  // 右边（窄屏时是下面）打开详情时多留出地方，选中的点可以挪到没被挡住的那一块
  const panelRoom = useCallback(() => {
    const el = box.current;
    if (!el || !selected) return { right: 0, bottom: 0 };
    return window.innerWidth >= 1024 ? { right: 376, bottom: 0 } : { right: 0, bottom: el.clientHeight * 0.62 };
  }, [selected]);

  const clampView = useCallback((v: { x: number; y: number; k: number }) => {
    const el = box.current;
    if (!el) return v;
    const w = el.clientWidth, h = el.clientHeight;
    const cw = L.width * v.k, ch = L.height * v.k;
    const room = panelRoom();
    const x = Math.min(cw <= w ? w - cw : 40, Math.max((cw <= w ? 0 : w - cw - 40) - room.right, v.x));
    const y = Math.min(ch <= h ? 20 : 40, Math.max((ch <= h ? -20 : h - ch - 40) - room.bottom, v.y));
    return { x, y, k: v.k };
  }, [L.width, L.height, panelRoom]);

  // 选中的点被详情挡住时，把图挪过去（带一点过渡）
  const viewRef = useRef(view);
  viewRef.current = view;
  const [glide, setGlide] = useState(false);
  useEffect(() => {
    const el = box.current;
    const at = selected ? L.pos.get(selected) : undefined;
    if (!el || !at) return;
    const v = viewRef.current;
    const room = panelRoom();
    const right = el.clientWidth - room.right, bottom = el.clientHeight - room.bottom;
    const cx = v.x + (at.x + CHIP_W / 2) * v.k, cy = v.y + (at.y + CHIP_H / 2) * v.k;
    if (cx > 60 && cx < right - 60 && cy > 40 && cy < bottom - 40) return;
    setGlide(true);
    setView(clampView({ ...v, x: v.x + right / 2 - cx, y: v.y + bottom / 2 - cy }));
    const id = window.setTimeout(() => setGlide(false), 450);
    return () => window.clearTimeout(id);
  }, [selected, L, panelRoom, clampView]);

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

  const hoveredPos = hover ? L.pos.get(hover) : undefined;
  const hovered = hover ? byCode.get(hover) : undefined;
  const single = L.bands.length === 1;

  return (
    <div className={`kg${drag.current?.moved ? " kg-dragging" : ""}`} ref={box}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      onClick={(e) => { if (!suppressClick.current && e.target === e.currentTarget) onSelect(null); }}>
      <div className={`kg-canvas${glide ? " kg-canvas-glide" : ""}`} style={{ width: L.width, height: L.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
        onClick={(e) => { if (!suppressClick.current && e.target === e.currentTarget) onSelect(null); }}>
        {L.bands.map((b) => (
          <div key={b.course} className={`kg-band kg-band-${b.course === home ? "home" : "base"}`}
            style={{ top: 12, height: (L.isoTop ?? L.height - 20) - 24, width: b.width, left: b.x }}>
            <div className="kg-band-head">
              <strong>{courseNames[b.course]}{b.course !== home ? " · 要用到的基础" : ""}</strong>
              <span>{b.count} 个有前置关系的知识点{single ? " · 从左到右：先学 → 后学" : b.course !== home ? " · 只有掌握程度" : ""}</span>
            </div>
          </div>
        ))}
        {L.isoTop !== null && (
          <div className="kg-iso-head" style={{ top: L.isoTop, left: OPTS.padX }}>
            <strong>没有和别的知识点连线的</strong><span>{L.isolated.length} 个</span>
          </div>
        )}
        <svg className="kg-edges" width={L.width} height={L.height} aria-hidden="true">
          <defs>
            {Object.entries(ARROWS).map(([id, color]) => (
              <marker key={id} id={`kg-${id}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0L8,4L0,8z" fill={color} />
              </marker>
            ))}
          </defs>
          {drawn.map(({ e, on, dim }) => {
            const kind = on ?? (e.cross ? "cross" : "base");
            return <path key={`${e.from}-${e.to}`} d={e.d} className={`kg-edge kg-edge-${kind}${dim ? " kg-edge-dim" : ""}`}
              markerEnd={dim ? undefined : `url(#kg-${kind})`} />;
          })}
        </svg>
        {nodes.map((n) => {
          const at = L.pos.get(n.code);
          if (!at) return null;
          const dim = (lit !== null && !lit.nodes.has(n.code)) || (matches !== null && !matches.has(n.code));
          const total = n.dist.reduce((s, v) => s + v, 0) || 1;
          const stu = mode === "student" ? n.student : undefined;
          const lvl = stu ? LEVEL_CLS[stu.level] ?? "none" : "";
          const weak = n.dist[2] ?? 0;
          const shown = hasData(n);
          const foundation = n.course !== home;
          // 数据结构的知识点还没有证据时：按 C 语言的前置知识推出来的准备度
          const ready = n.course === "D" && !shown ? (stu ? (stu.predicted ? stu.p : null) : n.readiness) : null;
          const label = stu
            ? `${n.name}，${stu.level}${stu.predicted ? "（推算）" : ""}`
            : shown ? `${n.name}，${n.dist.map((v, i) => `${["已掌握", "基本掌握", "待加强", "还没练到"][i]} ${v} 人`).join("，")}`
              : ready !== null ? `${n.name}，还没有证据，推算的准备度 ${ready.toFixed(2)}` : `${n.name}，作业还没涉及`;
          return (
            <button key={n.code} type="button"
              className={`kg-node${foundation ? " kg-node-ds" : ""}${!shown && !foundation ? " kg-node-uncovered" : ""}${lvl ? ` kg-lv-${lvl}` : ""}${selected === n.code ? " kg-node-selected" : ""}${dim ? " kg-node-dim" : ""}${matches?.has(n.code) && (needle || topic) ? " kg-node-match" : ""}`}
              style={{ left: at.x, top: at.y, width: CHIP_W, height: CHIP_H }}
              aria-label={label} aria-pressed={selected === n.code}
              onMouseEnter={() => setHover(n.code)} onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(n.code)} onBlur={() => setHover(null)}
              onPointerDown={(e) => { touch.current = e.pointerType === "touch"; }}
              onClick={(e) => {
                e.stopPropagation();
                if (touch.current) setHover(null); // 手机上点完不留提示框，详情在下面
                if (!suppressClick.current) onSelect(selected === n.code ? null : n.code);
              }}>
              <span className="kg-node-name">{n.name}</span>
              {(stu ? stu.score : n.score) !== null && (stu ? stu.score : n.score) !== undefined && (
                <span className="kg-node-score num" aria-hidden="true">{stu ? stu.score : n.score}</span>
              )}
              {mode === "class" && shown && (
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

      {hovered && hoveredPos && (
        <div className="tip kg-tip" style={{
          left: Math.min(view.x + (hoveredPos.x + CHIP_W / 2) * view.k, (box.current?.clientWidth ?? 0) - 120),
          top: view.y + hoveredPos.y * view.k - 8, transform: "translate(-50%, -100%)",
        }}>
          <span className="kg-tip-topic">{hovered.topic} {topicName.get(hovered.topic) ?? hovered.topicName}</span>
          <b>{hovered.name}</b>
          {mode === "student" && hovered.student ? (
            <>
              <div className="tip-row"><span>{hovered.student.predicted ? "推算的准备度" : hovered.student.level}</span>
                <b className="num">{hovered.student.score ?? hovered.student.p.toFixed(2)}</b></div>
              {hovered.student.text && <p className="kg-tip-text">{hovered.student.text}</p>}
            </>
          ) : !hasData(hovered) && hovered.course === "D" ? (
            <div className="tip-row"><span>全班平均准备度（推算）</span><b className="num">{hovered.readiness === null ? "—" : hovered.readiness.toFixed(2)}</b></div>
          ) : hasData(hovered) ? (
            <>
              {hovered.score !== null && <div className="tip-row"><span>平均掌握分</span><b className="num">{hovered.score}</b></div>}
              {["已掌握", "基本掌握", "待加强", "还没练到"].map((l, i) => (
                <div key={l} className="tip-row"><span><i className="swatch" style={{ background: LEVEL_COLORS[i] }} />{l}</span><b className="num">{hovered.dist[i]} 人</b></div>
              ))}
            </>
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
