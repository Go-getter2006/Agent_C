"""掌握程度的评价。学生学情页、智能体判断“基础怎么样”、复习安排都用它。

两个版本，证据是同一批（skill_evidence），档位都在查询时现算：

v1：附件一第 8 节的简单算法，原样保留，当基线。
    p = (a0 + S) / (a0 + b0 + S + F)，S、F 是加权并按固定的遗忘系数 λ=0.98 衰减的“会”“不会”。

v2（默认）：在 v1 上加四处改动。
    1. 证据可信度 r：和同学已通过的代码雷同、而且这种写法在班里少见、时间挨得近的通过，分量打折。
       依据：Qu 等（2019，Entropy 21:1216）发现快速提交、抄袭和成绩明显相关；Alexandron 等（2017）的 CAMEO 研究。
    2. 个体化遗忘：每个学生每个技能一个证据半衰期 H。隔天（间隔开）的独立成功越多 H 越长，失败越多 H 越短；
       证据按 0.5^(Δt/H) 衰减。依据：间隔效应；Settles 和 Meeder（2016）的半衰期回归。
    3. 图先验：起点 Beta(1+κm, 1+κ(1-m))，m 是先修技能（包括跨课程的先修）的掌握程度的加权平均。
       没有证据的技能也能由先修推出一个“准备度”，数据结构的技能因此能在开课前由 C 语言的情况预测。
       跨课程的先修（cross.py）按联系的类型和数据检验的结论加权；有跨课程先修的技能，先验的分量 κ 更大
       （相当于 n0 次作答），所以数据结构的估计 = η·C 那头给的先验 + (1−η)·自己的证据，η 随自己的证据变多而变小。
    4. 按不确定度定档：后验 Beta 的 80% 区间。“已掌握”要求区间下限也不低于 0.6，证据少时不会轻易给高档；
       “还没练到”看有没有真实的作答，不再被遗忘拉回去。
    6. 改对算会：一道题前面没过、后来自己改对了，前面那几次失败只按很小的分量算（resolved_fail_factor）；
       出错的那个技能如果不是这道题本身考的，改对它本身算一次“会”（他找到了错并改掉了）。
       “待加强”要求有**还没改对**的失败，而且估计低于 weak_below；改对过又犯（别的题上改对过、新的题上又错、
       还没改对）也是待加强。以前失败一次记 0.5、改对只记 0.8，
       交两次才改对的同学反而落到“待加强”，几乎人人待加强。
    5. 智能体评估的“理解”（app/assess.py）也是证据：会 / 不会按等级记，分量乘大模型的置信度。
       它只改变估计值，不算“练过”（不计入作答次数、不改变“最近一次是不是独立做对”），
       所以只聊过、没做过题的技能不会因此有档位；会做但讲不清的，估计值和区间下限会被拉低，不容易到“已掌握”。
       v1 是原来的基线，不用这类证据；图模型也不用。v2 可以用 include_agent=False 去掉它，做消融对照。
"""
from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, db
from . import models as m
from .taxonomy import LEVEL_BASIC, LEVEL_MASTERED, LEVEL_NONE, LEVEL_WEAK, kp_of_skill

Z80 = 1.2816  # 80% 双侧区间
AGENT_EVENTS = frozenset({"agent_understanding"})   # 智能体评估记下的证据
V2_ONLY_EVENTS = AGENT_EVENTS | {"submit_failed"}    # v1（附件一的基线）不用的证据


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
    recurred: bool = False           # v2：在别的题上改对过，现在又在新的题上错、还没改对（改对过又犯）

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
    def score(self) -> float:
        """教师端的“掌握分”：只看他自己的证据（做题、会话、小题），用很轻的先验 Beta(0.5, 0.5)，不掺图先验。
        档位仍按 p 和区间定（谨慎）；分数要能把“做得好”和“做得一般”的同学分开，所以不往 0.5 拉。"""
        return (0.5 + self.s) / (1.0 + self.s + self.f)

    @property
    def level(self) -> str:
        if self.model == "v1":
            return level_of(self.p, self.s + self.f, self.raw, self.last_independent_success)
        return level_v2(self.p, self.interval[0], self.events, self.raw, self.last_independent_success,
                        self.s, self.f_raw, self.recurred)


def level_v2(p: float, lower: float, events: int, raw: float, last_independent_success: bool,
             s: float = 0.0, f_raw: float = 0.0, recurred: bool = False) -> str:
    """定档。“待加强”必须有还没改对的失败：只因为证据少、先验拉着，或者交了几次自己改对了，都不算待加强。
    改对过又犯（别的题上改对过，新的题上又错、还没改对）一定是待加强。"""
    cfg = config.MASTERY_V2
    if events == 0 or raw < cfg["min_raw"]:
        return LEVEL_NONE
    if lower >= cfg["mastered_lower"] and last_independent_success:
        return LEVEL_MASTERED
    if f_raw <= 0:
        return LEVEL_BASIC if s >= cfg["min_current_success"] else LEVEL_NONE
    if p < cfg["weak_below"] or recurred:
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
                 model: str | None = None, with_predicted: bool = False, include_agent: bool = True) -> dict[str, SkillState]:
    """每个有证据的技能的状态。model 不填就用 config.MASTERY_MODEL。

    with_predicted（只对 v2）：把没有证据、但能由先修推出来的技能也带上（predicted=True），用于数据结构准备度。
    include_agent（只对 v2）：用不用智能体评估的证据。
    """
    model = model or config.MASTERY_MODEL
    memo = s.info.get(MEMO) if at is None else None
    key = ("skill", user_id, decay, model, with_predicted, include_agent)
    if memo is not None and key in memo:
        return memo[key]
    if model == "v2":
        out = _skill_states_v2(s, user_id, at or db.now(), decay, with_predicted, include_agent)
    else:
        out = _skill_states_v1(s, user_id, at or db.now(), decay)
    if memo is not None:
        memo[key] = out
    return out


