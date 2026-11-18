// 选中项的滑块：选中的底色从原来那一项滑到新点的那一项。
// 页面切换时外壳会重新挂载，所以记住上一次的位置（按 name 分开记），新挂载时先停在老位置，下一帧再滑过去。
import { useLayoutEffect, useRef, useState, type RefObject } from "react";

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

const last: Record<string, Box> = {};

/** container 里带 activeSelector 的那个元素就是选中项。watch 变了（比如换了页面、换了题）重新量。 */
export function useSlider(name: string, container: RefObject<HTMLElement>, activeSelector: string, watch: unknown) {
  const [box, setBox] = useState<Box | null>(() => last[name] ?? null);
  const [animate, setAnimate] = useState(false);
  const first = useRef(true);

  useLayoutEffect(() => {
    const el = container.current;
    if (!el) return;
    const measure = (smooth: boolean) => {
      const a = el.querySelector<HTMLElement>(activeSelector);
      if (!a) {
        setBox(null);
        return;
      }
      const next = { top: a.offsetTop, left: a.offsetLeft, width: a.offsetWidth, height: a.offsetHeight };
      last[name] = next;
      if (smooth) {
        // 先在老位置画一帧，再滑到新位置
        requestAnimationFrame(() => {
          setAnimate(true);
          setBox(next);
        });
      } else {
        setAnimate(false);
        setBox(next);
      }
    };
    const wasFirst = first.current;
    first.current = false;
    const prev = last[name];
    measure(!wasFirst || prev !== undefined);
    const ro = new ResizeObserver(() => measure(false));
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watch]);

  const style = box
    ? { transform: `translate(${box.left}px, ${box.top}px)`, width: box.width, height: box.height, opacity: 1 }
    : { opacity: 0 };
  return { style, className: animate ? "slider slider-anim" : "slider" };
}
