"""学生能看到什么：只有老师布置给他班级的作业和其中的题。权限在这里（后端）按班级控制。

也负责把提交整理成学生端能看的样子：只有“第几次、结果、每个测试点过没过”，
不带测试点的输入、输出、编号和说明。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import accounts
from . import models as m
from .labeling.labeler import current_problem_label
from .taxonomy import JUDGEMENTS


def problem_key(server_id: int, problem_id: int) -> str:
    return f"p{server_id}-{problem_id}"


def submission_key(server_id: int, submission_id: int) -> str:
    return f"s{server_id}-{submission_id}"


def homework_key(server_id: int, contest_id: int) -> str:
    return f"h{server_id}-{contest_id}"


def parse_key(key: str, prefix: str) -> tuple[int, int] | None:
    if not key or not key.startswith(prefix):
        return None
    try:
        a, b = key[1:].split("-", 1)
        return int(a), int(b)
    except ValueError:
        return None


def visible_contests(s: Session, user_id: str) -> list[m.Contest]:
    """这个学生所在班级被布置的作业。"""
    classes = accounts.active_class_keys(s, user_id)
    out: dict[tuple[int, int], m.Contest] = {}
    for server_id, class_id in classes:
        rows = s.scalars(
            select(m.Contest).join(m.ContestClass, (m.ContestClass.server_id == m.Contest.server_id)
                                   & (m.ContestClass.contest_id == m.Contest.contest_id))
            .where(m.ContestClass.server_id == server_id, m.ContestClass.class_id == class_id)).all()
        for c in rows:
            out[(c.server_id, c.contest_id)] = c
    return list(out.values())


def contest_problem_ids(s: Session, server_id: int, contest_id: int) -> list[int]:
    return list(s.scalars(select(m.ContestProblem.problem_id).where(
        m.ContestProblem.server_id == server_id, m.ContestProblem.contest_id == contest_id
    ).order_by(m.ContestProblem.seq, m.ContestProblem.problem_id)).all())


def visible_problems(s: Session, user_id: str) -> dict[tuple[int, int], tuple[int, int]]:
    """可见的题 -> 它所在的作业（有多个时取截止时间最晚的）。"""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    contests = sorted(visible_contests(s, user_id),
                      key=lambda c: c.end_time or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    for c in contests:
        for pid in contest_problem_ids(s, c.server_id, c.contest_id):
            out[(c.server_id, pid)] = (c.server_id, c.contest_id)
    return out


def is_visible(s: Session, user_id: str, server_id: int, problem_id: int) -> bool:
    return (server_id, problem_id) in visible_problems(s, user_id)


@dataclass
class ProblemInfo:
    server_id: int
    problem_id: int
    title: str
    brief: str

    @property
    def key(self) -> str:
        return problem_key(self.server_id, self.problem_id)

    @property
    def ref(self) -> str:
        """提到题目的固定写法：题目名（简述），不说题号。"""
        return f"「{self.title}」（{self.brief}）" if self.brief else f"「{self.title}」"


def problem_info(s: Session, server_id: int, problem_id: int) -> ProblemInfo | None:
    p = s.get(m.Problem, (server_id, problem_id))
    if p is None:
        return None
    label = current_problem_label(s, server_id, problem_id)
    return ProblemInfo(server_id, problem_id, p.title, label.brief if label else "")


def user_submissions(s: Session, user_id: str, server_id: int, problem_id: int,
                     until: dt.datetime | None = None) -> list[m.Submission]:
    q = select(m.Submission).where(
        m.Submission.server_id == server_id, m.Submission.user_id == user_id,
        m.Submission.problem_id == problem_id)
    if until is not None:
        q = q.where(m.Submission.submit_time <= until)
    return list(s.scalars(q.order_by(m.Submission.submission_id)).all())


def testcase_order(s: Session, server_id: int, problem_id: int) -> list[int]:
    return list(s.scalars(select(m.Testcase.testcase_id).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id
    ).order_by(m.Testcase.seq, m.Testcase.testcase_id)).all())


def grid(s: Session, sub: m.Submission, order: list[int] | None = None) -> list[bool] | None:
    """每个测试点过没过，按测试点顺序。编译错误、还没取到详情时返回 None（不显示格子）。"""
    if sub.judgement in ("CE", "PENDING"):
        return None
    judge = s.scalar(select(m.Judge).where(
        m.Judge.server_id == sub.server_id, m.Judge.submission_id == sub.submission_id, m.Judge.is_latest.is_(True)))
    if judge is None:
        return None
    runs = {r.testcase_id: bool(r.passed) for r in s.scalars(select(m.JudgeRun).where(
        m.JudgeRun.server_id == sub.server_id, m.JudgeRun.judge_id == judge.judge_id)).all()}
    if not runs:
        return None
    order = order if order is not None else testcase_order(s, sub.server_id, sub.problem_id)
    known = [tid for tid in order if tid in runs]
    extra = sorted(tid for tid in runs if tid not in order)
    if sub.judgement == "AC":
        return [True] * max(len(order), len(runs))
    return [runs.get(tid, False) for tid in order] + [runs[tid] for tid in extra] if order else \
        [runs[tid] for tid in known + extra]


def submission_view(s: Session, sub: m.Submission, attempt_no: int, order: list[int] | None = None) -> dict:
    """学生端的一条提交。这里出现的字段就是学生能看到的全部。"""
    cells = grid(s, sub, order)
    return {
        "key": submission_key(sub.server_id, sub.submission_id),
        "attemptNo": attempt_no,
        "result": sub.judgement,
        "resultText": JUDGEMENTS.get(sub.judgement, "结果未知"),
        "passed": sum(cells) if cells is not None else None,
        "total": len(cells) if cells is not None else None,
        "grid": cells,
        "time": sub.submit_time.isoformat() if sub.submit_time else None,
    }


def submissions_view(s: Session, user_id: str, server_id: int, problem_id: int) -> list[dict]:
    order = testcase_order(s, server_id, problem_id)
    return [submission_view(s, sub, i, order)
            for i, sub in enumerate(user_submissions(s, user_id, server_id, problem_id), start=1)]
