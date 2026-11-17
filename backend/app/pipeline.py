"""提交的详情取齐之后要做的事：没通过的标错误类型；第一次通过的记学情证据、结束回合、看要不要出小题。

主循环把它当本地任务调用（local_task）；网页端在需要时也会直接调用（比如学生问到一条还没标的提交）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db, mastery, review, similarity
from . import models as m
from .labeling import labeler, problems
from .taxonomy import HELP_ORDER

log = logging.getLogger("pipeline")


def help_before(s: Session, sub: m.Submission) -> str:
    """通过之前，他在这道题上得到过的最高帮助级别。"""
    rounds = s.scalars(select(m.ChatRound).where(
        m.ChatRound.user_id == sub.user_id, m.ChatRound.server_id == sub.server_id,
        m.ChatRound.problem_id == sub.problem_id)).all()
    best = "NA"
    for r in rounds:
        if sub.submit_time is not None and r.started_at > sub.submit_time:
            continue
        if HELP_ORDER.get(r.max_help_level, -1) > HELP_ORDER.get(best, -1):
            best = r.max_help_level
    return best


def process_submission(s: Session, server_id: int, submission_id: int, now=None) -> None:
    sub = s.get(m.Submission, (server_id, submission_id))
    if sub is None or sub.detail_status != "done" or sub.judgement == "PENDING":
        return
    if sub.judgement != "AC":
        labels = labeler.label_submission(s, server_id, submission_id)
        primary = next((lab for lab in labels if lab.role == "primary" and lab.confidence >= 0.5), None)
        if primary is not None and primary.skill_code:
            review.on_recurrence(s, sub, primary.error_code, primary.skill_code)   # 改正后又出现
        return
    earlier = s.scalars(select(m.Submission).where(
        m.Submission.server_id == server_id, m.Submission.user_id == sub.user_id,
        m.Submission.problem_id == sub.problem_id, m.Submission.submission_id < submission_id)).all()
    if any(x.judgement == "AC" for x in earlier):
        return  # 只有第一次通过算证据
    label = labeler.current_problem_label(s, server_id, sub.problem_id)
    if label is None:
        label = problems.annotate_problem(s, server_id, sub.problem_id)  # 兜底：还没标的题立刻标
    fp = similarity.check_pass(s, sub)
    reliability = config.MASTERY_V2["copy_reliability"] if fp is not None and fp.suspicious else 1.0
    if label is not None:
        mastery.record_pass(s, sub.user_id, label, f"pass:{server_id}:{submission_id}", sub.submit_time or db.now(),
                            len(earlier), help_before(s, sub), reliability)
        review.on_independent_practice(s, sub, label, len(earlier), help_before(s, sub), reliability)
    from .chat import workflow

    workflow.after_pass(s, sub, now)


def local_task(server_id: int, kind: str, target_id: int) -> None:
    """主循环里的本地任务。"""
    with db.session_scope() as s:
        if kind == "post_process":
            process_submission(s, server_id, target_id)
        elif kind == "label_problem":
            problems.annotate_problem(s, server_id, target_id)