MEMO = "mastery_memo"


def memoize(s: Session) -> None:
    """这个会话里，同一个学生“现在”的状态只算一次（教师端只读的页面用：一个页面要反复取每个学生的状态）。
    会话里写了新证据还要重算的地方不要开。结果只读，不要改里面的对象。"""
    s.info[MEMO] = {}


def _skill_states_v1(s: Session, user_id: str, at: dt.datetime, decay: bool) -> dict[str, SkillState]:
    lam = config.MASTERY["decay"] if decay else 1.0
    rows = _rows(s, user_id, at)
    out: dict[str, SkillState] = {}
    for r in rows:
        if r.ts > at or r.event_type in V2_ONLY_EVENTS:
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
    days = {r.ts.astimezone(db.BEIJING).date() for r in rows if r.success and r.independent and r.weight > 0 and (r.reliability is None or r.reliability >= 1)}
    fails = sum(1 for r in rows if not r.success and r.weight > 0)
    h = cfg["half_life_base"] * cfg["half_life_growth"] ** (max(0, len(days) - 1) - fails)
    return max(cfg["half_life_min"], min(cfg["half_life_max"], h))


def attempt_history(s: Session, user_id: str, at: dt.datetime) -> dict[tuple[int, int], list[tuple[int, str, str | None]]]:
    """这个学生到 at 为止每道题的提交：(服务器, 题号) → [(提交编号, 评测结果, 这次标出的主错误的技能)]，按编号排。"""
    rows = s.execute(select(m.Submission.server_id, m.Submission.problem_id, m.Submission.submission_id,
                            m.Submission.judgement)
                     .where(m.Submission.user_id == user_id, m.Submission.submit_time <= at)
                     .order_by(m.Submission.submission_id)).all()
    skills: dict[tuple[int, int], str] = {}
    if rows:
        for server_id, submission_id, skill in s.execute(
                select(m.SubmissionLabel.server_id, m.SubmissionLabel.submission_id, m.SubmissionLabel.skill_code)
                .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
                      & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
                .where(m.Submission.user_id == user_id, m.SubmissionLabel.is_current.is_(True),
                       m.SubmissionLabel.role == "primary", m.SubmissionLabel.confidence >= 0.5)).all():
            skills[(int(server_id), int(submission_id))] = skill
    out: dict[tuple[int, int], list[tuple[int, str, str | None]]] = {}
    for server_id, problem_id, submission_id, judgement in rows:
        out.setdefault((int(server_id), int(problem_id)), []).append(
            (int(submission_id), judgement or "", skills.get((int(server_id), int(submission_id)))))
    return out


def resolved_by(row: m.SkillEvidence, history: dict[tuple[int, int], list[tuple[int, str, str | None]]]) -> str | None:
    """这次没通过，后来改对了吗。改对了返回改对的那次提交（pass:服务器:提交 或 fixed:服务器:提交），没改对返回 None。

    两种算改对：这道题后来通过了；或者后来又交了一次，这次标出来的错误已经不在这个技能上（比如缺分号的错改掉了，
    现在错在算法上——缺分号这件事他改对了）。后来那次没标出错误的，不算（不知道改没改对）。
    """
    parts = (row.source_key or "").split(":")
    if row.event_type != "submit_failed" or len(parts) != 4:
        return None
    try:
        server_id, problem_id, submission_id = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    for later, judgement, skill in history.get((server_id, problem_id), []):
        if later <= submission_id or judgement == "PENDING":
            continue
        if judgement == "AC":
            return f"pass:{server_id}:{later}"
        if skill is not None and skill != row.skill_code:
            return f"fixed:{server_id}:{later}"
    return None


