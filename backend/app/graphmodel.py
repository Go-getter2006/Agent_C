"""知识点图模型（附件一第 9 节）：贝叶斯网络，用 v2 的提交和作答证据独立计算。

结果只在教师端显示，并标明“试验中”。学生端和智能体都不用它。

- 每个技能一个节点，取值“会”或“不会”；先修关系是边：
  P(会 | 先修) = σ(β + γ × 掌握的先修比例)
- 每条证据挂在它的技能下面（DINA 的单技能形式）：
  P(这次算“会” | 掌握) = 1 - s，P(这次算“会” | 没掌握) = g；按证据的权重（含遗忘）加权。
- 反推用吉布斯采样；参数先用经验值，以后用 EM 估计。
"""
from __future__ import annotations

import datetime as dt
import math
import random
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import config, db, mastery
from . import models as m
from .labeling.labeler import current_problem_label
from .taxonomy.prereq import PREREQ_EDGES, children_of, parents_of

_PARENTS = parents_of()
_CHILDREN = children_of()


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _prior(skill: str, state: dict[str, int]) -> float:
    cfg = config.GRAPH
    parents = _PARENTS.get(skill, [])
    if not parents:
        return cfg["root_prior"]
    proportion = sum(state.get(p, 0) for p in parents) / len(parents)
    return _sigmoid(cfg["beta"] + cfg["gamma"] * proportion)


def relevant_nodes(evidence_skills: set[str]) -> list[str]:
    """和这个学生的证据有关的节点：有证据的技能，加上它们的先修（一直往上）和直接后继。"""
    nodes = set(evidence_skills)
    stack = list(evidence_skills)
    while stack:
        for parent in _PARENTS.get(stack.pop(), []):
            if parent not in nodes:
                nodes.add(parent)
                stack.append(parent)
    for skill in list(evidence_skills):
        nodes.update(_CHILDREN.get(skill, []))
    return sorted(nodes)


def infer(evidence: dict[str, tuple[float, float]], rng: random.Random | None = None) -> dict[str, float]:
    """evidence：技能 -> (加权的“会”S, 加权的“不会”F)。返回每个相关技能“会”的后验概率。"""
    cfg = config.GRAPH
    rng = rng or random.Random(cfg["seed"])
    nodes = relevant_nodes(set(evidence))
    if not nodes:
        return {}
    g, slip = cfg["guess"], cfg["slip"]
    # 每个节点的证据似然（对数）：掌握 / 没掌握
    like = {}
    for k in nodes:
        s_w, f_w = evidence.get(k, (0.0, 0.0))
        like[k] = (s_w * math.log(1 - slip) + f_w * math.log(slip), s_w * math.log(g) + f_w * math.log(1 - g))
    state = {k: 1 if rng.random() < 0.5 else 0 for k in nodes}
    in_graph = set(nodes)
    counts = dict.fromkeys(nodes, 0.0)
    kept = 0
    for sweep in range(cfg["sweeps"]):
        for k in nodes:
            logp = [0.0, 0.0]
            for value in (0, 1):
                state[k] = value
                prior = _prior(k, state)
                lp = math.log(prior if value else 1 - prior)
                for child in _CHILDREN.get(k, []):
                    if child in in_graph:
                        pc = _prior(child, state)
                        lp += math.log(pc if state[child] else 1 - pc)
                lp += like[k][0] if value else like[k][1]
                logp[value] = lp
            p1 = 1.0 / (1.0 + math.exp(min(50.0, max(-50.0, logp[0] - logp[1]))))
            state[k] = 1 if rng.random() < p1 else 0
            if sweep >= cfg["burn_in"]:
                # 平均条件概率，减少有限样本把后验推成 0 或 1 的情况。
                counts[k] += p1
        if sweep >= cfg["burn_in"]:
            kept += 1
    return {k: counts[k] / kept for k in nodes}


def evidence_of(s: Session, user_id: str, at: dt.datetime | None = None) -> dict[str, tuple[float, float]]:
    """采用 v2 的作答证据权重，包括失败和通过可信度，排除对话的智能体估分。"""
    states = mastery.skill_states(s, user_id, at=at, model="v2", include_agent=False)
    return {code: (st.s, st.f) for code, st in states.items() if st.s + st.f > 0}


def update_student(s: Session, user_id: str) -> dict[str, float]:
    evidence = evidence_of(s, user_id)
    post = infer(evidence)
    s.execute(delete(m.GraphPosterior).where(m.GraphPosterior.user_id == user_id))
    for code, p in post.items():
        s.add(m.GraphPosterior(user_id=user_id, skill_code=code, p=round(p, 4), has_evidence=code in evidence,
                               model_version=config.GRAPH_VERSION, updated_at=db.now()))
    return post


