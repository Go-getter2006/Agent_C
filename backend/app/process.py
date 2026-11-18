"""做题过程：只看 OJ 上的提交记录，不需要学生问过任何问题。

一道题上从第一次提交到第一次通过（或者到现在还没通过）是一段“过程”。一次就过的不算改错的过程；
其余的看他是怎么改对的：交了几次、同一个错误是不是反复出现、是不是隔不到一分钟就重交（多半是在碰运气）。
依据：Qu 等（2019）发现快速提交和成绩明显负相关；改错的效率比“第一次就通过”更能说明会不会调试。

用在：多维画像的“改错”维度（profile.py）、智能体看代码评理解（assess.py）、教师端的“改错过程”。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from . import models as m
from .labeling.labeler import current_problem_label

RAPID_SECONDS = 60       # 两次没通过的提交隔不到这么久：算“急着重交”
STUCK_FAILS = 3          # 交了这么多次还没过，算卡住
STALE_DAYS = 2           # 没过、也有两天没再交了：算放下了


@dataclass
class Episode:
    server_id: int
    problem_id: int
    subs: list[m.Submission]
    passed: m.Submission | None
    errors: list[str] = field(default_factory=list)        # 每次没通过的主错误（没标出来的是空串）
    error_skills: list[str] = field(default_factory=list)  # 那些错误所属的技能
    problem_skills: list[str] = field(default_factory=list)
    now: dt.datetime | None = None

    @property
    def failed(self) -> list[m.Submission]:
        return [x for x in self.subs if x.judgement != "AC"]

    @property
    def same_repeats(self) -> int:
        """同一个错误紧接着又出现了几次。"""
        return sum(1 for a, b in zip(self.errors, self.errors[1:]) if a and a == b)

    @property
    def rapid(self) -> int:
        """没通过以后不到一分钟就重交的次数。"""
        times = [x.submit_time for x in self.subs if x.submit_time is not None]
        return sum(1 for a, b in zip(times, times[1:]) if (b - a).total_seconds() < RAPID_SECONDS)

    @property
    def state(self) -> str:
        """first_try 一次就过；fixed 改对了；stuck 卡住了（交了好几次没过，或放下了）；open 还在改。"""
        if self.passed is not None:
            return "first_try" if not self.failed else "fixed"
        last = self.subs[-1].submit_time if self.subs else None
        stale = last is not None and (self.now or db.now()) - last > dt.timedelta(days=STALE_DAYS)
        return "stuck" if len(self.failed) >= STUCK_FAILS or stale else "open"

    @property
    def score(self) -> float | None:
        """改错的效率，0~1。一次就过、还在改的没有分数。"""
        st = self.state
        if st == "fixed":
            return max(0.1, min(1.0, 1 - 0.12 * (len(self.failed) - 1) - 0.15 * self.same_repeats - 0.1 * self.rapid))
        if st == "stuck":
            return 0.1 if len(self.failed) >= STUCK_FAILS else 0.25
        return None

    @property
    def skills(self) -> list[str]:
        return list(dict.fromkeys(self.problem_skills + [c for c in self.error_skills if c]))


def episode_for(s: Session, user_id: str, server_id: int, problem_id: int,
                at: dt.datetime | None = None) -> Episode | None:
    return next((e for e in episodes(s, user_id, at, {server_id}) if e.problem_id == problem_id), None)


def episodes(s: Session, user_id: str, at: dt.datetime | None = None,
             server_ids: set[int] | None = None) -> list[Episode]:
    at = at or db.now()
    q = select(m.Submission).where(m.Submission.user_id == user_id, m.Submission.judgement != "PENDING")
    subs = [x for x in s.scalars(q.order_by(m.Submission.submission_id)).all()
            if (x.submit_time is None or x.submit_time <= at) and (server_ids is None or x.server_id in server_ids)]
    groups: dict[tuple[int, int], list[m.Submission]] = {}
    for x in subs:
        groups.setdefault((x.server_id, x.problem_id), []).append(x)
    failed_keys = [(x.server_id, x.submission_id) for x in subs if x.judgement != "AC"]
    labels: dict[tuple[int, int], m.SubmissionLabel] = {}
    if failed_keys:
        for lab in s.scalars(select(m.SubmissionLabel).where(
                m.SubmissionLabel.submission_id.in_([k[1] for k in failed_keys]),
                m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.role == "primary")).all():
            if lab.confidence >= 0.5:
                labels[(lab.server_id, lab.submission_id)] = lab
    out = []
    for (server_id, problem_id), rows in groups.items():
        upto: list[m.Submission] = []
        passed = None
        for x in rows:
            upto.append(x)
            if x.judgement == "AC":
                passed = x
                break
        label = current_problem_label(s, server_id, problem_id)
        ep = Episode(server_id, problem_id, upto, passed, now=at,
                     problem_skills=([label.primary_skill, *(label.secondary_skills or [])] if label else []))
        for x in upto:
            if x.judgement != "AC":
                lab = labels.get((x.server_id, x.submission_id))
                ep.errors.append(lab.error_code if lab else "")
                ep.error_skills.append(lab.skill_code if lab else "")
        out.append(ep)
    return out
