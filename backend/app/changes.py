"""每次提交和上一次比：改了什么、修好了什么问题、还剩什么。学生端直接显示，不用再点进去看差异。

只用他自己的代码和程序标注的错误类型（学生端的说法），不涉及测试点的数据。
“大改”（改动超过一半，或者 20 行以上）说明可能换了一种做法：通过后的总结会问他为什么换（chat/summary.py）。
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog
from . import models as m
from .taxonomy import error_name
from .xdoj.assemble import source_text

EXCERPT_LINES = 6
BIG_LINES = 20
BIG_SHARE = 0.5


def code_lines(s: Session, sub: m.Submission) -> list[str] | None:
    text = source_text(s, sub.server_id, sub.submission_id)
    return text.replace("\r\n", "\n").expandtabs(4).rstrip("\n").split("\n") if text else None


def error_codes(s: Session, sub: m.Submission) -> list[str]:
    """这次提交程序标出来的错误（置信度够的、不是“其他”），通过的提交没有。"""
    if sub.judgement in ("AC", "PENDING"):
        return []
    rows = s.scalars(select(m.SubmissionLabel).where(
        m.SubmissionLabel.server_id == sub.server_id, m.SubmissionLabel.submission_id == sub.submission_id,
        m.SubmissionLabel.is_current.is_(True)).order_by(m.SubmissionLabel.role.desc())).all()
    return list(dict.fromkeys(r.error_code for r in rows if r.confidence >= 0.5 and r.error_code != "OT99"))


@dataclass
class Change:
    changed: int = 0                       # 改动的行数（新增和删掉的都算）
    total: int = 0                         # 新代码的行数
    excerpt: list[dict] = field(default_factory=list)
    hunks: list[tuple[int, int]] = field(default_factory=list)   # 新代码里改动的行号范围
    available: bool = False                # 两次的代码都取到了

    @property
    def big(self) -> bool:
        return self.changed >= BIG_LINES or (self.total > 0 and self.changed / max(self.total, 1) > BIG_SHARE)


def compare(before: list[str] | None, after: list[str] | None) -> Change:
    if not before or not after:
        return Change()
    out = Change(total=len(after), available=True)
    matcher = difflib.SequenceMatcher(None, [x.strip() for x in before], [x.strip() for x in after], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        out.changed += max(i2 - i1, j2 - j1)
        if j2 > j1:
            out.hunks.append((j1 + 1, j2))
        for k in range(i1, i2):
            if len(out.excerpt) < EXCERPT_LINES and before[k].strip():
                out.excerpt.append({"type": "del", "n": k + 1, "text": before[k]})
        for k in range(j1, j2):
            if len(out.excerpt) < EXCERPT_LINES and after[k].strip():
                out.excerpt.append({"type": "add", "n": k + 1, "text": after[k]})
    return out


def _names(codes: list[str]) -> list[str]:
    return [n for n in dict.fromkeys(error_name(c, student_facing=True) for c in codes) if n]


def problem_changes(s: Session, user_id: str, server_id: int, problem_id: int) -> dict[str, dict]:
    """这道题每一次提交（第二次起）和上一次比的结果，按提交的 key。学生端看的，只有他自己的代码和错误的名称。"""
    subs = catalog.user_submissions(s, user_id, server_id, problem_id)
    if len(subs) < 2:
        return {}
    out: dict[str, dict] = {}
    prev, prev_lines, prev_errs = None, None, []
    for i, sub in enumerate(subs, start=1):
        lines = code_lines(s, sub)
        errs = error_codes(s, sub)
        if prev is not None:
            ch = compare(prev_lines, lines)
            fixed = [c for c in prev_errs if c not in errs]
            out[catalog.submission_key(sub.server_id, sub.submission_id)] = {
                "from": i - 1, "available": ch.available, "changed": ch.changed, "big": ch.big,
                "lines": [{"from": a, "to": b} for a, b in ch.hunks[:4]], "excerpt": ch.excerpt,
                "fixed": _names(fixed), "still": _names([c for c in errs if c in prev_errs]),
                "new": _names([c for c in errs if c not in prev_errs]),
                "passedNow": sub.judgement == "AC" and prev.judgement != "AC",
            }
        prev, prev_lines, prev_errs = sub, lines, errs
    return out