def _skill_states_v2(s: Session, user_id: str, at: dt.datetime, decay: bool,
                     with_predicted: bool, include_agent: bool = True) -> dict[str, SkillState]:
    from .cross import weighted_parents

    cfg = config.MASTERY_V2
    history = attempt_history(s, user_id, at)
    by_skill: dict[str, list[m.SkillEvidence]] = {}
    for r in _rows(s, user_id, at):
        if r.event_type in AGENT_EVENTS and not include_agent:
            continue
        by_skill.setdefault(r.skill_code, []).append(r)
    weak_passes = {r.source_key for rows in by_skill.values() for r in rows
                   if r.success and r.event_type.startswith("pass") and r.reliability is not None and r.reliability < 1}
    out: dict[str, SkillState] = {}
    for code, rows in by_skill.items():
        st = SkillState(code, model="v2")
        h = half_life_days([r for r in rows if r.event_type not in AGENT_EVENTS])
        st.half_life = h
        pass_keys = {r.source_key for r in rows if r.success and r.event_type.startswith("pass")}
        episodes = Counter(r.source_key.rsplit(":", 1)[0] for r in rows if r.event_type == "submit_failed")
        credited_fixes = set()
        fixed_problems: set[str] = set()   # 这个技能上改对过的题
        for r in rows:
            days = max(0.0, (at - r.ts).total_seconds() / 86400.0)
            w = r.weight * (r.reliability if r.reliability is not None else 1.0) * cfg["event_scale"].get(r.event_type, 1.0)
            if decay:
                w *= 0.5 ** (days / h)
            if r.event_type in AGENT_EVENTS:   # 智能体评估：只改估计值，不算练过
                if r.success:
                    st.s += w
                else:
                    st.f += w
                continue
            fixed_by = resolved_by(r, history) if r.event_type == "submit_failed" else None
            if fixed_by in weak_passes:
                fixed_by = None
            if fixed_by is not None:
                # 后来改对了：失败只算一小部分；这次通过没有记到这个技能上（错在别的技能），改对它算一次会
                repeated = episodes[r.source_key.rsplit(":", 1)[0]] >= 2
                st.f += w * cfg["repeated_fail_factor" if repeated else "resolved_fail_factor"]
                if fixed_by not in pass_keys and fixed_by not in credited_fixes:
                    st.s += w * cfg["resolved_fix_credit"]
                    credited_fixes.add(fixed_by)
            elif r.success:
                st.s += w
            else:
                st.f += w
                st.f_raw += r.weight   # 还没改对的失败
                problem = ":".join((r.source_key or "").split(":")[1:3]) if r.event_type == "submit_failed" else ""
                if problem and fixed_problems - {problem}:
                    st.recurred = True   # 别的题上改对过，这道新题上又错、还没改对
            if fixed_by is not None:
                fixed_problems.add(":".join(r.source_key.split(":")[1:3]))
            st.raw += r.weight
            st.events += 1
            if r.reliability is not None and r.reliability < 1.0:
                st.discounted += 1
            st.last_ts = r.ts
            st.last_independent_success = bool((r.success and r.independent and r.source_key not in weak_passes)
                                               or fixed_by is not None)
        out[code] = st

    # 图先验：按先修顺序走一遍，先修（有证据或能推出来的）的均值决定起点
    parents, strength = weighted_parents(s)
    base = cfg["prior_strength"]
    informative: dict[str, float] = {}
    for code in topo_order():
        ps = [(informative[p], w) for p, w in parents.get(code, []) if p in informative and w > 0]
        m_k = sum(v * w for v, w in ps) / sum(w for _v, w in ps) if ps else None
        kappa = strength.get(code, base) if m_k is not None else base
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
    score: float | None = None   # 教师端的掌握分（0~1），见 SkillState.score；推出来的没有


def kp_states(s: Session, user_id: str, model: str | None = None, with_predicted: bool = False) -> dict[str, KpState]:
    """粗知识点的档位：下面各技能按证据量加权的平均。"""
    model = model or config.MASTERY_MODEL
    memo = s.info.get(MEMO)
    key = ("kp", user_id, model, with_predicted)
    if memo is not None and key in memo:
        return memo[key]
    out = _kp_states(s, user_id, model, with_predicted)
    if memo is not None:
        memo[key] = out
    return out


def _kp_states(s: Session, user_id: str, model: str, with_predicted: bool) -> dict[str, KpState]:
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
            kp.score = sum((x.s + x.f) * x.score for x in real) / total
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
                                sum(x.s for x in real), sum(x.f_raw for x in real), any(x.recurred for x in real))
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


def record_failed_submission(s: Session, sub: m.Submission, skill_code: str) -> bool:
    """一次没通过的提交（只在 v2 用）。不需要学生问过问题：在 OJ 上交了，就是证据。

    记到出错的那个技能上（可能不是这道题的主技能，比如题考的是求最值，错在下标越界）。
    同一道题上同一个技能，第几次没过分量递减，见 config.MASTERY["w_submit_failed"]。
    """
    if not skill_code:
        return False
    weights = config.MASTERY["w_submit_failed"]
    prefix = f"fail:{sub.server_id}:{sub.problem_id}:"
    earlier = s.scalar(select(func.count()).select_from(m.SkillEvidence).where(
        m.SkillEvidence.user_id == sub.user_id, m.SkillEvidence.skill_code == skill_code,
        m.SkillEvidence.event_type == "submit_failed", m.SkillEvidence.source_key.like(prefix + "%"))) or 0
    if earlier >= len(weights):
        return False
    return add_evidence(s, sub.user_id, skill_code, "submit_failed", False, weights[earlier],
                        f"{prefix}{sub.submission_id}", sub.submit_time or db.now())


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
