"""复习（回访）。

原来的做法是卡住后固定 3 天出一道回访题，放在“其他问题”里。几个问题：
- 间隔是死的，不管他记得牢不牢；
- 他已经独立做对了用到这个技能的新题，还照样出题，白白占用时间；
- 没有“毕业”：什么时候算真的会了、不用再复习，没有说法；
- 题目脱离上下文，学生不知道为什么要做。

现在的做法。依据：遗忘曲线与间隔效应（Cepeda 等 2006）；提取练习（Roediger 和 Karpicke 2006）；
Leitner 盒子；半衰期回归（Settles 和 Meeder 2016）。

1. 什么时候进来：卡住（第一次卡住就进）；同一种错误改正后又出现；小题答错；讲了原理或给了代码才通过；
   小题选“没把握”却答对（可能是蒙的）。
2. 每个复习项有一个记忆半衰期 h（天）。预测的记忆保持 P = 2^(−Δ/h)，Δ 是距上一次成功回忆的天数。
   P 降到 target_retention 时到期：due = 上次 + h·log2(1/target)。
3. 复习做对：h 乘一个系数（很有把握 2.5，一半 2.0，没把握 1.5；第二次才改对 1.3）；
   做错：h 减半（不低于下限），记一次遗忘；同一天里连着做对不算间隔，不加长 h。
4. 独立做对一道用到这个技能的新题，也算一次成功的回忆：不用再专门出题，直接把下一次往后排。
   讲了原理或给了代码才做出来的，算一次遗忘。
5. 毕业：连续做对 graduate_streak 次，而且 h 已经超过 graduate_half_life 天。毕业后再出问题会重新进来。
6. 不打扰：不弹窗，不往正在聊的对话里插题。导航上显示到期的个数；复习页一次一道；
   每天最多 daily_limit 道，同一个技能一天最多一道；到期的里面先做记得最不牢的。
7. 出什么题：第一次复习用他自己当时那道题的代码回忆；之后换一个场景（迁移）；刚答错过就降一级，出判断题。
"""
from __future__ import annotations

import datetime as dt
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, config, db
from . import models as m
from .taxonomy import KNOWLEDGE_POINTS, kp_of_skill, skill_name

ACTIVE = "active"
GRADUATED = "graduated"


def cfg() -> dict:
    return config.REVIEW


def retention(item: m.ReviewItem, at: dt.datetime | None = None) -> float:
    at = at or db.now()
    since = item.last_success_at or item.created_at
    days = max(0.0, (at - since).total_seconds() / 86400.0)
    return 2 ** (-days / max(item.half_life, 0.1))


def _schedule(item: m.ReviewItem) -> None:
    base = item.last_success_at or item.updated_at or item.created_at
    gap = item.half_life * math.log2(1 / cfg()["target_retention"])
    item.due_at = base + dt.timedelta(days=max(cfg()["min_gap_days"], gap))


def enqueue(s: Session, user_id: str, skill: str, origin: str, server_id: int | None = None,
            problem_id: int | None = None, at: dt.datetime | None = None) -> m.ReviewItem | None:
    """加进复习。已经在复习的，算一次遗忘；毕业了的，重新开始。"""
    if not skill:
        return None
    at = at or db.now()
    item = s.scalar(select(m.ReviewItem).where(m.ReviewItem.user_id == user_id, m.ReviewItem.skill_code == skill)
                    .order_by(m.ReviewItem.id.desc()))
    start_h = cfg()["initial_half_life"].get(origin, cfg()["initial_half_life"]["stuck"])
    if item is None:
        item = m.ReviewItem(user_id=user_id, skill_code=skill, origin=origin, origin_server_id=server_id,
                            origin_problem_id=problem_id, half_life=start_h, last_success_at=at,
                            created_at=at, updated_at=at, state=ACTIVE)
        s.add(item)
    else:
        same_episode = (item.state == ACTIVE and problem_id is not None and item.origin_problem_id == problem_id
                        and (at - item.updated_at).total_seconds() < cfg()["spacing_hours"] * 3600)
        if same_episode:
            return item   # 同一道题、同一段时间里的又一次（比如卡住后靠示例代码通过）：不重复算遗忘
        if item.state != ACTIVE:
            item.state, item.reps, item.streak = ACTIVE, 0, 0
        item.lapses += 1
        item.streak = 0
        item.half_life = min(item.half_life, start_h)
        item.last_success_at = at
        item.last_outcome = "lapse"
        item.origin = origin
        if problem_id is not None:
            item.origin_server_id, item.origin_problem_id = server_id, problem_id
        item.updated_at = at
    _schedule(item)
    s.flush()
    return item


def _success(item: m.ReviewItem, factor: float, at: dt.datetime, outcome: str) -> None:
    spaced = item.last_success_at is None or (at - item.last_success_at).total_seconds() >= cfg()["spacing_hours"] * 3600
    if spaced:
        item.half_life = min(cfg()["max_half_life"], item.half_life * factor)
        item.streak += 1
        item.last_success_at = at
    item.reps += 1
    item.last_outcome = outcome
    item.updated_at = at
    if item.streak >= cfg()["graduate_streak"] and item.half_life >= cfg()["graduate_half_life"]:
        item.state = GRADUATED
    _schedule(item)


