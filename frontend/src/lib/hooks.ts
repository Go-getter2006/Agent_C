import { useCallback, useEffect, useRef, useState } from "react";

/** 当前属于哪一档布局。和 layout.css 里的断点保持一致。 */
export type Layout = "phone" | "two" | "three" | "four";

function layoutOf(width: number): Layout {
  if (width < 768) return "phone";
  if (width < 1024) return "two";
  if (width < 1280) return "three";
  return "four";
}

export function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(() => layoutOf(window.innerWidth));
  useEffect(() => {
    const onResize = () => setLayout(layoutOf(window.innerWidth));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return layout;
}

interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (value: T) => void;
}

/** 取数据。deps 变了重新取；pollMs 设了就在页面可见时定时刷新（刷新失败不清掉已有的数据）。 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], pollMs = 0): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);

  const run = useCallback((silent: boolean) => {
    const mine = ++seq.current;
    if (!silent) setLoading(true);
    fnRef.current()
      .then((value) => {
        if (mine !== seq.current) return;
        setData(value);
        setError(null);
      })
      .catch((err: Error) => {
        if (mine !== seq.current) return;
        if (!silent) setError(err.message);
      })
      .finally(() => {
        if (mine === seq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    setData(null);
    setError(null);
    run(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    if (!pollMs) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") run(true);
    }, pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs, run]);

  return { data, error, loading, reload: () => run(true), setData };
}

/** 按 Esc 关闭。 */
export function useEscape(onClose: () => void, active = true) {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, active]);
}
