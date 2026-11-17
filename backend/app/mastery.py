"""掌握程度的评价。学生学情页、智能体判断“基础怎么样”、复习安排都用它。

两个版本，证据是同一批（skill_evidence），档位都在查询时现算：

v1：附件一第 8 节的简单算法，原样保留，当基线。
    p = (a0 + S) / (a0 + b0 + S + F)，S、F 是加权并按固定的遗忘系数 λ=0.98 衰减的“会”“不会”。

v2（默认）：在 v1 上加四处改动。
    1. 证据可信度 r：和同学已通过的代码雷同、而且这种写法在班里少见、时间挨得近的通过，分量打折。
       依据：Qu 等（2019，Entropy 21:1216）发现快速提交、抄袭和成绩明显相关；Alexandron 等（2017）的 CAMEO 研究。
    2. 个体化遗忘：每个学生每个技能一个证据半衰期 H。隔天（间隔开）的独立成功越多 H 越长，失败越多 H 越短；
       证据按 0.5^(Δt/H) 衰减。依据：间隔效应；Settles 和 Meeder（2016）的半衰期回归。
    3. 图先验：起点 Beta(1+m, 1+(1-m))，m 是先修技能（包括跨课程的先修）的掌握程度均值。
       没有证据的技能也能由先修推出一个“准备度”，数据结构的技能因此能在开课前由 C 语言的情况预测。
    4. 按不确定度定档：后验 Beta 的 80% 区间。“已掌握”要求区间下限也不低于 0.6，证据少时不会轻易给高档；
       “还没练到”看有没有真实的作答，不再被遗忘拉回去。
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db
from . import models as m
from .taxonomy import LEVEL_BASIC, LEVEL_MASTERED, LEVEL_NONE, LEVEL_WEAK, kp_of_skill

Z80 = 1.2816  # 80% 双侧区间


@dataclass
class SkillState:
    code: str
    s: float = 0.0          # 带遗忘的“会”
    f: float = 0.0          # 带遗忘的“不会”
    raw: float = 0.0        # 不带遗忘的证据总量，用来判断“练到没有”
    last_ts: dt.datetime | None = None
    last_independent_success: bool = False
    events: int = 0
    model: str = "v1"
    a0: float = 1.0
    b0: float = 1.0
    half_life: float | None = None   # v2：证据半衰期（天）
    discounted: int = 0              # v2：被打了折的证据条数（雷同）
    f_raw: float = 0.0               # v2：不衰减的“不会”证据
    predicted: bool = False          # v2：没有证据，完全由先修推出来的

    @property
    def p(self) -> float:
        if self.model == "v1":
            cfg = config.MASTERY
            return (cfg["a0"] + self.s) / (cfg["a0"] + cfg["b0"] + self.s + self.f)
        return (self.a0 + self.s) / (self.a0 + self.b0 + self.s + self.f)

    @property
    def sd(self) -> float:
        a, b = (config.MASTERY["a0"], config.MASTERY["b0"]) if self.model == "v1" else (self.a0, self.b0)
        a, b = a + self.s, b + self.f
        return math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))

    @property
    def interval(self) -> tuple[float, float]:
        return max(0.0, self.p - Z80 * self.sd), min(1.0, self.p + Z80 * self.sd)

    @property
    def level(self) -> str:
        if self.model == "v1":
            return level_of(self.p, self.s + self.f, self.raw, self.last_independent_success)
        return level_v2(self.p, self.interval[0], self.events, self.raw, self.last_independent_success,
                        self.s, self.f_raw)


def level_v2(p: float, lower: float, events: int, raw: float, last_independent_success: bool,
             s: float = 0.0, f_raw: float = 0.0) -> str:
    """定档。“待加强”必须有真实的失败证据：只因为证据少、先验拉着，不算待加强。"""
    cfg = config.MASTERY_V2
    if events == 0 or raw < cfg["min_raw"]:
        return LEVEL_NONE
    if lower >= cfg["mastered_lower"] and last_independent_success:
        return LEVEL_MASTERED
    if f_raw <= 0:
        return LEVEL_BASIC if s >= cfg["min_current_success"] else LEVEL_NONE
    if p < cfg["weak_below"]:
        return LEVEL_WEAK
    return LEVEL_BASIC


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
                 source_key: str, ts: dt.datetime | None = None, independent: bool = False,
                 reliability: float = 1.0) -> bool:
    """记一条证据。同一个来源、同一个技能、同一个方向只记一次。reliability 只有 v2 用。"""
    if not skill_code:
        return False
    exists = s.scalar(select(m.SkillEvidence.id).where(
        m.SkillEvidence.source_key == source_key, m.SkillEvidence.skill_code == skill_code,
        m.SkillEvidence.success == success))
    if exists is not None:
        return False
    s.add(m.SkillEvidence(
        user_id=user_id, skill_code=skill_code, event_type=event_type, success=success, weight=float(weight),
        independent=independent, ts=ts or db.now(), source_key=source_key, algo_version=config.MASTERY_VERSION,
        reliability=max(0.0, min(1.0, float(reliability)))))
    s.flush()
    return True


def _rows(s: Session, user_id: str, at: dt.datetime) -> list[m.SkillEvidence]:
    rows = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.user_id == user_id)
                     .order_by(m.SkillEvidence.ts, m.SkillEvidence.id)).all()
    return [r for r in rows if r.ts <= at]


def skill_states(s: Session, user_id: str, at: dt.datetime | None = None, decay: bool = True,
                 model: str | None = None, with_predicted: bool = False) -> dict[str, SkillState]:
    """每个有证据的技能的状态。model 不填就用 config.MASTERY_MODEL。

    with_predicted（只对 v2）：把没有证据、但能由先修推出来的技能也带上（predicted=True），用于数据结构准备度。
    """
    model = model or config.MASTERY_MODEL
    if model == "v2":
        return _skill_states_v2(s, user_id, at or db.now(), decay, with_predicted)
    return _skill_states_v1(s, user_id, at or db.now(), decay)


def _skill_states_v1(s: Session, user_id: str, at: dt.datetime, decay: bool) -> dict[str, SkillState]:
    lam = config.MASTERY["decay"] if decay else 1.0
    rows = _rows(s, user_id, at)
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


# ------------------------------------------------------------ v2
_TOPO: list[str] | None = None


def topo_order() -> list[str]:
    """全部技能（两门课）按先修关系排好的顺序：先修在前。"""
    global _TOPO
    if _TOPO is None:
        from .taxonomy import SKILLS
        from .taxonomy.prereq import parents_of

        parents = parents_of()
        done: set[str] = set()
        order: list[str] = []

        def visit(code: str) -> None:
            if code in done:
                return
            done.add(code)
            for p in parents.get(code, []):
                visit(p)
            order.append(code)

        for code in SKILLS:
            visit(code)
        _TOPO = order
    return _TOPO


def half_life_days(rows: list[m.SkillEvidence]) -> float:
    """证据半衰期：间隔开的独立成功（按北京时间的不同日期计）每多一天乘 growth，每次失败除以 growth。"""
    cfg = config.MASTERY_V2
    days = {r.ts.astimezone(db.BEIJING).date() for r in rows if r.success and r.independent and r.weight > 0}
    fails = sum(1 for r in rows if not r.success and r.weight > 0)
    h = cfg["half_life_base"] * cfg["half_life_growth"] ** (max(0, len(days) - 1) - fails)
    return max(cfg["half_life_min"], min(cfg["half_life_max"], h))


def _skill_states_v2(s: Session, user_id: str, at: dt.datetime, decay: bool,
                     with_predicted: bool) -> dict[str, SkillState]:
    from .taxonomy.prereq import parents_of

    cfg = config.MASTERY_V2
    by_skill: dict[str, list[m.SkillEvidence]] = {}
    for r in _rows(s, user_id, at):
        by_skill.setdefault(r.skill_code, []).append(r)
    out: dict[str, SkillState] = {}
    for code, rows in by_skill.items():
        st = SkillState(code, model="v2")
        h = half_life_days(rows)
        st.half_life = h
        for r in rows:
            days = max(0.0, (at - r.ts).total_seconds() / 86400.0)
            w = r.weight * (r.reliability if r.reliability is not None else 1.0)
            if decay:
                w *= 0.5 ** (days / h)
            if r.success:
                st.s += w
            else:
                st.f += w
                st.f_raw += r.weight
            st.raw += r.weight
            st.events += 1
            if (r.reliability or 1.0) < 1.0:
                st.discounted += 1
            st.last_ts = r.ts
            st.last_independent_success = bool(r.success and r.independent)
        out[code] = st

    # 图先验：按先修顺序走一遍，先修（有证据或能推出来的）的均值决定起点
    parents = parents_of()
    kappa = cfg["prior_strength"]
    informative: dict[str, float] = {}
    for code in topo_order():
        ps = [informative[p] for p in parents.get(code, []) if p in informative]
        m_k = sum(ps) / len(ps) if ps else None
        st = out.get(code)
        if st is not None:
            mean = 0.5 if m_k is None else m_k
            st.a0, st.b0 = 1.0 + kappa * mean, 1.0 + kappa * (1.0 - mean)
            informative[code] = st.p
        elif m_k is not None:
            informative[code] = m_k
            if with_predicted:   # 推出来的“准备度”：估计值就是先修的均值
                pred = SkillState(code, model="v2", predicted=True)
                pred.a0, pred.b0 = 2.0 * m_k, 2.0 * (1.0 - m_k)
                out[code] = pred
    return out


def skill_state(s: Session, user_id: str, skill_code: str, model: str | None = None) -> SkillState:
    return skill_states(s, user_id, model=model).get(skill_code, SkillState(skill_code, model=model or config.MASTERY_MODEL))


@dataclass
class KpState:
    code: str
    p: float = 0.5
    evidence: float = 0.0
    raw: float = 0.0
    level: str = LEVEL_NONE
    skills: list[SkillState] = field(default_factory=list)
    lower: float = 0.0
    predicted: bool = False


def kp_states(s: Session, user_id: str, model: str | None = None, with_predicted: bool = False) -> dict[str, KpState]:
    """粗知识点的档位：下面各技能按证据量加权的平均。"""
    model = model or config.MASTERY_MODEL
    out: dict[str, KpState] = {}
    for st in skill_states(s, user_id, model=model, with_predicted=with_predicted).values():
        kp = out.setdefault(kp_of_skill(st.code), KpState(kp_of_skill(st.code)))
        kp.skills.append(st)
    for kp in out.values():
        real = [x for x in kp.skills if not x.predicted]
        total = sum(x.s + x.f for x in real)
        kp.evidence = total
        kp.raw = sum(x.raw for x in real)
        if total > 0:
            kp.p = sum((x.s + x.f) * x.p for x in real) / total
        elif kp.skills:
            kp.p = sum(x.p for x in kp.skills) / len(kp.skills)
        if not real:
            kp.level, kp.predicted = LEVEL_NONE, True
            kp.lower = kp.p
            continue
        latest = max(real, key=lambda x: x.last_ts or dt.datetime.min.replace(tzinfo=db.UTC))
        if model == "v2":
            kp.lower = (sum((x.s + x.f) * x.interval[0] for x in real) / total) if total > 0 else min(x.interval[0] for x in real)
            kp.level = level_v2(kp.p, kp.lower, sum(x.events for x in real), kp.raw, latest.last_independent_success,
                                sum(x.s for x in real), sum(x.f_raw for x in real))
        else:
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
                earlier_attempts: int, help_level: str, reliability: float = 1.0) -> str:
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
        add_evidence(s, user_id, code, event, True, weight * factor, source_key, ts, independent, reliability)
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
    if reason in ("recall", "revisit", "review"):  # 隔几天的回访题、回忆题、复习题
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
