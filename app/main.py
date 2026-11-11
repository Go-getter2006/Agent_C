"""Web 服务入口。运行：python -m app.main"""
import logging
import re

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from . import chat_service, config, db, diagnosis, export
from . import learner_model as lm
from .db import loads
from .knowledge import KNOWLEDGE_POINTS, kp_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ctutor")

STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{2,32}$")


def create_app():
    db.init_db()
    app = Flask(__name__, static_folder=None)
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def student_id_from(data):
        sid = str(data.get("student_id", "")).strip()
        if not STUDENT_ID_RE.match(sid):
            abort(400, description="学生编号只能包含字母、数字、下划线或短横线，长度 2~32。")
        return sid

    def require_teacher():
        token = request.headers.get("X-Teacher-Token") or request.args.get("token", "")
        if config.TEACHER_TOKEN:
            if token != config.TEACHER_TOKEN:
                abort(403, description="教师口令不正确。")
        elif request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403, description="未设置 TEACHER_TOKEN 时，教师页只允许在本机访问。")

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def _err(e):
        return jsonify({"error": getattr(e, "description", str(e))}), e.code

    # ------------------------------------------------------------ 页面
    @app.get("/")
    def index():
        return send_from_directory(config.STATIC_DIR, "index.html")

    @app.get("/teacher")
    def teacher_page():
        return send_from_directory(config.STATIC_DIR, "teacher.html")

    # ------------------------------------------------------------ 学生端接口
    @app.get("/api/meta")
    def meta():
        return jsonify({
            "mock": config.MOCK_LLM, "model": config.DEEPSEEK_MODEL, "code_runner": config.ENABLE_CODE_RUNNER,
            "show_debug": config.SHOW_DEBUG,
            "knowledge_points": [{"id": k["id"], "name": k["name"]} for k in KNOWLEDGE_POINTS],
            "kps_with_items": [{"id": k, "name": kp_name(k)} for k in diagnosis.kps_with_items()],
            "steps": [{"name": n, "supports": s} for n, s in zip(config.STEP_NAMES, config.STEP_SUPPORTS)],
        })

    @app.post("/api/chat")
    def chat():
        data = body()
        sid = student_id_from(data)
        msg = str(data.get("message", ""))
        code = str(data.get("code", "") or "")
        if not msg.strip() and not code.strip():
            abort(400, description="消息不能为空。")
        with db.session() as conn:
            out = chat_service.handle_message(conn, sid, msg or "（见代码）", code)
        if not config.SHOW_DEBUG:
            out.pop("analysis", None)
            out.pop("engagement", None)
        return jsonify(out)

    @app.post("/api/resolve")
    def resolve():
        data = body()
        with db.session() as conn:
            return jsonify(chat_service.mark_resolved(conn, student_id_from(data)))

    @app.post("/api/diagnosis/start")
    def diag_start():
        data = body()
        sid = student_id_from(data)
        with db.session() as conn:
            out = diagnosis.start(conn, sid, kp_id=data.get("kp_id") or None, episode_id=data.get("episode_id"))
        return jsonify(out), (400 if "error" in out else 200)

    @app.post("/api/diagnosis/answer")
    def diag_answer():
        data = body()
        sid = student_id_from(data)
        try:
            session_id, step = int(data.get("session_id")), int(data.get("step"))
        except (TypeError, ValueError):
            abort(400, description="缺少 session_id 或 step。")
        with db.session() as conn:
            owner = conn.execute("SELECT student_id FROM diag_sessions WHERE id=?", (session_id,)).fetchone()
            if not owner or owner["student_id"] != sid:
                abort(403, description="这不是你的诊断。")
            out = diagnosis.answer(conn, session_id, step, data.get("answer", ""))
        return jsonify(out), (400 if "error" in out else 200)

    @app.get("/api/mastery/<student_id>")
    def mastery(student_id):
        if not STUDENT_ID_RE.match(student_id):
            abort(400, description="学生编号格式不对。")
        with db.session() as conn:
            return jsonify({"student_id": student_id, "mastery": diagnosis.mastery_of(conn, student_id)})

    # ------------------------------------------------------------ 教师端与研究数据
    @app.get("/api/teacher/overview")
    def overview():
        require_teacher()
        with db.session() as conn:
            ms = {(r["student_id"], r["kp_id"]): dict(r) for r in conn.execute("SELECT * FROM mastery").fetchall()}
            agg = conn.execute(
                "SELECT student_id, kp_id, COUNT(*) AS n_episodes, SUM(CASE WHEN reask_of IS NOT NULL THEN 1 ELSE 0 END) "
                "AS n_reask, AVG(engagement_index) AS mean_engagement FROM episodes GROUP BY student_id, kp_id").fetchall()
            keys = {(r["student_id"], r["kp_id"]) for r in agg} | set(ms.keys())
            aggd = {(r["student_id"], r["kp_id"]): dict(r) for r in agg}
            rows = []
            for sid, kp in sorted(keys):
                m, a = ms.get((sid, kp)), aggd.get((sid, kp), {})
                prof = loads(m["profile_json"], {}) if m else {}
                rows.append({
                    "student_id": sid, "kp_id": kp, "kp_name": kp_name(kp),
                    "n_episodes": a.get("n_episodes", 0), "n_reask": a.get("n_reask", 0),
                    "mean_engagement": round(a["mean_engagement"], 3) if a.get("mean_engagement") is not None else None,
                    "mu": round(m["mu"], 3) if m else None, "sd": round(m["sd"], 3) if m else None,
                    "p_mastered": round(m["p_mastered"], 3) if m else None,
                    "level": lm.level_label(m["p_mastered"]) if m else "未诊断",
                    "profile": prof.get("steps", []), "weakest_step": prof.get("weakest_step"),
                })
        return jsonify({"rows": rows})

    @app.get("/api/export/<name>.csv")
    def export_csv(name):
        require_teacher()
        if name not in ("episodes", "turns", "diagnoses", "responses", "ablation"):
            abort(404, description="没有这张表。")
        with db.session() as conn:
            text = export.to_csv(export.table_rows(conn, name))
        return Response("﻿" + text, mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}.csv"})

    return app


app = create_app()

if __name__ == "__main__":
    mode = "模拟模式（未调用 DeepSeek）" if config.MOCK_LLM else f"DeepSeek 模型 {config.DEEPSEEK_MODEL}"
    log.info("启动：http://%s:%s  %s", config.HOST, config.PORT, mode)
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False)
