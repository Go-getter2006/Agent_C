"""诊断流程：用先验 + 自适应选题，逐题逐步作答，直到能可靠估计独立掌握程度。"""
import json
import os

from . import config, grader, history
from . import learner_model as lm
from .db import dumps, loads, now
from .knowledge import kp_name

SKIP = "__skip__"
_BANK = {"mtime": None, "items": {}}


def bank():
    """读取题库；文件被修改后自动重新加载。"""
    path = config.ITEM_BANK_PATH
    mtime = os.path.getmtime(path)
    if _BANK["mtime"] != mtime:
        data = json.loads(path.read_text(encoding="utf-8"))
        _BANK["items"] = {it["id"]: it for it in data["items"]}
        _BANK["mtime"] = mtime
    return _BANK["items"]


def items_for_kp(kp_id):
    return [it for it in bank().values() if it["kp"] == kp_id]


def kps_with_items():
    return sorted({it["kp"] for it in bank().values()})


# ---------------------------------------------------------------- 内部工具
def _session(conn, session_id):
    row = conn.execute("SELECT * FROM diag_sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def _responses(conn, session_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM responses WHERE session_id=? ORDER BY id", (session_id,)).fetchall()]


def administered(conn, session_id):
    """[(item, [(step, passed), ...]), ...]，按作答顺序。"""
    groups = {}
    order = []
    for r in _responses(conn, session_id):
        key = r["item_order"]
        if key not in groups:
            groups[key] = (r["item_id"], [])
            order.append(key)
        groups[key][1].append((r["step"], bool(r["passed"])))
    items = bank()
    return [(items[groups[k][0]], groups[k][1]) for k in order if groups[k][0] in items]


def _posterior(sess, adm):
    thetas = lm.grid()
    prior = lm.normal_prior(sess["prior_mu"], sess["prior_sd"], thetas)
    post = lm.posterior(prior, thetas, adm)
    return thetas, post


def _candidates(conn, sess, adm):
    used_here = {it["id"] for it, _ in adm}
    seen_before = {r["item_id"] for r in conn.execute(
        "SELECT DISTINCT item_id FROM responses WHERE student_id=? AND kp_id=? AND session_id != ?",
        (sess["student_id"], sess["kp_id"], sess["id"])).fetchall()}
    pool = [it for it in items_for_kp(sess["kp_id"]) if it["id"] not in used_here]
    fresh = [it for it in pool if it["id"] not in seen_before]
    return fresh or pool  # 优先没做过的题；都做过了才重复


def _choose_next(conn, sess, thetas, post, adm):
    cands = _candidates(conn, sess, adm)
    best, score, scored = lm.select_item(post, thetas, cands)
    log = loads(sess.get("selection_log"), []) or []
    log.append({"after_items": len(adm), "scores": scored})
    conn.execute("UPDATE diag_sessions SET selection_log=? WHERE id=?", (dumps(log), sess["id"]))
    best_evr = scored[0]["evr"] if scored else None
    return best, best_evr, scored


def _step_payload(item, step):
    payload = {
        "step": step, "step_name": config.STEP_NAMES[step - 1], "supports": config.STEP_SUPPORTS[step - 1],
        "prompt": grader.STEP_PROMPTS[step], "input": grader.STEP_INPUT[step],
    }
    if step == 1:
        payload["choices"] = ["有错", "没错"]
    if step == 4:
        payload["prefill_code"] = item["code"]
    if step == 5:
        payload["prompt"] = grader.STEP_PROMPTS[5] + " " + item["variant"]["prompt"]
        payload["prefill_code"] = "#include <stdio.h>\n\nint main(void) {\n    \n    return 0;\n}"
    return payload


def _item_payload(item):
    return {"id": item["id"], "title": item["title"], "task": item["task"],
            "code_lines": item["code"].split("\n"), "n_steps": len(lm.step_params(item))}


