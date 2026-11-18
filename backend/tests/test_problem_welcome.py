from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import db, models as m
from app.chat import proactive
from app.web.app import create_app

UID = "2500000001"


def clear_thread(s, sid):
    s.execute(delete(m.ChatMessage).where(m.ChatMessage.user_id == UID,
                                        m.ChatMessage.server_id == sid, m.ChatMessage.problem_id == 406))


def test_open_failure_adds_one_agent_question_without_fabricating_student_messages(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        clear_thread(s, sid)
    with TestClient(create_app()) as client:
        url = f"/api/threads/p{sid}-406/welcome"
        assert client.post(url).status_code == 401
        client.post("/api/auth/login", json={"loginName":UID, "password":"test-pass"})
        first = client.post(url)
        assert first.status_code == 200
        messages = first.json()["messages"]
        assert len(messages) == 1 and messages[0]["role"] == "agent"
        assert messages[0]["actions"] == ["think", "hint", "tell"]
        assert any(b["t"] == "code" and len(b["lines"]) >= 5 for b in messages[0]["blocks"])
        assert [m["id"] for m in client.post(url).json()["messages"]] == [messages[0]["id"]]
        assert client.post("/api/threads/p999-99999/welcome").status_code == 404
        assert all("proactive" not in msg and "evidence" not in msg for msg in messages)
    with db.session_scope() as s:
        messages = s.scalars(select(m.ChatMessage).where(m.ChatMessage.user_id == UID,
                             m.ChatMessage.server_id == sid, m.ChatMessage.problem_id == 406)).all()
        assert len(messages) == 1 and all(msg.role == "agent" for msg in messages)


@pytest.mark.parametrize("state", ["passed", "pending", "unknown", "active_chat", "wrong_course"])
def test_open_does_not_interrupt_passed_pending_unknown_or_current_chat(demo, state):
    sid, now = demo["server_id"], db.now()
    with db.session_scope() as s:
        clear_thread(s, sid)
        sub = s.get(m.Submission, (sid, 537201))
        if state in ("passed", "pending", "unknown"):
            sub.judgement = {"passed":"AC", "pending":"PENDING", "unknown":"UNKNOWN"}[state]
        if state == "active_chat":
            s.add(m.ChatMessage(user_id=UID, server_id=sid, problem_id=406, course="C", role="student",
                                kind="text", created_at=now, content={"blocks":[{"t":"p","text":"我在检查循环。"}]}))
        s.flush()
        course = "D" if state == "wrong_course" else "C"
        assert proactive.on_open_problem(s, UID, (sid, 406), course, now) is None


def test_new_failed_submission_can_receive_one_new_question_after_quiet_period(demo):
    sid, now = demo["server_id"], db.now()
    with db.session_scope() as s:
        clear_thread(s, sid)
        first = proactive.on_open_problem(s, UID, (sid, 406), "C", now)
        assert first is not None
        later = now + dt.timedelta(minutes=proactive.cfg()["quiet_minutes"] + 2)
        s.add(m.Submission(server_id=sid, submission_id=900000, problem_id=406, user_id=UID,
                           judgement="WA", judgement_text="答案错误", detail_status="pending", submit_time=later))
        s.flush()
        second = proactive.on_open_problem(s, UID, (sid, 406), "C", later + dt.timedelta(seconds=1))
        assert second is not None and second.id != first.id
        assert all(b["t"] == "p" for b in second.content["blocks"])  # 新源码没取到，不能引用上一份代码。
        assert proactive.on_open_problem(s, UID, (sid, 406), "C", later + dt.timedelta(seconds=2)) is None
