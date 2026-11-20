"""复习（回访）。

原来的做法是卡住后固定 3 天出一道回访题，放在“其他问题”里。几个问题：
- 间隔是死的，不管他记得牢不牢；
- 他已经独立做对了用到这个技能的新题，还照样出题，白白占用时间；
- 没有“毕业”：什么时候算真的会了、不用再复习，没有说法；
- 题目脱离上下文，学生不知道为什么要做。

现在的做法。依据：遗忘曲线与间隔效应（Cepeda 等 2006）；提取练习（Roediger 和 Karpicke 2006）；
Leitner 盒子；半衰期回归（Settles 和 Meeder 2016）。

1. 什么时候进来：提交没通过、后来改对、卡住或同一种错误再现、小题答错或靠提示通过；
   独立通过的题偶尔低频抽查，优先级低于错误和不确定的内容。
2. 每个复习项有一个记忆半衰期 h（天）。预测的记忆保持 P = 2^(−Δ/h)，Δ 是距上一次成功回忆的天数。
   P 降到 target_retention 时到期：due = 上次 + h·log2(1/target)。
3. 复习做对：h 乘一个系数（很有把握 2.5，一半 2.0，没把握 1.5；第二次才改对 1.3）；
   做错：h 减半（不低于下限），记一次遗忘；同一天里连着做对不算间隔，不加长 h。
4. 独立做对一道用到这个技能的新题，也算一次成功的回忆：不用再专门出题，直接把下一次往后排。
   讲了原理或给了代码才做出来的，算一次遗忘。
5. 毕业：连续做对 graduate_streak 次，而且 h 已经超过 graduate_half_life 天。毕业后再出问题会重新进来。
6. 不打扰：不弹窗，不往对话里插题。小题在“复习提升”里做（界面改造方案第 4 节）：后台空闲时给最近来过的学生
   提前备好一道（prepare_active），没备好时后台退避重试；
   侧边栏的数字只数已经备好的题（ready_count）。中途离开的，回来接着做同一道。
   每天最多 daily_limit 道，同一个技能一天最多一道；到期后按掌握证据、反复修改和独立性确认安排。
7. 出什么题：正式检查使用同一技能的新场景；刚答错过就降一级，出判断题。
8. 两门课分开：复习项按技能编码属于哪门课，C 语言登录只看 C 语言的，数据结构只看数据结构的；每天的上限也各算各的。
"""
from __future__ import annotations

import datetime as dt
import math

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import catalog, config, db, mastery
from . import models as m
from .taxonomy import LEVEL_WEAK, KNOWLEDGE_POINTS, course_of, kp_of_skill, skill_name

ACTIVE = "active"
GRADUATED = "graduated"


def cfg() -> dict:
    return config.REVIEW


def _in_course(skill_code: str, course: str | None) -> bool:
    return course is None or (bool(skill_code) and course_of(skill_code) == course)


def retention(item: m.ReviewItem, at: dt.datetime | None = None) -> float:
    at = at or db.now()
    since = item.last_success_at or item.created_at
    days = max(0.0, (at - since).total_seconds() / 86400.0)
    return 2 ** (-days / max(item.half_life, 0.1))


def available_at(item: m.ReviewItem) -> dt.datetime:
    # 旧版本可能将刚通过的题排到当天；已开始的小题仍可继续。
    evidence_at = item.trigger_at or item.created_at
    return max(item.due_at, evidence_at + dt.timedelta(days=cfg()["min_gap_days"]))


def memory(item: m.ReviewItem, at: dt.datetime | None = None) -> dict:
    """显示复习状态。估计的记忆保持率只用于内部安排间隔。"""
    if item.state == GRADUATED:
        return {"text": "已巩固", "segments": 3}
    if item.attempt_id is not None or available_at(item) <= (at or db.now()):
        return {"text": "建议复习", "segments": 1}
    return {"text": "已安排复习", "segments": 2}


def _schedule(item: m.ReviewItem) -> None:
    base = item.last_success_at or item.updated_at or item.created_at
    gap = item.half_life * math.log2(1 / cfg()["target_retention"])
    item.due_at = base + dt.timedelta(days=max(cfg()["min_gap_days"], gap))


