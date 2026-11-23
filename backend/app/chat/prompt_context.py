"""作业答疑的题面、共用测试点和按提交顺序排列的证据。"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .. import llm, pdftext
from ..xdoj.assemble import assemble_submission
from .context import Ctx

def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class PromptContext:
    document: str
    images: list[tuple[str, bytes]]
    prefix: str
    index: str
    tests: list[dict]


def student_notes(ctx: Ctx) -> str:
    lines = []
    if ctx.profile_note:
        lines.append(f"他在这个知识点上的情况：{ctx.profile_note}")
    if ctx.learning:
        label = "他的学情（优先参考相关知识点）：" if ctx.knowledge else "他的学情（薄弱的地方）："
        lines.append(label + "；".join(ctx.learning))
    if ctx.struggles:
        lines.append("他在别的题上纠结过的地方：" + "；".join(ctx.struggles))
    return llm.strip_identity("\n".join(lines), ctx.user_id, ctx.user_name)


def build(s: Session | None, ctx: Ctx, compared: tuple[dict, ...] = ()) -> PromptContext:
    who = (ctx.user_id, ctx.user_name)
    current = ctx.evidence or {}
    current_no = ctx.attempt_no or current.get("attemptNo") or (1 if current else 0)
    cache = {row["attemptNo"]: row for row in compared}
    if current_no and current:
        cache[current_no] = current
    attempts = dict(enumerate(ctx.attempts, 1))

    def load(no: int) -> dict | None:
        if no not in cache and s is not None and no in attempts:
            sub = attempts[no]
            cache[no] = assemble_submission(s, sub.server_id, sub.submission_id, include_problem_text=False)
        return cache.get(no)

    def render(no: int) -> tuple[str, list[dict]] | None:
        raw = load(no)
        if raw is None:
            return None
        result = llm.evidence_for_model(raw, *who)
        rows = result["testCases"]
        runs = [{key: value for key, value in row.items() if not key.startswith("standard")} for row in rows]
        source = raw.get("sourceCode")
        if not source and no == current_no and ctx.source_kind != "pasted":
            source = ctx.source
        record = {"judgement": result["judgement"], "judgementCode": result["judgementCode"],
                  "compileOutput": result["compileOutput"],
                  "sourceCode": llm.numbered(source, *who, limit=len(source.split("\n"))) if source else None,
                  "testCases": runs}
        return f"【第 {no} 次提交】\n{_json(record)}", rows

    details = []
    for no in sorted(attempts.keys() | cache.keys()):
        if (record := render(no)) is not None:
            details.append(record)

    original = ctx.statement or current.get("problemText") or ""
    statement = llm.strip_identity(original, *who)
    document = f"【题目】\n{statement or '（没有取到文字）'}" if ctx.problem or statement or ctx.document else ""
    standard = {row["testcaseId"]: row for row in ctx.all_tests}
    for _, rows in details:
        for row in rows:
            standard.setdefault(row["testcaseId"], {k: row.get(k) for k in (
                "testcaseId", "standardInput", "standardOutput", "standardInputAvailable", "standardOutputAvailable")})
    tests = [row for _, rows in details for row in rows]
    prefix = "\n\n".join([f"【共用测试点】\n{_json([standard[tid] for tid in sorted(standard)])}",
                          "【按次序排列的提交明细】\n\n" + "\n\n".join(record[0] for record in details)])
    index = "\n".join(f"第 {no} 次：{sub.judgement_text or sub.judgement}（{sub.detail_status}）"
                      for no, sub in attempts.items())
    return PromptContext(document, pdftext.sidebar_images(s, ctx.document, original), prefix, index, tests)
