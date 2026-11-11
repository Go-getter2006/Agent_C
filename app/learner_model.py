"""学情模型：用历史和本轮投入做先验，用诊断题作答更新，估计学生对知识点的独立掌握程度 θ。

1. 逐步作答模型（每道有错题有 5 步，前一步通过才问下一步）：
       P(通过第 j 步 | 已通过前 j-1 步, θ) = c_j + (1 - c_j) * sigmoid(a_i * (θ - b_ij))
   其中 b_ij = d_i + δ_j，d_i 是题目难度，δ_j 是第 j 步相对难度，c_j 是猜对概率
   （判断有没有错 c=0.5，点中错误行 c≈1/行数，其余步骤 c=0）。
2. 先验：
       μ0 = μ_h - ρ * min(r, 3) + β * (e - 0.5)
       σ0² = σ_prev² + q * Δt          （有上次诊断时）
3. 选题：在候选题中选“期望后验方差下降最大”的一道。
4. 停止：后验标准差 ≤ 目标值，或独立掌握的概率 ≥ 0.9 / ≤ 0.1，或题数达到上限。
所有计算都在 θ 的离散网格上做，不依赖第三方库。
"""
import math

from . import config


# ---------------------------------------------------------------- 基础工具
def grid(cfg=None):
    cfg = cfg or config.MODEL
    n = int(round((cfg["grid_max"] - cfg["grid_min"]) / cfg["grid_step"])) + 1
    return [round(cfg["grid_min"] + i * cfg["grid_step"], 6) for i in range(n)]


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def normalize(weights):
    s = sum(weights)
    if s <= 0 or not math.isfinite(s):
        return [1.0 / len(weights)] * len(weights)
    return [w / s for w in weights]


def normal_prior(mu: float, sd: float, thetas):
    return normalize([math.exp(-0.5 * ((t - mu) / sd) ** 2) for t in thetas])


def summarize(post, thetas, cfg=None) -> dict:
    cfg = cfg or config.MODEL
    mean = sum(w * t for w, t in zip(post, thetas))
    var = sum(w * (t - mean) ** 2 for w, t in zip(post, thetas))
    p_m = sum(w for w, t in zip(post, thetas) if t >= cfg["theta_cut"])
    return {"mu": mean, "sd": math.sqrt(max(var, 0.0)), "var": var, "p_mastered": p_m}


# ---------------------------------------------------------------- 题目参数
def n_code_lines(item) -> int:
    return sum(1 for line in item["code"].split("\n") if line.strip() not in ("", "{", "}"))


def step_params(item, cfg=None):
    """返回该题每一步的 (a, b, c)。无错题只有 1 步；没有变式题的有错题有 4 步。"""
    cfg = cfg or config.MODEL
    irt = item.get("irt", {})
    a = float(irt.get("a", cfg["default_a"]))
    d = float(irt.get("d", 0.0))
    if not item.get("has_bug", True):
        return [(a, d + cfg["clean_offset"], 0.5)]
    offsets = irt.get("offsets", cfg["step_offsets"])
    guesses = [0.5, 1.0 / max(n_code_lines(item), 2), 0.0, 0.0, 0.0]
    m = 5 if item.get("variant") else 4
    return [(a, d + float(offsets[j]), guesses[j]) for j in range(m)]


def p_step(param, theta: float) -> float:
    a, b, c = param
    return c + (1.0 - c) * sigmoid(a * (theta - b))


def outcome_probs(params, theta: float):
    """P(恰好通过前 k 步, 然后失败或作答结束)，k = 0..M。"""
    probs, reach = [], 1.0
    for prm in params:
        p = p_step(prm, theta)
        probs.append(reach * (1.0 - p))
        reach *= p
    probs.append(reach)
    return probs


def obs_likelihood(params, observed, theta: float) -> float:
    """observed：[(第几步, 是否通过), ...]，只计实际问过的步骤。"""
    lik = 1.0
    for step, passed in observed:
        p = p_step(params[step - 1], theta)
        lik *= p if passed else (1.0 - p)
    return lik


def update(post, thetas, params, observed):
    return normalize([w * obs_likelihood(params, observed, t) for w, t in zip(post, thetas)])


def posterior(prior, thetas, administered, cfg=None):
    """administered：[(item, observed), ...]"""
    post = list(prior)
    for item, observed in administered:
        if observed:
            post = update(post, thetas, step_params(item, cfg), observed)
    return post


# ---------------------------------------------------------------- 选题与停止
def expected_posterior_variance(post, thetas, params) -> float:
    table = [outcome_probs(params, t) for t in thetas]
    total = 0.0
    for k in range(len(params) + 1):
        joint = [w * row[k] for w, row in zip(post, table)]
        pk = sum(joint)
        if pk <= 1e-12:
            continue
        mean_k = sum(j * t for j, t in zip(joint, thetas)) / pk
        var_k = sum(j * (t - mean_k) ** 2 for j, t in zip(joint, thetas)) / pk
        total += pk * var_k
    return total


