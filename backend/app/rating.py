"""老师盲评“理解”：检验智能体评估（创新点一）和人的判断一致不一致。

为什么要有：assess.py 的四档量规是教学上的划分，大模型按它评、规则也给一个等级，但两者都没有和人对照过
（docs/创新点与方法.md 第 7 节）。这里请老师按同一个量规评一批回合，算老师和
大模型原始等级、约束（原话锚定、和事实冲突下调）以后的等级、规则等级三者各自的一致率；
下调过的那些另算“下调以后离老师更近还是更远”，用来检验约束有没有用。

盲：老师看到的材料和大模型看到的完全一样（assess.dialogue：已知情况 + 最后 16 条对话，去掉学号姓名），
不显示学生是谁，不显示大模型、规则的等级和摘抄的原话。每位老师的出题顺序按（老师, 评估编号）的哈希打乱，
每位老师顺序不同、但每次打开都一样；评过的不再出。看不出来可以选“看不出来”，不进一致率。
范围：只评自己班上这门课学生的、看对话评的（basis = chat）回合。
"""
from __future__ import annotations

import hashlib
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import assess, db
from . import models as m
from .taxonomy import KNOWLEDGE_POINTS, kp_of_skill, skill_name

VERSION = "rating-1"


class RatingError(ValueError):
    """不能评这一条，message 直接给老师看。"""


def _pool(s: Session, ids: list[str], course: str) -> list[m.AgentAssessment]:
    if not ids:
        return []
    rows = list(s.scalars(select(m.AgentAssessment).where(
        m.AgentAssessment.user_id.in_(ids), m.AgentAssessment.course == course,
        m.AgentAssessment.basis == "chat", m.AgentAssessment.round_id.is_not(None))).all())
    said = set(s.scalars(select(m.ChatMessage.round_id).where(
        m.ChatMessage.round_id.in_([r.round_id for r in rows] or [-1]), m.ChatMessage.role == "student",
        m.ChatMessage.kind == "text").distinct()).all())
    return [r for r in rows if r.round_id in said]


def _order(rater_id: int, assessment_id: int) -> str:
    return hashlib.sha256(f"{rater_id}:{assessment_id}".encode()).hexdigest()


def levels() -> list[dict]:
    return [{"level": k, "name": v, "hint": assess.LEVEL_HINTS[k]} for k, v in assess.LEVEL_NAMES.items()]


def material(s: Session, a: m.AgentAssessment) -> dict | None:
    rnd = s.get(m.ChatRound, a.round_id) if a.round_id is not None else None
    if rnd is None:
        return None
    facts, lines = assess.dialogue(assess.Material(s, rnd), a.skill_code, rnd.error_code)
    if not any(who == "学生" for who, _ in lines):
        return None
    problem = s.get(m.Problem, (rnd.server_id, rnd.problem_id)) if rnd.problem_id is not None else None
    return {"id": a.id, "kp": KNOWLEDGE_POINTS.get(kp_of_skill(a.skill_code), ""), "skill": skill_name(a.skill_code),
            "problem": problem.title if problem else "", "facts": facts[1:],
            "lines": [{"who": who, "text": text} for who, text in lines], "shownAt": int(time.time())}


def next_item(s: Session, rater: m.User, ids: list[str], course: str) -> dict:
    pool = _pool(s, ids, course)
    in_pool = {a.id for a in pool}
    done = set(s.scalars(select(m.HumanRating.assessment_id).where(m.HumanRating.rater_id == rater.id)).all())
    item = None
    for a in sorted((a for a in pool if a.id not in done), key=lambda a: _order(rater.id, a.id)):
        item = material(s, a)
        if item is not None:
            break
    return {"item": item, "rated": len(done & in_pool), "total": len(pool), "levels": levels()}


def rate(s: Session, rater: m.User, ids: list[str], course: str, assessment_id: int, level: int | None,
         shown_at: int | None = None) -> m.HumanRating:
    if level is not None and level not in assess.LEVEL_NAMES:
        raise RatingError("等级只能是 0、1、2、3，或者看不出来。")
    a = s.get(m.AgentAssessment, assessment_id)
    if a is None or a.user_id not in set(ids) or a.course != course or a.basis != "chat":
        raise RatingError("这一条不在你的班里。")
    seconds = max(0, min(3600, int(time.time()) - shown_at)) if shown_at else None
    row = s.scalar(select(m.HumanRating).where(m.HumanRating.assessment_id == a.id, m.HumanRating.rater_id == rater.id))
    if row is None:
        row = m.HumanRating(assessment_id=a.id, rater_id=rater.id, course=course)
        s.add(row)
    row.level, row.seconds, row.created_at = level, seconds, db.now()
    s.flush()
    return row


def agreement(s: Session, ids: list[str], course: str) -> dict:
    """老师和大模型（原始 / 约束后）、规则的一致率；下调过的离老师更近还是更远；两位老师之间的一致率。"""
    rows = s.execute(select(m.HumanRating, m.AgentAssessment).join(
        m.AgentAssessment, m.AgentAssessment.id == m.HumanRating.assessment_id).where(
        m.AgentAssessment.user_id.in_(ids or ["-"]), m.AgentAssessment.course == course)
        .order_by(m.HumanRating.id)).all()
    rated = [(h, a) for h, a in rows if h.level is not None]
    llm = [(h, a) for h, a in rated if a.source == "llm"]
    closer = same = farther = 0
    for h, a in llm:
        if a.adjusted and a.raw_level is not None and a.level is not None:
            d_raw, d_final = abs(h.level - a.raw_level), abs(h.level - a.level)
            closer += d_final < d_raw
            same += d_final == d_raw
            farther += d_final > d_raw
    by_item: dict[int, list[int]] = {}
    for h, a in rated:
        by_item.setdefault(a.id, []).append(h.level)
    two = [(v[0], v[1]) for v in by_item.values() if len(v) >= 2]
    return {
        "version": VERSION, "rated": len({a.id for _h, a in rows}), "unsure": sum(1 for h, _a in rows if h.level is None),
        "raters": len({h.rater_id for h, _a in rows}),
        "llmRaw": assess.pair_stats([(h.level, a.raw_level) for h, a in llm if a.raw_level is not None]),
        "llmFinal": assess.pair_stats([(h.level, a.level) for h, a in llm if a.level is not None]),
        "rule": assess.pair_stats([(h.level, a.rule_level) for h, a in rated if a.rule_level is not None]),
        "adjusted": {"n": closer + same + farther, "closer": closer, "same": same, "farther": farther},
        "betweenRaters": assess.pair_stats(two),
    }
