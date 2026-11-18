import type { ProblemRow } from "../types";

const numberOf = (p: ProblemRow) => Number(p.key.match(/-(\d+)$/)?.[1] ?? Number.MAX_SAFE_INTEGER);
const timeOf = (p: ProblemRow) => {
  const time = Date.parse(p.last?.time ?? "");
  return Number.isFinite(time) ? time : 0;
};

/** 已确认失败、未提交、评测中/结果未知、已通过；未提交按题号，其余按时间倒序。 */
export function sortProblems(problems: ProblemRow[]): ProblemRow[] {
  const group = (p: ProblemRow) => p.passed ? 3 : !p.last ? 1 : ["PENDING", "UNKNOWN"].includes(p.last.result) ? 2 : 0;
  return [...problems].sort((a, b) => group(a) - group(b)
    || (group(a) === 1 ? 0 : timeOf(b) - timeOf(a))
    || numberOf(a) - numberOf(b) || a.key.localeCompare(b.key));
}