def enqueue(s: Session, user_id: str, skill: str, origin: str, server_id: int | None = None,
            problem_id: int | None = None, at: dt.datetime | None = None,
            submission_id: int | None = None) -> m.ReviewItem | None:
    """加进复习。已经在复习的，算一次遗忘；毕业了的，重新开始。"""
    if not skill:
        return None
    at = at or db.now()
    item = s.scalar(select(m.ReviewItem).where(m.ReviewItem.user_id == user_id, m.ReviewItem.skill_code == skill)
                    .order_by(m.ReviewItem.id.desc()))
    if submission_id is not None and item is not None and item.trigger_at is not None:
        if at < item.trigger_at or (item.origin_server_id == server_id and
                                    item.trigger_submission_id == submission_id):
            return item

    def record_trigger():
        if submission_id is not None:
            item.trigger_submission_id, item.trigger_at = submission_id, at
            item.origin_server_id, item.origin_problem_id, item.origin = server_id, problem_id, origin

    start_h = cfg()["initial_half_life"].get(origin, cfg()["initial_half_life"]["stuck"])
    if item is None:
        item = m.ReviewItem(user_id=user_id, skill_code=skill, origin=origin, origin_server_id=server_id,
                            origin_problem_id=problem_id, half_life=start_h, last_success_at=at,
                            created_at=at, updated_at=at, state=ACTIVE)
        s.add(item)
    else:
        same_episode = (item.state == ACTIVE and item.origin != "spot_check" and problem_id is not None and item.origin_problem_id == problem_id
                        and item.origin_server_id == server_id
                        and (at - item.updated_at).total_seconds() < cfg()["spacing_hours"] * 3600)
        if same_episode:
            if origin == "corrected" and item.origin == "failed":
                item.origin = "corrected"
            record_trigger()
            return item   # 同一道题、同一段时间里的又一次（比如卡住后靠示例代码通过）：不重复算遗忘
        if item.state != ACTIVE:
            item.state, item.reps, item.streak = ACTIVE, 0, 0
        item.lapses += 1
        item.streak = 0
        item.half_life = min(item.half_life, start_h)
        item.last_success_at = at
        item.last_outcome = "lapse"
        if submission_id is None:
            item.trigger_submission_id, item.trigger_at = None, None
        item.origin = origin
        if problem_id is not None:
            item.origin_server_id, item.origin_problem_id = server_id, problem_id
        item.updated_at = at
    _schedule(item)
    record_trigger()
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
    if reliability < 1.0:
        enqueue(s, sub.user_id, label.primary_skill, "verify", sub.server_id, sub.problem_id, at, sub.submission_id)
        return
    if earlier_attempts >= 2:
        history = mastery.attempt_history(s, sub.user_id, at).get((sub.server_id, sub.problem_id), [])
        errors = {}
        for sid, verdict, skill in history:
            if sid < sub.submission_id and verdict not in ("AC", "PENDING", "UNKNOWN", "") and skill:
                errors[skill] = errors.get(skill, 0) + 1
        repeated = [skill for skill, count in errors.items() if count >= 2] or [label.primary_skill]
        for skill in repeated:
            enqueue(s, sub.user_id, skill, "repeated_fix", sub.server_id, sub.problem_id, at, sub.submission_id)
    elif help_level in ("H2", "H3"):
        enqueue(s, sub.user_id, label.primary_skill, "heavy_help", sub.server_id, sub.problem_id, at, sub.submission_id)
    elif earlier_attempts:
        enqueue(s, sub.user_id, label.primary_skill, "corrected", sub.server_id, sub.problem_id, at, sub.submission_id)
    elif cfg().get("spot_check_every", 10) > 0:
        # 偶尔抽查独立做对的题；既有薄弱项不被抽查重置，也不占据高优先级。
        passed_problems = select(m.Submission.server_id, m.Submission.problem_id).join(
            m.Server, m.Server.id == m.Submission.server_id).where(
                m.Submission.user_id == sub.user_id, m.Submission.judgement == "AC", m.Submission.submit_time <= at,
                m.Server.course == catalog.server_course(s, sub.server_id)).group_by(m.Submission.server_id, m.Submission.problem_id)
        n = s.scalar(select(func.count()).select_from(passed_problems.subquery())) or 0
        exists = s.scalar(select(m.ReviewItem.id).where(m.ReviewItem.user_id == sub.user_id,
                                                       m.ReviewItem.skill_code == label.primary_skill))
        if n and n % cfg().get("spot_check_every", 10) == 0 and exists is None:
            enqueue(s, sub.user_id, label.primary_skill, "spot_check", sub.server_id, sub.problem_id, at, sub.submission_id)
    if help_level not in ("NA", "") or earlier_attempts >= 2:
        return
    # 只是顺带用到的次技能不算“隔了一段时间还记得”（mastery.problem_skills）
    skills = [label.primary_skill, *sorted(mastery.tested_secondary(s, label))]
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
    course = catalog.server_course(s, sub.server_id)
    for server_id, problem_id, sid in earlier:
        if catalog.server_course(s, server_id) != course:
            continue   # 只和同一门课里的比
        fixed = s.scalar(select(m.Submission.submission_id).where(
            m.Submission.server_id == server_id, m.Submission.user_id == sub.user_id,
            m.Submission.problem_id == problem_id, m.Submission.judgement == "AC",
            m.Submission.submission_id > sid, m.Submission.submission_id < sub.submission_id))
        if fixed is not None:
            return enqueue(s, sub.user_id, skill, "recurrence", sub.server_id, sub.problem_id, at, sub.submission_id)
    return None


