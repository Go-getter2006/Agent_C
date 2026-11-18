// 学生端在这台设备上记住的东西：各栏目最近的位置、对话里没发出去的草稿、滚动位置。
// - 按账户和课程分开（键里放的是哈希，不直接写学号）；
// - 切栏目时各自保留最近的位置：在某题写了一半，去知识问答查个概念，再回来还是那道题、那段草稿、那个滚动位置；
// - 第一次登录进“作业引导”；再次登录回到上次的位置；外部链接优先于上次的位置；
// - 退出登录时清掉这台设备上的草稿（位置留着）。
// 都放在 localStorage，读写一律 try：无痕窗口、禁用存储时照常能用，只是记不住。
import type { Me } from "../types";

export type Section = "ask" | "homework" | "review" | "progress";

export const SECTION_HOME: Record<Section, string> = {
  ask: "/ask",
  homework: "/homework",
  review: "/review",
  progress: "/progress",
};

let scope = "";

function hash(text: string): string {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

export function setScope(me: Me | null): void {
  scope = me && me.role === "student" ? hash(`${me.loginName}|${me.course}`) : "";
}

const key = (name: string) => `pda:${scope}:${name}`;

function read(name: string): string | null {
  if (!scope) return null;
  try {
    return localStorage.getItem(key(name));
  } catch {
    return null;
  }
}

function write(name: string, value: string | null): void {
  if (!scope) return;
  try {
    if (value === null || value === "") localStorage.removeItem(key(name));
    else localStorage.setItem(key(name), value);
  } catch {
    /* 存不了就算了 */
  }
}

export function sectionOf(path: string): Section | null {
  if (path.startsWith("/ask")) return "ask";
  if (path.startsWith("/homework")) return "homework";
  if (path.startsWith("/review")) return "review";
  if (path.startsWith("/progress") || path.startsWith("/submissions")) return "progress";
  return null;
}

/** 记下现在的位置（路径加查询参数）。 */
export function rememberLocation(path: string): void {
  const section = sectionOf(path);
  if (!section) return;
  write(`loc:${section}`, path);
  write("last", path);
}

/** 这个栏目最近的位置，没有就是栏目首页。 */
export function sectionTarget(section: Section): string {
  return read(`loc:${section}`) ?? SECTION_HOME[section];
}

/** 这个栏目的位置失效了（题不在作业里了、问答删了）：退回栏目首页。 */
export function forgetSection(section: Section): void {
  write(`loc:${section}`, null);
  const last = read("last");
  if (last && sectionOf(last) === section) write("last", null);
}

/** 登录以后先去哪：上次的位置；第一次是作业引导。 */
export function landing(): string {
  return read("last") ?? SECTION_HOME.homework;
}

/** 作业引导里最近打开的题（知识问答里“结合当前题”用）。 */
export function lastProblemKey(): string | null {
  const path = read("loc:homework");
  const m = path?.match(/^\/homework\/(p[^/?#]+)/);
  return m ? m[1] : null;
}

export function readDraft(thread: string): string {
  return read(`draft:${thread}`) ?? "";
}

export function writeDraft(thread: string, text: string): void {
  write(`draft:${thread}`, text.trim() ? text : null);
}

export function clearDrafts(): void {
  if (!scope) return;
  try {
    const prefix = key("draft:");
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && k.startsWith(prefix)) localStorage.removeItem(k);
    }
  } catch {
    /* 存不了就算了 */
  }
}

// 滚动位置只在这次打开的页面里记（换栏目再回来时用），不写进存储
const scrolls = new Map<string, number>();
export const saveScroll = (thread: string, top: number) => scrolls.set(`${scope}:${thread}`, top);
export const readScroll = (thread: string): number | null => scrolls.get(`${scope}:${thread}`) ?? null;
