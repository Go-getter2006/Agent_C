import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

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
  /** keep 时：deps 变了、新数据还没到，data 还是上一份（用来避免切换时页面闪一下） */
  stale: boolean;
  reload: () => void;
  setData: (value: T) => void;
}

/** 取数据。deps 变了重新取；pollMs 设了就在页面可见时定时刷新（刷新失败不清掉已有的数据）。
 *  keep：deps 变了不先清空，新数据到了再换（切换题目、作业时不闪）。 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], pollMs = 0, keep = false): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
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
        setStale(false);
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
    if (keep) setStale(true);
    else setData(null);
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

  const reload = useCallback(() => run(true), [run]);
  return { data, error, loading, stale, reload, setData };
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

/** 元素的宽度，随容器变化。图表用它算比例尺。 */
export function useWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver((entries) => setWidth(Math.floor(entries[0].contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}