# ------------------------------------------------------------ 学生看到的
def _today_count(s: Session, user_id: str, at: dt.datetime, course: str | None = None) -> tuple[int, set[str]]:
    local = at.astimezone(db.BEIJING)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(db.UTC)
    rows = s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.reason == "review", m.QuizAttempt.created_at >= start,
        m.QuizAttempt.created_at < start + dt.timedelta(days=1))).all()
    rows = [r for r in rows if _in_course(r.skill_code, course)]
    return len(rows), {r.skill_code for r in rows}


def _weak(st: mastery.SkillState | None) -> bool:
    return st is not None and st.events > 0 and (st.level == LEVEL_WEAK or st.score < config.MASTERY_V2["weak_below"])


def due_items(s: Session, user_id: str, at: dt.datetime | None = None, course: str | None = None,
              *, candidates: bool = False) -> list[m.ReviewItem]:
    """今天可以做的：到期的、今天还没做过这个技能的，按掌握证据和复习原因排优先级，最多 daily_limit 个。"""
    at = at or db.now()
    done, skills_today = _today_count(s, user_id, at, course)
    room = max(0, cfg()["daily_limit"] - done)
    rows = s.scalars(select(m.ReviewItem).where(
        m.ReviewItem.user_id == user_id, m.ReviewItem.state == ACTIVE,
        or_(m.ReviewItem.due_at <= at, m.ReviewItem.attempt_id.is_not(None)))).all()
    rows = [r for r in rows if _in_course(r.skill_code, course)
            and (r.attempt_id is not None or (r.skill_code not in skills_today and available_at(r) <= at))]
    states = mastery.skill_states(s, user_id, at=at, model="v2") if any(r.attempt_id is None for r in rows) else {}

    def priority(r):
        st = states.get(r.skill_code)
        weak = _weak(st)
        repeat = r.origin in ("recurrence", "repeated_fix")
        verify = r.origin == "verify"
        importance = 3 * weak + 2 * repeat + verify
        return (r.attempt_id is None, r.origin == "spot_check" and not weak, -importance,
                st.score if st is not None else 0.5, available_at(r), r.id)

    rows.sort(key=priority)
    pending = [r for r in rows if r.attempt_id is not None]
    available = [r for r in rows if r.attempt_id is None]
    return pending + (available if candidates and room else available[:room])


def _origin_ref(s: Session, user_id: str, item: m.ReviewItem) -> dict | None:
    if item.origin_problem_id is None or not catalog.is_visible(s, user_id, item.origin_server_id, item.origin_problem_id):
        return None
    info = catalog.problem_info(s, item.origin_server_id, item.origin_problem_id)
    return {"key": info.key, "title": info.title, "brief": info.brief} if info else None


