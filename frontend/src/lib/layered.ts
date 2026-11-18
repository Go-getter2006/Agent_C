// 知识图谱的分层布局（Sugiyama 分层画法的简化版）。目标：知识点很多、很细的时候连线也看得清。
//
// 1. 传递约简：A→B、B→C 都有时，A→C 能由别的路径推出来，不画（详情里照样列出）。
// 2. 分层：从左到右是学习的先后。每个知识点放在它最长前置链的那一列；没有前置知识的点往右挪，
//    挨着它的后续知识，免得连线拉得很长。两门课一起看时，C 语言在左，数据结构在右。
// 3. 跨好几列的连线，在中间每一列放一个看不见的中转点，占一个位置。这样连线只走列与列之间的空隙
//    和中转点的位置，不会从知识点底下穿过去。
// 4. 每一列的上下顺序用重心法排（看左右邻居的平均位置），来回扫多轮，留交叉最少的那一次。
// 5. 纵坐标：每个点尽量和它的邻居对齐，长连线尽量拉直；在保持顺序和最小间距的前提下求最近的位置
//    （保序回归，PAV）。
// 6. 同一个点连出去的几条线在边上错开一点出口，不挤成一点。
//
// 参考：Sugiyama 等的分层画法；Gansner 等，A Technique for Drawing Directed Graphs（dot）。
// 只依赖输入，同样的数据每次排出同样的位置（换学生着色、搜索时图不动）。

export interface LNode {
  code: string;
  course: "C" | "D";
  topic: string;
}

export interface LEdge {
  from: string;
  to: string;
  cross: boolean;
}

export interface LayoutOpts {
  nodeW: number;
  nodeH: number;
  colGap: number; // 列与列之间的空隙（连线从这里走）
  rowGap: number; // 同一列两个知识点之间
  dummyGap: number; // 中转点之间
  bandGap: number; // 两门课之间多留的空
  padX: number;
  top: number; // 图上面留给标题的高度
  isoGap: number; // 下面“没有连线的知识点”的格子间距
}

export interface Pt {
  x: number;
  y: number;
}

export interface RoutedEdge {
  from: string;
  to: string;
  cross: boolean;
  d: string; // SVG 路径
}

export interface Band {
  course: "C" | "D";
  x: number;
  width: number;
  count: number;
}

export interface LayeredLayout {
  pos: Map<string, Pt>; // 每个知识点的左上角
  layerOf: Map<string, number>;
  edges: RoutedEdge[]; // 约简以后要画的边
  hidden: LEdge[]; // 约简掉的边
  bands: Band[];
  isolated: string[]; // 和别的点都没有连线的
  isoTop: number | null; // “没有连线的知识点”那一块的标题位置
  width: number;
  height: number;
  crossings: number;
}

/** 去掉能由别的路径推出来的边。 */
export function transitiveReduce(edges: LEdge[]): { kept: LEdge[]; hidden: LEdge[] } {
  const out = new Map<string, string[]>();
  for (const e of edges) {
    if (!out.has(e.from)) out.set(e.from, []);
    out.get(e.from)!.push(e.to);
  }
  const reachableOtherwise = (a: string, b: string): boolean => {
    const stack = (out.get(a) ?? []).filter((x) => x !== b);
    const seen = new Set<string>();
    while (stack.length) {
      const x = stack.pop()!;
      if (x === b) return true;
      if (seen.has(x)) continue;
      seen.add(x);
      for (const y of out.get(x) ?? []) stack.push(y);
    }
    return false;
  };
  const kept: LEdge[] = [];
  const hidden: LEdge[] = [];
  for (const e of edges) (reachableOtherwise(e.from, e.to) ? hidden : kept).push(e);
  return { kept, hidden };
}

