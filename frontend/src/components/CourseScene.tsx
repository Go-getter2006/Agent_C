// 首页背景的立体场景：C 语言是一个“开口的环”（字母 C 本身就是开口的环），由一格格内存单元、指针箭头和 C 的记号组成；
// 切到数据结构时，整个环绕竖轴转过来，合成深蓝色的“闭合的环”：循环链表、上面一棵二叉树、几条图的边（浅色冷纸上）。
//
// - 立体：每个内存单元是真正的立方体（8 个顶点做透视投影，按法线挑看得见的面、按深度画）；记号分前后几层，越远越淡。
// - 跟着光标：整个场景朝光标轻微倾斜（左右 ±0.32、上下 ±0.2 弧度），离光标近的格子、节点亮起来并被轻轻吸过去。
// - 平时：场景左右轻轻摆动，记号上下浮动。都很慢、幅度很小。
// - 减少动态效果（prefers-reduced-motion）：不摆动、不跟光标，切换课程时直接换，不转。
// - 只用 Canvas 2D，不加 3D 库；一共几十个元素，标签页切到后台时停下。
import { useEffect, useRef } from "react";

type V3 = [number, number, number];
type Course = "C" | "D";

interface Item {
  kind: "cell" | "token";
  c: V3;           // C 语言里的位置
  d: V3;           // 数据结构里的位置
  labelC: string;
  labelD: string;
  showC: boolean;  // 平时就显示标签（否则光标靠近才显示）
  showD: boolean;
  phase: number;
}

interface Link {
  a: number;
  b: number;
  kind: "seq" | "ptr" | "next" | "tree" | "graph";
}

// ------------------------------------------------------------ 场景（固定，同样的数据每次一样）
function seeded(seed: number) {
  let x = seed;
  return () => {
    x = (x * 1664525 + 1013904223) % 4294967296;
    return x / 4294967296;
  };
}

const CELLS = 20;
const C_LABELS = ["0x7f", "a[0]", "a[1]", "a[2]", "a[3]", "p", "*p", "&x", "'C'", "42", "\\0", "char", "int", "0x2a",
  "buf", "i", "n", "sum", "0x00", "ptr"];
// 数据结构：环上第 6 个节点在最前面，标 head；中间的树是一棵二叉排序树
const D_LABELS = ["", "", "", "", "", "", "head", "", "", "", "", "", "8", "3", "12", "1", "5", "9", "15", "0"];
const TOKENS_C = ["#include", "<stdio.h>", "int main()", "{", "}", "printf", "scanf", "for (;;)", "while", "return 0;",
  "*p", "&x", "->", "NULL", "malloc", "sizeof", "char *s", "%d"];
const TOKENS_D = ["next", "tail", "NULL", "root", "left", "right", "push", "pop", "queue", "stack", "O(log n)",
  "DFS", "BFS", "heap", "hash", "top", "front", "rear"];