ORIGIN_TEXT = {
    "stuck": "卡住过", "recurrence": "改正后又出现了同样的错误", "quiz_wrong": "小题没做对",
    "heavy_help": "讲了原理或给了代码才做出来", "lucky": "小题没把握但答对了",
    "failed": "之前提交没有通过", "corrected": "之前做错过，后来改对了", "spot_check": "独立做对后的偶尔抽查",
    "repeated_fix": "这部分曾多次修改后通过", "verify": "换道题确认能否独立完成",
    "weak": "以往作答显示这部分还需要巩固",
}


def item_view(s: Session, user_id: str, item: m.ReviewItem, at: dt.datetime) -> dict:
    """学生端看到的一个复习项：知识点名称（不给技能编码）、为什么要复习、记得多牢、下次什么时候。"""
    kp = KNOWLEDGE_POINTS.get(kp_of_skill(item.skill_code), "")
    return {
        "id": item.id, "title": kp, "detail": skill_name(item.skill_code),
        "why": ORIGIN_TEXT.get(item.origin, ""), "origin": _origin_ref(s, user_id, item),
        "memory": memory(item, at), "due": available_at(item).isoformat(), "dueNow": available_at(item) <= at,
        "reps": item.reps, "streak": item.streak, "toGraduate": max(0, cfg()["graduate_streak"] - item.streak),
        "state": item.state, "pending": item.attempt_id is not None,
    }


def summary(s: Session, user_id: str, at: dt.datetime | None = None, course: str | None = None) -> dict:
    at = at or db.now()
    due = due_items(s, user_id, at, course)
    due_ids = {d.id for d in due}
    rows = [r for r in s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == user_id)).all()
            if _in_course(r.skill_code, course)]
    upcoming = sorted([r for r in rows if r.state == ACTIVE and r.id not in due_ids], key=available_at)
    start = at.astimezone(db.BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    completed = select(func.count(m.QuizAttempt.id)).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.reason == "review", m.QuizAttempt.state == "done",
        m.QuizAttempt.skipped.is_(False), m.QuizAttempt.finished_at >= start,
        m.QuizAttempt.finished_at < start + dt.timedelta(days=1))
    if course:
        completed = completed.where(m.QuizAttempt.skill_code.like(course + "%"))
    done_today = s.scalar(completed) or 0
    return {
        "due": [item_view(s, user_id, r, at) for r in due],
        "upcoming": [item_view(s, user_id, r, at) for r in upcoming[:12]],
        "graduated": [item_view(s, user_id, r, at) for r in rows if r.state == GRADUATED][:12],
        "doneToday": done_today, "dailyLimit": cfg()["daily_limit"],
        "active": sum(1 for r in rows if r.state == ACTIVE),
    }


def prepared_attempt(s: Session, user_id: str, item_id: int, at: dt.datetime | None = None, course: str | None = None):
    item = s.get(m.ReviewItem, item_id)
    if item is None or item.user_id != user_id or item.state != ACTIVE or not _in_course(item.skill_code, course):
        return None, None
    if item.attempt_id is not None:
        attempt = s.get(m.QuizAttempt, item.attempt_id)
        if attempt is not None and attempt.state != "done":
            return item, attempt
    return item, None


def start(s: Session, user_id: str, item_id: int, at: dt.datetime | None = None, course: str | None = None):
    """后台准备独立作答。已准备的作答复用，页面只调用 prepared_attempt。"""
    from . import quiz, quizgen

    at = at or db.now()
    item, attempt = prepared_attempt(s, user_id, item_id, at, course)
    if item is None or attempt is not None:
        return item, attempt
    if item.id not in {d.id for d in due_items(s, user_id, at, course, candidates=True)}:
        return item, None
    q = quizgen.generate(s, user_id, item.skill_code, "review",
                         origin=(item.origin_server_id, item.origin_problem_id) if item.origin_problem_id else None,
                         review_item=item)
    if q is None:
        return item, None
    attempt = quiz.new_attempt(s, user_id, q, "review", at, review_item_id=item.id)
    if item.trigger_at is not None:
        attempt.events = [{**attempt.events[0], "triggerAt": item.trigger_at.isoformat(),
                           "triggerReason": item.origin, "submissionId": item.trigger_submission_id,
                           "serverId": item.origin_server_id,
                           "phase": "delayed"}]
    item.attempt_id = attempt.id
    return item, attempt


