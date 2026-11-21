"""多维掌握画像：一个学生对每个知识点掌握得怎么样，从几个方面分别看，再融合成一个数。

只看做题对错，分不出“会做”和“懂了”、“自己做出来”和“看了提示才做出来”、“现在会”和“过几天还会”。
所以分成六个维度，每个维度的数据来源和算法都不同（多维度、多方法）：

| 维度 | 看什么 | 怎么算 |
|---|---|---|
| 做题 perf | 评测结果、提交次数、求助级别、雷同 | 掌握程度算法 v2（Beta 后验，带遗忘和图先验） |
| 理解 understanding | 对话里学生自己说的话；没问过问题的，看他自己是怎么改代码的 | 智能体按量规评 0~3 档（assess.py），按置信度和时间加权平均 |
| 独立 independence | 通过之前要了多少帮助 | 一次做对 1、自己改对 0.85、反问或指位置后 0.6、讲原理后 0.3、给示例代码后 0 |
| 改错 debugging | OJ 上的提交过程（不需要问过问题） | 没过的题怎么改对的：交了几次、同一个错误反复没有、是不是急着重交（process.py） |
| 保持 retention | 复习记录 | 半衰期回归：2^(−距上次成功的天数 / 半衰期) |
| 自知 calibration | 小题前选的把握和实际对错 | 1 − 平均 |把握 − 对错|；偏差为正是高估自己 |
| 先修 prereq | 先修知识点的掌握程度 | 知识图上先修技能的 v2 估计的平均（图节点辅助）；另附贝叶斯网络的后验作对照 |

融合（fused）：把有数据的维度取 logit 加权相加，再变回概率。权重一开始用经验值，攒够数据后按
“下一次做新题能不能一次做对”用带收缩的逻辑回归学（每门课一组，每天夜里更新），
所以哪个维度更能预测表现，就占更大的分量；数据少时权重往经验值收缩，不会乱跳。
学到的权重和融合模型的预测准确度在教师端“模型对比”页公开。

教师端的模型检验和学生明细使用这些维度。
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db, mastery, review
from . import models as m
from .chat.analyze import ACTS, CONSTRUCTIVE_ACTS
from .taxonomy import KNOWLEDGE_POINTS, LEVEL_NONE, course_of, kp_of_skill, kp_rank

log = logging.getLogger("profile")

DIM_NAMES = {"perf": "做题", "understanding": "理解", "independence": "独立", "debugging": "改错", "retention": "保持",
             "calibration": "自知", "prereq": "前置知识"}
FLAG_TEXT = {   # 教师端的说法
    "explain_gap": "会做，但讲不清为什么",
    "help_reliant": "做出来的题多半靠提示",
    "fading": "可能快忘了",
    "overconfident": "自以为会：有把握的小题答错过",
    "weak_prereq": "前置知识没打牢",
    "specific_gap": "前置知识都扎实，这里是具体的缺口",
    "trial_error": "改错靠反复试：同一个错误一再出现，或者急着重交",
}


def _clip(p: float) -> float:
    return min(0.95, max(0.05, p))


def logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


@dataclass
class Dims:
    perf: float | None = None
    perf_lower: float | None = None
    level: str = LEVEL_NONE
    understanding: float | None = None
    n_assess: int = 0
    independence: float | None = None
    n_pass: float = 0.0
    debugging: float | None = None
    n_episodes: int = 0
    retention: float | None = None
    n_review: int = 0
    calibration: float | None = None
    bias: float | None = None
    n_quiz: int = 0
    prereq: float | None = None
    graph: float | None = None
    weight: float = 1.0           # 汇总到知识点时的分量（证据量）
    fused: float | None = None    # 多维融合
    flags: list[str] = field(default_factory=list)

    def get(self, dim: str) -> float | None:
        return getattr(self, dim)


# ------------------------------------------------------------ 各维度
class Sources:
    """一个学生算画像要用的数据，一次取齐。at：算哪一刻的（预测记录要用过去的时刻）。"""

    def __init__(self, s: Session, user_id: str, at: dt.datetime | None = None, with_graph: bool = True):
        self.s, self.user_id, self.at = s, user_id, at or db.now()
        self.states = mastery.skill_states(s, user_id, at=at, model="v2", with_predicted=True)   # at 为空：现在
        self.evidence = [r for r in s.scalars(select(m.SkillEvidence).where(m.SkillEvidence.user_id == user_id)).all()
                         if r.ts <= self.at]
        self.assessments = [a for a in s.scalars(select(m.AgentAssessment).where(
            m.AgentAssessment.user_id == user_id, m.AgentAssessment.level.is_not(None))).all() if a.created_at <= self.at]
        self.reviews = list(s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == user_id)).all())
        self.quizzes = [q for q in s.scalars(select(m.QuizAttempt).where(
            m.QuizAttempt.user_id == user_id, m.QuizAttempt.state == "done", m.QuizAttempt.skipped.is_(False),
            m.QuizAttempt.confidence != "")).all() if q.correct is not None and q.created_at <= self.at]
        self.graph = {r.skill_code: r.p for r in s.scalars(select(m.GraphPosterior).where(
            m.GraphPosterior.user_id == user_id, m.GraphPosterior.model_version == config.GRAPH_VERSION)).all()} if with_graph else {}
        from .cross import weighted_parents
        from .process import episodes

        self.parents, _ = weighted_parents(s)   # 数据结构的技能带上采用的跨课程联系（按类型和数据检验加权）
        self.episodes = [e for e in episodes(s, user_id, self.at) if e.score is not None]   # OJ 上的改错过程


def _understanding(src: Sources, skill: str) -> tuple[float | None, int]:
    h = config.PROFILE["understanding_half_life"]
    num = den = 0.0
    n = 0
    for a in src.assessments:
        if a.skill_code != skill:
            continue
        days = max(0.0, (src.at - a.created_at).total_seconds() / 86400.0)
        w = max(a.confidence, 0.1) * 0.5 ** (days / h)
        num += w * (a.level / 3.0)
        den += w
        n += 1
    return (num / den if den > 0 else None), n


def _independence(src: Sources, skill: str) -> tuple[float | None, float]:
    table = config.PROFILE["independence_weight"]
    num = den = 0.0
    for r in src.evidence:
        if r.skill_code == skill and r.event_type in table:
            w = max(r.weight, 0.1)   # 次技能的证据分量小
            num += w * table[r.event_type]
            den += w
    return (num / den if den > 0 else None), den


def _debugging(src: Sources, skill: str) -> tuple[float | None, int]:
    vals = [e.score for e in src.episodes if skill in e.skills]
    return (sum(vals) / len(vals) if vals else None), len(vals)


def _retention(src: Sources, skill: str) -> tuple[float | None, int]:
    vals = []
    for item in src.reviews:
        if item.skill_code != skill or item.created_at > src.at:
            continue
        if item.state == review.GRADUATED:
            vals.append(1.0)
        else:
            vals.append(review.retention(item, src.at))
    return (sum(vals) / len(vals) if vals else None), len(vals)


def _calibration(src: Sources, skill: str) -> tuple[float | None, float | None, int]:
    value = config.PROFILE["calibration_value"]
    pairs = [(value.get(q.confidence), 1.0 if q.correct else 0.0) for q in src.quizzes if q.skill_code == skill]
    pairs = [(c, y) for c, y in pairs if c is not None]
    if len(pairs) < 2:
        return None, None, len(pairs)
    return (1 - sum(abs(c - y) for c, y in pairs) / len(pairs), sum(c - y for c, y in pairs) / len(pairs), len(pairs))


def _prereq(src: Sources, skill: str) -> float | None:
    ps = [(src.states[p].p, w) for p, w in src.parents.get(skill, []) if p in src.states and w > 0]
    return sum(v * w for v, w in ps) / sum(w for _v, w in ps) if ps else None


def skill_dims(src: Sources, skill: str) -> Dims:
    d = Dims()
    st = src.states.get(skill)
    if st is not None and not st.predicted:
        d.perf, d.perf_lower, d.level = st.p, st.interval[0], st.level
        d.weight = max(1.0, st.raw)
    d.understanding, d.n_assess = _understanding(src, skill)
    d.independence, d.n_pass = _independence(src, skill)
    d.debugging, d.n_episodes = _debugging(src, skill)
    d.retention, d.n_review = _retention(src, skill)
    d.calibration, d.bias, d.n_quiz = _calibration(src, skill)
    d.prereq = _prereq(src, skill)
    d.graph = src.graph.get(skill)
    return d


def _flags(d: Dims) -> list[str]:
    out = []
    if d.perf is not None and d.perf >= 0.6 and d.understanding is not None and d.understanding < 0.4:
        out.append("explain_gap")
    if d.independence is not None and d.independence < 0.5 and d.n_pass >= 1 and (d.perf or 0) >= 0.5:
        out.append("help_reliant")
    if d.retention is not None and d.retention < 0.5:
        out.append("fading")
    if d.bias is not None and d.bias > 0.25 and d.n_quiz >= 2:
        out.append("overconfident")
    if d.prereq is not None and d.prereq < 0.45 and d.perf is not None:
        out.append("weak_prereq")
    if d.prereq is not None and d.prereq >= 0.7 and d.perf is not None and d.perf < 0.45:
        out.append("specific_gap")
    if d.debugging is not None and d.debugging < 0.4 and d.n_episodes >= 2:
        out.append("trial_error")
    return out


def _merge(parts: list[Dims]) -> Dims:
    """几个技能汇总成知识点：每个维度在有数据的技能里按证据量加权平均。"""
    out = Dims()
    for dim in ("perf", "perf_lower", "understanding", "independence", "debugging", "retention", "calibration", "bias",
                "prereq", "graph"):
        vals = [(p.get(dim), p.weight) for p in parts if p.get(dim) is not None]
        if vals:
            setattr(out, dim, sum(v * w for v, w in vals) / sum(w for _v, w in vals))
    out.n_assess = sum(p.n_assess for p in parts)
    out.n_pass = sum(p.n_pass for p in parts)
    out.n_episodes = sum(p.n_episodes for p in parts)
    out.n_review = sum(p.n_review for p in parts)
    out.n_quiz = sum(p.n_quiz for p in parts)
    out.weight = sum(p.weight for p in parts)
    return out


# ------------------------------------------------------------ 融合
def features(d: Dims) -> dict[str, float]:
    """融合用的特征：有数据的维度取 logit，没有数据的记 0（不往任何一边推）。"""
    out = {}
    for dim in config.PROFILE["dims"]:
        v = d.get(dim)
        out[dim] = round(logit(v), 4) if v is not None else 0.0
    return out


def weights_for(s: Session, course: str) -> dict[str, float]:
    row = s.get(m.ModelWeights, course)
    return dict(row.weights) if row is not None and row.weights else dict(config.PROFILE["default_weights"])


def fuse(d: Dims, weights: dict[str, float]) -> float | None:
    if d.perf is None:
        return None
    z = weights.get("bias", 0.0) + sum(weights.get(k, 0.0) * v for k, v in features(d).items())
    return sigmoid(z)


def fit_weights(samples: list[tuple[dict[str, float], bool]], prior: dict[str, float], ridge: float,
                iters: int = 600, lr: float = 0.3) -> dict[str, float]:
    """带收缩的逻辑回归：最小化 对数损失之和 + ridge·Σ(w − 先验)²。样本少，用梯度下降就够了。"""
    keys = ["bias", *config.PROFILE["dims"]]
    w = {k: prior.get(k, 0.0) for k in keys}
    n = len(samples)
    if n == 0:
        return w
    for _ in range(iters):
        grad = {k: 2 * ridge * (w[k] - prior.get(k, 0.0)) for k in keys}
        for x, y in samples:
            p = sigmoid(w["bias"] + sum(w[k] * x.get(k, 0.0) for k in keys if k != "bias"))
            err = p - (1.0 if y else 0.0)
            grad["bias"] += err
            for k in keys[1:]:
                grad[k] += err * x.get(k, 0.0)
        for k in keys:
            w[k] -= lr * grad[k] / (n + 2 * ridge)   # 按样本数加收缩力度归一，样本少、收缩强时也不会发散
    return {k: round(v, 4) for k, v in w.items()}


def refit(s: Session) -> dict[str, dict]:
    """每天夜里：用已经记下的预测（那一刻的各维度和后来的结果）重新学每门课的权重。"""
    cfg = config.PROFILE
    rows = s.scalars(select(m.PredictionLog)).all()
    by_course: dict[str, list[tuple[dict, bool]]] = defaultdict(list)
    for r in rows:
        if r.features:
            by_course[course_of(r.skill_code)].append((r.features, bool(r.outcome)))
    out = {}
    for course in ("C", "D"):
        samples = by_course.get(course, [])
        learned = len(samples) >= cfg["min_samples"]
        w = fit_weights(samples, cfg["default_weights"], cfg["ridge"]) if learned else dict(cfg["default_weights"])
        brier = None
        if samples:
            brier = round(sum((sigmoid(w["bias"] + sum(w[k] * x.get(k, 0.0) for k in cfg["dims"]))
                               - (1.0 if y else 0.0)) ** 2 for x, y in samples) / len(samples), 4)
        row = s.get(m.ModelWeights, course)
        if row is None:
            row = m.ModelWeights(course=course)
            s.add(row)
        row.weights, row.n, row.brier, row.learned, row.updated_at = w, len(samples), brier, learned, db.now()
        out[course] = {"weights": w, "n": len(samples), "learned": learned, "brier": brier}
    return out


def prediction_snapshot(s: Session, user_id: str, skill: str, at: dt.datetime) -> tuple[dict, float | None, float]:
    """预测记录用：那一刻这个技能的各维度特征、融合的预测、v2 去掉智能体评估的预测。"""
    src = Sources(s, user_id, at, with_graph=False)
    d = skill_dims(src, skill)
    if d.perf is None:   # 新技能：做题维度用先修推出来的准备度
        st = src.states.get(skill)
        d.perf = st.p if st is not None else 0.5
    base = mastery.skill_states(s, user_id, at=at, model="v2", with_predicted=True, include_agent=False).get(skill)
    feats = features(d)
    return feats, fuse(d, weights_for(s, course_of(skill))), (base.p if base is not None else 0.5)


# ------------------------------------------------------------ 画像
def kp_profile(s: Session, user_id: str, course: str, at: dt.datetime | None = None) -> dict[str, Dims]:
    """这门课每个有数据的知识点的画像。"""
    src = Sources(s, user_id, at)
    skills: set[str] = {c for c, st in src.states.items() if not st.predicted}
    skills |= {a.skill_code for a in src.assessments} | {r.skill_code for r in src.reviews}
    skills |= {c for e in src.episodes for c in e.skills}   # 只在 OJ 上交过题（没问过问题）的也要有
    by_kp: dict[str, list[Dims]] = defaultdict(list)
    for code in skills:
        if course_of(code) == course:
            by_kp[kp_of_skill(code)].append(skill_dims(src, code))
    w = weights_for(s, course)
    out = {}
    kp_all = mastery.kp_states(s, user_id)
    for kp, parts in by_kp.items():
        d = _merge(parts)
        kp_state = kp_all.get(kp)
        d.level = kp_state.level if kp_state is not None else LEVEL_NONE
        d.flags = _flags(d)
        d.fused = fuse(d, w)
        out[kp] = d
    return out


class _Light:
    """explain_gaps 用：只有做题的状态和智能体评的理解（_understanding 要的两样）。"""

    def __init__(self, states: dict, assessments: list, at: dt.datetime):
        self.states, self.assessments, self.at = states, assessments, at


def explain_gaps(s: Session, states_by_uid: dict[str, dict], course: str) -> dict[str, list[str]]:
    """全班“会做但讲不清”的知识点（和 kp_profile 的 explain_gap 同一个规则、同一个结果）。
    这条规则只用做题和理解两维；kp_profile 要把七个维度都取一遍，班级总览给每个学生各算一次要好几秒。
    states_by_uid：每个学生现在的技能状态（班级页已经算好的）。"""
    if not states_by_uid:
        return {}
    at = db.now()
    rows = s.scalars(select(m.AgentAssessment).where(
        m.AgentAssessment.user_id.in_(list(states_by_uid)), m.AgentAssessment.level.is_not(None))).all()
    by_uid: dict[str, list] = defaultdict(list)
    for a in rows:
        if a.created_at <= at:
            by_uid[a.user_id].append(a)
    out: dict[str, list[str]] = {}
    for uid, states in states_by_uid.items():
        src = _Light(states, by_uid.get(uid, []), at)
        skills = {c for c, st in states.items() if not st.predicted} | {a.skill_code for a in src.assessments}
        by_kp: dict[str, list[Dims]] = defaultdict(list)
        for code in skills:
            if course_of(code) != course:
                continue
            d = Dims()
            st = states.get(code)
            if st is not None and not st.predicted:
                d.perf, d.weight = st.p, max(1.0, st.raw)
            d.understanding, d.n_assess = _understanding(src, code)
            by_kp[kp_of_skill(code)].append(d)
        out[uid] = sorted(kp for kp, parts in by_kp.items() if "explain_gap" in _flags(_merge(parts)))
    return out


def _r(x: float | None, nd: int = 2) -> float | None:
    return round(x, nd) if x is not None else None


def teacher_view(s: Session, user_id: str, course: str) -> list[dict]:
    rows = []
    for kp, d in sorted(kp_profile(s, user_id, course).items(), key=lambda kv: kp_rank(kv[0])):
        rows.append({
            "code": kp, "name": KNOWLEDGE_POINTS.get(kp, ""), "level": d.level, "perf": _r(d.perf),
            "perfLower": _r(d.perf_lower), "understanding": _r(d.understanding), "nAssess": d.n_assess,
            "independence": _r(d.independence), "debugging": _r(d.debugging), "nEpisodes": d.n_episodes,
            "retention": _r(d.retention), "calibration": _r(d.calibration),
            "bias": _r(d.bias), "prereq": _r(d.prereq), "graph": _r(d.graph), "fused": _r(d.fused),
            "flags": [{"code": f, "text": FLAG_TEXT[f]} for f in d.flags]})
    return rows


def tutor_note(s: Session, user_id: str, skill: str) -> str:
    """给对话里的大模型看的一句话：这个学生在这个知识点上的画像（学生看不到）。"""
    if not skill:
        return ""
    d = kp_profile(s, user_id, course_of(skill)).get(kp_of_skill(skill))
    if d is None:
        return ""
    parts = [f"做题：{d.level}"]
    if d.understanding is not None:
        parts.append("理解：" + ("讲得清原因" if d.understanding >= 0.6 else "能照做但讲不清" if d.understanding >= 0.3 else "还不理解"))
    if d.independence is not None:
        parts.append("独立：" + ("多数自己做出来" if d.independence >= 0.7 else "常要提示"))
    tips = [FLAG_TEXT[f] for f in d.flags if f != "fading"]
    if tips:
        parts.append("；".join(tips[:2]))
    return "，".join(parts)


# ------------------------------------------------------------ 对话行为
def dialogue_profile(s: Session, user_id: str, course: str) -> dict:
    steps = s.scalars(select(m.ChatStep).where(m.ChatStep.user_id == user_id, m.ChatStep.course_id == course)).all()
    acts = [st.act for st in steps if st.act]
    n = len(acts)
    counts = defaultdict(int)
    for a in acts:
        counts[a] += 1
    return {"total": n,
            "constructive": round(sum(counts[a] for a in CONSTRUCTIVE_ACTS) / n, 2) if n else None,
            "answerSeeking": round(counts["answer_seeking"] / n, 2) if n else None,
            "acts": [{"code": a, "name": ACTS[a], "n": counts[a]} for a in ACTS if counts[a]]}
