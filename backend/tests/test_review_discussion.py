"""复习问答只属于自己的这一道小题，完成前不能用它获取评判依据。"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, llm, models as m, quiz, diagnosis
from app.web.app import create_app


def test_finished_review_has_private_retryable_followups(demo, monkeypatch):
    uid = "2500000001"
    with db.session_scope() as s:
        item = s.scalars(select(m.QuizItem).where(m.QuizItem.skill_code.like("C%"))).first()
        a = quiz.new_attempt(s, uid, item, "review")
        aid = a.id
        assert not any(x.shown for x in diagnosis.count([(a, item)]).values())

    with TestClient(create_app()) as client:
        def login(user):
            assert client.post("/api/auth/login", json={"loginName": user, "password": "test-pass"}).status_code == 200

        login(uid)
        url = f"/api/review/{aid}/discussion"
        question = {"text": "这个循环从哪里开始？", "requestId": "example-turn-1"}
        assert client.post(url, json=question).status_code == 409
        for _ in range(2):
            assert client.post(f"/api/quiz/{aid}/viewed").status_code == 200
        with db.session_scope() as s:
            a = s.get(m.QuizAttempt, aid)
            assert [ev["kind"] for ev in a.events] == ["prepared", "viewed"]
            a.state, a.outcome, a.correct = "done", "right_judge", True
            a.judged_has_bug, a.judge_correct = False, True
        calls = []
        def model(prompt_id, system, user, validate):
            calls.append(user)
            assert uid not in user and "standardOutput" not in user
            return validate({"answer": "先看初始化语句。"})
        monkeypatch.setattr(llm, "call_json", model)
        before = client.get("/api/threads/general").json()
        assert client.get(url).json() == {"turns": []}
        response = client.post(url, json=question)
        assert response.status_code == 200 and response.json()["turns"][0]["answer"] == "先看初始化语句。"
        assert client.post(url, json=question).json() == client.get(url).json() == response.json()
        assert len(calls) == 1 and client.get("/api/threads/general").json() == before
        with db.session_scope() as s:   # 每问记了时间，存进了库（导出给研究用）
            assert s.get(m.QuizAttempt, aid).discussion[0]["t"]
        client.post("/api/auth/logout")
        login("2500000003")
        assert client.get(url).status_code == client.post(url, json=question).status_code == 404


def test_review_history_lists_finished_questions(demo):
    """复习提升左边的列表：只有自己做完的复习题；没做完的打不开（反馈之前不给题的结果）。"""
    uid = "2500000001"
    with db.session_scope() as s:
        item = s.scalars(select(m.QuizItem).where(m.QuizItem.skill_code.like("C%"))).first()
        done = quiz.new_attempt(s, uid, item, "review")
        done.state, done.outcome, done.correct, done.finished_at = "done", "right_judge", True, db.now()
        done.judged_has_bug, done.judge_correct = False, True
        open_ = quiz.new_attempt(s, uid, item, "review")
        done_id, open_id = done.id, open_.id
    with TestClient(create_app()) as client:
        client.post("/api/auth/login", json={"loginName": uid, "password": "test-pass"})
        rows = client.get("/api/review/attempts").json()["attempts"]
        assert rows[0]["attemptId"] == done_id and open_id not in {r["attemptId"] for r in rows}
        assert set(rows[0]) == {"attemptId", "title", "time", "right", "asked"} and "C0" not in rows[0]["title"]
        one = client.get(f"/api/review/attempts/{done_id}").json()
        assert one["quiz"]["attemptId"] == done_id and one["quiz"]["state"] == "done"
        assert client.get(f"/api/review/attempts/{open_id}").status_code == 409
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"loginName": "2500000003", "password": "test-pass"})
        assert client.get(f"/api/review/attempts/{done_id}").status_code == 404
        assert done_id not in {r["attemptId"] for r in client.get("/api/review/attempts").json()["attempts"]}
