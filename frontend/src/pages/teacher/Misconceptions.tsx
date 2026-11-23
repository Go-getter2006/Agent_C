// 误区：班里要在课上强调的错误点（app/misconceptions.py）。总览列前几条，这一页列全部。
// 一条 = 一种错误 × 一个技能：几个人、几次提交、还没过的几人、卡住几次、小题里没看出来的几人、出现在哪几道题、谁、他们的说法。
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CircleCheck, Search } from "lucide-react";
import { get } from "../../api";
import { Card, Empty, Loading, Problem, Segmented } from "../../components/ui";
import { useLoad } from "../../lib/hooks";
import { StaffLink, useClasses, type ClassInfo } from "./common";

export interface Misconception {
  key: string; error: string; errorName: string; skill: string; skillName: string; kp: string; kpName: string;
  students: number; submissions: number; unresolved: number; fromSubmissions: number; stuck: number; quizMissed: number;
  problems: { problemId: number; title: string; count: number }[];
  who: { userId: string; name: string }[];
  quotes: string[];
  last: string | null;
}

export type Period = "week" | "all";
export const PERIOD_TEXT: Record<Period, string> = { week: "本周", all: "全部" };

function facts(x: Misconception): string[] {
  const out: string[] = [];
  if (x.submissions) out.push(`${x.submissions} 次提交`);
  if (x.unresolved) out.push(`还没过 ${x.unresolved} 人`);
  if (x.stuck) out.push(`卡住 ${x.stuck} 次`);
  if (x.quizMissed) out.push(`小题里没看出来 ${x.quizMissed} 人`);
  return out;
}

/** 一组误区，排成两列（窄屏一列）。whoLimit：每条列出几个名字。 */
export function MisconceptionGrid({ items, whoLimit = 6 }: { items: Misconception[]; whoLimit?: number }) {
  return (
    <ul className="misc-grid">
      {items.map((x) => (
        <li key={x.key} className="misc-item">
          <div className="misc-head">
            <strong className="misc-title">{x.errorName || x.skillName}</strong>
            <span className="misc-people num">{x.students} 人</span>
          </div>
          {(x.errorName && x.skillName) || x.kpName ? (
            <span className="misc-sub">
              {x.errorName && x.skillName ? x.skillName : null}
              {x.kpName ? <>{x.errorName && x.skillName ? " · " : ""}<StaffLink className="link" to={`/map?kp=${x.kp}`}>{x.kpName}</StaffLink></> : null}
            </span>
          ) : null}
          {facts(x).length > 0 && <span className="misc-facts note">{facts(x).join(" · ")}</span>}
          {x.quotes.length > 0 && (
            <ul className="misc-quotes">{x.quotes.map((q) => <li key={q}>“{q}”</li>)}</ul>
          )}
          {x.problems.length > 0 && (
            <span className="misc-where note">出现在：{x.problems.map((p) => `「${p.title || "某道题"}」${p.count} 次`).join("、")}</span>
          )}
          <span className="misc-who">
            {x.who.slice(0, whoLimit).map((w) => <StaffLink key={w.userId} to={`/students/${w.userId}`} className="chip-sm">{w.name || w.userId}</StaffLink>)}
            {x.students > Math.min(whoLimit, x.who.length) && <span className="note">等 {x.students} 人</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function Misconceptions({ cls }: { cls: ClassInfo }) {
  const { tick } = useClasses();
  const [params, setParams] = useSearchParams();
  const period: Period = params.get("period") === "all" ? "all" : "week";
  const [query, setQuery] = useState("");
  const [kp, setKp] = useState("");
  const data = useLoad(() => get<{ items: Misconception[] }>(`/api/teacher/classes/${cls.key}/misconceptions?period=${period}`),
    [cls.key, period, tick], 0, true, `t:misc:${cls.key}:${period}`, String(tick));
  const items = data.data?.items ?? [];
  const kps = useMemo(() => [...new Map(items.filter((x) => x.kp).map((x) => [x.kp, x.kpName])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1], "zh")), [items]);
  const shown = items.filter((x) => (!kp || x.kp === kp) && (!query.trim() ||
    [x.errorName, x.skillName, x.kpName, ...x.quotes, ...x.who.map((w) => w.name), ...x.problems.map((p) => p.title)]
      .some((v) => v.includes(query.trim()))));

  if (data.error && !data.data) return <Problem message={data.error} onRetry={data.reload} />;
  return (
    <div className="stack">
      <div className="table-tools card">
        <Segmented label="范围" value={period} onChange={(v) => setParams(v === "all" ? { period: "all" } : {}, { replace: true })}
          options={[{ value: "week", label: "本周" }, { value: "all", label: "全部" }]} />
        <label className="search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">搜索误区</span>
          <input className="field" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="错误、知识点、题目、学生" />
        </label>
        <select className="field" value={kp} onChange={(e) => setKp(e.target.value)} aria-label="按知识点看">
          <option value="">全部知识点</option>
          {kps.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
        <span className="note">{shown.length} 条</span>
      </div>
      <Card title={`${PERIOD_TEXT[period]}的误区`} sub="一种错误 × 一个技能；人数多的在前">
        {!data.data ? <Loading what="正在整理误区" lines={4} /> : shown.length === 0
          ? <Empty icon={CircleCheck} title={items.length ? "没有符合的误区" : `${PERIOD_TEXT[period]}没有记到误区`} />
          : <MisconceptionGrid items={shown} whoLimit={12} />}
      </Card>
    </div>
  );
}