def current_posteriors(s: Session, user_id: str) -> dict[str, m.GraphPosterior]:
    rows = s.scalars(select(m.GraphPosterior).where(m.GraphPosterior.user_id == user_id,
                                                   m.GraphPosterior.model_version == config.GRAPH_VERSION))
    return {r.skill_code: r for r in rows}


def refresh_old_posteriors(limit: int = 2) -> int:
    """版本更新后分批刷新存量后验。原始证据和预测日志留在数据库里。"""
    evidence, posterior = m.SkillEvidence, m.GraphPosterior
    current = select(posterior.user_id).where(posterior.user_id == evidence.user_id,
                                             posterior.model_version == config.GRAPH_VERSION).exists()
    with db.session_scope() as s:
        users = list(s.scalars(select(evidence.user_id).where(
            evidence.weight > 0, evidence.event_type.not_in(mastery.AGENT_EVENTS),
            (evidence.reliability.is_(None) | (evidence.reliability > 0)), ~current
        ).distinct().order_by(evidence.user_id).limit(limit)))
    for uid in users:
        with db.session_scope() as s:
            update_student(s, uid)
    return len(users)


def log_predictions(s: Session, user_id: str) -> int:
    """记下两个模型在每次“新题第一次提交”之前的预测，和这次是不是一次做对了。已经记过的不重复算。"""
    done = set(s.scalars(select(m.PredictionLog.source_key).where(m.PredictionLog.user_id == user_id)).all())
    subs = s.scalars(select(m.Submission).where(m.Submission.user_id == user_id)
                     .order_by(m.Submission.submission_id)).all()
    seen: set[tuple[int, int]] = set()
    n = 0
    for sub in subs:
        key = (sub.server_id, sub.problem_id)
        if key in seen:
            continue
        seen.add(key)
        if sub.submit_time is None or sub.judgement in ("PENDING", "UNKNOWN", ""):
            continue
        source_key = f"first:{sub.server_id}:{sub.submission_id}"
        if source_key in done:
            continue
        label = current_problem_label(s, sub.server_id, sub.problem_id)
        if label is None:
            continue
        before = sub.submit_time - dt.timedelta(seconds=1)
        simple = mastery.skill_states(s, user_id, at=before, model="v1").get(label.primary_skill)
        v2 = mastery.skill_states(s, user_id, at=before, model="v2", with_predicted=True).get(label.primary_skill)
        evidence = evidence_of(s, user_id, at=before)
        evidence.setdefault(label.primary_skill, (0.0, 0.0))
        graph = infer(evidence)
        guess, slip = config.GRAPH["guess"], config.GRAPH["slip"]
        graph_pass = guess + (1.0 - slip - guess) * graph[label.primary_skill]
        from . import profile

        feats, fused, v2_base = profile.prediction_snapshot(s, user_id, label.primary_skill, before)
        s.add(m.PredictionLog(
            user_id=user_id, skill_code=label.primary_skill, source_key=source_key, ts=sub.submit_time,
            outcome=sub.judgement == "AC", p_simple=round(simple.p if simple else 0.5, 4),
            p_v2=round(v2.p if v2 else 0.5, 4),
            p_graph=round(graph_pass, 4),
            p_fused=round(fused, 4) if fused is not None else None, p_v2_base=round(v2_base, 4), features=feats,
            model_version=config.GRAPH_VERSION))
        n += 1
    return n


def nightly(user_ids: list[str] | None = None) -> dict:
    """每天夜里：把还没评的回合评完；全图更新一次，补记预测；再用新的预测记录重新学多维融合的权重。

    先补预测、后学权重：当天记下的融合预测用的是学之前的权重，评比才公平（不拿答案学了再考）。
    """
    from . import assess, cross, profile
    from .chat import workflow

    with db.session_scope() as s:
        workflow.close_stale_rounds(s)   # 先把超时的回合结束，才能评
    assessed = assess.run_pending(limit=500)
    with db.session_scope() as s:
        ids = user_ids or list(s.scalars(select(m.SkillEvidence.user_id).distinct()).all())
    n_pred = 0
    for uid in ids:
        with db.session_scope() as s:
            update_student(s, uid)
            n_pred += log_predictions(s, uid)
    with db.session_scope() as s:
        weights = profile.refit(s)
        links = cross.auto_links(s)   # 跨课程的联系：智能体补充、用新记下的预测检验、按检验结果采用或不用
    return {"students": len(ids), "predictions": n_pred, "assessed": assessed, "weights": weights, "links": links}


