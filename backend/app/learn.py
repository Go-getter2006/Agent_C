"""每周学一次掌握程度的参数（证据的系数、遗忘的半衰期），学完冻结，下一周都用这一版。

为什么：以前“一次做对算多少、提示后做对算多少、半衰期每次乘几”都是定的经验数，论文里说不清依据。
现在经验数只当先验，每周用学生后来的真实表现去校正。

- 目标：“新题的第一次提交能不能一次做对”。机会就是预测记录（prediction_log）里的每一次：记录时只用了那次
  提交之前的证据（先记预测、后学参数），结果是那次提交本身。
- 模型就是学情页用的掌握分：p = (1 + S) / (2 + S + F)。
  S、F 是加权的“会”“不会”：每条证据的分量 = 存下的分量 × 可信度 × 这一类证据的系数 c_k × 0.5^(天数 / 半衰期)，
  半衰期 H = h0 · g^e（e = 间隔开的独立成功天数 − 1 − 失败次数），夹在上下限之间。
  证据的类别见 mastery.SkillState.contrib（一次做对、自己改对、提示后才对、没过且没改对、没过但后来改对、
  小题各种结果、卡住、智能体评的理解……，“会”“不会”分开）。
- 要学的：每一类的系数 c_k，h0，g。都在对数尺度上，先验以经验值为中心（log c_k ~ N(0, σ²)，
  log h0 ~ N(log 经验值, σh²)，log g 同样），求最大后验：数据少时几乎不动，数据多了被数据拉过去。
- 优化：半衰期的两个参数在经验值附近的格子上挑（每个格子把证据按那个半衰期衰减一遍），
  格子里的系数用 Adam 求（梯度解析求）；取后验最大的那一组。
- 冻结：每周一次（北京时间的周，版本 = 课程-年W周），写进 mastery_params，下一周内学情、复习选题、
  预测都用这一版；机会不够 min_opportunities 次时也记一版（learned 为假），用的还是经验值。
- 评价：拟合时的对数损失只说明拟合得怎样；新参数好不好，看之后的预测记录（p_score 是当时这一版算的，
  p_score_fixed 是经验值算的，同时记下来比 Brier、对数损失）。
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db, mastery
from . import models as m

VERSION = "learn-1"


@dataclass
class Opportunity:
    y: int                                       # 新题第一次提交一次做对了
    contrib: list[tuple[str, bool, float, float]]   # 那之前这个技能上的证据贡献（类别键, 会?, 基数, 距那时的天数）
    exp: int                                     # 半衰期的指数


def week_version(course: str, at: dt.datetime | None = None) -> str:
    local = (at or db.now()).astimezone(db.BEIJING)
    year, week, _ = local.isocalendar()
    return f"{course}-{year}W{week:02d}"


def opportunities(s: Session, course: str, before: dt.datetime | None = None) -> list[Opportunity]:
    """预测记录里这门课的每一次“新题第一次提交”，配上那之前这个技能的证据贡献（按经验值参数整理，参数另乘）。"""
    q = select(m.PredictionLog).where(m.PredictionLog.skill_code.startswith(course))
    if before is not None:
        q = q.where(m.PredictionLog.ts < before)
    out = []
    for row in s.scalars(q.order_by(m.PredictionLog.ts, m.PredictionLog.id)).all():
        at = row.ts - dt.timedelta(seconds=1)
        st = mastery.skill_states(s, row.user_id, at=at, model="v2", params=mastery.PRIOR,
                                  graph_prior=False).get(row.skill_code)
        out.append(Opportunity(int(bool(row.outcome)), list(st.contrib) if st else [], st.hl_exp if st else 0))
    return out


def _aggregate(opps: list[Opportunity], h0: float, g: float) -> list[dict[str, float]]:
    """按这个半衰期把每个机会的证据衰减、按类别加起来。"""
    lo, hi = config.MASTERY_V2["half_life_min"], config.MASTERY_V2["half_life_max"]
    out = []
    for o in opps:
        h = max(lo, min(hi, h0 * g ** o.exp))
        agg: dict[str, float] = {}
        for key, _success, base, age in o.contrib:
            agg[key] = agg.get(key, 0.0) + base * 0.5 ** (age / h)
        out.append(agg)
    return out


def _nll(opps: list[Opportunity], aggs: list[dict[str, float]], scales: dict[str, float]) -> float:
    total = 0.0
    for o, agg in zip(opps, aggs):
        S = sum(scales.get(k, 1.0) * v for k, v in agg.items() if k.endswith("+"))
        F = sum(scales.get(k, 1.0) * v for k, v in agg.items() if k.endswith("-"))
        p = min(1 - 1e-6, max(1e-6, (1.0 + S) / (2.0 + S + F)))
        total -= math.log(p) if o.y else math.log(1.0 - p)
    return total


def _fit_scales(opps: list[Opportunity], aggs: list[dict[str, float]], keys: list[str]) -> tuple[dict[str, float], float]:
    """固定半衰期，求各类系数（对数尺度、以 0 为中心的正态先验）。返回 (系数, 后验的负对数)。"""
    cfg = config.LEARN
    var = cfg["prior_sd"] ** 2
    u = {k: 0.0 for k in keys}
    m1 = {k: 0.0 for k in keys}
    m2 = {k: 0.0 for k in keys}
    b1, b2, eps = 0.9, 0.999, 1e-8
    for step in range(1, cfg["steps"] + 1):
        c = {k: math.exp(v) for k, v in u.items()}
        grad = {k: u[k] / var for k in keys}
        for o, agg in zip(opps, aggs):
            S = sum(c[k] * v for k, v in agg.items() if k.endswith("+"))
            F = sum(c[k] * v for k, v in agg.items() if k.endswith("-"))
            d = 2.0 + S + F
            p = min(1 - 1e-6, max(1e-6, (1.0 + S) / d))
            dl_dp = -(o.y / p) + (1 - o.y) / (1.0 - p)
            dp_ds, dp_df = (1.0 + F) / d ** 2, -(1.0 + S) / d ** 2
            for k, v in agg.items():
                grad[k] += dl_dp * (dp_ds if k.endswith("+") else dp_df) * c[k] * v
        for k in keys:
            m1[k] = b1 * m1[k] + (1 - b1) * grad[k]
            m2[k] = b2 * m2[k] + (1 - b2) * grad[k] ** 2
            mh, vh = m1[k] / (1 - b1 ** step), m2[k] / (1 - b2 ** step)
            u[k] -= cfg["lr"] * mh / (math.sqrt(vh) + eps)
            u[k] = max(-3.0, min(3.0, u[k]))   # 系数在 1/20 到 20 倍之间
    scales = {k: math.exp(v) for k, v in u.items()}
    return scales, _nll(opps, aggs, scales) + sum(v * v for v in u.values()) / (2 * var)


def fit(opps: list[Opportunity]) -> tuple[mastery.Params, dict]:
    """求最大后验的参数。返回 (参数, 细节：每类出现在几次机会里、系数、半衰期、拟合前后的平均对数损失)。"""
    cfg, mv = config.LEARN, config.MASTERY_V2
    h_var = cfg["half_life_prior_sd"] ** 2
    h0_prior, g_prior = mv["half_life_base"], mv["half_life_growth"]
    keys = sorted({k for o in opps for k, *_ in o.contrib})
    best = None
    for fh in (0.6, 0.8, 1.0, 1.25, 1.6):
        for fg in (0.85, 1.0, 1.15):
            h0, g = h0_prior * fh, max(1.0, g_prior * fg)
            aggs = _aggregate(opps, h0, g)
            scales, post = _fit_scales(opps, aggs, keys)
            post += math.log(fh) ** 2 / (2 * h_var) + math.log(g / g_prior) ** 2 / (2 * h_var)
            if best is None or post < best[0]:
                best = (post, h0, g, scales, aggs)
    _post, h0, g, scales, aggs = best
    n = max(1, len(opps))
    prior_aggs = _aggregate(opps, h0_prior, g_prior)
    detail = {
        "keys": {k: {"opportunities": sum(1 for a in aggs if k in a), "scale": round(scales[k], 4)} for k in keys},
        "h0": round(h0, 3), "g": round(g, 4), "h0Prior": h0_prior, "gPrior": g_prior,
        "nllPrior": round(_nll(opps, prior_aggs, {}) / n, 5), "nllFit": round(_nll(opps, aggs, scales) / n, 5),
        "positives": sum(o.y for o in opps), "learnVersion": VERSION,
    }
    return mastery.Params("", {k: round(v, 4) for k, v in scales.items()}, round(h0, 3), round(g, 4)), detail


def weekly(s: Session, at: dt.datetime | None = None) -> dict[str, str]:
    """每天夜里调用：这门课这一周还没有参数就学一版（每周一次）。返回 {课程: 版本或说明}。"""
    at = at or db.now()
    out = {}
    for course in ("C", "D"):
        version = week_version(course, at)
        if s.scalar(select(m.MasteryParams.id).where(m.MasteryParams.version == version)) is not None:
            continue
        opps = opportunities(s, course, before=at)
        row = m.MasteryParams(course=course, version=version, n=len(opps), fitted_at=at)
        if len(opps) < config.LEARN["min_opportunities"]:
            row.params, row.learned = {}, False
            row.detail = {"reason": f"只有 {len(opps)} 次机会，不到 {config.LEARN['min_opportunities']} 次，沿用经验值",
                          "learnVersion": VERSION}
        else:
            prm, detail = fit(opps)
            row.params = {"scales": prm.scales, "h0": prm.h0, "g": prm.g}
            row.learned, row.detail = True, detail
            row.nll_prior, row.nll_fit = detail["nllPrior"], detail["nllFit"]
        s.add(row)
        s.flush()
        out[course] = version + ("" if row.learned else "（沿用经验值）")
    mastery.forget_active_params()
    return out
