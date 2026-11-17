// 时间一律按北京时间显示。

const TZ = "Asia/Shanghai";
const dayFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
const clockFmt = new Intl.DateTimeFormat("zh-CN", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const partsFmt = new Intl.DateTimeFormat("zh-CN", { timeZone: TZ, year: "numeric", month: "numeric", day: "numeric" });

function dayKey(d: Date): string {
  return dayFmt.format(d); // 2026-11-16
}

function dayDiff(a: Date, b: Date): number {
  const pa = Date.parse(dayKey(a) + "T00:00:00Z");
  const pb = Date.parse(dayKey(b) + "T00:00:00Z");
  return Math.round((pa - pb) / 86400000);
}

function monthDay(d: Date, withYear = false): string {
  const parts = partsFmt.formatToParts(d);
  const pick = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const md = `${pick("month")} 月 ${pick("day")} 日`;
  return withYear ? `${pick("year")} 年 ${md}` : md;
}

export function clock(iso: string | null): string {
  return iso ? clockFmt.format(new Date(iso)) : "";
}

/** 列表里的短时间：今天显示 21:03，昨天显示“昨天”，更早显示“11 月 12 日”。 */
export function shortTime(iso: string | null, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = dayDiff(now, d);
  if (diff === 0) return clock(iso);
  if (diff === 1) return "昨天";
  if (diff === 2) return "前天";
  return monthDay(d, d.getFullYear() !== now.getFullYear());
}

/** 完整一点的时间：“今天 21:03”“昨天 20:15”“11 月 12 日 09:30”。 */
export function fullTime(iso: string | null, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = dayDiff(now, d);
  const head = diff === 0 ? "今天" : diff === 1 ? "昨天" : diff === 2 ? "前天" : monthDay(d, d.getFullYear() !== now.getFullYear());
  return `${head} ${clock(iso)}`;
}

/** 对话里的日期分隔条。 */
export function dayLabel(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const diff = dayDiff(now, d);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff === 2) return "前天";
  return monthDay(d, d.getFullYear() !== now.getFullYear());
}

export function sameDay(a: string, b: string): boolean {
  return dayKey(new Date(a)) === dayKey(new Date(b));
}

/** 截止时间：“11 月 28 日截止”“已截止”。 */
export function deadline(iso: string | null, ended: boolean): string {
  if (ended) return "已截止";
  if (!iso) return "没有截止时间";
  const d = new Date(iso);
  const diff = dayDiff(d, new Date());
  if (diff === 0) return `今天 ${clock(iso)} 截止`;
  if (diff === 1) return `明天 ${clock(iso)} 截止`;
  return `${monthDay(d)}截止`;
}