def seed_missing(s: Session, user_id: str, course: str, at: dt.datetime, limit: int = 8) -> int:
    """为旧版本漏掉的错误技能补排复习；每个活跃学生半天检查一次，不重置现有或已巩固项。"""
    checked = s.scalar(select(m.UiEvent.id).where(m.UiEvent.user_id == user_id, m.UiEvent.course == course,
                       m.UiEvent.kind == "review_seed_v2", m.UiEvent.ts > at - dt.timedelta(hours=12)).limit(1))
    if checked:
        return 0
    existing = {item.skill_code: item for item in s.scalars(
        select(m.ReviewItem).where(m.ReviewItem.user_id == user_id))}
    done = 0
    states = mastery.skill_states(s, user_id, at=at, model="v2")
    # 延迟确认以前已检测到的雷同通过。后来有过独立作答或已入队的，无需因为旧记录重开。
    fp, pl = m.CodeFingerprint, m.ProblemLabel
    suspect = s.execute(select(fp, pl.primary_skill).join(
        pl, (pl.server_id == fp.server_id) & (pl.problem_id == fp.problem_id) & pl.is_current.is_(True)
    ).join(m.Server, m.Server.id == fp.server_id).where(
        fp.user_id == user_id, fp.suspicious.is_(True), m.Server.course == course,
        fp.submit_time.between(at - dt.timedelta(days=180), at)
    ).order_by(fp.submit_time.desc()).limit(50))
    for fact, skill in suspect:
        if done >= limit:
            break
        if not skill or not _in_course(skill, course) or not catalog.is_visible(
                s, user_id, fact.server_id, fact.problem_id, course=course):
            continue
        st = states.get(skill)
        if skill in existing or (st and st.last_independent_success and st.last_ts and st.last_ts > fact.submit_time):
            continue
        existing[skill] = enqueue(s, user_id, skill, "verify", fact.server_id, fact.problem_id,
                                  fact.submit_time, fact.submission_id)
        done += 1
    candidates = s.execute(select(m.Submission, m.SubmissionLabel.skill_code).join(
        m.SubmissionLabel, (m.SubmissionLabel.server_id == m.Submission.server_id)
        & (m.SubmissionLabel.submission_id == m.Submission.submission_id)).join(
        m.Server, m.Server.id == m.Submission.server_id).where(
            m.Submission.user_id == user_id, m.Server.course == course,
            m.Submission.judgement.not_in(("AC", "PENDING", "UNKNOWN", "")), m.Submission.submit_time <= at,
            m.Submission.submit_time >= at - dt.timedelta(days=180), m.SubmissionLabel.is_current.is_(True),
            m.SubmissionLabel.role == "primary", m.SubmissionLabel.confidence >= .5
        ).order_by(m.Submission.submit_time.desc(), m.Submission.submission_id.desc()).limit(100)).all()
    repeats = {}
    for sub, skill in candidates:
        key = (sub.server_id, sub.problem_id, skill)
        repeats[key] = repeats.get(key, 0) + 1
    for sub, skill in candidates:
        if done >= limit:
            break
        item = existing.get(skill)
        repeated = repeats[(sub.server_id, sub.problem_id, skill)] >= 2
        if not skill or not _in_course(skill, course) or (item is not None and not (
                item.state == ACTIVE and item.origin == "corrected" and not item.reps and not item.attempt_id
                and item.origin_server_id == sub.server_id and item.origin_problem_id == sub.problem_id and repeated)):
            continue
        if not catalog.is_visible(s, user_id, sub.server_id, sub.problem_id, course=course):
            continue
        fixed = s.scalars(select(m.Submission).where(
            m.Submission.user_id == user_id, m.Submission.server_id == sub.server_id,
            m.Submission.problem_id == sub.problem_id, m.Submission.judgement == "AC",
            m.Submission.submission_id > sub.submission_id, m.Submission.submit_time <= at)
            .order_by(m.Submission.submission_id).limit(1)).first()
        if item is not None:
            if fixed is not None and item.origin != "repeated_fix":
                item.origin = "repeated_fix"
                item.trigger_submission_id, item.trigger_at = fixed.submission_id, fixed.submit_time
            continue
        origin = ("repeated_fix" if repeated else "corrected") if fixed is not None else "failed"
        trigger = fixed or sub
        existing[skill] = enqueue(s, user_id, skill, origin, sub.server_id, sub.problem_id,
                                  trigger.submit_time, trigger.submission_id)
        done += 1
    if done < limit:
        weak = sorted((st for st in states.values() if _weak(st) and st.last_ts is not None
                       and _in_course(st.code, course) and st.code not in existing), key=lambda st: st.score)
        for st in weak[:limit - done]:
            enqueue(s, user_id, st.code, "weak", at=st.last_ts)
            done += 1
    s.add(m.UiEvent(user_id=user_id, course=course, kind="review_seed_v2", target="", ts=at))
    return done


