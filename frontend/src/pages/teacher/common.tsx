// 教师端共用：导航、班级选择、页面骨架、几个对照表。
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { CircleAlert, ClipboardCheck, Database, FileDown, GitCompare, LayoutDashboard, Link2, Users, Waypoints } from "lucide-react";
import { get } from "../../api";
import { Shell, type NavItem } from "../../components/Shell";
import { Empty, Loading, Problem } from "../../components/ui";
import { useLoad } from "../../lib/hooks";

export const NAV: NavItem[] = [
  { to: "/teacher/class", label: "总览", icon: LayoutDashboard, section: "班级" },
  { to: "/teacher/students", label: "学生", icon: Users, section: "班级" },
  { to: "/teacher/stuck", label: "卡点", icon: CircleAlert, section: "班级" },
  { to: "/teacher/map", label: "知识图谱", icon: Waypoints, section: "班级" },
  { to: "/teacher/models", label: "模型对比", icon: GitCompare, section: "研究" },
  { to: "/teacher/rating", label: "盲评", icon: ClipboardCheck, section: "研究" },
  { to: "/teacher/logs", label: "日志申请", icon: FileDown, section: "数据" },
  { to: "/teacher/sources", label: "数据源", icon: Database, section: "数据" },
];

/** 数据结构这边多一页“跨课程”（C 语言这边不出现）。 */
export const navFor = (course: "C" | "D"): NavItem[] =>
  course === "D"
    ? NAV.flatMap((n) => (n.to === "/teacher/map" ? [n, { to: "/teacher/cross", label: "跨课程", icon: Link2, section: "班级" }] : [n]))
    : NAV;

export interface ClassInfo {
  key: string;
  name: string;
  server: string;
  students: number;
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

interface ClassesValue {
  classes: ClassInfo[];
  current: ClassInfo | null;
  pick: (key: string) => void;
  loading: boolean;
  error: string | null;
  role: string;
  course: "C" | "D"; // 这次登录的课程：两门课分开登录
  reload: () => void;
}

const ClassesContext = createContext<ClassesValue>({ classes: [], current: null, pick: () => undefined, loading: true, error: null, role: "teacher", course: "C", reload: () => undefined });

export function ClassesProvider({ children }: { children: ReactNode }) {
  // 定时刷新：第一次登录后班级和名单是后台陆续同步进来的，页面要跟着出现（还没有班级时 15 秒一次，有了以后一分钟一次）
  const [poll, setPoll] = useState(15000);
  const list = useLoad(() => get<{ classes: ClassInfo[]; name: string; role: string; course: "C" | "D" }>("/api/teacher/classes"), [], poll, false, "t:classes");
  useEffect(() => setPoll(list.data?.classes.length ? 60000 : 15000), [list.data]);
  const [picked, setPicked] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(CLASS_KEY);
    } catch {
      return null;
    }
  });
  const classes = list.data?.classes ?? [];
  const current = classes.find((c) => c.key === picked) ?? classes[0] ?? null;
  const pick = useCallback((key: string) => {
    setPicked(key);
    try {
      window.localStorage.setItem(CLASS_KEY, key);
    } catch {
      /* 存不了也不影响使用 */
    }
  }, []);
  const value = useMemo(() => ({ classes, current, pick, loading: list.loading && !list.data, error: list.error,
    role: list.data?.role ?? "teacher", course: list.data?.course ?? "C", reload: list.reload }),
  [classes, current, pick, list.loading, list.data, list.error, list.reload]);
  return <ClassesContext.Provider value={value}>{children}</ClassesContext.Provider>;
}

export const useClasses = () => useContext(ClassesContext);

export function ClassPicker() {
  const { classes, current, pick } = useClasses();
  if (classes.length === 0) return null;
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
  children: (cls: ClassInfo | null) => ReactNode;
}

export function TeacherPage({ title, sub, withClass = true, tools, crumb, wide = false, children }: PageProps) {
  const { current, loading, error, course } = useClasses();
  return (
    <Shell nav={navFor(course)} kind="teacher">
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
