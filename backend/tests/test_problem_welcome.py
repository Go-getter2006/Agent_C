"""从同步评测到会话已读的闭环，全程使用假 XDOJ。"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, models as m
from app.chat import proactive
from app.web.app import create_app
from app.xdoj import endpoints, sync_meta, sync_submissions

UID = "2500000001"


def test_sync_writes_once_and_only_acknowledges_the_visible_notice(ctx, clock, monkeypatch):
    monkeypatch.setattr("app.config.AUTH_MODE", "dev")
    sid = ctx.server_id
    sync_meta.sync_metadata(ctx)
    with db.session_scope() as s:
        active = m.ChatRound(user_id=UID, course="C", server_id=sid, problem_id=407,
                             started_at=clock.now(), last_message_at=clock.now(), status="open")
        s.add(active)
    sync_submissions.poll_new_submissions(ctx)
    sync_submissions.fetch_detail(ctx, 537101)
    with db.session_scope() as s:
        first = s.scalar(select(m.ChatMessage).where(m.ChatMessage.failure_submission_id == 537101))
        assert first is not None and first.round_id is None
        assert s.get(m.ChatRound, active.id).status == "open"
        row = endpoints.SubmissionRow(990001, UID, "", 406, 501, 31, clock.now(), "C", "答案错误", 0)
        assert sync_submissions._store_submission(s, ctx, row, False) == 1
        assert sync_submissions._store_submission(s, ctx, row, False) == 0
        second = s.scalar(select(m.ChatMessage).where(m.ChatMessage.failure_submission_id == row.submission_id))
        assert second and second.id != first.id
        assert proactive.on_failed_submission(s, s.get(m.Submission, (sid, row.submission_id)), clock.now()) is None

    key, other = f"p{sid}-406", f"p{sid}-407"
    with TestClient(create_app()) as client:
        url = "/api/threads/" + key
        assert client.get(url).status_code == 401
        assert client.post("/api/auth/login", json={"loginName":UID, "password":"test-pass"}).status_code == 200
        def unread():
            return client.get("/api/status").json()["unread"]
        assert unread()["problems"][key] == second.id and f"h{sid}-501" in unread()["homeworks"]
        msgs = client.get(url).json()["messages"]
        assert [msg["id"] for msg in msgs if msg.get("unreadNotice")] == [first.id, second.id]
        assert client.post(url + "/welcome").json()["messages"] == msgs
        assert all(msg["role"] == "agent" and "proactive" not in msg for msg in msgs)
        client.post("/api/threads/" + other + "/read", json={"ids":[second.id]})
        client.post(url + "/read", json={"ids":[first.id]})
        assert unread()["problems"][key] == second.id
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"loginName":"2500000002", "password":"test-pass"})
        client.post(url + "/read", json={"ids":[second.id]})
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"loginName":UID, "password":"test-pass"})
        assert unread()["problems"][key] == second.id
        client.post(url + "/read", json={"ids":[second.id]})
        assert key not in unread()["problems"] and other in unread()["problems"]


@pytest.mark.parametrize("backfill", [True, False])
def test_pending_to_failed_and_old_backfill(ctx, clock, backfill):
    sync_meta.sync_metadata(ctx)
    row = endpoints.SubmissionRow(537101, UID, "", 406, 501, 31, clock.now(), "C", "评测中", None)
    with db.session_scope() as s:
        sync_submissions._store_submission(s, ctx, row, backfill)
        assert s.scalar(select(m.ChatMessage.id)) is None
    clock.advance(dt.timedelta(days=1).total_seconds())
    sync_submissions.fetch_detail(ctx, row.submission_id)
    sync_submissions.fetch_detail(ctx, row.submission_id)
    with db.session_scope() as s:
        notices = s.scalars(select(m.ChatMessage).where(m.ChatMessage.failure_submission_id == row.submission_id)).all()
        assert len(notices) == (0 if backfill else 1)


def test_reading_the_newest_notice_clears_older_ones(ctx, clock, monkeypatch):
    """看到了一道题最新的提醒，更早的、没往上翻到的提醒也算看过：作业和题目上的红点不再一直挂着。"""
    monkeypatch.setattr("app.config.AUTH_MODE", "dev")
    sid = ctx.server_id
    sync_meta.sync_metadata(ctx)
    with db.session_scope() as s:
        for no in (990101, 990102):
            row = endpoints.SubmissionRow(no, UID, "", 406, 501, 31, clock.now(), "C", "答案错误", 0)
            sync_submissions._store_submission(s, ctx, row, False)
            clock.advance(60)
        older, newer = (s.scalar(select(m.ChatMessage.id).where(m.ChatMessage.failure_submission_id == no))
                        for no in (990101, 990102))
    key = f"p{sid}-406"
    with TestClient(create_app()) as client:
        client.post("/api/auth/login", json={"loginName": UID, "password": "test-pass"})
        assert client.get("/api/status").json()["unread"]["problems"][key] == newer
        client.post(f"/api/threads/p{sid}-407/read", json={"ids": [newer]})   # 别的题的对话里报的不算
        assert client.get("/api/status").json()["unread"]["problems"][key] == newer
        client.post(f"/api/threads/{key}/read", json={"ids": [newer]})
        assert key not in client.get("/api/status").json()["unread"]["problems"]
    with db.session_scope() as s:
        assert s.get(m.ChatMessage, older).notice_read_at is not None
