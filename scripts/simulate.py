"""模拟实验：加入“历史 + 本轮投入”先验，能不能用更少的诊断题达到同样的准确度？

做法：生成虚拟学生（真实能力 θ），按逐步作答模型模拟他们回答诊断题；
历史信号 = 上次诊断的估计（θ 加噪声），投入信号 = 与 θ 弱相关的投入指数。
同一批学生分别用四种先验跑自适应诊断，比较平均题数、估计误差和“是否独立掌握”的判断准确率。

注意：结果取决于这里假设的信号强弱（--hist-noise、--eng-corr），只能说明方法在什么条件下有用；
真正的结论要用真实数据（导出的 ablation.csv）来检验。

用法：python scripts/simulate.py --n 400 --seed 1
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app import learner_model as lm  # noqa: E402


def simulate_student_answer(item, theta, rng):
    observed = []
    for j, prm in enumerate(lm.step_params(item), start=1):
        passed = rng.random() < lm.p_step(prm, theta)
        observed.append((j, passed))
        if not passed:
            break
    return observed


def run_cat(items, theta, mu0, sd0, rng):
    thetas = lm.grid()
    post = lm.normal_prior(mu0, sd0, thetas)
    used, n = set(), 0
    while True:
        cands = [it for it in items if it["id"] not in used]
        best, _, scored = lm.select_item(post, thetas, cands)
        if best is None:
            break
        obs = simulate_student_answer(best, theta, rng)
        post = lm.update(post, thetas, lm.step_params(best), obs)
        used.add(best["id"])
        n += 1
        s = lm.summarize(post, thetas)
        nxt = [it for it in items if it["id"] not in used]
        best_evr = lm.select_item(post, thetas, nxt)[2][0]["evr"] if nxt else None
        if lm.stop_reason(s, n, best_evr) or not nxt:
            break
    s = lm.summarize(post, thetas)
    return n, s


def synthetic_bank():
    """模拟用的题库：每个难度若干道有错题 + 两道无错题。"""
    items = []
    for k, d in enumerate([-1.0, -0.5, -0.5, 0.0, 0.0, 0.0, 0.5, 0.5, 1.0, 1.0]):
        items.append({"id": f"sim-{k}", "has_bug": True, "variant": {"prompt": "x"},
                      "code": "\n".join(["line"] * 10), "irt": {"a": 1.3, "d": d}})
    for k, d in enumerate([0.0, 0.4]):
        items.append({"id": f"sim-clean-{k}", "has_bug": False, "code": "x", "irt": {"a": 1.0, "d": d}})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hist-noise", type=float, default=0.5, help="上次诊断估计的误差标准差")
    ap.add_argument("--days", type=float, default=14, help="距上次诊断的天数")
    ap.add_argument("--eng-corr", type=float, default=0.3, help="投入指数与真实能力的相关强度（0~1）")
    ap.add_argument("--max-items", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.max_items:
        config.MODEL["max_items"] = args.max_items

    rng = random.Random(args.seed)
    items = synthetic_bank()
    cut = config.MODEL["theta_cut"]
    variants = ["flat", "history", "engagement", "both"]
    stats = {v: {"items": [], "err2": [], "correct": []} for v in variants}
    for _ in range(args.n):
        theta = rng.gauss(0, 1)
        last_mu = theta + rng.gauss(0, args.hist_noise)
        reask = 1 if (theta < -0.5 and rng.random() < 0.5) else 0
        z = args.eng_corr * theta + math.sqrt(max(1 - args.eng_corr ** 2, 0)) * rng.gauss(0, 1)
        e = 1 / (1 + math.exp(-z))
        hist = {"last_diag": {"mu": last_mu, "sd": args.hist_noise, "days_ago": args.days}, "reask_count": reask}
        eng = {"engagement_index": e}
        seed = rng.random()
        for v in variants:
            mu0, sd0, _ = lm.compute_prior(hist, eng, variant=v)
            n, s = run_cat(items, theta, mu0, sd0, random.Random(seed))  # 同一学生在各条件下用相同随机数
            stats[v]["items"].append(n)
            stats[v]["err2"].append((s["mu"] - theta) ** 2)
            stats[v]["correct"].append((s["p_mastered"] >= 0.5) == (theta >= cut))

    rows = []
    names = {"flat": "只用诊断题", "history": "+历史", "engagement": "+本轮投入", "both": "+历史+投入"}
    for v in variants:
        st = stats[v]
        rows.append({"variant": v, "name": names[v], "mean_items": sum(st["items"]) / args.n,
                     "rmse": math.sqrt(sum(st["err2"]) / args.n), "accuracy": sum(st["correct"]) / args.n})
    if args.json:
        print(json.dumps({"args": vars(args), "results": rows}, ensure_ascii=False, indent=2))
        return
    print(f"虚拟学生 {args.n} 人；历史估计误差 {args.hist_noise}，距上次 {args.days} 天；投入与能力相关 {args.eng_corr}")
    print(f"{'先验':<10}{'平均题数':>10}{'估计误差RMSE':>14}{'掌握判断准确率':>16}")
    for r in rows:
        print(f"{r['name']:<10}{r['mean_items']:>10.2f}{r['rmse']:>14.3f}{r['accuracy']:>16.1%}")


if __name__ == "__main__":
    main()