/** 一门课里的分层：最长前置链；再把每个点往右挪到紧挨着它最近的后续知识。 */
function bandLayers(codes: string[], edges: LEdge[], pullRight: Set<string>): Map<string, number> {
  const inBand = new Set(codes);
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  for (const c of codes) {
    parents.set(c, []);
    children.set(c, []);
  }
  for (const e of edges) {
    if (inBand.has(e.from) && inBand.has(e.to)) {
      parents.get(e.to)!.push(e.from);
      children.get(e.from)!.push(e.to);
    }
  }
  // 拓扑序（Kahn）；按编码排，结果稳定
  const indeg = new Map(codes.map((c) => [c, parents.get(c)!.length]));
  const queue = codes.filter((c) => indeg.get(c) === 0).sort();
  const order: string[] = [];
  while (queue.length) {
    const c = queue.shift()!;
    order.push(c);
    for (const k of children.get(c)!) {
      indeg.set(k, indeg.get(k)! - 1);
      if (indeg.get(k) === 0) queue.push(k);
    }
  }
  const layer = new Map<string, number>();
  for (const c of order) layer.set(c, Math.max(-1, ...parents.get(c)!.map((p) => layer.get(p)!)) + 1);
  const maxL = Math.max(0, ...layer.values());
  for (let i = order.length - 1; i >= 0; i--) {
    const c = order[i];
    const ks = children.get(c)!;
    if (ks.length > 0) layer.set(c, Math.max(layer.get(c)!, Math.min(...ks.map((k) => layer.get(k)!)) - 1));
    else if (parents.get(c)!.length === 0 && pullRight.has(c)) layer.set(c, maxL); // 只连到另一门课的：挨着那门课
  }
  return layer;
}

interface Item {
  id: string;
  real: boolean;
  layer: number;
  key: string; // 初始顺序
}

/** 保序回归：在 z 不减的约束下，求离 t 最近（加权平方和）的 z。 */
function pav(t: number[], w: number[]): number[] {
  const blocks: { sum: number; wt: number; n: number }[] = [];
  for (let i = 0; i < t.length; i++) {
    blocks.push({ sum: t[i] * w[i], wt: w[i], n: 1 });
    while (blocks.length > 1) {
      const b = blocks[blocks.length - 1], a = blocks[blocks.length - 2];
      if (a.sum / a.wt <= b.sum / b.wt) break;
      blocks.pop();
      a.sum += b.sum;
      a.wt += b.wt;
      a.n += b.n;
    }
  }
  const z: number[] = [];
  for (const b of blocks) for (let k = 0; k < b.n; k++) z.push(b.sum / b.wt);
  return z;
}

