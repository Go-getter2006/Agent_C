"""对话主流程：分析发言 → 划分回合 → 查历史 → 生成回复 → 记录。"""
from . import analyzer, history, tutor
from .config import ICAP_SCORE
from .db import dumps, ensure_student, loads, now
from .knowledge import kp_name
from .learner_model import engagement_index

MAX_MESSAGE = 4000
MAX_CODE = 20000


def current_episode(conn, student_id):
    row = conn.execute("SELECT * FROM episodes WHERE student_id=? AND status='open' "
                       "ORDER BY started_at DESC LIMIT 1", (student_id,)).fetchone()
    return dict(row) if row else None


def recent_turns(conn, episode_id, limit=10):
    rows = conn.execute("SELECT * FROM turns WHERE episode_id=? ORDER BY id DESC LIMIT ?",
                        (episode_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def refresh_episode_stats(conn, episode_id) -> dict:
    """根据本回合学生发言重新计算投入统计。"""
    rows = conn.execute("SELECT analysis_json FROM turns WHERE episode_id=? AND role='student'",
                        (episode_id,)).fetchall()
    analyses = [loads(r["analysis_json"], {}) for r in rows]
    analyses = [a for a in analyses if a]
    n = len(analyses)
    if n == 0:
        return {"n_student_turns": 0}
    stats = {
        "n_student_turns": n,
        "mean_icap": sum(ICAP_SCORE.get(a.get("icap"), 1) for a in analyses) / n,
        "attempt_ratio": sum(1 for a in analyses if a.get("has_own_attempt")) / n,
        "answer_seek_ratio": sum(1 for a in analyses if a.get("help_type") == "answer_seeking") / n,
    }
    stats["engagement_index"] = engagement_index(stats)
    resolved = any(a.get("resolved") for a in analyses)
    conn.execute("UPDATE episodes SET n_student_turns=?, mean_icap=?, attempt_ratio=?, answer_seek_ratio=?, "
                 "engagement_index=?, resolved=MAX(resolved, ?) WHERE id=?",
                 (n, stats["mean_icap"], stats["attempt_ratio"], stats["answer_seek_ratio"],
                  stats["engagement_index"], 1 if resolved else 0, episode_id))
    return stats


def close_episode(conn, episode_id):
    conn.execute("UPDATE episodes SET status='closed', ended_at=? WHERE id=?", (now(), episode_id))


def handle_message(conn, student_id: str, message: str, code: str = "") -> dict:
    message = (message or "").strip()[:MAX_MESSAGE]
    code = (code or "")[:MAX_CODE]
    ensure_student(conn, student_id)
    ep = current_episode(conn, student_id)
    turns = recent_turns(conn, ep["id"]) if ep else []

    # 1. 分析这条发言
    analysis = analyzer.analyze(message, code, ep, turns)

    # 2. 划分回合：新问题就关闭旧回合、开新回合
    is_new = bool(analysis["is_new_question"]) or ep is None
    reask, reask_sim = None, None
    if is_new:
        if ep:
            close_episode(conn, ep["id"])
        kp_id = analysis["kp_id"]
        # 3. 回复之前先查历史：这个学生以前是否问过同样的问题
        reask, reask_sim = history.find_reask(conn, student_id, kp_id, analysis["question_summary"])
        cur = conn.execute(
            "INSERT INTO episodes(student_id, kp_id, question_summary, started_at, reask_of, reask_sim) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (student_id, kp_id, analysis["question_summary"], now(),
             reask["id"] if reask else None, round(reask_sim or 0.0, 3)))
        ep = conn.execute("SELECT * FROM episodes WHERE id=?", (cur.lastrowid,)).fetchone()
        ep = dict(ep)
        turns = []
    kp_id = ep["kp_id"]

    # 记录学生发言（含响应时间：距上一条智能体回复多少秒）
    last_tutor = conn.execute("SELECT created_at FROM turns WHERE student_id=? AND role='tutor' "
                              "ORDER BY id DESC LIMIT 1", (student_id,)).fetchone()
    latency = (now() - last_tutor["created_at"]) if (last_tutor and not is_new) else None
    conn.execute("INSERT INTO turns(episode_id, student_id, role, content, code, created_at, latency_sec, analysis_json) "
                 "VALUES (?, ?, 'student', ?, ?, ?, ?, ?)",
                 (ep["id"], student_id, message, code or None, now(), latency, dumps(analysis)))
    stats = refresh_episode_stats(conn, ep["id"])

    # 4. 生成回复（历史摘要一并交给辅导模型）
    hist_ctx = history.context_text(conn, student_id, kp_id, current_episode_id=ep["id"])
    text, source = tutor.reply(kp_id, hist_ctx, reask, turns, message, code, analysis)
    conn.execute("INSERT INTO turns(episode_id, student_id, role, content, created_at) VALUES (?, ?, 'tutor', ?, ?)",
                 (ep["id"], student_id, text, now()))

    return {
        "reply": text,
        "reply_source": source,
        "episode": {"id": ep["id"], "kp_id": kp_id, "kp_name": kp_name(kp_id), "is_new": is_new,
                    "question_summary": ep["question_summary"],
                    "reask_of": ep.get("reask_of"), "reask_sim": ep.get("reask_sim"),
                    "reask_summary": reask["question_summary"] if reask else None},
        "analysis": {k: v for k, v in analysis.items() if not k.startswith("_error")},
        "engagement": stats,
        "offer_diagnosis": bool(analysis.get("resolved")),
    }


def mark_resolved(conn, student_id: str) -> dict:
    ep = current_episode(conn, student_id)
    if not ep:
        return {"ok": False, "error": "还没有正在讨论的问题。"}
    conn.execute("UPDATE episodes SET resolved=1 WHERE id=?", (ep["id"],))
    return {"ok": True, "episode_id": ep["id"], "kp_id": ep["kp_id"], "kp_name": kp_name(ep["kp_id"])}