# ------------------------------------------------------------ 教师端用的汇总
def root_causes(states: dict[str, mastery.SkillState], observed_post: dict[str, float]) -> list[dict]:
    """反复失败时，查找有直接证据且图模型估计偏低的先修技能。"""
    out = []
    for code, st in states.items():
        if st.unresolved_submissions < 2 or code not in observed_post:
            continue
        stack, seen, weak = list(_PARENTS.get(code, [])), set(), []
        while stack:
            parent = stack.pop()
            if parent in seen:
                continue
            seen.add(parent)
            if observed_post.get(parent, 1.0) < 0.4:
                weak.append((observed_post[parent], parent))
            stack.extend(_PARENTS.get(parent, []))
        if weak:
            weak.sort()
            out.append({"skill": code, "root": weak[0][1], "p": round(weak[0][0], 2)})
    return out


def edge_stats(per_student: dict[str, dict[str, float]]) -> list[dict]:
    """先修边的描述性统计：两头都有数据的学生里，先修会与不会时，后继“会”的比例各是多少。

    这不是边的强度估计（那要等数据够了用 EM 估），只用来提示哪些边可能不成立。
    """
    rows = []
    for parent, child in PREREQ_EDGES:
        both = [(p[parent], p[child]) for p in per_student.values() if parent in p and child in p]
        if not both:
            continue
        with_parent = [c for a, c in both if a >= 0.6]
        without = [c for a, c in both if a < 0.6]
        rate = lambda xs: round(sum(1 for x in xs if x >= 0.6) / len(xs), 2) if xs else None  # noqa: E731
        ok_rate, no_rate = rate(with_parent), rate(without)
        doubtful = len(both) >= 10 and ok_rate is not None and no_rate is not None and ok_rate - no_rate <= 0.0
        rows.append({"parent": parent, "child": child, "n": len(both), "withParent": ok_rate, "withoutParent": no_rate,
                     "nWith": len(with_parent), "nWithout": len(without), "doubtful": doubtful})
    rows.sort(key=lambda r: (-r["n"], r["parent"], r["child"]))
    return rows


def weekly_accuracy(s: Session, user_ids: list[str], weeks: int = 6, course: str | None = None,
                    version: str | None = None) -> list[dict]:
    """按周统计两个模型的预测准确度（概率 ≥ 0.5 算预测“能做对”），以及 Brier 分数（越小越好）。"""
    if not user_ids:
        return []
    query = select(m.PredictionLog).where(m.PredictionLog.user_id.in_(user_ids))
    if version is not None:
        query = query.where(m.PredictionLog.model_version == version)
    rows = s.scalars(query).all()
    if course is not None:
        rows = [r for r in rows if r.skill_code and r.skill_code[0] == course]
    now = db.now().astimezone(db.BEIJING)
    monday = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    buckets: dict[int, list[m.PredictionLog]] = defaultdict(list)
    for r in rows:
        idx = -((r.ts.astimezone(db.BEIJING).date() - monday.date()).days // 7)
        if 0 <= idx < weeks:
            buckets[idx].append(r)
    out = []
    for idx in range(weeks):
        items = buckets.get(idx, [])
        if not items:
            continue
        n = len(items)
        acc = lambda get: round(sum(1 for r in items if (get(r) >= 0.5) == r.outcome) / n, 2)  # noqa: E731
        brier = lambda get: round(sum((get(r) - (1.0 if r.outcome else 0.0)) ** 2 for r in items) / n, 3)  # noqa: E731
        start = monday - dt.timedelta(weeks=idx)
        # 多维融合和“v2 去掉智能体评估”是后来加的：只在记了这两个值的预测上比
        newer = [r for r in items if r.p_fused is not None and r.p_v2_base is not None]
        nb = len(newer)
        brier_new = lambda get: round(sum((get(r) - (1.0 if r.outcome else 0.0)) ** 2 for r in newer) / nb, 3) if nb else None  # noqa: E731
        out.append({"weekStart": start.date().isoformat(), "n": n,
                    "simpleAccuracy": acc(lambda r: r.p_simple), "v2Accuracy": acc(lambda r: r.p_v2),
                    "graphAccuracy": acc(lambda r: r.p_graph),
                    "simpleBrier": brier(lambda r: r.p_simple), "v2Brier": brier(lambda r: r.p_v2),
                    "graphBrier": brier(lambda r: r.p_graph),
                    "nFused": nb, "fusedBrier": brier_new(lambda r: r.p_fused),
                    "v2BaseBrier": brier_new(lambda r: r.p_v2_base), "v2SameBrier": brier_new(lambda r: r.p_v2)})
    return out
