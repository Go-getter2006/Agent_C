"""掌握程度的评价（简单算法，附件一第 8 节）。学生学情页和智能体判断“基础怎么样”都用它。

对学生 i 的技能 k：p = (a0 + S) / (a0 + b0 + S + F)
S、F 是加权并带遗忘的“会”“不会”证据总量。每条证据存进 skill_evidence，档位在查询时现算。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db
from . import models as m
from .taxonomy import LEVEL_BASIC, LEVEL_MASTERED, LEVEL_NONE, LEVEL_WEAK, kp_of_skill


@dataclass
class SkillState:
    code: str
    s: float = 0.0          # 带遗忘的“会”
    f: float = 0.0          # 带遗忘的“不会”
    raw: float = 0.0        # 不带遗忘的证据总量，用来判断“练到没有”
    last_ts: dt.datetime | None = None
    last_independent_success: bool = False
    events: int = 0

    @property
    def p(self) -> float:
        cfg = config.MASTERY
        return (cfg["a0"] + self.s) / (cfg["a0"] + cfg["b0"] + self.s + self.f)

    @property
    def level(self) -> str:
        return level_of(self.p, self.s + self.f, self.raw, self.last_independent_success)


def level_of(p: float, evidence: float, raw: float, last_independent_success: bool) -> str:
    cfg = config.MASTERY
    if raw < cfg["min_evidence"]:
        return LEVEL_NONE
    if p < cfg["level_weak_below"]:
        return LEVEL_WEAK
    if p >= cfg["level_mastered_from"] and evidence >= cfg["mastered_min_evidence"] and last_independent_success:
        return LEVEL_MASTERED
    return LEVEL_BASIC


def add_evidence(s: Session, user_id: str, skill_code: str, event_type: str, success: bool, weight: float,
                 source_key: str, ts: dt.datetime | None = None, independent: bool = False) -> bool:
    """记一条证据。同一个来源、同一个技能、同一个方向只记一次。"""
    if not skill_code:
        return False
    exists = s.scalar(select(m.SkillEvidence.id).where(
        m.SkillEvidence.source_key == source_key, m.SkillEvidence.skill_code == skill_code,
        m.SkillEvidence.success == success))
    if exists is not None:
        return False
    s.add(m.SkillEvidence(
        user_id=user_id, skill_code=skill_code, event_type=event_type, success=success, weight=float(weight),
        independent=independent, ts=ts or db.now(), source_key=source_key, algo_version=config.MASTERY_VERSION))
    s.flush()
    return True


def skill_states(s: Session, user_id: str, at: dt.datetime | None = None, decay: bool = True) -> dict[str, SkillState]:
    at = at or db.now()
    lam = config.MASTERY["decay"] if decay else 1.0
    rows = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.user_id == user_id)
                     .order_by(m.SkillEvidence.ts, m.SkillEvidence.id)).all()
    out: dict[str, SkillState] = {}
    for r in rows:
        if r.ts > at:
            continue
        st = out.setdefault(r.skill_code, SkillState(r.skill_code))
        days = max(0.0, (at - r.ts).total_seconds() / 86400.0)
        w = r.weight * (lam ** days)
        if r.success:
            st.s += w
        else:
            st.f += w
        st.raw += r.weight
        st.events += 1
        # “最近一次是独立成功”：权重为 0 的记录（给了示例代码后通过）也算一次事件
        st.last_ts = r.ts
        st.last_independent_success = bool(r.success and r.independent)
    return out


def skill_state(s: Session, user_id: str, skill_code: str) -> SkillState:
    return skill_states(s, user_id).get(skill_code, SkillState(skill_code))


@dataclass
class KpState:
    code: str
    p: float = 0.5
    evidence: float = 0.0
    raw: float = 0.0
    level: str = LEVEL_NONE
    skills: list[SkillState] = field(default_factory=list)


def kp_states(s: Session, user_id: str) -> dict[str, KpState]:
    """粗知识点的档位：下面各技能按证据量加权的平均。"""
    out: dict[str, KpState] = {}
    for st in skill_states(s, user_id).values():
        kp = out.setdefault(kp_of_skill(st.code), KpState(kp_of_skill(st.code)))
        kp.skills.append(st)
    for kp in out.values():
        total = sum(x.s + x.f for x in kp.skills)
        kp.evidence = total
        kp.raw = sum(x.raw for x in kp.skills)
        kp.p = (sum((x.s + x.f) * x.p for x in kp.skills) / total) if total > 0 else 0.5
        latest = max(kp.skills, key=lambda x: x.last_ts or dt.datetime.min.replace(tzinfo=db.UTC))
        kp.level = level_of(kp.p, kp.evidence, kp.raw, latest.last_independent_success)
    return out


# ------------------------------------------------------------ 各类事件怎么记
def problem_skills(label: m.ProblemLabel | None) -> list[tuple[str, float]]:
    """一道题的证据记到主技能（全额）和次技能（按比例折算）上。"""
    if label is None:
        return []
    factor = config.MASTERY["secondary_skill_factor"]
    return [(label.primary_skill, 1.0)] + [(c, factor) for c in (label.secondary_skills or [])]


def record_pass(s: Session, user_id: str, label: m.ProblemLabel | None, source_key: str, ts: dt.datetime,
                earlier_attempts: int, help_level: str) -> str:
    """一道题第一次通过。help_level：通过之前在这道题上得到过的最高帮助级别（NA 表示没求助）。"""
    cfg = config.MASTERY
    if help_level in ("NA", ""):
        if earlier_attempts == 0:
            event, weight, independent = "pass_first_try", cfg["w_first_try_pass"], True
        else:
            event, weight, independent = "pass_self_fixed", cfg["w_self_fixed_pass"], True
    elif help_level in ("H0", "H1"):
        event, weight, independent = "pass_after_h0_h1", cfg["w_pass_after_h0_h1"], False
    elif help_level == "H2":
        event, weight, independent = "pass_after_h2", cfg["w_pass_after_h2"], False
    else:
        event, weight, independent = "pass_after_h3", cfg["w_pass_after_h3"], False
    for code, factor in problem_skills(label):
        add_evidence(s, user_id, code, event, True, weight * factor, source_key, ts, independent)
    return event


def record_stuck(s: Session, user_id: str, skill_code: str, nth: int, source_key: str,
                 ts: dt.datetime | None = None) -> None:
    cfg = config.MASTERY
    if nth <= 1:
        add_evidence(s, user_id, skill_code, "stuck_first", False, cfg["w_stuck_first"], source_key, ts)
    else:
        add_evidence(s, user_id, skill_code, "stuck_again", False, cfg["w_stuck_again"], source_key, ts)


def record_quiz(s: Session, user_id: str, skill_code: str, outcome: str, confidence: str, reason: str,
                source_key: str, confidence_reliable: bool = True, ts: dt.datetime | None = None) -> None:
    """小题作答。outcome：right / right_second / right_bypass / wrong_fix / wrong_judge。"""
    cfg = config.MASTERY
    yes, no = 0.0, 0.0
    if outcome == "right":
        yes, event = cfg["w_quiz_right"], "quiz_fix_right"
    elif outcome == "right_judge":
        yes, event = cfg["w_quiz_right"], "quiz_judge_right"
    elif outcome == "right_second":
        (yes, no), event = cfg["w_quiz_fix_second_try"], "quiz_fix_second_try"
    elif outcome == "right_bypass":
        (yes, no), event = cfg["w_quiz_fix_bypass"], "quiz_fix_bypass"
    elif outcome == "wrong_fix":
        no, event = cfg["w_quiz_wrong"], "quiz_fix_failed"
    elif outcome == "wrong_judge":
        no, event = cfg["w_quiz_wrong"], "quiz_judge_wrong"
    else:
        return
    if reason in ("recall", "revisit"):  # 隔几天的回访题、回忆题
        yes *= cfg["revisit_bonus"]
    if confidence_reliable:
        if confidence == "没把握" and no == 0:
            yes *= cfg["unsure_right_factor"]
        if confidence == "很有把握" and yes == 0:
            no *= cfg["sure_wrong_factor"]
    if yes > 0:
        add_evidence(s, user_id, skill_code, event, True, yes, source_key, ts, independent=True)
    if no > 0:
        add_evidence(s, user_id, skill_code, event, False, no, source_key, ts)


def record_streak_bonus(s: Session, user_id: str, skill_code: str, source_key: str,
                        ts: dt.datetime | None = None) -> bool:
    """同一技能连续两道小题都很有把握地答对：额外算“会”1 分。7 天内最多一次。"""
    cfg = config.MASTERY
    at = ts or db.now()
    recent = s.scalar(select(m.SkillEvidence.id).where(
        m.SkillEvidence.user_id == user_id, m.SkillEvidence.skill_code == skill_code,
        m.SkillEvidence.event_type == "quiz_streak_bonus",
        m.SkillEvidence.ts >= at - dt.timedelta(days=cfg["streak_bonus_days"])))
    if recent is not None:
        return False
    return add_evidence(s, user_id, skill_code, "quiz_streak_bonus", True, cfg["streak_bonus"], source_key, at,
                        independent=True)
