"""班里的误区：老师上课要强调的错误点（班级总览列前几条，教师端“误区”页列全部）。

一条误区 = 一种错误 × 一个技能（比如“变量没给合适的初值就使用 × 最值的初值设定”）。从四处来，都是记录里已有的：
- 提交：程序或大模型给没通过的提交标的错误（submission_labels，当前那一版；OT99“其他”不算）；
- 卡住：对话里卡住时记下的错误（stuck_points）；
- 小题：复习小题里有这个错的代码，他判断“满足要求”（没看出来）；
- 说法：智能体评“理解”时从他的原话里看出的错误想法（agent_assessments.misconception），按技能挂到这个技能上
  出现最多的那条误区下；这个技能没有别的记录时单独一条。

每条的数（week 是北京时间本周一零点至当前时刻，all 是全部）：
按原提交、交互的时间归属；后导入或后补标注不改变归属周。
- students：有过这个误区的学生人数（四处来源的并集）；
- submissions：标了这个错误的没通过的提交次数；
- unresolved：出过这个错的题到现在还没通过的学生人数（分母是 students 里从提交来的那些人）；
- stuck：卡住的次数；quizMissed：小题里没看出来的人数；
- problems：出现最多的几道题和次数；who：哪些学生（上课用的页面可以写谁）；quotes：最多三句他们的错误想法。
排序：人数多的在前，同样多的按提交和卡住的次数。定义改了就改 VERSION。
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import db
from . import models as m
from .taxonomy import KNOWLEDGE_POINTS, course_of, error_name, kp_of_skill, skill_name

VERSION = "misconception-2"
PERIODS = ("week", "all")


class _Item:
    def __init__(self, error: str, skill: str):
        self.error, self.skill = error, skill
        self.students: set[str] = set()
        self.from_subs: set[str] = set()
        self.submissions = 0
        self.unresolved: set[str] = set()
        self.stuck = 0
        self.quiz: set[str] = set()
        self.problems: Counter = Counter()
        self.quotes: list[str] = []
        self.last: dt.datetime | None = None

    def seen(self, uid: str, at: dt.datetime | None) -> None:
        self.students.add(uid)
        if at is not None and (self.last is None or at > self.last):
            self.last = at


def collect(s: Session, server_id: int, students: dict[str, str], course: str, period: str = "all",
            now: dt.datetime | None = None) -> list[dict]:
    """students：学号 → 姓名（这个班的）。返回全部误区（排好序）。"""
    now = now or db.now()
    ids = list(students)
    if not ids:
        return []
    local = now.astimezone(db.BEIJING)
    since = (local - dt.timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(db.UTC) if period == "week" else None
    items: dict[tuple[str, str], _Item] = {}

    def item(error: str, skill: str) -> _Item:
        return items.setdefault((error, skill), _Item(error, skill))

    def in_course(skill: str) -> bool:
        return not skill or course_of(skill) == course

    # 提交
    q = (select(m.Submission.user_id, m.Submission.problem_id, m.Submission.submit_time,
                m.SubmissionLabel.error_code, m.SubmissionLabel.skill_code)
         .join(m.SubmissionLabel, (m.SubmissionLabel.server_id == m.Submission.server_id)
               & (m.SubmissionLabel.submission_id == m.Submission.submission_id))
         .where(m.SubmissionLabel.is_current.is_(True), m.Submission.server_id == server_id,
                m.Submission.user_id.in_(ids), m.Submission.judgement != "AC", m.SubmissionLabel.error_code != "OT99",
                m.SubmissionLabel.error_code != ""))
    if since is not None:
        q = q.where(m.Submission.submit_time >= since, m.Submission.submit_time <= now)
    rows = [r for r in s.execute(q).all() if in_course(r[4] or "")]
    passed = set(s.execute(select(m.Submission.user_id, m.Submission.problem_id).where(
        m.Submission.server_id == server_id, m.Submission.user_id.in_(ids), m.Submission.judgement == "AC")).all())
    for uid, pid, at, error, skill in rows:
        it = item(error, skill or "")
        it.seen(uid, at)
        it.from_subs.add(uid)
        it.submissions += 1
        it.problems[pid] += 1
        if (uid, pid) not in passed:
            it.unresolved.add(uid)
    # 卡住
    q = select(m.StuckPoint).where(m.StuckPoint.user_id.in_(ids), m.StuckPoint.skill_code.like(course + "%"))
    if since is not None:
        q = q.where(m.StuckPoint.created_at >= since, m.StuckPoint.created_at <= now)
    for sp in s.scalars(q).all():
        it = item(sp.error_code or "", sp.skill_code)
        it.seen(sp.user_id, sp.created_at)
        it.stuck += 1
    # 小题里没看出来
    q = (select(m.QuizAttempt.user_id, m.QuizAttempt.skill_code, m.QuizItem.error_code, m.QuizAttempt.created_at)
         .join(m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id)
         .where(m.QuizAttempt.user_id.in_(ids), m.QuizItem.has_bug.is_(True), m.QuizAttempt.judged_has_bug.is_(False)))
    if since is not None:
        q = q.where(m.QuizAttempt.created_at >= since, m.QuizAttempt.created_at <= now)
    for uid, skill, error, at in s.execute(q).all():
        if not skill or not in_course(skill):
            continue
        it = item(error or "", skill)
        it.seen(uid, at)
        it.quiz.add(uid)
    # 说法：挂到这个技能上人数最多的那条
    # 评估可能晚几天才做，按被评估的提交或会话时间统计，不按后台评估时间统计。
    a, rnd, sub = m.AgentAssessment, m.ChatRound, m.Submission
    event_time = func.coalesce(case((a.basis == "code", sub.submit_time), else_=rnd.last_message_at), a.created_at)
    q = (select(a, event_time.label("event_time"))
         .outerjoin(rnd, rnd.id == a.round_id)
         .outerjoin(sub, (sub.server_id == a.server_id) & (sub.submission_id == a.submission_id))
         .where(a.user_id.in_(ids), a.course == course, a.misconception != ""))
    if since is not None:
        q = q.where(event_time >= since, event_time <= now)
    quotes: dict[str, list[tuple[str, dt.datetime, str]]] = defaultdict(list)
    for assessment, at in s.execute(q.order_by(event_time.desc())).all():
        quotes[assessment.skill_code].append((assessment.user_id, at, assessment.misconception.strip()))
    for skill, said in quotes.items():
        same = [it for (e, sk), it in items.items() if sk == skill]
        it = max(same, key=lambda x: (len(x.students), x.submissions + x.stuck)) if same else item("", skill)
        for uid, at, text in said:
            it.seen(uid, at)
            if text and text not in it.quotes and len(it.quotes) < 3:
                it.quotes.append(text)

    titles = dict(s.execute(select(m.Problem.problem_id, m.Problem.title).where(
        m.Problem.server_id == server_id)).all())
    out = []
    for (error, skill), it in items.items():
        if not it.students:
            continue
        kp = kp_of_skill(skill) if skill else ""
        out.append({
            "key": f"{error}:{skill}", "error": error, "errorName": error_name(error) if error else "",
            "skill": skill, "skillName": skill_name(skill) if skill else "", "kp": kp, "kpName": KNOWLEDGE_POINTS.get(kp, ""),
            "students": len(it.students), "submissions": it.submissions,
            "unresolved": len(it.unresolved), "fromSubmissions": len(it.from_subs),
            "stuck": it.stuck, "quizMissed": len(it.quiz),
            "problems": [{"problemId": pid, "title": titles.get(pid, ""), "count": n} for pid, n in it.problems.most_common(3)],
            "who": [{"userId": u, "name": students.get(u, "")} for u in sorted(it.students)][:12],
            "quotes": it.quotes, "last": it.last.isoformat() if it.last else None,
        })
    out.sort(key=lambda r: (-r["students"], -(r["submissions"] + r["stuck"] + r["quizMissed"]), r["key"]))
    return out
