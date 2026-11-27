"""跨课程：数据结构这边怎么联系学生在 C 语言里的掌握情况。

两门课分开登录、分开显示。数据结构这边可以看 C 语言里相关知识点掌握得怎么样（只有档位和估计），
但不出现 C 语言的答题记录（提交、对话、卡点、小题）。C 语言这边不出现数据结构的任何东西，也不调用这里。

和 TransKT（Han 等 2025）的关系：TransKT 让大模型零样本给两门课的概念建立带类型的联系，再用图网络和对比学习
得到联合的知识状态。这里保留“带类型的联系”和“联合状态”两个想法，换了三处：

1. 联系要经过检验才进模型，全部自动：每天夜里智能体给还没提过的数据结构知识点补充联系；把握大的自动采用
   （还没检验时分量打七折），把握不大的先“待检验”。每天夜里再用数据检验每条联系：C 那头掌握了的学生，
   数据结构这头新题一次做对的比例是不是更高（两组的 Beta 后验比较）。数据支持的自动采用、分量最大；
   智能体提出、数据不支持的自动不用；人工初稿数据不支持时只把分量降到 0.3。老师可以改，但不用去点。
2. 联合状态用可解释的贝叶斯更新，不用黑箱网络：数据结构技能的先验 = 先修（包括 C 那头）估计的加权平均，
   权重看联系的类型和数据检验的结论；先验的分量 n0 相当于几次作答，所以
   估计 = η·先验 + (1−η)·数据结构自己的证据，η = n0 / (n0 + 2 + 数据结构这头的证据量)。
   刚开课时主要看 C 语言的基础，做的题越多，越由数据结构自己的表现决定。
3. 给老师的是能直接用的东西：开课前哪些学生的 C 基础不够（按这门课接下来要学的知识点），全班哪块 C 基础最薄弱。
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db, llm, mastery
from . import models as m
from .taxonomy import KNOWLEDGE_POINTS, LEVEL_NONE, LEVEL_WEAK, SKILLS, kp_of_skill, kp_rank, skill_name, skills_of_kp
from .taxonomy.prereq import CROSS_EDGES, CROSS_NOTES, parents_of

log = logging.getLogger("cross")


def cfg() -> dict:
    return config.CROSS


def relation_name(rel: str) -> str:
    return cfg()["relation"].get(rel, (rel, 0.0))[0]


# ------------------------------------------------------------ 联系
def ensure_seed(s: Session) -> int:
    """把人工初稿的跨课程先修边写进表里（采用、类型是先修）。已经有的不动（老师可能已经改过）。"""
    have = {(r.c_skill, r.d_skill) for r in s.scalars(select(m.CrossLink)).all()}
    n = 0
    for c, d in CROSS_EDGES:
        if (c, d) not in have:
            s.add(m.CrossLink(c_skill=c, d_skill=d, relation="prerequisite", source="seed", status="accepted",
                              rationale=CROSS_NOTES.get((c, d), ""), created_at=db.now()))
            n += 1
    for row in s.scalars(select(m.CrossLink).where(m.CrossLink.source == "seed", m.CrossLink.rationale == "人工初稿")).all():
        row.rationale = CROSS_NOTES.get((row.c_skill, row.d_skill), "")   # 以前写的是“人工初稿”，换成为什么成立
    if n:
        s.flush()
    return n


def accepted(s: Session) -> list[m.CrossLink]:
    memo = s.info.get(mastery.MEMO)
    if memo is not None and "cross_links" in memo:
        return memo["cross_links"]
    rows = list(s.scalars(select(m.CrossLink).where(m.CrossLink.status == "accepted")).all())
    if not rows and s.scalar(select(m.CrossLink.id).limit(1)) is None:
        # 表还是空的（还没写入初稿）：用初稿
        rows = [m.CrossLink(c_skill=c, d_skill=d, relation="prerequisite", source="seed", status="accepted",
                            verdict="unknown") for c, d in CROSS_EDGES]
    if memo is not None:
        memo["cross_links"] = rows  # 教师只读汇总中，全班共用一次读取；写入流程不启用 memoize。
    return rows


def link_weight(link: m.CrossLink) -> float:
    rel = cfg()["relation"].get(link.relation, ("", 0.5))[1]
    return rel * cfg()["reliability"].get(link.verdict or "unknown", 0.7)


def weighted_parents(s: Session) -> tuple[dict[str, list[tuple[str, float]]], dict[str, float]]:
    """先修，带权重：课内的边是 1，跨课程的边按类型和数据检验的结论。另返回有跨课程先修的技能的先验分量 n0。"""
    out: dict[str, list[tuple[str, float]]] = {c: [(p, 1.0) for p in ps] for c, ps in parents_of(include_cross=False).items()}
    cross_w: dict[str, list[float]] = defaultdict(list)
    for link in accepted(s):
        if link.c_skill in SKILLS and link.d_skill in SKILLS:
            w = link_weight(link)
            out.setdefault(link.d_skill, []).append((link.c_skill, w))
            cross_w[link.d_skill].append(w)
    base = config.MASTERY_V2["prior_strength"]
    strength = {d: max(base, cfg()["n0"] * sum(ws) / len(ws)) for d, ws in cross_w.items()}
    return out, strength


def feeds(s: Session | None = None) -> dict[str, set[str]]:
    """C 语言知识点 → 它支撑的数据结构知识点（由采用的联系汇总）。"""
    rows = accepted(s) if s is not None else [m.CrossLink(c_skill=c, d_skill=d) for c, d in CROSS_EDGES]
    out: dict[str, set[str]] = defaultdict(set)
    for link in rows:
        out[kp_of_skill(link.c_skill)].add(kp_of_skill(link.d_skill))
    return out


def kp_cross_edges(s: Session) -> list[tuple[str, str, int]]:
    """知识点一层的跨课程边：(C 知识点, 数据结构知识点, 由几条联系汇总而来)。"""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for link in accepted(s):
        counts[(kp_of_skill(link.c_skill), kp_of_skill(link.d_skill))] += 1
    return [(a, b, n) for (a, b), n in sorted(counts.items())]


# ------------------------------------------------------------ 数据检验
def _beta(k: int, n: int) -> tuple[float, float]:
    a, b = 1 + k, 1 + n - k
    mean = a / (a + b)
    var = a * b / ((a + b) ** 2 * (a + b + 1))
    return mean, var


def prob_greater(k1: int, n1: int, k2: int, n2: int) -> float:
    """两组一次做对的比例，第一组更高的后验概率（Beta(1,1) 先验，正态近似）。"""
    m1, v1 = _beta(k1, n1)
    m2, v2 = _beta(k2, n2)
    z = (m1 - m2) / math.sqrt(v1 + v2)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def verdict_of(k1: int, n1: int, k2: int, n2: int) -> tuple[float | None, str]:
    c = cfg()
    if n1 + n2 < c["min_n"] or min(n1, n2) < c["min_group"]:
        return None, "unknown"
    p = prob_greater(k1, n1, k2, n2)
    if p >= c["support_prob"]:
        return p, "supported"
    if p <= 0.5:
        return p, "unsupported"
    return p, "unknown"


def validate_links(s: Session, now: dt.datetime | None = None) -> int:
    """每天夜里自动：检验每条联系（采用的和待检验的）。用的是“新题的第一次提交能不能一次做对”的记录。"""
    now = now or db.now()
    outcomes: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for r in s.scalars(select(m.PredictionLog).where(m.PredictionLog.skill_code.like("D%"))).all():
        outcomes[(r.user_id, r.skill_code)].append(bool(r.outcome))
    users = {u for u, _ in outcomes}
    states = {u: mastery.skill_states(s, u, model="v2") for u in users}
    rows = list(s.scalars(select(m.CrossLink).where(m.CrossLink.status != "rejected")).all())
    for link in rows:
        k1 = n1 = k2 = n2 = 0
        for u in users:
            outs = outcomes.get((u, link.d_skill))
            st = states[u].get(link.c_skill)
            if not outs or st is None or st.predicted or st.raw < 1:
                continue
            if st.p >= cfg()["mastered"]:
                k1, n1 = k1 + sum(outs), n1 + len(outs)
            else:
                k2, n2 = k2 + sum(outs), n2 + len(outs)
        link.n_with, link.n_without = n1, n2
        link.rate_with = round(k1 / n1, 3) if n1 else None
        link.rate_without = round(k2 / n2, 3) if n2 else None
        prob, link.verdict = verdict_of(k1, n1, k2, n2)
        link.prob_support = round(prob, 3) if prob is not None else None
        link.checked_at = now
    return len(rows)


# ------------------------------------------------------------ 智能体提出新的联系
_SYSTEM = """你是数据结构课程的助教。数据结构课的代码用 C 语言写，学生先学过 C 语言。
任务：对给出的数据结构技能，找出 C 语言课里和它有关的技能，说明是什么关系。只输出 JSON：
{"links":[{"c":"C 语言技能编码","d":"数据结构技能编码","type":"prerequisite","why":"不超过 30 字","confidence":0.8}]}
type 只能取（和 TransKT 的四种关系一致）：prerequisite（前置：学它之前要先会 C 的这个技能）、
used_for（用到：它里面直接用到 C 的这个技能）、part_of（组成部分：C 的这个技能是它的一部分）、
hyponym_of（是一种：C 的这个技能是它的一个特例或一种具体做法）。
要求：编码只能从给出的列表里选；已有的联系不要重复；只给确实有关的，宁缺毋滥，最多 8 条。"""


def propose_links(s: Session, d_kp: str) -> list[m.CrossLink]:
    """智能体给一个数据结构知识点提出新的跨课程联系，先存成“待检验”（auto_links 再决定采不采用）。没有大模型时抛 LLMUnavailable。"""
    d_skills = [c for c in skills_of_kp(d_kp)]
    if not d_skills:
        return []
    existing = {(r.c_skill, r.d_skill) for r in s.scalars(select(m.CrossLink).where(
        m.CrossLink.d_skill.in_(d_skills))).all()}
    c_skills = [c for c in SKILLS if c.startswith("C")]
    user = ("【数据结构技能】\n" + "\n".join(f"{c} {skill_name(c)}" for c in d_skills) +
            "\n\n【C 语言技能】\n" + "\n".join(f"{c} {skill_name(c)}（{KNOWLEDGE_POINTS.get(kp_of_skill(c), '')}）"
                                         for c in c_skills) +
            "\n\n【已有的联系】\n" + ("\n".join(f"{a} → {b}" for a, b in sorted(existing)) or "（无）"))

    def validate(d: dict) -> list[dict]:
        items = d.get("links")
        if not isinstance(items, list):
            raise ValueError("links 要是列表")
        out, seen = [], set()
        for it in items[:cfg()["max_proposals"]]:
            if not isinstance(it, dict):
                raise ValueError("每条联系要是对象")
            c, dd, typ = str(it.get("c", "")), str(it.get("d", "")), str(it.get("type", ""))
            if c not in SKILLS or not c.startswith("C"):
                raise ValueError(f"{c} 不是 C 语言的技能编码")
            if dd not in d_skills:
                raise ValueError(f"{dd} 不在给出的数据结构技能里")
            if typ not in cfg()["relation"] or typ == "similar":
                raise ValueError(f"type {typ} 不在枚举里")
            if (c, dd) in existing or (c, dd) in seen:
                continue
            seen.add((c, dd))
            try:
                conf = float(it.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            out.append({"c": c, "d": dd, "type": typ, "why": str(it.get("why") or "")[:60],
                        "confidence": max(0.0, min(1.0, conf))})
        return out

    items = llm.call_json("propose_cross_links", _SYSTEM, user, validate, retries=1, max_tokens=900)
    rows = []
    for it in items:
        row = m.CrossLink(c_skill=it["c"], d_skill=it["d"], relation=it["type"], source="llm", status="proposed",
                          rationale=it["why"], confidence=it["confidence"], created_at=db.now(), verdict="unknown")
        s.add(row)
        rows.append(row)
    s.flush()
    llm.flush_records(s)
    return rows


def auto_links(s: Session, now: dt.datetime | None = None) -> dict:
    """每天夜里自动做：写入初稿、智能体补充联系、用数据检验、按检验结果采用或不用。老师手动改过的不动。"""
    now = now or db.now()
    seeded = ensure_seed(s)
    proposed = accepted_n = rejected_n = 0
    if llm.available():
        tried = {kp_of_skill(r.d_skill) for r in s.scalars(select(m.CrossLink).where(m.CrossLink.source == "llm")).all()}
        tried |= {k for k in (s.scalars(select(m.SyncState.value).where(m.SyncState.key.like("cross_proposed:%"))).all())}
        todo = [kp for kp in sorted(KNOWLEDGE_POINTS) if kp.startswith("D") and kp not in tried]
        for kp in todo[:cfg()["propose_per_night"]]:
            _mark_proposed(s, kp)
            try:
                rows = propose_links(s, kp)
            except llm.LLMUnavailable:
                continue
            proposed += len(rows)
            for row in rows:
                if (row.confidence or 0) >= cfg()["auto_accept_confidence"]:
                    row.status, row.reviewed_at = "accepted", now
                    accepted_n += 1
    checked = validate_links(s, now)
    for link in s.scalars(select(m.CrossLink).where(m.CrossLink.reviewed_by.is_(None))).all():
        if link.status == "proposed" and link.verdict == "supported":
            link.status, link.reviewed_at = "accepted", now
            accepted_n += 1
        elif link.source == "llm" and link.status != "rejected" and link.verdict == "unsupported":
            link.status, link.reviewed_at = "rejected", now
            rejected_n += 1
    return {"seeded": seeded, "proposed": proposed, "accepted": accepted_n, "rejected": rejected_n, "checked": checked}


def _mark_proposed(s: Session, kp: str) -> None:
    """记下这个知识点智能体已经提过了（提不出合格的也算），不每天重复提。"""
    from .xdoj.store import set_state

    set_state(s, 0, f"cross_proposed:{kp}", kp)


def link_view(link: m.CrossLink) -> dict:
    return {"id": link.id, "c": {"code": link.c_skill, "name": skill_name(link.c_skill),
                                 "kp": KNOWLEDGE_POINTS.get(kp_of_skill(link.c_skill), "")},
            "d": {"code": link.d_skill, "name": skill_name(link.d_skill),
                  "kp": KNOWLEDGE_POINTS.get(kp_of_skill(link.d_skill), ""), "kpCode": kp_of_skill(link.d_skill)},
            "relation": link.relation, "relationName": relation_name(link.relation), "source": link.source,
            "status": link.status, "decidedBy": "teacher" if link.reviewed_by else ("auto" if link.reviewed_at else ""), "rationale": link.rationale, "confidence": link.confidence,
            "nWith": link.n_with, "nWithout": link.n_without, "rateWith": link.rate_with,
            "rateWithout": link.rate_without, "probSupport": link.prob_support, "verdict": link.verdict,
            "weight": round(link_weight(link), 2), "checkedAt": link.checked_at.isoformat() if link.checked_at else None}


# ------------------------------------------------------------ 联合状态、C 基础
def joint_state(s: Session, user_id: str) -> list[dict]:
    """数据结构每个知识点的联合状态：自己的证据、C 那头给的先验、两者各占多少（η）、合起来的估计。"""
    states = mastery.skill_states(s, user_id, model="v2", with_predicted=True)
    parents, _strength = weighted_parents(s)
    by_kp: dict[str, list[dict]] = defaultdict(list)
    for code, st in states.items():
        if not code.startswith("D"):
            continue
        kappa = max(0.0, st.a0 + st.b0 - 2)
        n = st.s + st.f
        # 还没有证据（推出来的准备度）：全部来自先验，η = 1
        eta = 1.0 if st.predicted else (kappa / (kappa + 2 + n) if kappa > 0 else 0.0)
        c_par = [(p, w) for p, w in parents.get(code, []) if p.startswith("C") and p in states and not states[p].predicted]
        p_c = sum(states[p].p * w for p, w in c_par) / sum(w for _p, w in c_par) if c_par else None
        by_kp[kp_of_skill(code)].append({"pD": (1 + st.s) / (2 + n) if not st.predicted else None, "pC": p_c,
                                         "eta": eta, "p": st.p, "w": 1 + st.raw, "predicted": st.predicted})
    kp_states = mastery.kp_states(s, user_id, model="v2", with_predicted=True)
    out = []
    for kp, items in sorted(by_kp.items(), key=lambda kv: kp_rank(kv[0])):
        real = [x for x in items if not x["predicted"]]
        items = real or items   # 有证据的技能优先（和知识点的档位一个算法）

        def avg(key: str) -> float | None:
            vals = [(x[key], x["w"]) for x in items if x[key] is not None]
            return round(sum(v * w for v, w in vals) / sum(w for _v, w in vals), 2) if vals else None
        st = kp_states.get(kp)
        out.append({"code": kp, "name": KNOWLEDGE_POINTS.get(kp, ""), "pD": avg("pD"), "pC": avg("pC"),
                    "eta": avg("eta"), "p": avg("p"), "level": st.level if st else LEVEL_NONE,
                    "predicted": not real})
    return out


def c_foundation(s: Session, user_id: str) -> list[dict]:
    """这个学生在数据结构要用到的 C 语言知识点上的掌握程度。只有档位和估计，没有任何答题记录。"""
    states = mastery.kp_states(s, user_id, model="v2")
    rows = []
    for c_kp, d_kps in sorted(feeds(s).items()):
        st = states.get(c_kp)
        known = st is not None and not st.predicted
        rows.append({"code": c_kp, "name": KNOWLEDGE_POINTS.get(c_kp, ""),
                     "level": st.level if known else LEVEL_NONE, "p": round(st.p, 2) if known else None,
                     "supports": [{"code": d, "name": KNOWLEDGE_POINTS.get(d, "")} for d in sorted(d_kps, key=kp_rank)]})
    return rows


def weak_foundation(rows: list[dict], focus: set[str] | None = None) -> list[dict]:
    """薄弱的 C 基础：待加强，或者估计低于 weak。focus：只看支撑这些数据结构知识点的。"""
    out = []
    for r in rows:
        if focus is not None and not ({x["code"] for x in r["supports"]} & focus):
            continue
        if r["level"] == LEVEL_WEAK or (r["p"] is not None and r["p"] < cfg()["weak"]):
            out.append(r)
    return out


def class_warning(s: Session, students: list[tuple[str, str]], focus: set[str]) -> dict:
    """数据结构的班：C 基础预警。focus 是这个班接下来要学（作业涉及）的数据结构知识点；为空时看全部。"""
    by_c: dict[str, list[str]] = defaultdict(list)
    no_data: dict[str, int] = defaultdict(int)
    people = []
    rows_of = {}
    for uid, name in students:
        rows = c_foundation(s, uid)
        rows_of[uid] = rows
        weak = weak_foundation(rows, focus or None)
        for r in weak:
            by_c[r["code"]].append(name)
        for r in rows:
            if r["p"] is None and (not focus or {x["code"] for x in r["supports"]} & focus):
                no_data[r["code"]] += 1
        if weak:
            people.append({"userId": uid, "name": name,
                           "weak": [{"code": r["code"], "name": r["name"], "level": r["level"], "p": r["p"],
                                     "supports": [x["name"] for x in r["supports"] if not focus or x["code"] in focus]}
                                    for r in weak]})
    people.sort(key=lambda x: -len(x["weak"]))
    sample = next(iter(rows_of.values()), [])
    supports = {r["code"]: [x["name"] for x in r["supports"] if not focus or x["code"] in focus] for r in sample}
    topics = [{"code": c, "name": KNOWLEDGE_POINTS.get(c, ""), "weak": len(names), "noData": no_data.get(c, 0),
               "supports": supports.get(c, [])} for c, names in by_c.items()]
    topics.sort(key=lambda x: -x["weak"])
    return {"topics": topics, "students": people, "total": len(students)}