def _failure(item: m.ReviewItem, at: dt.datetime) -> None:
    item.half_life = max(cfg()["min_half_life"], item.half_life * 0.5)
    item.lapses += 1
    item.streak = 0
    item.reps += 1
    item.last_outcome = "wrong"
    item.last_success_at = at
    item.updated_at = at
    _schedule(item)


def on_quiz_result(s: Session, attempt: m.QuizAttempt) -> None:
    """小题做完之后。复习题：更新半衰期；不是复习题：答错了、或没把握却答对，就加进复习。"""
    at = attempt.finished_at or db.now()
    item = s.get(m.ReviewItem, attempt.review_item_id) if attempt.review_item_id else None
    if item is not None:
        item.attempt_id = None
        if attempt.outcome == "skipped":
            item.updated_at = at   # 跳过：不算对也不算错，明天再说
            item.due_at = at + dt.timedelta(days=1)
            return
        if attempt.correct:
            factor = cfg()["growth"].get(attempt.confidence or "一半", 2.0)
            if attempt.outcome == "right_second":
                factor = cfg()["growth"]["second_try"]
            _success(item, factor, at, "right")
        else:
            _failure(item, at)
        return
    if attempt.outcome in ("wrong_judge", "wrong_fix"):
        enqueue(s, attempt.user_id, attempt.skill_code, "quiz_wrong", at=at)
    elif attempt.correct and attempt.confidence == "没把握":
        enqueue(s, attempt.user_id, attempt.skill_code, "lucky", at=at)


def on_independent_practice(s: Session, sub: m.Submission, label: m.ProblemLabel, earlier_attempts: int,
                            help_level: str, reliability: float) -> None:
    """第一次通过一道题。它用到的技能如果正在复习：独立做对算一次成功的回忆；靠讲解或示例代码才做出来算一次遗忘。"""
    at = sub.submit_time or db.now()
    skills = [label.primary_skill, *(label.secondary_skills or [])]
    if help_level in ("H2", "H3"):
        enqueue(s, sub.user_id, label.primary_skill, "heavy_help", sub.server_id, sub.problem_id, at)
        return
    if reliability < 1.0:
        return   # 雷同的通过不当作他自己记住了
    for code in skills:
        item = s.scalar(select(m.ReviewItem).where(
            m.ReviewItem.user_id == sub.user_id, m.ReviewItem.skill_code == code, m.ReviewItem.state == ACTIVE))
        if item is None or (item.origin_problem_id == sub.problem_id and item.origin_server_id == sub.server_id):
            continue   # 卡住的那道题自己改过，不算“隔了一段时间还记得”
        factor = cfg()["growth"]["practice_first_try" if earlier_attempts == 0 else "practice_fixed"]
        if code != label.primary_skill:
            factor = 1 + (factor - 1) / 2
        _success(item, factor, at, "practice")


def on_recurrence(s: Session, sub: m.Submission, error_code: str, skill: str) -> m.ReviewItem | None:
    """同一种错误以前改正过、这次又出现了。"""
    at = sub.submit_time or db.now()
    earlier = s.execute(
        select(m.Submission.server_id, m.Submission.problem_id, m.Submission.submission_id)
        .join(m.SubmissionLabel, (m.SubmissionLabel.server_id == m.Submission.server_id)
              & (m.SubmissionLabel.submission_id == m.Submission.submission_id))
        .where(m.Submission.user_id == sub.user_id, m.SubmissionLabel.error_code == error_code,
               m.SubmissionLabel.is_current.is_(True), m.Submission.submission_id < sub.submission_id)).all()
    for server_id, problem_id, sid in earlier:
        fixed = s.scalar(select(m.Submission.submission_id).where(
            m.Submission.server_id == server_id, m.Submission.user_id == sub.user_id,
            m.Submission.problem_id == problem_id, m.Submission.judgement == "AC",
            m.Submission.submission_id > sid, m.Submission.submission_id < sub.submission_id))
        if fixed is not None:
            return enqueue(s, sub.user_id, skill, "recurrence", sub.server_id, sub.problem_id, at)
    return None


# ------------------------------------------------------------ 学生看到的
def _today_count(s: Session, user_id: str, at: dt.datetime) -> tuple[int, set[str]]:
    local = at.astimezone(db.BEIJING)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(db.UTC)
    rows = s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.reason == "review", m.QuizAttempt.created_at >= start)).all()
    return len(rows), {r.skill_code for r in rows}