function buildScene(): { items: Item[]; linksC: Link[]; linksD: Link[] } {
  const items: Item[] = [];
  // C：开口的环（缺口朝右，像字母 C），内存单元沿着环排开，前后有一点起伏
  const R = 215;
  for (let i = 0; i < CELLS; i++) {
    const t = i / (CELLS - 1);
    const ang = (40 + t * 280) * (Math.PI / 180);
    const c: V3 = [R * Math.cos(ang), R * Math.sin(ang), 46 * Math.sin(ang * 2)];
    items.push({ kind: "cell", c, d: [0, 0, 0], labelC: C_LABELS[i], labelD: D_LABELS[i],
      showC: i % 3 === 1 || i === 5 || i === 6, showD: D_LABELS[i] !== "", phase: i * 0.7 });
  }
  // 数据结构：12 个节点组成闭合的环（循环链表，放倒成从上往下看的轨道，近的一边在下面），
  // 环心上方立着一棵 8 个节点的二叉树
  const ring = 12;
  const tilt = (76 * Math.PI) / 180;
  for (let i = 0; i < ring; i++) {
    const ang = (i / ring) * Math.PI * 2 + Math.PI / 2;
    const x = 280 * Math.cos(ang);
    const y = 280 * Math.sin(ang);
    items[i].d = [x, y * Math.cos(tilt) - 70, -y * Math.sin(tilt)];
  }
  const tree: V3[] = [[0, 210, 0], [-92, 132, 18], [92, 132, -18], [-138, 52, 30], [-46, 52, 6], [46, 52, -6],
    [138, 52, -30], [-170, -28, 40]];
  tree.forEach((p, i) => { items[ring + i].d = p; });
  // 记号：分前后几层浮在环的四周
  const rnd = seeded(7);
  for (let i = 0; i < TOKENS_C.length; i++) {
    const shell = () => {
      const ang = rnd() * Math.PI * 2;
      const r = 280 + rnd() * 100;
      return [r * Math.cos(ang) * 1.05, r * Math.sin(ang) * 0.62, (rnd() - 0.5) * 440] as V3;
    };
    items.push({ kind: "token", c: shell(), d: shell(), labelC: TOKENS_C[i], labelD: TOKENS_D[i],
      showC: true, showD: true, phase: rnd() * Math.PI * 2 });
  }
  const linksC: Link[] = [];
  for (let i = 0; i + 1 < CELLS; i++) linksC.push({ a: i, b: i + 1, kind: "seq" });   // 相邻的内存
  linksC.push({ a: 5, b: 13, kind: "ptr" }, { a: 6, b: 9, kind: "ptr" }, { a: 19, b: 2, kind: "ptr" });   // 指针
  const linksD: Link[] = [];
  for (let i = 0; i < ring; i++) linksD.push({ a: i, b: (i + 1) % ring, kind: "next" });   // next 指针，首尾相接
  const parent = [-1, 0, 0, 1, 1, 2, 2, 3];
  parent.forEach((p, i) => { if (p >= 0) linksD.push({ a: ring + p, b: ring + i, kind: "tree" }); });
  linksD.push({ a: 0, b: ring, kind: "graph" }, { a: 4, b: ring + 3, kind: "graph" }, { a: 8, b: ring + 6, kind: "graph" });
  return { items, linksC, linksD };
}

const SCENE = buildScene();