def lead_for(s: Session, user_id: str, item: m.ReviewItem, q: m.QuizItem) -> str:
    ref = _origin_ref(s, user_id, item)
    if item.origin == "spot_check":
        return "这部分你曾独立做对，偶尔换个小场景，确认一下是否还记得："
    if q.source == "own_code" and ref and q.source_problem_id == item.origin_problem_id:
        reason = {"failed":"有过没通过的提交", "corrected":"修改后通过了"}.get(item.origin, ORIGIN_TEXT.get(item.origin, "卡过"))
        return f"你在「{ref['title']}」（{ref['brief']}）里{reason}。用你自己当时的代码回忆一下："
    reason = ORIGIN_TEXT.get(item.origin)
    return f"{reason}。用一段新代码试试。" if reason else "换一段代码，独立判断它能否完成题目要求。"


def teacher_stats(s: Session, user_ids: list[str], at: dt.datetime | None = None, course: str | None = None) -> dict:
    at = at or db.now()
    rows = s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id.in_(user_ids))).all() if user_ids else []
    rows = [r for r in rows if _in_course(r.skill_code, course)]
    active = [r for r in rows if r.state == ACTIVE]
    return {"active": len(active), "due": sum(1 for r in active if available_at(r) <= at),
            "graduated": sum(1 for r in rows if r.state == GRADUATED), "lapses": sum(r.lapses for r in rows),
            "students": len({r.user_id for r in active})}


# ------------------------------------------------------------ 复习提升：提前备题
def _pending(s: Session, user_id: str, course: str) -> m.QuizAttempt | None:
    return s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.reason == "review", m.QuizAttempt.state != "done",
        m.QuizAttempt.skill_code.like(course + "%")).order_by(m.QuizAttempt.id)).first()


def ready_count(s: Session, user_id: str, course: str) -> int:
    """侧边栏上的数字：已经备好、还没做完的复习题（题目可用了才加，到期数和已完成数另外算）。"""
    return int(s.scalar(select(func.count(m.QuizAttempt.id)).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.reason == "review", m.QuizAttempt.state != "done",
        m.QuizAttempt.skill_code.like(course + "%"))) or 0)


def prepare(s: Session, user_id: str, course: str, at: dt.datetime | None = None) -> str:
    """有到期的复习、还没有备好的题：备一道（出题器会先在沙箱里验证）。返回 ready / waiting / idle。

    出题失败退避 5 分钟（记一条 review_wait），已经备好的题先给。
    """
    at = at or db.now()
    seed_missing(s, user_id, course, at)
    # 兼容以前跳过后未解除的复习项。沿用原作答时间安排下一次复习。
    stale = s.scalars(select(m.QuizAttempt).join(m.ReviewItem, m.ReviewItem.attempt_id == m.QuizAttempt.id)
                      .where(m.QuizAttempt.user_id == user_id, m.QuizAttempt.skill_code.like(course + "%"),
                             m.QuizAttempt.skipped.is_(True), m.QuizAttempt.review_item_id == m.ReviewItem.id)).all()
    for skipped in stale:
        on_quiz_result(s, skipped)
    if _pending(s, user_id, course) is not None:
        return "ready"
    waiting = s.scalar(select(m.UiEvent.id).where(
        m.UiEvent.user_id == user_id, m.UiEvent.course == course, m.UiEvent.kind == "review_wait",
        m.UiEvent.ts > at - dt.timedelta(minutes=5)).limit(1))
    if waiting:
        return "waiting"
    due = due_items(s, user_id, at, course, candidates=True)
    if not due:
        return "idle"
    tried = set(s.scalars(select(m.UiEvent.target).where(m.UiEvent.user_id == user_id,
        m.UiEvent.course == course, m.UiEvent.kind == "review_item_wait", m.UiEvent.ts > at - dt.timedelta(hours=1))))
    misses = []
    for item in [r for r in due if str(r.id) not in tried][:3]:
        _item, attempt = start(s, user_id, item.id, at, course)
        if attempt is not None:
            s.add_all(misses)
            return "ready"
        misses.append(m.UiEvent(user_id=user_id, course=course, kind="review_item_wait", target=str(item.id), ts=at))
    s.add_all(misses)
    s.add(m.UiEvent(user_id=user_id, course=course, kind="review_wait", target="", ts=at))
    return "waiting"


