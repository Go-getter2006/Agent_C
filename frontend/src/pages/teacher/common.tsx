// 教师端共用：导航、班级选择、页面骨架、几个对照表。
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ClipboardCheck, Database, Download, FileDown, GitCompare, History, LayoutDashboard, LifeBuoy, Link2, TriangleAlert, Users, Waypoints } from "lucide-react";
import { get } from "../../api";
import { Shell, type NavItem } from "../../components/Shell";
import { Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";

// 三块（docs/教师端结构.md）：班级（上课用）、研究（论文用）、数据。每一页回答一个问题。
// 老师只有“班级”；研究和数据两块只给开发者账号（角色 admin，.env 的 DEV_ACCOUNT），后端也只让它取。
export const NAV: NavItem[] = [
  { to: "/teacher/class", label: "总览", icon: LayoutDashboard, section: "班级" },
  { to: "/teacher/students", label: "学生", icon: Users, section: "班级" },
  { to: "/teacher/submissions", label: "提交记录", icon: History, section: "班级" },
  { to: "/teacher/misconceptions", label: "误区", icon: TriangleAlert, section: "班级" },
  { to: "/teacher/map", label: "知识图谱", icon: Waypoints, section: "班级" },
  { to: "/teacher/models", label: "模型检验", icon: GitCompare, section: "研究" },
  { to: "/teacher/rating", label: "理解评估", icon: ClipboardCheck, section: "研究" },
  { to: "/teacher/help", label: "求助效果", icon: LifeBuoy, section: "研究" },
  { to: "/teacher/logs", label: "日志导出", icon: FileDown, section: "数据" },
  { to: "/teacher/sources", label: "数据源", icon: Database, section: "数据" },
];

/** 只给开发者账号的几页（研究、数据）。 */
export const DEV_ONLY = new Set(NAV.filter((n) => n.section !== "班级").map((n) => n.to));
export const isDeveloper = (role: string) => role === "admin";

/** 数据结构这边多一页“跨课程”（C 语言这边不出现）；老师看不到研究和数据两块。 */
export const navFor = (course: "C" | "D", role = "teacher"): NavItem[] => {
  const nav = isDeveloper(role) ? NAV : NAV.filter((n) => !DEV_ONLY.has(n.to));
  return course === "D"
    ? nav.flatMap((n) => (n.to === "/teacher/map" ? [n, { to: "/teacher/cross", label: "跨课程", icon: Link2, section: "班级" }] : [n]))
    : nav;
};

export interface ClassInfo {
  key: string;
  name: string;
  server: string;
  students: number;
  /** 几个班合起来看时，是哪几个班 */
  members?: string[];
}

export interface SkillRef {
  code: string;
  name: string;
  kp: string;
}

export const RESOLUTION: Record<string, string> = {
  pending: "还在处理", recalled: "自己回忆出来", blank_filled: "挖空补上", example_given: "给了示例代码", exercise: "小练习",
};
export const HELP: Record<string, string> = { NA: "没有求助", H0: "反问", H1: "指出位置", H2: "讲原理", H3: "给了示例代码" };
export const OUTCOME: Record<string, string> = {
  right: "改对了", right_judge: "判断对", right_second: "第二次改对", right_bypass: "绕开原错误改对",
  wrong_fix: "两次没改对", wrong_judge: "判断错", skipped: "跳过", "": "没做完",
};
export const REASON: Record<string, string> = { after_pass: "通过后", recall: "回忆题", revisit: "回访题", review: "复习", stubborn: "顽固卡点" };
export const endReason = (r: { status: string; endReason: string }) => (r.status === "open" ? "进行中" : r.endReason || "已结束");
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "没有数据" : `${Math.round(x * 100)}%`);

export const Code = ({ children }: { children: ReactNode }) => <span className="code-tag">{children}</span>;

// ---------- 班级：几个页面共用同一个选择，记在本机 ----------
const CLASS_KEY = "pda.teacher.class";
const MULTI_KEY = "pda.teacher.classes";   // 开发者多选的几个班

/** 班级编号 c服务器-班 → [服务器, 班]。 */
const parseKey = (key: string) => {
  const m = /^c(\d+)-(\d+)$/.exec(key);
  return m ? [Number(m[1]), Number(m[2])] as const : null;
};

/** 同一台服务器上的几个班合成一个编号：c4-31_32_35（后端当成一个班算，学生、题目取并集）。 */
export function groupClasses(picked: ClassInfo[]): ClassInfo | null {
  if (picked.length === 0) return null;
  if (picked.length === 1) return picked[0];
  const ids = picked.map((c) => parseKey(c.key));
  if (ids.some((x) => !x) || new Set(ids.map((x) => x![0])).size !== 1) return picked[0];
  const names = picked.map((c) => c.name);
  return {
    key: `c${ids[0]![0]}-${ids.map((x) => x![1]).join("_")}`,
    name: names.length <= 3 ? names.join("、") : `${names.length} 个班`,
    server: picked[0].server,
    students: picked.reduce((n, c) => n + c.students, 0),   // 一个人在两个班里时会多算，只做显示
    members: picked.map((c) => c.key),
  };
}

