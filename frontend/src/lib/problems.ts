import type { ProblemRow } from "../types";

const numberOf = (p: ProblemRow) => Number(p.key.match(/-(\d+)$/)?.[1] ?? Number.MAX_SAFE_INTEGER);
const timeOf = (p: ProblemRow) => {
  const time = Date.parse(p.last?.time ?? "");
  return Number.isFinite(time) ? time : 0;
};

/** 未通过、未提交、已通过；提交按时间倒序，未提交按题目编号升序。 */
export function sortProblems(problems: ProblemRow[]): ProblemRow[] {
  const group = (p: ProblemRow) => p.passed ? 2 : p.last ? 0 : 1;
  return [...problems].sort((a, b) => group(a) - group(b)
    || (group(a) === 1 ? 0 : timeOf(b) - timeOf(a))
    || numberOf(a) - numberOf(b) || a.key.localeCompare(b.key));
}
