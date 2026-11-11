"""研究数据导出，以及“先验消融”分析。

命令行：python -m app.export            # 导出到 data/exports/
导出内容：
    episodes.csv     每个讨论回合的投入统计、是否重复提问
    turns.csv        每条发言（含 ICAP、求助类型、响应时间）
    diagnoses.csv    每次诊断的先验、后验、题数、停止原因
    responses.csv    每一步作答及判分
    ablation.csv     同一组作答记录在四种先验下的后验：只用诊断题(flat)、+历史、+投入、+历史+投入，
                     以及按停止规则在第几题就可以停下——用来回答“加入投入和历史是否更准、是否更省题”
"""
import csv
import io
import sys
from pathlib import Path

from . import config, db
from . import learner_model as lm
from .db import loads

VARIANTS = ["flat", "history", "engagement", "both"]


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sql).fetchall()]


def table_rows(conn, name):
    if name == "episodes":
        return _rows(conn, "SELECT * FROM episodes ORDER BY id")
    if name == "turns":
        rows = _rows(conn, "SELECT * FROM turns ORDER BY id")
        for r in rows:
            a = loads(r.pop("analysis_json"), {}) or {}
            for key in ("kp_id", "is_new_question", "icap", "help_type", "has_own_attempt", "resolved", "_source"):
                r["a_" + key.strip("_")] = a.get(key)
        return rows
    if name == "diagnoses":
        rows = _rows(conn, "SELECT * FROM diag_sessions ORDER BY id")
        for r in rows:
            pj = loads(r.pop("prior_json"), {}) or {}
            hist, eng = pj.get("history") or {}, pj.get("engagement") or {}
            r["h_n_past_episodes"] = hist.get("n_past_episodes")
            r["h_reask_count"] = hist.get("reask_count")
            r["h_last_mu"] = (hist.get("last_diag") or {}).get("mu")
            r["h_days_since_last"] = (hist.get("last_diag") or {}).get("days_ago")
            r["e_engagement_index"] = eng.get("engagement_index")
            r["e_mean_icap"] = eng.get("mean_icap")
            r["e_attempt_ratio"] = eng.get("attempt_ratio")
            r["e_answer_seek_ratio"] = eng.get("answer_seek_ratio")
            r.pop("selection_log", None)
        return rows
    if name == "responses":
        return _rows(conn, "SELECT * FROM responses ORDER BY id")
    if name == "ablation":
        return ablation_rows(conn)
    raise ValueError(name)


def ablation_rows(conn):
    from .diagnosis import administered
    out = []
    thetas = lm.grid()
    for sess in _rows(conn, "SELECT * FROM diag_sessions WHERE status='finished' ORDER BY id"):
        pj = loads(sess["prior_json"], {}) or {}
        adm = administered(conn, sess["id"])
        for variant in VARIANTS:
            mu0, sd0, _ = lm.compute_prior(pj.get("history"), pj.get("engagement"), variant=variant)
            post = lm.normal_prior(mu0, sd0, thetas)
            would_stop_at = None
            for k, (item, observed) in enumerate(adm, start=1):
                post = lm.update(post, thetas, lm.step_params(item), observed)
                s = lm.summarize(post, thetas)
                rule = lm.stop_reason(s, k, None)
                if rule and would_stop_at is None:
                    would_stop_at = k
                out.append({"session_id": sess["id"], "student_id": sess["student_id"], "kp_id": sess["kp_id"],
                            "variant": variant, "prior_mu": round(mu0, 4), "prior_sd": round(sd0, 4),
                            "after_items": k, "mu": round(s["mu"], 4), "sd": round(s["sd"], 4),
                            "p_mastered": round(s["p_mastered"], 4), "stop_rule_met": rule or ""})
            for r in out:
                if r["session_id"] == sess["id"] and r["variant"] == variant:
                    r["would_stop_at"] = would_stop_at if would_stop_at is not None else ""
    return out


def to_csv(rows) -> str:
    buf = io.StringIO()
    if not rows:
        return ""
    fields = list(rows[0].keys())
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def main(out_dir=None):
    out_dir = Path(out_dir or (config.DATA_DIR / "exports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()
    with db.session() as conn:
        for name in ("episodes", "turns", "diagnoses", "responses", "ablation"):
            text = to_csv(table_rows(conn, name))
            # utf-8-sig：用 Excel 直接打开时中文不乱码
            (out_dir / f"{name}.csv").write_text(text, encoding="utf-8-sig")
            print(f"{name}.csv -> {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