def expected_steps(post, thetas, params) -> float:
    m = len(params)
    probs = [0.0] * (m + 1)
    for w, t in zip(post, thetas):
        for k, p in enumerate(outcome_probs(params, t)):
            probs[k] += w * p
    return sum(p * (min(k + 1, m)) for k, p in enumerate(probs))


def select_item(post, thetas, candidates, cfg=None):
    """返回 (最佳题目, 得分, 各候选题得分列表)。"""
    cfg = cfg or config.MODEL
    var_now = summarize(post, thetas, cfg)["var"]
    scored = []
    for item in candidates:
        params = step_params(item, cfg)
        evr = var_now - expected_posterior_variance(post, thetas, params)
        score = evr
        if cfg.get("selection") == "evr_per_step":
            score = evr / max(expected_steps(post, thetas, params), 1e-6)
        scored.append({"item_id": item["id"], "evr": round(evr, 5), "score": round(score, 5)})
    if not scored:
        return None, None, []
    scored.sort(key=lambda s: s["score"], reverse=True)
    best_id = scored[0]["item_id"]
    best = next(it for it in candidates if it["id"] == best_id)
    return best, scored[0]["score"], scored


def stop_reason(summary: dict, n_items: int, best_evr, cfg=None):
    cfg = cfg or config.MODEL
    if n_items >= cfg["max_items"]:
        return "max_items"
    if n_items < cfg["min_items"]:
        return None
    if summary["sd"] <= cfg["sd_target"]:
        return "precise_enough"
    if summary["p_mastered"] >= cfg["confidence"]:
        return "confident_mastered"
    if summary["p_mastered"] <= 1.0 - cfg["confidence"]:
        return "confident_not_mastered"
    if best_evr is not None and best_evr < 1e-3:
        return "no_informative_item"
    return None


# ---------------------------------------------------------------- 先验
def engagement_index(stats: dict):
    """本轮对话投入指数 e ∈ [0,1]。stats 来自回合统计。"""
    if not stats or not stats.get("n_student_turns"):
        return None
    w = config.ENGAGEMENT_WEIGHTS
    return (w["icap"] * (stats["mean_icap"] / 3.0)
            + w["attempt"] * stats["attempt_ratio"]
            + w["not_answer_seeking"] * (1.0 - stats["answer_seek_ratio"]))


def compute_prior(hist: dict, eng: dict, variant: str = None, cfg=None):
    """返回 (μ0, σ0, 说明)。variant：flat / history / engagement / both。"""
    cfg = cfg or config.MODEL
    variant = variant or cfg["prior_variant"]
    mu, sd = cfg["pop_mean"], cfg["pop_sd"]
    info = {"variant": variant, "base": "总体先验"}
    if variant in ("history", "both") and hist:
        last = hist.get("last_diag")
        if last and last.get("mu") is not None:
            var = last["sd"] ** 2 + cfg["drift_per_day"] * max(0.0, last["days_ago"])
            mu, sd = last["mu"], min(math.sqrt(var), cfg["pop_sd"])
            info["base"] = f"上次诊断（{last['days_ago']:.1f} 天前）"
        r = min(hist.get("reask_count", 0) or 0, cfg["reask_cap"])
        if r:
            mu -= cfg["reask_penalty"] * r
            info["reask_shift"] = -cfg["reask_penalty"] * r
    if variant in ("engagement", "both") and eng and eng.get("engagement_index") is not None:
        shift = cfg["engagement_weight"] * (eng["engagement_index"] - 0.5)
        mu += shift
        info["engagement_shift"] = round(shift, 3)
    mu = max(-2.0, min(2.0, mu))
    sd = max(cfg["prior_sd_min"], min(cfg["prior_sd_max"], sd))
    info["mu0"], info["sd0"] = round(mu, 3), round(sd, 3)
    return mu, sd, info


# ---------------------------------------------------------------- 五维画像
def profile(post, thetas, cfg=None) -> dict:
    """五个维度的达到概率：对一道中等难度题（d=0），P(θ ≥ 第 j 步的难度)。
    “修复并说明”一步的难度等于 theta_cut，所以它的概率就是“独立掌握”的概率。"""
    cfg = cfg or config.MODEL
    probs = []
    for j in range(5):
        b = cfg["step_offsets"][j]
        probs.append(round(sum(w for w, t in zip(post, thetas) if t >= b), 3))
    names = config.STEP_NAMES
    reached = [names[j] for j in range(5) if probs[j] >= 0.5]
    weakest = next((names[j] for j in range(5) if probs[j] < 0.5), "无（五步都较稳）")
    return {"steps": [{"name": names[j], "supports": config.STEP_SUPPORTS[j], "p": probs[j]} for j in range(5)],
            "highest_step": reached[-1] if reached else "尚未稳定达到第一步",
            "weakest_step": weakest}


def level_label(p_mastered: float) -> str:
    if p_mastered >= 0.9:
        return "已独立掌握"
    if p_mastered >= 0.5:
        return "基本掌握"
    if p_mastered >= 0.1:
        return "部分掌握"
    return "尚未掌握"