def prepare_active(limit: int = 3, at: dt.datetime | None = None) -> int:
    """后台空闲时：给最近一周来过的学生提前备好复习题。每次最多 limit 个学生（出题可能调用大模型）。"""
    at = at or db.now()
    done = 0
    with db.session_scope() as s:
        activity = set(s.execute(select(m.UiEvent.user_id, m.UiEvent.course).where(
            m.UiEvent.kind.in_(("login", "open_review", "open_problem", "open_knowledge")),
            m.UiEvent.ts > at - dt.timedelta(days=7)).distinct()).tuples())
        seeded = set(s.execute(select(m.UiEvent.user_id, m.UiEvent.course).where(
            m.UiEvent.kind == "review_seed_v2", m.UiEvent.ts > at - dt.timedelta(hours=12)).distinct()).tuples())
        waiting = set(s.execute(select(m.UiEvent.user_id, m.UiEvent.course).where(
            m.UiEvent.kind == "review_wait", m.UiEvent.ts > at - dt.timedelta(minutes=5)).distinct()).tuples())
        ready = {(uid, course_of(skill)) for uid, skill in s.execute(select(
            m.QuizAttempt.user_id, m.QuizAttempt.skill_code).where(m.QuizAttempt.reason == "review",
            m.QuizAttempt.state != "done").distinct())}
        queued = {}
        for uid, skill, trigger in s.execute(select(m.ReviewItem.user_id, m.ReviewItem.skill_code,
                m.ReviewItem.trigger_at).where(m.ReviewItem.state == ACTIVE, m.ReviewItem.due_at <= at)
                .order_by(m.ReviewItem.due_at, m.ReviewItem.id)):
            key = (uid, course_of(skill))
            if key in activity or (trigger and trigger >= at - dt.timedelta(days=7)):
                queued[key] = None
    for key in sorted(activity - seeded):
        queued.setdefault(key, None)
    tried = 0
    for user_id, course in queued:
        if tried >= limit:
            break
        key = (user_id, course)
        if key in ready or key in waiting:
            continue
        if key not in seeded:
            with db.session_scope() as s:
                seed_missing(s, user_id, course, at)
        with db.session_scope() as s:
            if s.get_bind().dialect.name == "postgresql":
                if not s.scalar(select(func.pg_try_advisory_xact_lock(19574, func.hashtext(user_id + ":" + course)))):
                    continue
            result = prepare(s, user_id, course, at)
            tried += result in ("ready", "waiting")
            done += result == "ready"
    return done


def last_feedback(s: Session, user_id: str, course: str) -> dict | None:
    """最近做完的一道复习题：今天没有可做的题时给他看。"""
    from . import quiz

    a = s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.reason == "review", m.QuizAttempt.state == "done",
        m.QuizAttempt.skipped.is_(False), m.QuizAttempt.skill_code.like(course + "%"))
        .order_by(m.QuizAttempt.finished_at.desc(), m.QuizAttempt.id.desc())).first()
    if a is None:
        return None
    result = quiz.view(s, a).get("result") or {}
    return {"attemptId": a.id, "explanation": result.get("explanation", ""),
            "title": KNOWLEDGE_POINTS.get(kp_of_skill(a.skill_code), ""),
            "time": (a.finished_at or a.created_at).isoformat(),
            "headline": result.get("headline", ""), "summary": result.get("summary", [])}
