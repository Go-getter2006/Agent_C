import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

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

/** 上一次取到的数据（按 memoKey），只在内存里、换账户或退出就清掉。切回一个页面先显示它；
 *  取的时候数据的版本（fresh）和现在一样就不再请求，不一样才在后台取新的。 */
const memo = new Map<string, { value: unknown; fresh?: string }>();
const MEMO_MAX = 40;
let memoSession = 0;

export function clearLoadMemo() {
  memo.clear();
  memoSession += 1;
}

function remember(key: string, value: unknown, fresh?: string) {
  memo.delete(key);
  memo.set(key, { value, fresh });
  while (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value as string);
}

/** 取数据。deps 变了重新取；pollMs 设了就在页面可见时定时刷新（刷新失败不清掉已有的数据）。
 *  keep：deps 变了不先清空，新数据到了再换（切换题目、作业时不闪）。
 *  memoKey：切回来时先显示上一次的数据（每组 deps 一个键），新数据到了再换。
 *  fresh：数据的版本（教师端是 useClasses().tick）；上一次就是在这个版本取的，切回来不再请求。 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[], pollMs = 0, keep = false, memoKey?: string,
  fresh?: string): Loaded<T> {
  const [data, setData] = useState<T | null>(() => (memoKey && memo.has(memoKey) ? (memo.get(memoKey)!.value as T) : null));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const keyRef = useRef(memoKey);
  keyRef.current = memoKey;
  const freshRef = useRef(fresh);
  freshRef.current = fresh;
  const seq = useRef(0);
  const retry = useRef<number>();
  const retries = useRef(0);

  const run = useCallback((silent: boolean) => {
    const mine = ++seq.current;
    const session = memoSession;
    window.clearTimeout(retry.current);
    if (!silent) setLoading(true);
    fnRef.current()
      .then((value) => {
        if (mine !== seq.current || session !== memoSession) return;
        // 服务器先给了上一次算好的（教师端汇总页，app/web/cache.py），新的正在后台算：先显示，过两秒再取
        const refreshing = !!value && typeof value === "object" && (value as { refreshing?: boolean }).refreshing === true;
        if (keyRef.current) remember(keyRef.current, value, refreshing ? undefined : freshRef.current);
        if (refreshing && retries.current < 15) {
          retries.current += 1;
          retry.current = window.setTimeout(() => { if (mine === seq.current) run(true); }, 2000);
        } else {
          retries.current = 0;
        }
        setData(value);
        setStale(false);
        setError(null);
      })
      .catch((err: Error) => {
        if (mine !== seq.current || session !== memoSession) return;
        if (!silent) setError(err.message);
      })
      .finally(() => {
        if (mine === seq.current && session === memoSession) setLoading(false);
      });
  }, []);

  // 等着再取的那一次（refreshing）：卸载时取消；换了 deps 以后旧的那次本来就作废（mine !== seq.current）
  useEffect(() => () => window.clearTimeout(retry.current), []);

  useLayoutEffect(() => {
    retries.current = 0;
    setError(null);
    const hit = memoKey ? memo.get(memoKey) : undefined;
    if (hit && fresh !== undefined && hit.fresh === fresh) {
      setData(hit.value as T);   // 上一次就是这个版本的数据：不用再取
      setStale(false);
      setLoading(false);
    } else if (hit) {
      setData(hit.value as T);   // 先显示上一次的，后台取新的
      setStale(true);
      run(true);
    } else {
      if (keep) setStale(true);
      else setData(null);
      run(false);
    }
    return () => { seq.current += 1; }; // 切换或卸载后，丢弃旧请求的响应。
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
  const session = memoSession;
  const replace = useCallback((value: T) => {
    if (session !== memoSession) return;
    seq.current += 1;
    if (keyRef.current) remember(keyRef.current, value, freshRef.current);
    setData(value);
    setLoading(false);
    setStale(false);
    setError(null);
  }, [session]);
  return { data, error, loading, stale, reload, setData: replace };
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