// ------------------------------------------------------------ 颜色
type RGB = [number, number, number];
const PAL: Record<Course, { ink: RGB; soft: RGB; accent: RGB; top: RGB; front: RGB; side: RGB; glow: RGB }> = {
  C: { ink: [30, 28, 24], soft: [120, 102, 84], accent: [214, 72, 28], top: [255, 252, 246], front: [240, 228, 211],
    side: [212, 193, 168], glow: [214, 72, 28] },
  // 数据结构：浅色冷纸上的深蓝图案（和登录以后的页面同一套颜色，tokens.css 里的 --brand、--sidebar）
  D: { ink: [19, 40, 77], soft: [84, 108, 150], accent: [33, 96, 196], top: [236, 242, 252], front: [214, 227, 247],
    side: [180, 200, 232], glow: [33, 96, 196] },
};
const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const rgba = (c: RGB, a: number) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a.toFixed(3)})`;
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// ------------------------------------------------------------ 3D
function rotate([x, y, z]: V3, yaw: number, pitch: number): V3 {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  return [x1, y * cp - z1 * sp, y * sp + z1 * cp];
}

const CUBE_YAW = 0.62, CUBE_PITCH = 0.42;
const CUBE: V3[] = [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]];
const FACES: { idx: number[]; n: V3; tone: "top" | "front" | "side" }[] = [
  { idx: [4, 5, 6, 7], n: [0, 0, 1], tone: "front" }, { idx: [1, 0, 3, 2], n: [0, 0, -1], tone: "front" },
  { idx: [3, 7, 6, 2], n: [0, 1, 0], tone: "top" }, { idx: [0, 1, 5, 4], n: [0, -1, 0], tone: "side" },
  { idx: [1, 2, 6, 5], n: [1, 0, 0], tone: "side" }, { idx: [0, 4, 7, 3], n: [-1, 0, 0], tone: "side" },
];

interface Props {
  course: Course;
  // hero：首页的整幅背景；mini：登录以后用的小图（对话空着的时候），只有环，没有浮动的记号和标签
  variant?: "hero" | "mini";
  className?: string;
}

export default function CourseScene({ course, variant = "hero", className }: Props) {
  const mini = variant === "mini";
  const canvas = useRef<HTMLCanvasElement>(null);
  const target = useRef(course === "D" ? 1 : 0);

  useEffect(() => {
    target.current = course === "D" ? 1 : 0;
  }, [course]);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx0 = el.getContext("2d");
    if (!ctx0) return;
    const ctx: CanvasRenderingContext2D = ctx0;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let w = 0, h = 0, dpr = 1;
    let k = target.current;             // 0 = C 语言，1 = 数据结构
    let raf = 0, last = performance.now(), time = 0;
    const pointer = { x: 0, y: 0, nx: 0, ny: 0, inside: false };
    const tilt = { yaw: 0, pitch: 0 };
    const font = getComputedStyle(document.documentElement).getPropertyValue("--font-code").trim() || "monospace";

    const resize = () => {
      const r = el.getBoundingClientRect();
      dpr = Math.min(2, window.devicePixelRatio || 1);
      w = r.width;
      h = r.height;
      el.width = Math.max(1, Math.round(w * dpr));
      el.height = Math.max(1, Math.round(h * dpr));
    };
    const onPointer = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      pointer.x = e.clientX - r.left;
      pointer.y = e.clientY - r.top;
      // 倾斜按整个窗口算：小图常常不在光标底下，按画布算会一直歪到底
      pointer.nx = Math.max(-1, Math.min(1, (e.clientX / Math.max(1, window.innerWidth)) * 2 - 1));
      pointer.ny = Math.max(-1, Math.min(1, (e.clientY / Math.max(1, window.innerHeight)) * 2 - 1));
      pointer.inside = pointer.x >= 0 && pointer.y >= 0 && pointer.x <= w && pointer.y <= h;
    };
    const onLeave = () => { pointer.inside = false; pointer.nx = 0; pointer.ny = 0; };

    const draw = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      // 代码字体有连字（-> 会画成箭头）；画布上没有 CSS 的 font-variant-ligatures，加一点字距让浏览器不连字
      if ("letterSpacing" in ctx) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = "0.4px";
      const e = ease(k);
      const pal = {
        ink: mix(PAL.C.ink, PAL.D.ink, e), soft: mix(PAL.C.soft, PAL.D.soft, e), accent: mix(PAL.C.accent, PAL.D.accent, e),
        top: mix(PAL.C.top, PAL.D.top, e), front: mix(PAL.C.front, PAL.D.front, e), side: mix(PAL.C.side, PAL.D.side, e),
        glow: mix(PAL.C.glow, PAL.D.glow, e),
      };
      const still = reduce.matches;
      // 宽屏：环在左栏下半部分（上面是标题，右边是登录框）；窄屏：画布是页面顶上的一条，环在标题右边
      // （和 login.css 里 .login-bg 展开的圆心一致）
      const wide = w >= 1024;
      const unit = mini ? Math.min(w, h * 1.3) / 700
        : wide ? Math.min(w * 0.6, h * 0.8) / 820 : Math.min(w * 0.72, h * 0.9) / 850;
      // 宽屏上环放大了，四周的记号不跟着往外扩，免得钻到登录框底下
      const spread = wide ? 0.85 : 1;
      const cx = mini ? w * 0.5 : wide ? w * 0.37 : w * 0.76;
      const cy = mini ? h * 0.5 + (target.current === 1 ? 18 : 0) * unit : wide ? h * 0.7 : h * 0.5;
      const tokenAlpha = wide ? 1 : 0.6;   // 窄屏上记号压在文字后面，再淡一些
      const boost = mini ? 1.25 : 1;         // 小图里格子、节点相对大一些，不然只剩几个点
      const F = 1100;
      // 环转过来：C → 数据结构转半圈（数据结构的位置事先转了 -π，转过来正好摆正）；中途前后拉开一点，更有立体感
      const spin = Math.PI * e;
      const sway = still ? 0 : Math.sin(time * 0.22) * 0.1;
      const yaw = spin + sway + tilt.yaw;
      const pitch = (still ? 0 : Math.sin(time * 0.17) * 0.04) + tilt.pitch;
      const depthBoost = 1 + 0.7 * Math.sin(Math.PI * e);

      const project = (p: V3) => {
        const q = rotate(p, yaw, pitch);
        const s = F / (F - q[2] * unit);
        return { x: cx + q[0] * unit * s, y: cy - q[1] * unit * s, s, z: q[2] };
      };
      const posOf = (it: Item): V3 => {
        const d = rotate(it.d, -Math.PI, 0);   // 数据结构的摆法预先转半圈
        const bob = still || it.kind === "cell" ? 0 : Math.sin(time * 0.6 + it.phase) * 6;
        const f = it.kind === "token" ? spread : 1;
        return [lerp(it.c[0], d[0], e) * f, (lerp(it.c[1], d[1], e) + bob) * f, lerp(it.c[2], d[2], e) * depthBoost];
      };

      const pts = SCENE.items.map((it) => ({ it, p3: posOf(it), ...project(posOf(it)) }));
      // 光标附近亮起来，并且被轻轻吸过去
      const near = pts.map((p) => {
        if (still || !pointer.inside) return 0;
        const d = Math.hypot(p.x - pointer.x, p.y - pointer.y);
        return Math.max(0, 1 - d / 130);
      });
      pts.forEach((p, i) => {
        if (near[i] > 0) {
          p.x += (pointer.x - p.x) * 0.08 * near[i];
          p.y += (pointer.y - p.y) * 0.08 * near[i];
        }
      });

      // 连线：C 的相邻内存、指针；数据结构的 next、树边、图的边。按进度淡入淡出
      const drawLinks = (links: Link[], alpha: number) => {
        if (alpha <= 0.01) return;
        for (const l of links) {
          const a = pts[l.a], b = pts[l.b];
          const fog = Math.max(0.25, Math.min(1, 0.6 + (a.z + b.z) / 900));
          ctx.beginPath();
          if (l.kind === "ptr") {
            // 指针：从格子弯向环心再指过去
            const mx = (a.x + b.x) / 2 + (cx - (a.x + b.x) / 2) * 0.55, my = (a.y + b.y) / 2 + (cy - (a.y + b.y) / 2) * 0.55;
            ctx.moveTo(a.x, a.y);
            ctx.quadraticCurveTo(mx, my, b.x, b.y);
            ctx.strokeStyle = rgba(pal.accent, 0.75 * alpha * fog);
            ctx.lineWidth = 1.6;
            ctx.setLineDash([]);
            ctx.stroke();
            arrow(b.x, b.y, Math.atan2(b.y - my, b.x - mx), pal.accent, 0.8 * alpha * fog);
            continue;
          }
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          if (l.kind === "graph") ctx.setLineDash([4, 6]);
          else ctx.setLineDash([]);
          const col = l.kind === "next" ? pal.accent : pal.soft;
          ctx.strokeStyle = rgba(col, (l.kind === "seq" ? 0.32 : l.kind === "graph" ? 0.35 : 0.6) * alpha * fog);
          ctx.lineWidth = l.kind === "next" ? 1.5 : 1.1;
          ctx.stroke();
          if (l.kind === "next") {
            const ang = Math.atan2(b.y - a.y, b.x - a.x);
            const r = 11 * b.s * unit;
            arrow(b.x - Math.cos(ang) * r, b.y - Math.sin(ang) * r, ang, pal.accent, 0.7 * alpha * fog);
          }
        }
        ctx.setLineDash([]);
      };
      const arrow = (x: number, y: number, ang: number, col: RGB, a: number) => {
        const s = 6;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x - s * Math.cos(ang - 0.45), y - s * Math.sin(ang - 0.45));
        ctx.lineTo(x - s * Math.cos(ang + 0.45), y - s * Math.sin(ang + 0.45));
        ctx.closePath();
        ctx.fillStyle = rgba(col, a);
        ctx.fill();
      };
      drawLinks(SCENE.linksC, 1 - e);
      drawLinks(SCENE.linksD, e);

      // 元素按远近画：远的先画
      const order = pts.map((_p, i) => i).sort((a, b) => pts[a].z - pts[b].z);
      for (const i of order) {
        const p = pts[i];
        const glow = near[i];
        const fog = Math.max(0.18, Math.min(1, 0.62 + p.z / 520));
        if (mini && p.it.kind === "token") continue;
        if (p.it.kind === "cell") {
          // C：立方体的内存单元；数据结构：发光的节点。按进度交叉淡入淡出
          if (e < 0.98) drawCube(p.p3, (1 - e) * fog, glow);
          if (e > 0.02) drawNode(p.x, p.y, p.s, e * fog, glow);
          const label = e < 0.5 ? p.it.labelC : p.it.labelD;
          const show = (e < 0.5 ? p.it.showC : p.it.showD) || glow > 0.35;
          if (label && show && !mini) {
            const a = Math.abs(e - 0.5) * 2 * fog * (glow > 0 ? 1 : 0.85);
            ctx.font = `${Math.round(11 * p.s * unit * 1.3)}px ${font}`;
            ctx.textAlign = "center";
            ctx.fillStyle = rgba(glow > 0.35 ? pal.accent : pal.ink, a);
            ctx.fillText(label, p.x, p.y - 24 * p.s * unit * 1.1);
          }
        } else {
          const label = e < 0.5 ? p.it.labelC : p.it.labelD;
          const a = Math.abs(e - 0.5) * 2 * fog * (0.42 + 0.58 * glow) * tokenAlpha;
          ctx.font = `${glow > 0.3 ? 600 : 500} ${Math.round(13 * p.s * unit * 1.25)}px ${font}`;
          ctx.textAlign = "center";
          ctx.fillStyle = rgba(glow > 0.3 ? pal.accent : pal.soft, a);
          ctx.fillText(label, p.x, p.y);
        }
      }

      function drawCube(center: V3, alpha: number, glow: number) {
        if (alpha <= 0.01) return;
        // 每个格子自己斜着摆（露出顶面和侧面），看起来才是立方体
        const half = (17 + glow * 4) * boost;
        const corners = CUBE.map((v) => {
          const o = rotate(v, CUBE_YAW, CUBE_PITCH);
          return project([center[0] + o[0] * half, center[1] + o[1] * half, center[2] + o[2] * half]);
        });
        const faces = FACES.map((f) => ({ f, n: rotate(rotate(f.n, CUBE_YAW, CUBE_PITCH), yaw, pitch) })).filter((x) => x.n[2] > 0.02)
          .sort((a, b) => a.n[2] - b.n[2]);
        for (const { f, n } of faces) {
          const tone = f.tone === "top" ? pal.top : f.tone === "front" ? pal.front : pal.side;
          const lit = 0.82 + 0.18 * n[2];
          const col = mix(mix(tone, [255, 255, 255], 0), pal.glow, glow * 0.35).map((v) => v * lit) as RGB;
          ctx.beginPath();
          f.idx.forEach((ci, j) => (j ? ctx.lineTo(corners[ci].x, corners[ci].y) : ctx.moveTo(corners[ci].x, corners[ci].y)));
          ctx.closePath();
          ctx.fillStyle = rgba(col, alpha);
          ctx.fill();
          ctx.strokeStyle = rgba(pal.ink, 0.42 * alpha);
          ctx.lineWidth = 0.8;
          ctx.stroke();
        }
      }
      function drawNode(x: number, y: number, s: number, alpha: number, glow: number) {
        const r = (9 + glow * 4) * s * unit * 1.25 * boost;
        const g = ctx.createRadialGradient(x, y, 0, x, y, r * 3.2);
        g.addColorStop(0, rgba(pal.glow, (0.16 + glow * 0.2) * alpha));
        g.addColorStop(1, rgba(pal.glow, 0));
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(x, y, r * 3.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = rgba(mix(pal.front, [255, 255, 255], 0.15 + glow * 0.4), alpha);
        ctx.fill();
        ctx.strokeStyle = rgba(pal.accent, 0.9 * alpha);
        ctx.lineWidth = 1.4;
        ctx.stroke();
      }
    };

    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      time += dt;
      const goal = target.current;
      if (reduce.matches) {
        k = goal;
        tilt.yaw = tilt.pitch = 0;
      } else {
        // 切换用 1.6 秒走完；光标倾斜慢慢跟上
        const step = dt / 1.6;
        k = goal > k ? Math.min(goal, k + step) : Math.max(goal, k - step);
        tilt.yaw += (pointer.nx * 0.32 - tilt.yaw) * Math.min(1, dt * 3);
        tilt.pitch += (pointer.ny * 0.2 - tilt.pitch) * Math.min(1, dt * 3);
      }
      draw();
      raf = requestAnimationFrame(tick);
    };
    const start = () => {
      cancelAnimationFrame(raf);
      last = performance.now();
      raf = requestAnimationFrame(tick);
    };
    const onVisible = () => (document.hidden ? cancelAnimationFrame(raf) : start());

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    window.addEventListener("pointermove", onPointer, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVisible);
    start();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onPointer);
      document.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return <canvas ref={canvas} className={className ?? "course-scene"} aria-hidden="true" />;
}
