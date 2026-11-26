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

2026-11-21 起（MASTERY_VERSION 2026-11-21）：
    - 掌握分 = (1 + S) / (2 + S + F)，只看他自己的证据（不掺图先验）。档位只由掌握分定：75 分以上已掌握，
      50 分以上基本掌握，以下待加强；没有作答是还没练到。不再另看区间下限、“最近一次是不是独立做对”，
      分数和档位总是一致。p（带图先验）照旧，给准备度、图模型、研究对照用。
    - 每类证据的系数、半衰期的起点和增长率不再是写死的数：每周用数据学一次（app/learn.py），学完冻结，
      下一周都用这一版（Params，版本记在 mastery_params）。先验就是原来的经验值，数据少时不动。
      每条证据记成一项“贡献”（类别键、会还是不会、基数、距今天数），S、F 由这一周的参数算出来。
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
from .taxonomy import LEVEL_BASIC, LEVEL_MASTERED, LEVEL_NONE, LEVEL_WEAK, course_of, kp_of_skill

Z80 = 1.2816  # 80% 双侧区间


@dataclass
class Params:
    """掌握程度里要学的参数（app/learn.py 每周学一次）：每类证据的系数（键见 SkillState.contrib，默认 1），
    半衰期的起点 h0 和增长率 g。PRIOR 就是原来的经验值。"""
    version: str = "prior"
    scales: dict[str, float] = field(default_factory=dict)
    h0: float = 0.0
    g: float = 0.0

    def scale(self, key: str) -> float:
        return self.scales.get(key, 1.0)

    def half_life(self, exponent: int) -> float:
        cfg = config.MASTERY_V2
        h0 = self.h0 or cfg["half_life_base"]
        g = self.g or cfg["half_life_growth"]
        return max(cfg["half_life_min"], min(cfg["half_life_max"], h0 * g ** exponent))


PRIOR = Params()
_ACTIVE: dict[str, tuple[float, Params]] = {}


def params_as_of(s: Session, course: str, at: dt.datetime) -> Params:
    row = s.scalar(select(m.MasteryParams).where(m.MasteryParams.course == course, m.MasteryParams.fitted_at <= at)
                   .order_by(m.MasteryParams.fitted_at.desc(), m.MasteryParams.id.desc()).limit(1))
    return _params_from(row)


def _params_from(row: m.MasteryParams | None) -> Params:
    if row is None:
        return PRIOR
    values = row.params or {}
    return Params(row.version, dict(values.get("scales", {})), float(values.get("h0", 0)), float(values.get("g", 0)))


def active_params(s: Session, course: str) -> Params:
    """这门课这一周用的参数（mastery_params 里最新的一版），没有就用经验值。进程里缓存 5 分钟。"""
    import time

    hit = _ACTIVE.get(course)
    if hit is not None and time.monotonic() - hit[0] < 300:
        return hit[1]
    row = s.scalars(select(m.MasteryParams).where(m.MasteryParams.course == course)
                    .order_by(m.MasteryParams.fitted_at.desc(), m.MasteryParams.id.desc()).limit(1)).first()
    prm = _params_from(row)
    _ACTIVE[course] = (time.monotonic(), prm)
    return prm


def forget_active_params() -> None:
    _ACTIVE.clear()
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
    unresolved_submissions: int = 0  # 尚无改正证据的已标注失败提交数
    recurred: bool = False           # v2：在别的题上改对过，现在又在新的题上错、还没改对（改对过又犯）
    # 最近一条“直接”证据：顺带用到的次技能的通过（problem_skills）不算正面也不算反面，不改变它们
    last_direct_ts: dt.datetime | None = None
    last_kind: str = ""              # independent 独立做对 / helped 靠提示或示例才过 / failed 没过 / copied 疑似雷同
    # v2：每条证据的“贡献”(类别键, 会还是不会, 基数, 距今天数)；S、F = Σ 基数 × 这类的系数 × 0.5^(天数/半衰期)
    contrib: list = field(default_factory=list, repr=False)
    hl_exp: int = 0                  # 半衰期的指数：间隔开的独立成功天数 − 1 − 失败次数
    params_version: str = ""

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
        """掌握分：(1 + S) / (2 + S + F)，只看他自己的证据（做题、会话、小题），不掺图先验。
        起点 Beta(1, 1)：一次独立做对 67 分，连对两次 75 分，一次没过 33 分。档位就按它定。"""
        return (1.0 + self.s) / (2.0 + self.s + self.f)

    @property
    def level(self) -> str:
        if self.model == "v1":
            return level_of(self.p, self.s + self.f, self.raw, self.last_independent_success)
        return level_from_score(self.score, self.events, self.raw)