def _debug(sess, thetas, post, scored=None):
    if not config.SHOW_DEBUG:
        return None
    s = lm.summarize(post, thetas)
    return {"prior": loads(sess.get("prior_json"), {}).get("prior_info"),
            "posterior": {"mu": round(s["mu"], 3), "sd": round(s["sd"], 3), "p_mastered": round(s["p_mastered"], 3)},
            "selection_scores": scored}


def _finish(conn, sess, thetas, post, reason):
    s = lm.summarize(post, thetas)
    prof = lm.profile(post, thetas)
    t = now()
    conn.execute("UPDATE diag_sessions SET status='finished', ended_at=?, post_mu=?, post_sd=?, p_mastered=?, "
                 "profile_json=?, stop_reason=?, current_item_id=NULL, current_step=NULL WHERE id=?",
                 (t, s["mu"], s["sd"], s["p_mastered"], dumps(prof), reason, sess["id"]))
    conn.execute("INSERT INTO mastery(student_id, kp_id, mu, sd, p_mastered, profile_json, session_id, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(student_id, kp_id) DO UPDATE SET "
                 "mu=excluded.mu, sd=excluded.sd, p_mastered=excluded.p_mastered, "
                 "profile_json=excluded.profile_json, session_id=excluded.session_id, updated_at=excluded.updated_at",
                 (sess["student_id"], sess["kp_id"], s["mu"], s["sd"], s["p_mastered"], dumps(prof), sess["id"], t))
    n_items = conn.execute("SELECT n_items FROM diag_sessions WHERE id=?", (sess["id"],)).fetchone()["n_items"]
    return {
        "mu": round(s["mu"], 3), "sd": round(s["sd"], 3), "p_mastered": round(s["p_mastered"], 3),
        "level": lm.level_label(s["p_mastered"]), "profile": prof, "n_items": n_items,
        "stop_reason": reason, "kp_id": sess["kp_id"], "kp_name": kp_name(sess["kp_id"]),
    }


# ---------------------------------------------------------------- 对外接口
def start(conn, student_id, kp_id=None, episode_id=None):
    ep = None
    if episode_id:
        row = conn.execute("SELECT * FROM episodes WHERE id=? AND student_id=?", (episode_id, student_id)).fetchone()
        ep = dict(row) if row else None
    if ep is None:
        row = conn.execute("SELECT * FROM episodes WHERE student_id=? AND status='open' ORDER BY started_at DESC LIMIT 1",
                           (student_id,)).fetchone()
        ep = dict(row) if row else None
    kp_id = kp_id or (ep["kp_id"] if ep else None)
    if not kp_id or not items_for_kp(kp_id):
        return {"error": f"“{kp_name(kp_id) if kp_id else '未知知识点'}”还没有诊断题。",
                "available_kps": [{"id": k, "name": kp_name(k)} for k in kps_with_items()]}

    # 同一学生同时只保留一个进行中的诊断
    conn.execute("UPDATE diag_sessions SET status='abandoned', ended_at=? WHERE student_id=? AND status='in_progress'",
                 (now(), student_id))

    use_ep = ep if (ep and ep["kp_id"] == kp_id) else None
    hist = history.features(conn, student_id, kp_id, current_episode_id=use_ep["id"] if use_ep else None)
    eng = None
    if use_ep and use_ep.get("n_student_turns"):
        eng = {k: use_ep.get(k) for k in ("n_student_turns", "mean_icap", "attempt_ratio",
                                           "answer_seek_ratio", "engagement_index")}
    mu0, sd0, info = lm.compute_prior(hist, eng)
    cur = conn.execute("INSERT INTO diag_sessions(student_id, kp_id, episode_id, started_at, prior_mu, prior_sd, prior_json) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (student_id, kp_id, use_ep["id"] if use_ep else None, now(), mu0, sd0,
                        dumps({"history": hist, "engagement": eng, "prior_info": info})))
    sess = _session(conn, cur.lastrowid)
    thetas, post = _posterior(sess, [])
    item, best_evr, scored = _choose_next(conn, sess, thetas, post, [])
    conn.execute("UPDATE diag_sessions SET current_item_id=?, current_step=1 WHERE id=?", (item["id"], sess["id"]))
    sess = _session(conn, sess["id"])
    return {"session_id": sess["id"], "status": "in_progress", "kp_id": kp_id, "kp_name": kp_name(kp_id),
            "item": _item_payload(item), "step": _step_payload(item, 1),
            "progress": {"items_done": 0, "max_items": config.MODEL["max_items"]},
            "debug": _debug(sess, thetas, post, scored)}


