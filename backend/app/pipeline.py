"""提交的详情取齐之后要做的事：没通过的标错误类型；第一次通过的记学情证据、结束回合、看要不要出小题。

主循环把它当本地任务调用（local_task）；网页端在需要时也会直接调用（比如学生问到一条还没标的提交）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import accounts, config, db, mastery, review, similarity
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
    if sub is None or sub.detail_status != "done" or sub.judgement == "PENDING" \
            or accounts.submission_removed(s, sub.server_id, sub.user_id, sub.class_id, sub.contest_id):
        return
    if sub.judgement != "AC":
        labels = labeler.label_submission(s, server_id, submission_id)
        primary = next((lab for lab in labels if lab.role == "primary" and lab.confidence >= 0.5), None)
        if primary is not None and primary.skill_code:
            # 在 OJ 上交了没过就是证据，不需要他问过问题（v2；同一道题同一个技能分量递减）
            mastery.record_failed_submission(s, sub, primary.skill_code)
            if review.on_recurrence(s, sub, primary.error_code, primary.skill_code) is None:
                review.enqueue(s, sub.user_id, primary.skill_code, "failed", sub.server_id,
                               sub.problem_id, sub.submit_time or db.now(), sub.submission_id)
        elif sub.judgement != "UNKNOWN":
            problem = labeler.current_problem_label(s, server_id, sub.problem_id)
            if problem is not None:
                review.enqueue(s, sub.user_id, problem.primary_skill, "failed", server_id,
                               sub.problem_id, sub.submit_time or db.now(), sub.submission_id)
        from .chat import proactive

        proactive.on_failed_submission(s, sub, now)   # 卡住或重犯：智能体主动先说一句
        return
    earlier = s.scalars(select(m.Submission).where(
        m.Submission.server_id == server_id, m.Submission.user_id == sub.user_id,
        m.Submission.problem_id == sub.problem_id, m.Submission.submission_id < submission_id)).all()
    if any(x.judgement == "AC" for x in earlier):
        return  # 只有第一次通过算证据
    label = labeler.current_problem_label(s, server_id, sub.problem_id)
    if label is None:
        # 还没标的题立刻标：先试大模型，标不了就按 XDOJ 的标签兜底。以前这里没有标注就不记证据，
        # 做对的题全都白做，只剩没过的那几次被记下来，几乎人人“待加强”
        label = problems.ensure_label(s, server_id, sub.problem_id)
    fp = similarity.check_pass(s, sub)
    reliability = config.MASTERY_V2["copy_reliability"] if fp is not None and fp.suspicious else 1.0
    if label is not None:
        failures = sum(x.judgement not in ("AC", "PENDING", "UNKNOWN", "") for x in earlier)
        mastery.record_pass(s, sub.user_id, label, f"pass:{server_id}:{submission_id}", sub.submit_time or db.now(),
                            failures, help_before(s, sub), reliability)
        review.on_independent_practice(s, sub, label, failures, help_before(s, sub), reliability)
    from .chat import summary, workflow

    summary.on_pass(s, sub, now)
    workflow.after_pass(s, sub, now)


def local_task(server_id: int, kind: str, target_id: int) -> None:
    """主循环里的本地任务。"""
    with db.session_scope() as s:
        if kind == "post_process":
            process_submission(s, server_id, target_id)
        elif kind == "label_problem":
            problems.annotate_problem(s, server_id, target_id)


def backfill_passes(s: Session, limit: int = 100, *, existing_labels_only: bool = False) -> int:
    """以前因为题目还没有标注、没记上证据的“第一次通过”，补记上（后台空闲时自动做）。返回补了几条。

    只补掌握程度的证据，不再发对话里的总结、不出小题（那是当时的事）。
    """
    from sqlalchemy import func

    recorded = set(s.scalars(select(m.SkillEvidence.source_key).where(
        m.SkillEvidence.event_type.like("pass%"))).all())
    firsts = s.execute(select(m.Submission.server_id, m.Submission.user_id, m.Submission.problem_id,
                              func.min(m.Submission.submission_id))
                       .where(m.Submission.judgement == "AC", m.Submission.detail_status == "done")
                       .group_by(m.Submission.server_id, m.Submission.user_id, m.Submission.problem_id)).all()
    done = 0
    for server_id, user_id, problem_id, submission_id in firsts:
        if done >= limit:
            break
        if f"pass:{server_id}:{submission_id}" in recorded:
            continue
        sub = s.get(m.Submission, (server_id, submission_id))
        label = labeler.current_problem_label(s, server_id, problem_id)
        if label is None and not existing_labels_only:
            label = problems.tag_label(s, server_id, problem_id)
        if sub is None or label is None:
            continue
        if accounts.submission_removed(s, server_id, user_id, sub.class_id, sub.contest_id):
            continue
        earlier = s.scalar(select(func.count()).select_from(m.Submission).where(
            m.Submission.server_id == server_id, m.Submission.user_id == user_id,
            m.Submission.problem_id == problem_id, m.Submission.submission_id < submission_id,
            m.Submission.judgement.not_in(("AC", "PENDING", "UNKNOWN", "")))) or 0
        fp = similarity.check_pass(s, sub)
        reliability = config.MASTERY_V2["copy_reliability"] if fp is not None and fp.suspicious else 1.0
        mastery.record_pass(s, user_id, label, f"pass:{server_id}:{submission_id}", sub.submit_time or db.now(),
                            earlier, help_before(s, sub), reliability)
        done += 1
    if done:
        log.info("补记了 %d 次以前没记上的通过", done)
    return done


def repair_mastery_evidence(s: Session, limit: int = 200) -> int:
    """只用已取齐的提交和已有标注补记证据，不生成回复、不调模型，也不重复计分。"""
    done = backfill_passes(s, limit=limit, existing_labels_only=True)
    if done >= limit:
        return done
    from sqlalchemy import String, cast, exists, func, literal

    sub, label, ev = m.Submission, m.SubmissionLabel, m.SkillEvidence
    key = literal("fail:") + cast(sub.server_id, String) + ":" + cast(sub.problem_id, String) + ":" + cast(sub.submission_id, String)
    recorded = exists(select(ev.id).where(ev.user_id == sub.user_id, ev.source_key == key,
                                         ev.skill_code == label.skill_code))
    episode = literal("fail:") + cast(sub.server_id, String) + ":" + cast(sub.problem_id, String) + ":%"
    count = select(func.count(ev.id)).where(ev.user_id == sub.user_id,
        ev.skill_code == label.skill_code, ev.event_type == "submit_failed",
        ev.source_key.like(episode)).correlate(sub, label).scalar_subquery()
    rows = s.execute(select(sub, label.skill_code).join(label,
        (label.server_id == sub.server_id) & (label.submission_id == sub.submission_id)).where(
        sub.detail_status == "done", sub.judgement.not_in(("AC", "PENDING", "UNKNOWN", "")),
        label.is_current.is_(True), label.role == "primary", label.confidence >= 0.5,
        label.skill_code.is_not(None), label.skill_code != "", ~recorded,
        count < len(config.MASTERY["w_submit_failed"])
    ).order_by(sub.submission_id).limit(limit - done)).all()
    for submission, skill in rows:
        if not accounts.submission_removed(s, submission.server_id, submission.user_id,
                                           submission.class_id, submission.contest_id):
            done += int(mastery.record_failed_submission(s, submission, skill))
    return done