def level_from_score(score: float | None, events: int, raw: float) -> str:
    """档位只看掌握分（四舍五入到整数分，和页面上显示的一样）：75 以上已掌握，50 以上基本掌握，以下待加强。
    没有作答（或证据量太小）是还没练到。"""
    cfg = config.MASTERY_V2
    if score is None or events == 0 or raw < cfg["min_raw"]:
        return LEVEL_NONE
    points = round(score * 100)
    if points >= cfg["mastered_score"]:
        return LEVEL_MASTERED
    if points >= cfg["basic_score"]:
        return LEVEL_BASIC
    return LEVEL_WEAK


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
    memo = s.info.get(MEMO, {})
    if ("rows", user_id) in memo:
        return [r for r in memo["rows", user_id] if r.ts <= at]
    rows = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.user_id == user_id)
                     .order_by(m.SkillEvidence.ts, m.SkillEvidence.id)).all()
    return [r for r in rows if r.ts <= at]


def skill_states(s: Session, user_id: str, at: dt.datetime | None = None, decay: bool = True,
                 model: str | None = None, with_predicted: bool = False, include_agent: bool = True,
                 params: Params | None = None, graph_prior: bool = True, *, replay: bool = False) -> dict[str, SkillState]:
    """每个有证据的技能的状态。model 不填就用 config.MASTERY_MODEL。

    with_predicted（只对 v2）：把没有证据、但能由先修推出来的技能也带上（predicted=True），用于数据结构准备度。
    include_agent（只对 v2）：用不用智能体评估的证据。
    params（只对 v2）：用哪一版参数；不填用每门课这一周的（active_params）。PRIOR 是经验值（对照用）。
    graph_prior（只对 v2）：算不算图先验（每周学参数时只要贡献，不算它）。
    """
    model = model or config.MASTERY_MODEL
    memo = s.info.get(MEMO) if at is None and params is None and graph_prior else None
    key = ("skill", user_id, decay, model, with_predicted, include_agent)
    if memo is not None and key in memo:
        return memo[key]
    if model == "v2":
        out = _skill_states_v2(s, user_id, at or db.now(), decay, with_predicted, include_agent, params, graph_prior, replay)
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


def preload(s: Session, user_ids: list[str]) -> None:
    """只读班级页批量取证据和提交历史，避免每个学生各查三次。仍按各次计算的截止时间筛选。

    保留学生在所有服务器上的历史，数据结构的 C 语言基础也要用；不改变历史回放的标注口径。
    和 memoize 一样，只用于不会写证据的会话，缓存随数据库会话结束释放。
    """
    memo = s.info.get(MEMO)
    if memo is None:
        return
    missing = list(dict.fromkeys(uid for uid in user_ids if ("rows", uid) not in memo))
    for offset in range(0, len(missing), 400):
        batch = missing[offset:offset + 400]
        for uid in batch:
            memo["rows", uid] = []
            memo["attempt_rows", uid] = []
            memo["attempt_labels", uid] = []
        for row in s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.user_id.in_(batch))
                             .order_by(m.SkillEvidence.ts, m.SkillEvidence.id)):
            memo["rows", row.user_id].append(row)
        for uid, *row in s.execute(select(
                m.Submission.user_id, m.Submission.server_id, m.Submission.problem_id, m.Submission.submission_id,
                m.Submission.judgement, m.Submission.submit_time).where(m.Submission.user_id.in_(batch))
                .order_by(m.Submission.submission_id)):
            memo["attempt_rows", uid].append(row)
        for uid, *row in s.execute(select(
                m.Submission.user_id, m.SubmissionLabel.server_id, m.SubmissionLabel.submission_id,
                m.SubmissionLabel.skill_code, m.SubmissionLabel.confidence, m.Submission.submit_time)
                .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
                      & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
                .where(m.Submission.user_id.in_(batch), m.SubmissionLabel.is_current.is_(True),
                       m.SubmissionLabel.role == "primary")
                .order_by(m.SubmissionLabel.created_at, m.SubmissionLabel.id)):
            memo["attempt_labels", uid].append(row)


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
        if not incidental(r):
            st.last_direct_ts = r.ts
            st.last_independent_success = bool(r.success and r.independent)
    return out


def incidental(r: m.SkillEvidence) -> bool:
    """顺带用到的次技能的通过（problem_skills 记成不算独立成功的那种）：不算正面也不算反面，
    不改变“最近一次是不是独立做对”。以前每道题都会顺带用到的技能（算术运算、输出），做题多的同学最近一条
    证据几乎总是这种，结果分数很高却到不了“已掌握”。"""
    return bool(r.success and not r.independent and r.event_type in ("pass_first_try", "pass_self_fixed"))


