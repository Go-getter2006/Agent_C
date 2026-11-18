// 外壳：深色侧边栏 + 内容。
// 很宽（学生端 1440 以上、教师端 1280 以上）时侧边栏展开，带文字；中等宽度收成图标竖条；手机上学生端是底部标签栏，教师端是顶栏加抽屉。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { BarChart3, Braces, ListChecks, LogOut, Menu, MessageSquare, RotateCcw, type LucideIcon } from "lucide-react";
import { useSession, useStatus } from "../session";
import { useSlider } from "./Slider";
import { Avatar, Drawer } from "./ui";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  badge?: number;
  section?: string;
}

export const studentNav = (dueReviews = 0): NavItem[] => [
  { to: "/chat", label: "对话", icon: MessageSquare },
  { to: "/submissions", label: "提交", icon: ListChecks },
  { to: "/review", label: "复习", icon: RotateCcw, badge: dueReviews },
  { to: "/progress", label: "学情", icon: BarChart3 },
];

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true"><Braces size={18} strokeWidth={2.4} /></span>
      <span className="brand-name">程序设计智能体</span>
    </div>
  );
}

function NavList({ nav, onNavigate, name = "side" }: { nav: NavItem[]; onNavigate?: () => void; name?: string }) {
  const location = useLocation();
  const box = useRef<HTMLDivElement>(null);
  const slider = useSlider(name, box, ".nav-item.active", location.pathname);
  let section: string | undefined;
  return (
    <div className="nav-track" ref={box}>
      <span className={`${slider.className} nav-slider`} style={slider.style} aria-hidden="true" />
      {nav.map((item) => {
        const head = item.section && item.section !== section ? ((section = item.section), <div key={`s-${item.section}`} className="nav-label">{item.section}</div>) : null;
        const Icon = item.icon;
        return [
          head,
          <NavLink key={item.to} to={item.to} className="nav-item" onClick={onNavigate}
            aria-label={item.badge ? `${item.label}，${item.badge} 项待处理` : undefined}>
            <span className="nav-icon">
              <Icon size={20} aria-hidden="true" />
              {!!item.badge && <span className="nav-dot" aria-hidden="true" />}
            </span>
            <span className="nav-text">{item.label}</span>
            {!!item.badge && <span className="nav-badge num" aria-hidden="true">{item.badge}</span>}
          </NavLink>,
        ];
      })}
    </div>
  );
}

function UserFoot() {
  const { me, logout } = useSession();
  if (!me) return null;
  const role = me.role === "student" ? me.loginName : me.role === "admin" ? "管理员" : "教师";
  const course = me.course === "D" ? "数据结构" : "C 语言";
  return (
    <div className="side-foot">
      <Avatar name={me.name} dark />
      <span className="side-user">
        <strong>{me.name}</strong>
        <small>{role} · {course}</small>
      </span>
      <button type="button" className="side-logout" onClick={() => void logout()} aria-label="退出登录" title="退出登录">
        <LogOut size={18} />
      </button>
    </div>
  );
}

/** 手机上的底部标签栏，选中的那一项上面有一条滑块。 */
function TabBar({ nav }: { nav: NavItem[] }) {
  const location = useLocation();
  const box = useRef<HTMLElement>(null);
  const slider = useSlider("tab", box, "a.active", location.pathname);
  return (
    <nav className="tabbar" aria-label="主导航" ref={box}>
      <span className={`${slider.className} tab-slider`} style={slider.style} aria-hidden="true" />
      {nav.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink key={item.to} to={item.to} aria-label={item.badge ? `${item.label}，${item.badge} 项待处理` : undefined}>
            <span className="nav-icon">
              <Icon size={22} aria-hidden="true" />
              {!!item.badge && <span className="nav-dot" aria-hidden="true" />}
            </span>
            <span>{item.label}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}

interface ShellProps {
  nav: NavItem[];
  kind?: "student" | "teacher";
  children: ReactNode;
}

export function Shell({ nav, kind = "student", children }: ShellProps) {
  const [menu, setMenu] = useState(false);
  const location = useLocation();
  useEffect(() => setMenu(false), [location.pathname]);
  const current = nav.find((n) => location.pathname.startsWith(n.to));

  return (
    <div className={`shell shell-${kind}`}>
      <aside className="sidebar" aria-label="主导航">
        <Brand />
        <nav className="nav">
          <NavList nav={nav} />
        </nav>
        <UserFoot />
      </aside>

      {kind === "teacher" && (
        <header className="appbar">
          <button type="button" className="icon-btn appbar-menu" onClick={() => setMenu(true)} aria-label="打开导航">
            <Menu size={22} />
          </button>
          <span className="appbar-title">{current?.label ?? "教师端"}</span>
          <span className="brand-mark brand-mark-sm" aria-hidden="true"><Braces size={15} strokeWidth={2.4} /></span>
        </header>
      )}

      <div className="shell-main">{children}</div>

      {kind === "student" && (
        <TabBar nav={nav} />
      )}

      {menu && (
        <Drawer side="left" title="导航" dark onClose={() => setMenu(false)}>
          <nav className="nav nav-drawer">
            <NavList nav={nav} onNavigate={() => setMenu(false)} name="drawer" />
          </nav>
          <UserFoot />
        </Drawer>
      )}
    </div>
  );
}

/** 学生端的外壳：侧边栏上“复习”带上该复习的数量。 */
export function StudentShell({ children }: { children: ReactNode }) {
  const { status } = useStatus();
  return <Shell nav={studentNav(status?.dueReviews ?? 0)}>{children}</Shell>;
}