interface ClassesValue {
  classes: ClassInfo[];
  current: ClassInfo | null;
  pick: (key: string) => void;
  /** 开发者：几个班一起看（同一台服务器） */
  pickMany: (keys: string[]) => void;
  loading: boolean;
  error: string | null;
  role: string;
  course: "C" | "D"; // 这次登录的课程：两门课分开登录
  reload: () => void;
  /** 数据的版本：数据变了加一。页面拿它当依赖（变了才重取），不各自定时刷新。 */
  tick: number;
}

const ClassesContext = createContext<ClassesValue>({ classes: [], current: null, pick: () => undefined, pickMany: () => undefined, loading: true, error: null, role: "teacher", course: "C", reload: () => undefined, tick: 0 });

/** 教师端只在这一个地方定时问“数据变没变”（/api/teacher/version）：页面看得见时每分钟一次（还没有班级时 15 秒），
 *  切回这个标签页马上问一次；变了才让页面重取。返回两个计数：data（任何数据）、classes（班级和名单）。 */
function useVersionTicks(fast: boolean): { data: number; classes: number } {
  const [ticks, setTicks] = useState({ data: 0, classes: 0 });
  const last = useRef<{ data: string; classes: string } | null>(null);
  useEffect(() => {
    let alive = true;
    const check = () => {
      if (document.visibilityState !== "visible") return;
      get<{ data: string; classes: string }>("/api/teacher/version").then((v) => {
        if (!alive) return;
        const prev = last.current;
        last.current = v;
        if (!prev || (prev.data === v.data && prev.classes === v.classes)) return;   // 第一次只记下（页面刚取过）
        setTicks((t) => ({ data: t.data + (prev.data !== v.data ? 1 : 0), classes: t.classes + (prev.classes !== v.classes ? 1 : 0) }));
      }).catch(() => undefined);
    };
    if (!last.current) check();   // 只是快慢变了（有了班级）就不用马上再问
    const timer = window.setInterval(check, fast ? 15000 : 60000);
    const onShow = () => { if (document.visibilityState === "visible") check(); };
    document.addEventListener("visibilitychange", onShow);
    return () => { alive = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", onShow); };
  }, [fast]);
  return ticks;
}

export function ClassesProvider({ children }: { children: ReactNode }) {
  // 第一次登录后班级和名单是后台陆续同步进来的：还没有班级时问得勤一点，班级、名单变了才重取
  const [fast, setFast] = useState(true);
  const ticks = useVersionTicks(fast);
  const list = useLoad(() => get<{ classes: ClassInfo[]; name: string; role: string; course: "C" | "D" }>("/api/teacher/classes"),
    [ticks.classes], 0, false, "t:classes", String(ticks.classes));
  useEffect(() => setFast(!list.data?.classes.length), [list.data]);
  const [picked, setPicked] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(CLASS_KEY);
    } catch {
      return null;
    }
  });
  const [many, setMany] = useState<string[]>(() => {
    try {
      const v = JSON.parse(window.localStorage.getItem(MULTI_KEY) ?? "[]");
      return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
    } catch {
      return [];
    }
  });
  const classes = list.data?.classes ?? [];
  const role = list.data?.role ?? "teacher";
  const chosen = isDeveloper(role) ? classes.filter((c) => many.includes(c.key)) : [];
  const current = (chosen.length > 1 ? groupClasses(chosen) : null)
    ?? classes.find((c) => c.key === (chosen.length === 1 ? chosen[0].key : picked)) ?? classes[0] ?? null;
  const pick = useCallback((key: string) => {
    setPicked(key);
    setMany([]);
    try {
      window.localStorage.setItem(CLASS_KEY, key);
      window.localStorage.removeItem(MULTI_KEY);
    } catch {
      /* 存不了也不影响使用 */
    }
  }, []);
  const pickMany = useCallback((keys: string[]) => {
    setMany(keys);
    if (keys.length) setPicked(keys[0]);
    try {
      window.localStorage.setItem(MULTI_KEY, JSON.stringify(keys));
      if (keys.length) window.localStorage.setItem(CLASS_KEY, keys[0]);
    } catch {
      /* 存不了也不影响使用 */
    }
  }, []);
  const value = useMemo(() => ({ classes, current, pick, pickMany, loading: list.loading && !list.data, error: list.error,
    role, course: list.data?.course ?? "C", reload: list.reload, tick: ticks.data }),
  [classes, current, pick, pickMany, list.loading, list.data, list.error, list.reload, ticks.data, role]);
  return <ClassesContext.Provider value={value}>{children}</ClassesContext.Provider>;
}

export const useClasses = () => useContext(ClassesContext);