def kind_of(r: m.SkillEvidence, weak_passes: set[str], fixed: bool) -> str:
    if fixed or (r.success and r.independent and r.source_key not in weak_passes):
        return "independent"
    if r.success and r.source_key in weak_passes:
        return "copied"
    return "helped" if r.success else "failed"


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


def half_life_exponent(rows: list[m.SkillEvidence]) -> int:
    """半衰期的指数：间隔开的独立成功（按北京时间的不同日期计）多一天加 1，每次失败减 1。"""
    days = {r.ts.astimezone(db.BEIJING).date() for r in rows if r.success and r.independent and r.weight > 0 and (r.reliability is None or r.reliability >= 1)}
    fails = sum(1 for r in rows if not r.success and r.weight > 0)
    return max(0, len(days) - 1) - fails


def half_life_days(rows: list[m.SkillEvidence], params: Params | None = None) -> float:
    """证据半衰期：起点 h0 乘增长率 g 的指数次方（指数见 half_life_exponent），夹在上下限之间。"""
    return (params or PRIOR).half_life(half_life_exponent(rows))


def apply_params(st: SkillState, params: Params, decay: bool = True) -> None:
    """按这一版参数由贡献算出 S、F。"""
    h = params.half_life(st.hl_exp)
    st.half_life, st.params_version = h, params.version
    st.s = st.f = 0.0
    for key, success, base, age in st.contrib:
        w = base * params.scale(key) * (0.5 ** (age / h) if decay else 1.0)
        if success:
            st.s += w
        else:
            st.f += w


