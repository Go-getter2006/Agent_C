// 从登录页进来的那一次，主界面轻轻淡入（不是刷新页面、不是切栏目）。登录页记一下，Shell 第一次挂上时取走。
let arrived = false;

export function markArrival(): void {
  arrived = true;
}

export function takeArrival(): boolean {
  const was = arrived;
  arrived = false;
  return was;
}

/** 用户在系统里设了“减少动态效果”：不做动画。 */
export function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}
