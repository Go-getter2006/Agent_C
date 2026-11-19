// 登录状态，以及学生端几个页面共用的“当前选的是哪个作业”。
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler } from "./api";
import { clearLoadMemo, useLoad } from "./lib/hooks";
import { clearDrafts, setScope } from "./lib/memory";
import type { Homework, Me, StatusInfo } from "./types";

interface SessionValue {
  me: Me | null;
  checked: boolean;
  setMe: (me: Me | null) => void;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({ me: null, checked: false, setMe: () => undefined, logout: async () => undefined });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMeRaw] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  // 本机记住的位置、草稿按账户和课程分开：先换好范围再渲染页面
  const setMe = useCallback((value: Me | null) => {
    clearLoadMemo();   // 换账户、退出：上一位用户的页面数据不留
    setScope(value);
    setMeRaw(value);
  }, []);

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe(null)).finally(() => setChecked(true));
    setUnauthorizedHandler(() => setMe(null)); // 会话过期：回到登录页
    return () => setUnauthorizedHandler(null);
  }, [setMe]);

  const logout = useCallback(async () => {
    clearDrafts();   // 退出登录：清掉这台设备上没发出去的草稿
    try {
      await api.logout();
    } finally {
      setMe(null);
    }
  }, [setMe]);

  const value = useMemo(() => ({ me, checked, setMe, logout }), [me, checked, logout]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);

const HW_KEY = "pda.homework";

interface HomeworksValue {
  homeworks: Homework[];
  current: Homework | null;
  pick: (key: string) => void;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

const HomeworksContext = createContext<HomeworksValue>({
  homeworks: [], current: null, pick: () => undefined, loading: true, error: null, reload: () => undefined,
});

/** 学生可见的作业，以及当前选中的那个。对话页、提交页共用同一份（以前各存一份，会互相打架）；记在本机，下次打开还是它。 */
export function HomeworksProvider({ children }: { children: ReactNode }) {
  const value = useHomeworksState();
  return <HomeworksContext.Provider value={value}>{children}</HomeworksContext.Provider>;
}

export const useHomeworks = () => useContext(HomeworksContext);

function useHomeworksState(): HomeworksValue {
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
  return useMemo(() => ({ homeworks, current, pick, loading: list.loading && !list.data, error: list.error, reload: list.reload }),
    [homeworks, current, pick, list.loading, list.data, list.error, list.reload]);
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