def attempt_history(s: Session, user_id: str, at: dt.datetime, *, historical: bool = False) -> dict[tuple[int, int], list[tuple[int, str, str | None]]]:
    """这个学生到 at 为止每道题的提交：(服务器, 题号) → [(提交编号, 评测结果, 这次标出的主错误的技能)]，按编号排。"""
    memo = s.info.get(MEMO, {})
    if not historical and ("attempt_rows", user_id) in memo:
        skills = {}
        for server_id, submission_id, skill, confidence, when in memo["attempt_labels", user_id]:
            if when is not None and when <= at:
                key = (int(server_id), int(submission_id))
                if confidence >= 0.5:
                    skills[key] = skill
                else:
                    skills.pop(key, None)
        out = {}
        for server_id, problem_id, submission_id, judgement, when in memo["attempt_rows", user_id]:
            if when is not None and when <= at:
                out.setdefault((int(server_id), int(problem_id)), []).append(
                    (int(submission_id), judgement or "", skills.get((int(server_id), int(submission_id)))))
        return out
    rows = s.execute(select(m.Submission.server_id, m.Submission.problem_id, m.Submission.submission_id,
                            m.Submission.judgement)
                     .where(m.Submission.user_id == user_id, m.Submission.submit_time <= at)
                     .order_by(m.Submission.submission_id)).all()
    skills: dict[tuple[int, int], str] = {}
    if rows:
        for server_id, submission_id, skill, confidence in s.execute(
                select(m.SubmissionLabel.server_id, m.SubmissionLabel.submission_id, m.SubmissionLabel.skill_code,
                       m.SubmissionLabel.confidence)
                .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
                      & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
                .where(m.Submission.user_id == user_id, m.Submission.submit_time <= at,
                       m.SubmissionLabel.created_at <= at if historical else m.SubmissionLabel.is_current.is_(True),
                       m.SubmissionLabel.role == "primary").order_by(m.SubmissionLabel.created_at, m.SubmissionLabel.id)):
            key = (int(server_id), int(submission_id))
            if confidence >= 0.5:
                skills[key] = skill
            else:
                skills.pop(key, None)
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
                     with_predicted: bool, include_agent: bool = True, params: Params | None = None,
                     graph_prior: bool = True, historical: bool = False) -> dict[str, SkillState]:
    from .cross import weighted_parents

    cfg = config.MASTERY_V2
    by_course: dict[str, Params] = {}

    def params_for(code: str) -> Params:
        if params is not None:
            return params
        c = course_of(code)
        if c not in by_course:
            by_course[c] = params_as_of(s, c, at) if historical else active_params(s, c)
        return by_course[c]
    history = attempt_history(s, user_id, at, historical=historical)
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
        st.hl_exp = half_life_exponent([r for r in rows if r.event_type not in AGENT_EVENTS])
        pass_keys = {r.source_key for r in rows if r.success and r.event_type.startswith("pass")}
        episodes = Counter(r.source_key.rsplit(":", 1)[0] for r in rows if r.event_type == "submit_failed")
        credited_fixes = set()
        fixed_problems: set[str] = set()   # 这个技能上改对过的题
        for r in rows:
            days = max(0.0, (at - r.ts).total_seconds() / 86400.0)
            w = r.weight * (r.reliability if r.reliability is not None else 1.0) * cfg["event_scale"].get(r.event_type, 1.0)
            sign = "+" if r.success else "-"
            if r.event_type in AGENT_EVENTS:   # 智能体评估：只改估计值，不算练过
                st.contrib.append((r.event_type + sign, bool(r.success), w, days))
                continue
            fixed_by = resolved_by(r, history) if r.event_type == "submit_failed" else None
            if fixed_by in weak_passes:
                fixed_by = None
            if fixed_by is not None:
                # 后来改对了：失败只算一小部分；这次通过没有记到这个技能上（错在别的技能），改对它算一次会
                repeated = episodes[r.source_key.rsplit(":", 1)[0]] >= 2
                st.contrib.append(("submit_failed:repeated-" if repeated else "submit_failed:resolved-", False,
                                   w * cfg["repeated_fail_factor" if repeated else "resolved_fail_factor"], days))
                if fixed_by not in pass_keys and fixed_by not in credited_fixes:
                    st.contrib.append(("fix_credit+", True, w * cfg["resolved_fix_credit"], days))
                    credited_fixes.add(fixed_by)
            elif r.success:
                st.contrib.append((r.event_type + sign, True, w, days))
            else:
                st.contrib.append((r.event_type + sign, False, w, days))
                st.f_raw += r.weight   # 还没改对的失败
                problem = ":".join((r.source_key or "").split(":")[1:3]) if r.event_type == "submit_failed" else ""
                if problem:
                    st.unresolved_submissions += 1
                if problem and fixed_problems - {problem}:
                    st.recurred = True   # 别的题上改对过，这道新题上又错、还没改对
            if fixed_by is not None:
                fixed_problems.add(":".join(r.source_key.split(":")[1:3]))
            st.raw += r.weight
            st.events += 1
            if r.reliability is not None and r.reliability < 1.0:
                st.discounted += 1
            st.last_ts = r.ts
            if not incidental(r):
                st.last_direct_ts = r.ts
                st.last_kind = kind_of(r, weak_passes, fixed_by is not None)
                st.last_independent_success = st.last_kind == "independent"
        apply_params(st, params_for(code), decay)
        out[code] = st
    if not graph_prior:
        return out

    # 图先验：按先修顺序走一遍，先修（有证据或能推出来的）的均值决定起点
    memo = s.info.get(MEMO)
    if memo is not None and "parents" in memo:
        parents, strength = memo["parents"]
    else:
        parents, strength = weighted_parents(s)
        if memo is not None:
            memo["parents"] = (parents, strength)
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
    score: float | None = None   # 掌握分（0~1），见 SkillState.score；推出来的没有。v2 的档位就按它定
    last_kind: str = ""          # 这个知识点上最近一条直接证据是哪种（SkillState.last_kind）


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
        # 按最近一条直接证据定（顺带用到的次技能的通过不算）
        oldest = dt.datetime.min.replace(tzinfo=db.UTC)
        direct = [x for x in real if x.last_direct_ts is not None] or real   # 只有顺带用到的技能不参与
        latest = max(direct, key=lambda x: x.last_direct_ts or x.last_ts or oldest)
        kp.last_kind = latest.last_kind
        if model == "v2":
            kp.lower = (sum((x.s + x.f) * x.interval[0] for x in real) / total) if total > 0 else min(x.interval[0] for x in real)
            if kp.score is None:   # 证据都衰减没了：按各技能的分数平均
                kp.score = sum(x.score for x in real) / len(real)
            kp.level = level_from_score(kp.score, sum(x.events for x in real), kp.raw)
        else:
            kp.level = level_of(kp.p, kp.evidence, kp.raw, latest.last_independent_success)
    return out


# ------------------------------------------------------------ 各类事件怎么记
def tested_secondary(s: Session, label: m.ProblemLabel | None) -> set[str]:
    """次技能里有测试点专门考它的（标注时每个测试点标了主要考哪个技能）。按 XDOJ 标签兜底的标注没有测试点，一个也没有。"""
    if label is None or not label.secondary_skills or label.id is None:
        return set()
    cases = set(s.scalars(select(m.TestcaseLabel.skill_code).where(m.TestcaseLabel.problem_label_id == label.id)).all())
    return {c for c in label.secondary_skills if c in cases}