def answer(conn, session_id, step, ans):
    sess = _session(conn, session_id)
    if not sess or sess["status"] != "in_progress":
        return {"error": "诊断不存在或已经结束。"}
    if int(step) != sess["current_step"]:
        return {"error": f"当前应作答第 {sess['current_step']} 步。"}
    item = bank()[sess["current_item_id"]]
    step = int(step)
    if ans == SKIP:
        res = {"passed": False, "feedback": "已跳过这一步。", "grader": "skip"}
    else:
        res = grader.grade(item, step, ans)
    item_order = sess["n_items"] + 1
    stored = ans if isinstance(ans, str) else dumps(ans)
    conn.execute("INSERT INTO responses(session_id, student_id, kp_id, item_id, item_order, step, answer, passed, "
                 "feedback, grader, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (session_id, sess["student_id"], sess["kp_id"], item["id"], item_order, step, stored[:8000],
                  1 if res["passed"] else 0, res["feedback"], res["grader"], now()))
    n_steps = len(lm.step_params(item))
    step_result = {"step": step, "passed": res["passed"], "feedback": res["feedback"], "grader": res["grader"]}

    if res["passed"] and step < n_steps:
        conn.execute("UPDATE diag_sessions SET current_step=? WHERE id=?", (step + 1, session_id))
        return {"session_id": session_id, "status": "in_progress", "step_result": step_result,
                "item": _item_payload(item), "step": _step_payload(item, step + 1),
                "progress": {"items_done": sess["n_items"], "max_items": config.MODEL["max_items"]}}

    # 这道题结束：更新后验，决定继续还是停止
    conn.execute("UPDATE diag_sessions SET n_items=n_items+1 WHERE id=?", (session_id,))
    sess = _session(conn, session_id)
    adm = administered(conn, session_id)
    thetas, post = _posterior(sess, adm)
    summary = lm.summarize(post, thetas)
    item_feedback = {"item_id": item["id"], "steps_passed": sum(1 for _, p in adm[-1][1] if p),
                     "n_steps": n_steps, "reveal": grader.reveal(item)}
    nxt, best_evr, scored = (None, None, [])
    reason = lm.stop_reason(summary, sess["n_items"], None)
    if reason is None:
        nxt, best_evr, scored = _choose_next(conn, sess, thetas, post, adm)
        reason = "no_items_left" if nxt is None else lm.stop_reason(summary, sess["n_items"], best_evr)
    if reason:
        result = _finish(conn, sess, thetas, post, reason)
        return {"session_id": session_id, "status": "finished", "step_result": step_result,
                "item_feedback": item_feedback, "result": result, "debug": _debug(sess, thetas, post)}
    conn.execute("UPDATE diag_sessions SET current_item_id=?, current_step=1 WHERE id=?", (nxt["id"], session_id))
    return {"session_id": session_id, "status": "in_progress", "step_result": step_result,
            "item_feedback": item_feedback, "item": _item_payload(nxt), "step": _step_payload(nxt, 1),
            "progress": {"items_done": sess["n_items"], "max_items": config.MODEL["max_items"]},
            "debug": _debug(sess, thetas, post, scored)}


def mastery_of(conn, student_id):
    rows = conn.execute("SELECT * FROM mastery WHERE student_id=? ORDER BY kp_id", (student_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kp_name"] = kp_name(d["kp_id"])
        d["profile"] = loads(d.pop("profile_json"), {})
        d["level"] = lm.level_label(d["p_mastered"])
        out.append(d)
    return out
