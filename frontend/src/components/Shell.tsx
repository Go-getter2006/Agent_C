// 外壳：导航条 + 内容。宽屏是左侧竖条，手机是底部标签栏。只有三项，不放头像和徽标。
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { Icon } from "./ui";

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

export const STUDENT_NAV: NavItem[] = [
  { to: "/chat", label: "对话", icon: <Icon.chat /> },
  { to: "/submissions", label: "提交", icon: <Icon.list /> },
  { to: "/progress", label: "学情", icon: <Icon.bars /> },
];

export function Shell({ nav = STUDENT_NAV, children }: { nav?: NavItem[]; children: ReactNode }) {
  return (
    <div className="shell">
      <nav className="rail" aria-label="主导航">
        {nav.map((item) => (
          <NavLink key={item.to} to={item.to}>
            {item.icon}
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="shell-main">{children}</div>
    </div>
  );
}
