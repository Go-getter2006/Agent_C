"""学生能看到什么：只有老师布置给他班级的作业和其中的题。权限在这里（后端）按班级控制。

C 语言和数据结构分开登录：传了 course 就只看这门课的服务器上的班级。

也负责把提交整理成学生端能看的样子：只有“第几次、结果、每个测试点过没过”，
不带测试点的输入、输出、编号和说明。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
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


def server_course(s: Session, server_id: int | None) -> str:
    """服务器是哪门课的（C / D）。"""
    server = s.get(m.Server, server_id) if server_id is not None else None
    return server.course if server is not None else "C"


def visible_contests(s: Session, user_id: str, course: str | None = None) -> list[m.Contest]:
    """这个学生所在班级被布置的作业。"""
    classes = accounts.active_class_keys(s, user_id)
    out: dict[tuple[int, int], m.Contest] = {}
    for server_id, class_id in classes:
        if course is not None and server_course(s, server_id) != course:
            continue
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


def visible_problems(s: Session, user_id: str, course: str | None = None) -> dict[tuple[int, int], tuple[int, int]]:
    """可见的题 -> 它所在的作业（有多个时取截止时间最晚的）。"""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    contests = sorted(visible_contests(s, user_id, course),
                      key=lambda c: c.end_time or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    for c in contests:
        for pid in contest_problem_ids(s, c.server_id, c.contest_id):
            out[(c.server_id, pid)] = (c.server_id, c.contest_id)
    return out


def problem_contest(s: Session, user_id: str, server_id: int, problem_id: int, course: str | None = None) -> int | None:
    """只查当前题所属的可见作业，保留有效班级、课程和截止时间顺序。"""
    if course is not None and server_course(s, server_id) != course:
        return None
    cp, cc, c, en = m.ContestProblem, m.ContestClass, m.Contest, m.Enrollment
    return s.scalar(select(cp.contest_id).select_from(cp)
                    .join(c, (c.server_id == cp.server_id) & (c.contest_id == cp.contest_id))
                    .join(cc, (cc.server_id == c.server_id) & (cc.contest_id == c.contest_id))
                    .join(en, (en.server_id == cc.server_id) & (en.class_id == cc.class_id))
                    .where(cp.server_id == server_id, cp.problem_id == problem_id,
                           en.user_id == user_id, en.status == "active")
                    .order_by(c.end_time.is_(None).desc(), c.end_time.desc(), c.contest_id.desc()).limit(1))


def is_visible(s: Session, user_id: str, server_id: int, problem_id: int, course: str | None = None) -> bool:
    return problem_contest(s, user_id, server_id, problem_id, course) is not None


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
    return _cells(sub.judgement, runs, order if order is not None else testcase_order(s, sub.server_id, sub.problem_id))


def _cells(result: str, runs: dict[int, bool], order: list[int]) -> list[bool] | None:
    if not runs or result in ("CE", "PENDING"):
        return None
    if result == "AC":
        return [True] * max(len(order), len(runs))
    extra = sorted(runs.keys() - set(order))
    return [runs.get(tid, False) for tid in order] + [runs[tid] for tid in extra]


def submission_view(s: Session, sub: m.Submission, attempt_no: int, order: list[int] | None = None) -> dict:
    """学生端的一条提交。这里出现的字段就是学生能看到的全部。"""
    return _submission_row(sub, attempt_no, grid(s, sub, order))


def _submission_row(sub: m.Submission, attempt_no: int, cells: list[bool] | None) -> dict:
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
    subs = user_submissions(s, user_id, server_id, problem_id)
    if not subs:
        return []
    order = testcase_order(s, server_id, problem_id)
    sub, judge, run = m.Submission, m.Judge, m.JudgeRun
    rows = s.execute(select(judge.submission_id, run.testcase_id, run.passed).select_from(sub)
                     .join(judge, (judge.server_id == sub.server_id) & (judge.submission_id == sub.submission_id))
                     .join(run, (run.server_id == judge.server_id) & (run.judge_id == judge.judge_id))
                     .where(sub.user_id == user_id, sub.server_id == server_id,
                            sub.problem_id == problem_id, judge.is_latest.is_(True)))
    runs: dict[int, dict[int, bool]] = defaultdict(dict)
    for sid, tid, passed in rows:
        runs[sid][tid] = bool(passed)
    return [_submission_row(x, i, _cells(x.judgement, runs[x.submission_id], order))
            for i, x in enumerate(subs, start=1)]
