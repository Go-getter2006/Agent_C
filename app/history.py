"""历史检索：智能体回复之前，先查这个学生以前是否问过同样的问题。"""
import math
import re
import time
from collections import Counter

from . import config, llm
from .db import loads

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)

SAME_Q_PROMPT = """判断学生的两次提问是不是本质上同一个问题（考查同一个知识点上的同一个困难，措辞不同也算）。
输出 json：{"same": true 或 false, "reason": "一句话"}"""


def _ngrams(text: str, n: int = 2) -> Counter:
    t = _PUNCT.sub("", (text or "").lower())
    if len(t) < n:
        return Counter([t]) if t else Counter()
    return Counter(t[i:i + n] for i in range(len(t) - n + 1))


def similarity(a: str, b: str) -> float:
    """字符二元组的余弦相似度，0~1。中文不用分词也能用。"""
    va, vb = _ngrams(a), _ngrams(b)
    if not va or not vb:
        return 0.0
    dot = sum(va[k] * vb[k] for k in va)
    return dot / (math.sqrt(sum(v * v for v in va.values())) * math.sqrt(sum(v * v for v in vb.values())))


def past_episodes(conn, student_id: str, kp_id: str, exclude_id=None, limit: int = 20):
    rows = conn.execute(
        "SELECT * FROM episodes WHERE student_id=? AND kp_id=? AND id != ? ORDER BY started_at DESC LIMIT ?",
        (student_id, kp_id, exclude_id or -1, limit)).fetchall()
    return [dict(r) for r in rows]


def find_reask(conn, student_id: str, kp_id: str, summary: str, exclude_id=None):
    """返回 (以前的回合, 相似度)；没有就返回 (None, 最高相似度)。"""
    cfg = config.MODEL
    best, best_sim = None, 0.0
    for ep in past_episodes(conn, student_id, kp_id, exclude_id):
        s = similarity(summary, ep["question_summary"])
        if s > best_sim:
            best, best_sim = ep, s
    if best is None or best_sim < cfg["similarity_candidate"]:
        return None, best_sim
    if best_sim >= cfg["similarity_threshold"]:
        return best, best_sim
    # 处在“可能相同”的区间时，真实模式下再请大模型确认一次
    if not llm.is_mock():
        out = llm.chat_json(SAME_Q_PROMPT, f"以前的提问：{best['question_summary']}\n现在的提问：{summary}",
                            fallback=lambda: {"same": False})
        if out.get("same") is True:
            return best, best_sim
    return None, best_sim


def last_diagnosis(conn, student_id: str, kp_id: str):
    row = conn.execute(
        "SELECT * FROM diag_sessions WHERE student_id=? AND kp_id=? AND status='finished' "
        "ORDER BY ended_at DESC LIMIT 1", (student_id, kp_id)).fetchone()
    return dict(row) if row else None


def features(conn, student_id: str, kp_id: str, current_episode_id=None, at_time=None) -> dict:
    """先验要用的历史特征（不含当前回合）。"""
    at_time = at_time or time.time()
    eps = past_episodes(conn, student_id, kp_id, exclude_id=current_episode_id, limit=200)
    eps = [e for e in eps if e["started_at"] <= at_time]
    reask = sum(1 for e in eps if e["reask_of"])
    if current_episode_id:
        cur = conn.execute("SELECT reask_of FROM episodes WHERE id=?", (current_episode_id,)).fetchone()
        if cur and cur["reask_of"]:
            reask += 1
    engs = [e["engagement_index"] for e in eps if e["engagement_index"] is not None]
    last = last_diagnosis(conn, student_id, kp_id)
    last_diag = None
    if last and last["ended_at"] and last["ended_at"] <= at_time:
        last_diag = {"mu": last["post_mu"], "sd": last["post_sd"],
                     "days_ago": (at_time - last["ended_at"]) / 86400.0, "session_id": last["id"]}
    return {"n_past_episodes": len(eps), "reask_count": reask,
            "past_engagement_mean": (sum(engs) / len(engs)) if engs else None,
            "last_diag": last_diag}


def context_text(conn, student_id: str, kp_id: str, current_episode_id=None) -> str:
    """给辅导模型看的历史摘要。"""
    eps = past_episodes(conn, student_id, kp_id, exclude_id=current_episode_id, limit=5)
    lines = []
    for e in eps:
        day = time.strftime("%m月%d日", time.localtime(e["started_at"]))
        tag = "已表示明白" if e["resolved"] else "未明确解决"
        lines.append(f"- {day}：{e['question_summary']}（{tag}）")
    last = last_diagnosis(conn, student_id, kp_id)
    if last:
        day = time.strftime("%m月%d日", time.localtime(last["ended_at"]))
        prof = loads(last["profile_json"], {}) or {}
        lines.append(f"- {day} 做过诊断：独立掌握概率约 {last['p_mastered']:.0%}，"
                     f"最弱的一步是“{prof.get('weakest_step', '未知')}”")
    return "\n".join(lines) if lines else "（这个知识点上没有历史记录）"