def due_items(s: Session, user_id: str, at: dt.datetime | None = None) -> list[m.ReviewItem]:
    """今天可以做的：到期的、今天还没做过这个技能的，记得最不牢的在前，最多 daily_limit 个。"""
    at = at or db.now()
    done, skills_today = _today_count(s, user_id, at)
    room = max(0, cfg()["daily_limit"] - done)
    rows = s.scalars(select(m.ReviewItem).where(
        m.ReviewItem.user_id == user_id, m.ReviewItem.state == ACTIVE, m.ReviewItem.due_at <= at)).all()
    rows = [r for r in rows if r.skill_code not in skills_today or r.attempt_id is not None]
    rows.sort(key=lambda r: (r.attempt_id is None, retention(r, at)))
    pending = [r for r in rows if r.attempt_id is not None]
    return (pending + [r for r in rows if r.attempt_id is None])[:max(room, len(pending))]


def _origin_ref(s: Session, user_id: str, item: m.ReviewItem) -> dict | None:
    if item.origin_problem_id is None or not catalog.is_visible(s, user_id, item.origin_server_id, item.origin_problem_id):
        return None
    info = catalog.problem_info(s, item.origin_server_id, item.origin_problem_id)
    return {"key": info.key, "title": info.title, "brief": info.brief} if info else None


ORIGIN_TEXT = {
    "stuck": "卡住过", "recurrence": "改正后又出现了同样的错误", "quiz_wrong": "小题没做对",
    "heavy_help": "讲了原理或给了代码才做出来", "lucky": "小题没把握但答对了",
}


def item_view(s: Session, user_id: str, item: m.ReviewItem, at: dt.datetime) -> dict:
    """学生端看到的一个复习项：知识点名称（不给技能编码）、为什么要复习、记得多牢、下次什么时候。"""
    kp = KNOWLEDGE_POINTS.get(kp_of_skill(item.skill_code), "")
    return {
        "id": item.id, "title": kp, "detail": skill_name(item.skill_code),
        "why": ORIGIN_TEXT.get(item.origin, ""), "origin": _origin_ref(s, user_id, item),
        "retention": round(retention(item, at), 2), "due": item.due_at.isoformat(), "dueNow": item.due_at <= at,
        "reps": item.reps, "streak": item.streak, "toGraduate": max(0, cfg()["graduate_streak"] - item.streak),
        "state": item.state, "pending": item.attempt_id is not None,
    }


def summary(s: Session, user_id: str, at: dt.datetime | None = None) -> dict:
    at = at or db.now()
    due = due_items(s, user_id, at)
    due_ids = {d.id for d in due}
    rows = s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == user_id)).all()
    upcoming = sorted([r for r in rows if r.state == ACTIVE and r.id not in due_ids], key=lambda r: r.due_at)
    done_today, _ = _today_count(s, user_id, at)
    return {
        "due": [item_view(s, user_id, r, at) for r in due],
        "upcoming": [item_view(s, user_id, r, at) for r in upcoming[:12]],
        "graduated": [item_view(s, user_id, r, at) for r in rows if r.state == GRADUATED][:12],
        "doneToday": done_today, "dailyLimit": cfg()["daily_limit"],
        "active": sum(1 for r in rows if r.state == ACTIVE),
    }


def start(s: Session, user_id: str, item_id: int, at: dt.datetime | None = None):
    """开始复习一项：用出题器给他出一道题。返回 (复习项, 小题作答)；出不了题返回 (复习项, None)。"""
    from . import quiz, quizgen

    at = at or db.now()
    item = s.get(m.ReviewItem, item_id)
    if item is None or item.user_id != user_id or item.state != ACTIVE:
        return None, None
    if item.attempt_id is not None:
        attempt = s.get(m.QuizAttempt, item.attempt_id)
        if attempt is not None and attempt.state != "done":
            return item, attempt
    if item.id not in {d.id for d in due_items(s, user_id, at)}:
        return item, None
    q = quizgen.generate(s, user_id, item.skill_code, "review",
                         origin=(item.origin_server_id, item.origin_problem_id) if item.origin_problem_id else None,
                         review_item=item)
    if q is None:
        return item, None
    attempt = quiz.new_attempt(s, user_id, q, "review", at, review_item_id=item.id)
    item.attempt_id = attempt.id
    return item, attempt


def lead_for(s: Session, user_id: str, item: m.ReviewItem, q: m.QuizItem) -> str:
    ref = _origin_ref(s, user_id, item)
    if q.source == "own_code" and ref and q.source_problem_id == item.origin_problem_id:
        return f"前几天你在「{ref['title']}」（{ref['brief']}）里{ORIGIN_TEXT.get(item.origin, '卡过')}。用你自己当时的代码回忆一下："
    if item.reps == 0:
        return "前几天这里卡过，回忆一下："
    return "换个场景，看看还记不记得："


def teacher_stats(s: Session, user_ids: list[str], at: dt.datetime | None = None) -> dict:
    at = at or db.now()
    rows = s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id.in_(user_ids))).all() if user_ids else []
    active = [r for r in rows if r.state == ACTIVE]
    return {"active": len(active), "due": sum(1 for r in active if r.due_at <= at),
            "graduated": sum(1 for r in rows if r.state == GRADUATED), "lapses": sum(r.lapses for r in rows),
            "students": len({r.user_id for r in active})}