/** 开发者的班级选择：勾几个班一起看，或者全选（同一台服务器上的班）。 */
function MultiClassPicker() {
  const { classes, current, pickMany } = useClasses();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const selected = current?.members ?? (current ? [current.key] : []);
  const server = (key: string) => parseKey(key)?.[0];
  const groups = useMemo(() => {
    const out = new Map<number, ClassInfo[]>();
    classes.forEach((c) => { const sv = server(c.key) ?? -1; out.set(sv, [...(out.get(sv) ?? []), c]); });
    return [...out.values()];
  }, [classes]);
  const toggle = (c: ClassInfo) => {
    const same = selected.filter((k) => server(k) === server(c.key));   // 不同服务器的班不能合在一起
    const next = same.includes(c.key) ? same.filter((k) => k !== c.key) : [...same, c.key];
    const ordered = classes.filter((x) => next.includes(x.key)).map((x) => x.key);
    if (ordered.length) pickMany(ordered);
  };
  return (
    <div className="class-pick class-multi" ref={box}>
      <Users size={16} aria-hidden="true" />
      <button type="button" className="field class-multi-btn" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{current ? `${current.name}（${current.students} 人）` : "选班级"}</span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open && (
        <div className="class-menu" role="group" aria-label="选几个班一起看">
          {groups.map((list) => {
            const all = list.every((c) => selected.includes(c.key));
            return (
              <div key={list[0].key} className="class-menu-group">
                <label className="check class-menu-all">
                  <input type="checkbox" checked={all} onChange={() => pickMany(all ? [list[0].key] : list.map((c) => c.key))} />
                  全选{groups.length > 1 && list[0].server ? `（${list[0].server}）` : ""}<span className="note">{list.length} 个班</span>
                </label>
                {list.map((c) => (
                  <label key={c.key} className="check">
                    <input type="checkbox" checked={selected.includes(c.key)} onChange={() => toggle(c)} />
                    {c.name}<span className="note">{c.students} 人</span>
                  </label>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ClassPicker() {
  const { classes, current, pick, role } = useClasses();
  if (classes.length === 0) return null;
  if (isDeveloper(role)) return <MultiClassPicker />;
  return (
    <label className="class-pick">
      <span className="sr-only">班级</span>
      <Users size={16} aria-hidden="true" />
      <select className="field" value={current?.key ?? ""} onChange={(e) => pick(e.target.value)}>
        {classes.map((c) => (
          <option key={c.key} value={c.key}>{c.name}（{c.students} 人）</option>
        ))}
      </select>
    </label>
  );
}

interface PageProps {
  title: string;
  sub?: ReactNode;
  withClass?: boolean;
  tools?: ReactNode;
  crumb?: ReactNode;
  wide?: boolean;
  /** 开发者：这一页的数据能导出（/api/teacher/export/页面）；csv 表示有主表、也能导 CSV */
  exportPage?: { page: string; csv?: boolean };
  children: (cls: ClassInfo | null) => ReactNode;
}

function ExportLinks({ page, csv, cls }: { page: string; csv?: boolean; cls: ClassInfo }) {
  const url = (fmt: string) => `/api/teacher/export/${page}?key=${encodeURIComponent(cls.key)}&format=${fmt}`;
  return (
    <span className="export-links">
      <a className="btn btn-sm" href={url("json")} download><Download size={15} aria-hidden="true" />导出</a>
      {csv && <a className="btn btn-sm" href={url("csv")} download>CSV</a>}
    </span>
  );
}

export function TeacherPage({ title, sub, withClass = true, tools, crumb, wide = false, exportPage, children }: PageProps) {
  const { current, loading, error, course, role } = useClasses();
  return (
    <Shell nav={navFor(course, role)} kind="teacher">
      <main className="page">
        <div className={wide ? "page-inner page-wide" : "page-inner"}>
          {crumb}
          <div className="page-head">
            <div>
              <h1>{title}</h1>
              {sub && <p className="page-sub">{sub}</p>}
            </div>
            <div className="page-tools">
              {tools}
              {exportPage && current && isDeveloper(role) && <ExportLinks {...exportPage} cls={current} />}
              {withClass && <ClassPicker />}
            </div>
          </div>
          {loading && <div className="card card-body"><Loading /></div>}
          {error && <Problem message={error} />}
          {!loading && !error && withClass && !current && (
            <div className="card">
              <Empty icon={Users} title="你名下还没有同步到班级">
                正在自动同步你名下的班级和名单，同步好了会自己出现，不用刷新。
              </Empty>
            </div>
          )}
          {!loading && (!withClass || current) && children(current)}
        </div>
      </main>
    </Shell>
  );
}

/** 掌握分：数字加一条细条，颜色跟档位一样（待加强红、基本掌握浅蓝、已掌握绿）。 */
export function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 75 ? "mastered" : score >= 50 ? "basic" : "weak";
  return (
    <span className={`score-badge score-${tone}`}>
      <b className="num">{score}</b>
      <span className="score-bar" aria-hidden="true"><span style={{ width: `${score}%` }} /></span>
    </span>
  );
}
