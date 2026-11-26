"""持续同步时，也要恢复漏记证据，并让班级分数与知识图谱读到新状态。"""
from sqlalchemy import delete, func, select
from fastapi.testclient import TestClient

from app import automation, db, llm, models as m, pipeline
from app.labeling import problems
from app.web.app import create_app
from app.xdoj.daemon import Daemon
from app.xdoj.store import SyncContext


def test_busy_sync_repairs_scores_and_knowledge_without_model_calls(demo, monkeypatch):
    uid, sid = "2500000001", demo["server_id"]
    with db.session_scope() as s:
        s.execute(delete(m.SkillEvidence).where(m.SkillEvidence.user_id == uid))

    def forbidden(*args, **kwargs):
        raise AssertionError("恢复证据不得调用模型或生成新的题目标注")

    monkeypatch.setattr(llm, "call_json", forbidden)
    monkeypatch.setattr(problems, "tag_label", forbidden)
    ctx = SyncContext(server_id=sid, client=None)
    daemon = Daemon([ctx], maintenance=automation.maintain_mastery)
    monkeypatch.setattr(daemon, "_meta_due", lambda c: False)
    monkeypatch.setattr(daemon, "_handle_triggers", lambda: False)
    monkeypatch.setattr(daemon, "_poll_due", lambda c: False)
    monkeypatch.setattr(daemon, "_run_task", lambda c, kinds, priority, **kw: priority == 1 and "detail" in kinds)
    with TestClient(create_app()) as client:
        assert client.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"}).status_code == 200
        base = f"/api/teacher/classes/c{sid}-31"

        def row():
            return next(r for r in client.get(base + "/students").json()["students"] if r["userId"] == uid)

        assert row()["score"] is None
        version = client.get("/api/teacher/version").json()["data"]
        before = client.get(base + "/knowledge-map", params={"student": uid}).json()
        assert not any(n.get("student", {}).get("score") is not None for n in before["nodes"] if n.get("student"))
        # 同步队列始终繁忙，也不能阻止维护执行。
        assert daemon.step() == "p1"
        assert row()["score"] is not None
        after = client.get(base + "/knowledge-map", params={"student": uid}).json()
        assert any(n.get("student", {}).get("score") is not None for n in after["nodes"] if n.get("student"))
        assert client.get("/api/teacher/version").json()["data"] != version
    with db.session_scope() as s:
        count = s.scalar(select(func.count()).select_from(m.SkillEvidence))
    assert automation.maintain_mastery() == 0
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(m.SkillEvidence)) == count


def test_maintenance_throttles_and_does_not_block_sync(ctx, clock, monkeypatch):
    calls = []
    daemon = Daemon([ctx], maintenance=lambda: calls.append(1))
    daemon._maintain(ctx)
    daemon._maintain(ctx)
    assert len(calls) == 1
    clock.advance(60)
    assert daemon._maintain(ctx) is False and len(calls) == 2