def problem_skills(label: m.ProblemLabel | None, tested: set[str] | frozenset = frozenset()) -> list[tuple[str, float, bool]]:
    """一道题通过时证据记到哪些技能：(技能, 分量的比例, 能不能算独立成功)。
    主技能全额；次技能里有测试点专门考的按 secondary_tested_factor，只是顺带用到的按 secondary_incidental_factor、
    不算独立成功（不让半衰期变长，也不能凭它到“已掌握”）。"""
    if label is None:
        return []
    cfg = config.MASTERY
    out = [(label.primary_skill, 1.0, True)]
    for c in label.secondary_skills or []:
        if c == label.primary_skill or any(c == x[0] for x in out):
            continue
        out.append((c, cfg["secondary_tested_factor"], True) if c in tested
                   else (c, cfg["secondary_incidental_factor"], False))
    return out


def follow_relabel(s: Session, server_id: int, problem_id: int, label: m.ProblemLabel | None) -> int:
    """一道题的标注换了（技能表改了、按标签兜底的换成大模型标的）：这道题以前每次“通过”的证据按新标注重记。
    事件、分量的基数（原来主技能那条）、是不是独立、可信度、时间都不变，只是记到新标注的技能上、按主次重新乘系数。
    没通过的证据记在出错的那个技能上（按错误标注），不跟题目标注走，不动。返回重记了几次通过。"""
    if label is None:
        return 0
    subs = s.scalars(select(m.Submission.submission_id).where(
        m.Submission.server_id == server_id, m.Submission.problem_id == problem_id,
        m.Submission.judgement == "AC")).all()
    if not subs:
        return 0
    keys = [f"pass:{server_id}:{x}" for x in subs]
    rows = s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.source_key.in_(keys),
                                                   m.SkillEvidence.success.is_(True))).all()
    groups: dict[str, list[m.SkillEvidence]] = {}
    for r in rows:
        groups.setdefault(r.source_key, []).append(r)
    new = problem_skills(label, tested_secondary(s, label))
    want = {code for code, _f, _i in new}
    done = 0
    for key, group in groups.items():
        if {r.skill_code for r in group} == want:
            continue
        base = max(group, key=lambda r: r.weight)   # 原来的主技能（系数 1）
        user, event, weight, independent, ts, reliability = (base.user_id, base.event_type, base.weight,
                                                             base.independent, base.ts, base.reliability)
        for r in group:
            s.delete(r)
        s.flush()
        for code, factor, counts_independent in new:
            add_evidence(s, user, code, event, True, weight * factor, key, ts, independent and counts_independent,
                         reliability)
        done += 1
    return done


def reweigh_passes(s: Session, limit: int = 500) -> int:
    """以前的版本（次技能一律按 1.0、算独立成功）记下的“通过”证据，按现在的规则重算分量（后台空闲时做，全用程序）。
    返回改了几条。主技能的只改版本号；标注已经换过、这个技能不在当前标注里的，分量不动。"""
    from .labeling.labeler import current_problem_label

    # 只重算次技能还按 1.0 记的那些（版本号都是日期，按字符串比先后）；之后改版本号不会重复乘
    rows = s.scalars(select(m.SkillEvidence).where(
        m.SkillEvidence.event_type.like("pass%"), m.SkillEvidence.algo_version < config.SECONDARY_SPLIT_VERSION)
        .order_by(m.SkillEvidence.id).limit(limit)).all()
    labels: dict[tuple[int, int], tuple[m.ProblemLabel | None, set[str]]] = {}
    for r in rows:
        parts = (r.source_key or "").split(":")
        sub = s.get(m.Submission, (int(parts[1]), int(parts[2]))) if len(parts) == 3 and parts[0] == "pass" \
            and parts[1].isdigit() and parts[2].isdigit() else None
        if sub is not None:
            key = (sub.server_id, sub.problem_id)
            if key not in labels:
                lab = current_problem_label(s, *key)
                labels[key] = (lab, tested_secondary(s, lab))
            lab, tested = labels[key]
            if lab is not None and r.skill_code != lab.primary_skill and r.skill_code in (lab.secondary_skills or []):
                cfg = config.MASTERY
                if r.skill_code in tested:
                    r.weight = round(r.weight * cfg["secondary_tested_factor"], 4)
                else:
                    r.weight, r.independent = round(r.weight * cfg["secondary_incidental_factor"], 4), False
        r.algo_version = config.MASTERY_VERSION
    return len(rows)


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
    for code, factor, counts_independent in problem_skills(label, tested_secondary(s, label)):
        add_evidence(s, user_id, code, event, True, weight * factor, source_key, ts,
                     independent and counts_independent, reliability)
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
