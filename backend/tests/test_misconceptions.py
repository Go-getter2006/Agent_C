"""误区按北京时间自然周统计，导入和评估延迟不改变原事件所属周。"""
import datetime as dt

from fastapi.testclient import TestClient

from app import db, misconceptions, models as m
from app.web.app import create_app

NOW = dt.datetime(2026, 11, 26, 12, tzinfo=db.BEIJING)
MONDAY = dt.datetime(2026, 11, 23, tzinfo=db.BEIJING)


def test_calendar_week_uses_original_submission_time_not_import_time(database):
    with db.session_scope() as s:
        s.add(m.Server(id=1, code="test", name="test", course="C", base_url="http://fake.test"))
        for i, at in enumerate([MONDAY - dt.timedelta(microseconds=1), MONDAY, NOW,
                                 NOW + dt.timedelta(seconds=1)], start=1):
            s.add(m.Submission(server_id=1, submission_id=i, user_id="u", problem_id=i,
                               judgement="WA", submit_time=at, fetched_at=NOW))
            # 标注在事件发生几天以后才得到，照样应进入原提交所属的本周。
            s.add(m.SubmissionLabel(server_id=1, submission_id=i, error_code="TY09",
                skill_code="C06.03.02", confidence=.9, created_at=NOW + dt.timedelta(days=7)))
    with db.session_scope() as s:
        rows = misconceptions.collect(s, 1, {"u": "测试学生"}, "C", "week", NOW)
        assert len(rows) == 1 and rows[0]["submissions"] == 2
        assert {r["problemId"] for r in rows[0]["problems"]} == {2, 3}
        # 北京时间周一零点在 UTC 仍是周日，不得按 UTC 的周一切。
        rows = misconceptions.collect(s, 1, {"u": "测试学生"}, "C", "week", MONDAY)
        assert rows[0]["submissions"] == 1 and rows[0]["problems"][0]["problemId"] == 2
        assert misconceptions.collect(s, 1, {"u": "测试学生"}, "D", "week", NOW) == []


def test_delayed_assessments_use_submission_and_chat_time(database):
    names = {}
    with db.session_scope() as s:
        s.add(m.Server(id=1, code="test", name="test", course="C", base_url="http://fake.test"))
        for i, (basis, at) in enumerate([
            ("code", MONDAY), ("chat", MONDAY),
            ("code", MONDAY - dt.timedelta(seconds=1)), ("chat", MONDAY - dt.timedelta(seconds=1))
        ], start=1):
            uid = f"u{i}"
            names[uid] = uid
            if basis == "code":
                s.add(m.Submission(server_id=1, submission_id=i, user_id=uid, problem_id=1,
                                   judgement="AC", submit_time=at))
            else:
                s.add(m.ChatRound(id=i, user_id=uid, server_id=1, course="C", status="closed",
                                 started_at=at, last_message_at=at, ended_at=at))
            s.add(m.AgentAssessment(user_id=uid, server_id=1, course="C", basis=basis,
                round_id=i if basis == "chat" else None, submission_id=i if basis == "code" else None,
                skill_code="C06.03.02", misconception=f"误解{i}",
                created_at=NOW + dt.timedelta(days=7) if i <= 2 else NOW))
    with db.session_scope() as s:
        rows = misconceptions.collect(s, 1, names, "C", "week", NOW)
        assert len(rows) == 1 and rows[0]["students"] == 2
        assert set(rows[0]["quotes"]) == {"误解1", "误解2"}
        assert rows[0]["last"] == MONDAY.astimezone(db.UTC).isoformat()


def test_late_label_invalidates_empty_or_old_misconception_cache(demo):
    sid, uid, now = demo["server_id"], "2500000001", db.now()
    with db.session_scope() as s:
        s.add(m.Submission(server_id=sid, submission_id=991234, user_id=uid, problem_id=406,
                           judgement="WA", submit_time=now, fetched_at=now))
    with TestClient(create_app()) as client:
        client.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        url = f"/api/teacher/classes/c{sid}-31/misconceptions?period=week"
        before = client.get(url).json()["items"]
        key = "TY09:C06.03.02"
        n = next((r["submissions"] for r in before if r["key"] == key), 0)
        version = client.get("/api/teacher/version").json()["data"]
        # 不新增提交、评测或掌握证据，只补上已入库的本周提交的错误标注。
        with db.session_scope() as s:
            s.add(m.SubmissionLabel(server_id=sid, submission_id=991234, error_code="TY09",
                                   skill_code="C06.03.02", confidence=.9, created_at=now))
        assert client.get("/api/teacher/version").json()["data"] != version
        after = client.get(url).json()["items"]
        assert next(r["submissions"] for r in after if r["key"] == key) == n + 1