export function layeredLayout(nodes: LNode[], edges: LEdge[], courses: ("C" | "D")[], o: LayoutOpts): LayeredLayout {
  const byCode = new Map(nodes.map((n) => [n.code, n]));
  const valid = edges.filter((e) => byCode.has(e.from) && byCode.has(e.to) && e.from !== e.to);
  const { kept, hidden } = transitiveReduce(valid);
  const linked = new Set(kept.flatMap((e) => [e.from, e.to]));
  const isolated = nodes.filter((n) => !linked.has(n.code))
    .sort((a, b) => courses.indexOf(a.course) - courses.indexOf(b.course) || (a.code < b.code ? -1 : 1))
    .map((n) => n.code);

  // 分层：每门课自己排，后一门课接在前一门课的右边
  const layerOf = new Map<string, number>();
  const bandRange: { course: "C" | "D"; first: number; last: number; count: number }[] = [];
  let offset = 0;
  for (const course of courses) {
    const codes = nodes.filter((n) => n.course === course && linked.has(n.code)).map((n) => n.code).sort();
    if (codes.length === 0) continue;
    const crossOut = new Set(kept.filter((e) => e.cross && byCode.get(e.from)!.course === course).map((e) => e.from));
    const ls = bandLayers(codes, kept, crossOut);
    const maxL = Math.max(...ls.values());
    for (const [c, l] of ls) layerOf.set(c, offset + l);
    bandRange.push({ course, first: offset, last: offset + maxL, count: codes.length });
    offset += maxL + 1;
  }
  const nLayers = offset;
  const bandIndexOfLayer = (l: number) => bandRange.findIndex((b) => l >= b.first && l <= b.last);
  const colX = (l: number) => o.padX + l * (o.nodeW + o.colGap) + Math.max(0, bandIndexOfLayer(l)) * o.bandGap;

  // 中转点
  const items = new Map<string, Item>();
  const preds = new Map<string, string[]>();
  const succs = new Map<string, string[]>();
  const link = (a: string, b: string) => {
    succs.get(a)!.push(b);
    preds.get(b)!.push(a);
  };
  const addItem = (it: Item) => {
    items.set(it.id, it);
    preds.set(it.id, []);
    succs.set(it.id, []);
  };
  for (const c of layerOf.keys()) {
    const n = byCode.get(c)!;
    addItem({ id: c, real: true, layer: layerOf.get(c)!, key: `${n.topic}|${c}` });
  }
  const chains = new Map<RoutedEdgeKey, string[]>(); // 每条边经过的中转点
  const forward = kept.filter((e) => layerOf.get(e.to)! > layerOf.get(e.from)!);
  for (const e of forward) {
    const a = layerOf.get(e.from)!, b = layerOf.get(e.to)!;
    const src = byCode.get(e.from)!;
    let prev = e.from;
    const ds: string[] = [];
    for (let l = a + 1; l < b; l++) {
      const id = `~${e.from}>${e.to}@${l}`;
      addItem({ id, real: false, layer: l, key: `${src.topic}|${e.from}|~${e.to}` });
      link(prev, id);
      ds.push(id);
      prev = id;
    }
    link(prev, e.to);
    chains.set(edgeKey(e), ds);
  }

  // 每一列的初始顺序：按章、按编码
  let order: string[][] = Array.from({ length: nLayers }, () => []);
  for (const it of items.values()) order[it.layer].push(it.id);
  for (const col of order) col.sort((x, y) => (items.get(x)!.key < items.get(y)!.key ? -1 : 1));

  const crossingsOf = (ord: string[][]): number => {
    let total = 0;
    for (let l = 0; l + 1 < ord.length; l++) {
      const pos = new Map(ord[l + 1].map((id, i) => [id, i]));
      const segs: [number, number][] = [];
      ord[l].forEach((id, i) => { for (const s of succs.get(id)!) segs.push([i, pos.get(s)!]); });
      for (let i = 0; i < segs.length; i++) {
        for (let j = i + 1; j < segs.length; j++) {
          if ((segs[i][0] - segs[j][0]) * (segs[i][1] - segs[j][1]) < 0) total++;
        }
      }
    }
    return total;
  };
  const sweep = (ord: string[][], down: boolean) => {
    const range = down ? [...Array(nLayers).keys()].slice(1) : [...Array(nLayers).keys()].slice(0, -1).reverse();
    for (const l of range) {
      const ref = new Map(ord[down ? l - 1 : l + 1].map((id, i) => [id, i]));
      const bary = new Map<string, number>();
      ord[l].forEach((id, i) => {
        const ns = (down ? preds : succs).get(id)!.map((n) => ref.get(n)).filter((v): v is number => v !== undefined);
        bary.set(id, ns.length ? ns.reduce((s, v) => s + v, 0) / ns.length : i);
      });
      ord[l] = [...ord[l]].sort((x, y) => bary.get(x)! - bary.get(y)!);
    }
  };
  let best = order.map((c) => [...c]);
  let bestCross = crossingsOf(best);
  for (let it = 0; it < 24 && bestCross > 0; it++) {
    sweep(order, it % 2 === 0);
    sweep(order, it % 2 !== 0);
    const c = crossingsOf(order);
    if (c < bestCross) {
      bestCross = c;
      best = order.map((col) => [...col]);
    }
  }
  order = best;

  // 再逐对试着交换相邻的两个（dot 的 transpose）：交换后和左右两列的交叉更少就换
  const pairCross = (u: string, v: string, l: number): number => {
    let c = 0;
    for (const [nb, ref] of [[preds, order[l - 1]], [succs, order[l + 1]]] as const) {
      if (!ref) continue;
      const pos = new Map(ref.map((id, i) => [id, i]));
      for (const a of nb.get(u)!) for (const b of nb.get(v)!) if (pos.get(a)! > pos.get(b)!) c++;
    }
    return c;
  };
  for (let pass = 0, improved = true; improved && pass < 12; pass++) {
    improved = false;
    for (let l = 0; l < nLayers; l++) {
      const col = order[l];
      for (let i = 0; i + 1 < col.length; i++) {
        if (pairCross(col[i + 1], col[i], l) < pairCross(col[i], col[i + 1], l)) {
          [col[i], col[i + 1]] = [col[i + 1], col[i]];
          improved = true;
        }
      }
    }
  }
  bestCross = crossingsOf(order);

  // 纵坐标（中心）
  const size = (id: string) => (items.get(id)!.real ? o.nodeH : 0);
  const sep = (a: string, b: string) => {
    const ra = items.get(a)!.real, rb = items.get(b)!.real;
    const gap = ra && rb ? o.rowGap : ra || rb ? (o.rowGap + o.dummyGap) / 2 : o.dummyGap;
    return (size(a) + size(b)) / 2 + gap;
  };
  const y = new Map<string, number>();
  for (const col of order) {
    let acc = 0;
    col.forEach((id, i) => {
      if (i > 0) acc += sep(col[i - 1], id);
      y.set(id, acc);
    });
  }
  const weight = (a: string, b: string) => {
    const ra = items.get(a)!.real, rb = items.get(b)!.real;
    return !ra && !rb ? 4 : !ra || !rb ? 2 : 1; // 中转点之间权重大：长线拉直
  };
  for (let round = 0; round < 40; round++) {
    const ls = round % 2 === 0 ? [...Array(nLayers).keys()] : [...Array(nLayers).keys()].reverse();
    for (const l of ls) {
      const col = order[l];
      if (col.length === 0) continue;
      const offs: number[] = [];
      col.forEach((id, i) => offs.push(i === 0 ? 0 : offs[i - 1] + sep(col[i - 1], id)));
      const t: number[] = [];
      const w: number[] = [];
      col.forEach((id, i) => {
        const ns = [...preds.get(id)!, ...succs.get(id)!];
        let sw = 0, s = 0;
        for (const n of ns) {
          const k = weight(id, n);
          sw += k;
          s += k * y.get(n)!;
        }
        t.push((sw > 0 ? s / sw : y.get(id)!) - offs[i]);
        w.push(sw > 0 ? 1 + sw / 4 : 0.25);
      });
      const z = pav(t, w);
      col.forEach((id, i) => y.set(id, z[i] + offs[i]));
    }
  }
  let minTop = Infinity, maxBottom = -Infinity;
  for (const [id, v] of y) {
    minTop = Math.min(minTop, v - size(id) / 2);
    maxBottom = Math.max(maxBottom, v + size(id) / 2);
  }
  if (!Number.isFinite(minTop)) {
    minTop = 0;
    maxBottom = 0;
  }
  const cy = (id: string) => o.top + y.get(id)! - minTop;

  const pos = new Map<string, Pt>();
  for (const c of layerOf.keys()) pos.set(c, { x: colX(layerOf.get(c)!), y: cy(c) - o.nodeH / 2 });

  // 连线：每个点的出口、入口按另一头的高低错开
  const PORT = 5;
  const portOut = new Map<string, number>();
  const portIn = new Map<string, number>();
  const spread = (list: { e: LEdge; other: number }[], put: (e: LEdge, off: number) => void) => {
    list.sort((a, b) => a.other - b.other);
    const span = Math.min(PORT * (list.length - 1), o.nodeH - 16);
    const step = list.length > 1 ? span / (list.length - 1) : 0;
    list.forEach((it, i) => put(it.e, -span / 2 + i * step));
  };
  const outs = new Map<string, { e: LEdge; other: number }[]>();
  const ins = new Map<string, { e: LEdge; other: number }[]>();
  for (const e of forward) {
    const ds = chains.get(edgeKey(e))!;
    const next = ds.length ? ds[0] : e.to;
    const prev = ds.length ? ds[ds.length - 1] : e.from;
    if (!outs.has(e.from)) outs.set(e.from, []);
    if (!ins.has(e.to)) ins.set(e.to, []);
    outs.get(e.from)!.push({ e, other: cy(next) });
    ins.get(e.to)!.push({ e, other: cy(prev) });
  }
  for (const list of outs.values()) spread(list, (e, off) => portOut.set(edgeKey(e), off));
  for (const list of ins.values()) spread(list, (e, off) => portIn.set(edgeKey(e), off));

  const routed: RoutedEdge[] = forward.map((e) => {
    const ds = chains.get(edgeKey(e))!;
    const pts: { enter: Pt; exit: Pt }[] = ds.map((id) => {
      const l = items.get(id)!.layer;
      return { enter: { x: colX(l), y: cy(id) }, exit: { x: colX(l) + o.nodeW, y: cy(id) } };
    });
    const start: Pt = { x: pos.get(e.from)!.x + o.nodeW, y: cy(e.from) + portOut.get(edgeKey(e))! };
    const end: Pt = { x: pos.get(e.to)!.x - 2, y: cy(e.to) + portIn.get(edgeKey(e))! };
    let d = `M${r(start.x)},${r(start.y)}`;
    let cur = start;
    for (const p of [...pts, { enter: end, exit: end }]) {
      const dx = (p.enter.x - cur.x) / 2;
      d += `C${r(cur.x + dx)},${r(cur.y)} ${r(p.enter.x - dx)},${r(p.enter.y)} ${r(p.enter.x)},${r(p.enter.y)}`;
      if (p.exit !== p.enter) d += `L${r(p.exit.x)},${r(p.exit.y)}`;
      cur = p.exit;
    }
    return { from: e.from, to: e.to, cross: e.cross, d };
  });

  const layeredH = layerOf.size ? maxBottom - minTop : 0;
  const layeredW = nLayers ? colX(nLayers - 1) + o.nodeW + o.padX : 0;
  const bands: Band[] = bandRange.map((b) => ({
    course: b.course, x: colX(b.first) - o.padX / 2, width: colX(b.last) + o.nodeW - colX(b.first) + o.padX, count: b.count,
  }));

  // 没有连线的知识点：放在下面，按课、按编码排成格子
  let width = Math.max(layeredW, o.padX * 2 + 4 * (o.nodeW + o.isoGap));
  let height = o.top + layeredH;
  let isoTop: number | null = null;
  if (isolated.length) {
    isoTop = height + (layerOf.size ? 40 : 0);
    const perRow = Math.max(1, Math.floor((width - o.padX * 2 + o.isoGap) / (o.nodeW + o.isoGap)));
    isolated.forEach((c, i) => {
      pos.set(c, {
        x: o.padX + (i % perRow) * (o.nodeW + o.isoGap),
        y: isoTop! + 30 + Math.floor(i / perRow) * (o.nodeH + o.isoGap),
      });
    });
    height = isoTop + 30 + Math.ceil(isolated.length / perRow) * (o.nodeH + o.isoGap) - o.isoGap;
  }
  width = Math.max(width, 0);
  return { pos, layerOf, edges: routed, hidden, bands, isolated, isoTop, width, height: height + 20, crossings: bestCross };
}

type RoutedEdgeKey = string;
const edgeKey = (e: LEdge): RoutedEdgeKey => `${e.from}>${e.to}`;
const r = (v: number) => Math.round(v * 10) / 10;
