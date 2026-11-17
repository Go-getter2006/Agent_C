// 登录状态，以及学生端几个页面共用的“当前选的是哪个作业”。
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler } from "./api";
import { useLoad } from "./lib/hooks";
import type { Homework, Me, StatusInfo } from "./types";

interface SessionValue {
  me: Me | null;
  checked: boolean;
  setMe: (me: Me | null) => void;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({ me: null, checked: false, setMe: () => undefined, logout: async () => undefined });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null)).finally(() => setChecked(true));
    setUnauthorizedHandler(() => setMe(null)); // 会话过期：回到登录页
    return () => setUnauthorizedHandler(null);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setMe(null);
    }
  }, []);

  const value = useMemo(() => ({ me, checked, setMe, logout }), [me, checked, logout]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);

const HW_KEY = "pda.homework";

/** 学生可见的作业，以及当前选中的那个（记在本机，下次打开还是它）。 */
export function useHomeworks() {
  const list = useLoad(() => api.homeworks().then((r) => r.homeworks), [], 60000);
  const [picked, setPicked] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(HW_KEY);
    } catch {
      return null;
    }
  });
  const homeworks: Homework[] = list.data ?? [];
  // 没选过：进最近交过题的那个作业；都没交过就进截止时间最近的
  const recent = homeworks.filter((h) => h.lastSubmit).sort((a, b) => (a.lastSubmit! < b.lastSubmit! ? 1 : -1))[0];
  const current = homeworks.find((h) => h.key === picked) ?? recent ?? homeworks.find((h) => !h.ended) ?? homeworks[0] ?? null;
  const pick = useCallback((key: string) => {
    setPicked(key);
    try {
      window.localStorage.setItem(HW_KEY, key);
    } catch {
      /* 存不了也不影响使用 */
    }
  }, []);
  return { homeworks, current, pick, loading: list.loading, error: list.error, reload: list.reload };
}

// ---------- 学生端：XDOJ 状态、该复习的数量（侧边栏的小红点用） ----------
interface StatusValue {
  status: StatusInfo | null;
  reload: () => void;
}

const StatusContext = createContext<StatusValue>({ status: null, reload: () => undefined });

export function StatusProvider({ children }: { children: ReactNode }) {
  const status = useLoad(() => api.status(), [], 60000);
  const value = useMemo(() => ({ status: status.data, reload: status.reload }), [status.data, status.reload]);
  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export const useStatus = () => useContext(StatusContext);
