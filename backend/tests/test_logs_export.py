"""日志导出补全：每条对话标栏目；勾了“包含原文”才带贴的代码、复习小题的全过程；再勾“包含提交的源码”才带源码。"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app import models as m
from app.web.app import create_app

UID = "2500000001"
WHEN = dt.datetime(2026, 11, 18, 10, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def client(demo):
    with TestClient(create_app()) as c:
        yield c


def _seed(sid: int) -> dict:
    """三个栏目的对话、一条主动消息、一道做完的复习题（首稿、运行、反馈后的问答、回顾都有）。"""
    with db.session_scope() as s:
        topic = m.ChatTopic(user_id=UID, course="C", title="指针", created_at=WHEN)
        s.add(topic)
        s.flush()
        s.add_all([
            m.ChatMessage(user_id=UID, topic_id=topic.id, course="C", role="student", kind="text", created_at=WHEN,
                          content={"blocks": [{"t": "p", "text": "指针加一是什么意思"}]}),
            m.ChatMessage(user_id=UID, server_id=sid, problem_id=406, course="C", role="student", kind="text",
                          created_at=WHEN + dt.timedelta(minutes=1),
                          content={"blocks": [{"t": "p", "text": "这样写哪里不对"},
                                              {"t": "code", "caption": "我的代码", "lines": ["int max = a[0];", "PASTED_MARK"]}]}),
            m.ChatMessage(user_id=UID, server_id=sid, problem_id=406, course="C", role="agent", kind="text",
                          failure_submission_id=987654, created_at=WHEN + dt.timedelta(minutes=2),
                          content={"blocks": [{"t": "p", "text": "这次没过，先看看循环从哪里开始"}]}),
        ])
        item = s.scalars(select(m.QuizItem).where(m.QuizItem.quiz_type == "FIX")).first()
        a = m.QuizAttempt(user_id=UID, item_id=item.id, quiz_type=item.quiz_type, skill_code=item.skill_code,
                          reason="review", state="done", protocol=3, outcome="right", created_at=WHEN + dt.timedelta(minutes=3),
                          first_lines=[2], first_fix="FIRST_DRAFT_MARK",
                          runs=[{"kind": "fail", "message": "输出不对", "counted": True, "code": "RUN1_MARK"},
                                {"kind": "pass", "message": "通过", "counted": True, "code": "RUN2_MARK"}],
                          events=[{"t": WHEN.isoformat(), "kind": "judge", "says": "bug", "confidence": ""},
                                  {"t": WHEN.isoformat(), "kind": "locate", "lines": [2], "role": "where"}],
                          discussion=[{"id": "q1", "question": "为什么从 1 开始", "answer": "ANSWER_MARK",
                                       "t": WHEN.isoformat()}],
                          recap={"items": [], "analysis": "RECAP_MARK", "source": "llm"})
        s.add(a)
        s.flush()
        return {"item": item.stem, "shown": item.shown_code}


def _download(client, body: dict) -> dict:
    r = client.post("/api/teacher/logs", json=body)
    assert r.status_code == 200, r.text
    req = r.json()["request"]
    return client.get(f"/api/teacher/logs/{req['id']}/download", params={"token": req["token"]}).json()


BODY = {"ids": UID, "dateFrom": "2026-06-01", "dateTo": "2027-05-31", "format": "json", "purpose": "论文数据分析",
        "kinds": ["submission", "ask", "reply", "review"]}


def test_sections_and_full_text(client, demo):
    seeded = _seed(demo["server_id"])
    client.post("/api/auth/login", json={"loginName": "dev-test", "password": "dev-pass"})   # 日志申请只给开发者

    plain = _download(client, BODY)
    rows = plain["rows"]
    asks = [r for r in rows if r["kind"] == "提问和求助"]
    assert {"知识问答", "作业引导"} <= {r["section"] for r in asks}
    notice = next(r for r in rows if r["action"] == "提交没过的提醒")
    assert notice["section"] == "主动消息" and notice["submission_id"] == 987654
    review = next(r for r in rows if r["kind"] == "复习" and r["data"].get("outcome") == "right"
                  and r["data"].get("firstLines") == [2])
    assert review["section"] == "复习提升"
    flat = str(plain)
    for mark in ("PASTED_MARK", "FIRST_DRAFT_MARK", "RUN1_MARK", "ANSWER_MARK", "RECAP_MARK"):
        assert mark not in flat, mark          # 不勾“包含原文”就不带
    assert "text" not in rows[0] and "code" not in rows[0]
    assert plain["request"]["versions"]["export"] == "logs-7"

    full = _download(client, {**BODY, "includeText": True})
    rows = full["rows"]
    pasted = next(r for r in rows if r["kind"] == "提问和求助" and r["section"] == "作业引导" and "PASTED_MARK" in r["code"])
    assert "【我的代码】" in pasted["code"] and pasted["data"]["codeBlocks"][0]["caption"] == "我的代码"
    review = next(r for r in rows if r["kind"] == "复习" and "FIRST_DRAFT_MARK" in r["code"])
    d = review["data"]
    assert d["quiz"]["stem"] == seeded["item"] and d["quiz"]["shownCode"] == seeded["shown"]
    assert d["firstFix"] == "FIRST_DRAFT_MARK" and [x["code"] for x in d["runs"]] == ["RUN1_MARK", "RUN2_MARK"]
    assert d["discussion"] == [{"question": "为什么从 1 开始", "answer": "ANSWER_MARK", "t": WHEN.isoformat()}]
    assert d["recap"]["analysis"] == "RECAP_MARK"
    assert {"says", "lines", "role"} <= {k for e in d["events"] for k in e}       # 完整事件
    assert "ANSWER_MARK" in review["text"] and "RECAP_MARK" in review["text"] and "RUN2_MARK" in review["code"]
    subs = [r for r in rows if r["kind"] == "在 XDOJ 提交"]
    assert subs and all(not r["code"] and "source" not in r["data"] for r in subs)   # 源码要另外勾

    with_code = _download(client, {**BODY, "includeText": True, "includeCode": True})
    subs = [r for r in with_code["rows"] if r["kind"] == "在 XDOJ 提交"]
    assert any(r["code"] and r["data"]["source"] == r["code"] for r in subs)
    assert with_code["request"]["includeCode"] is True

    only_code = client.post("/api/teacher/logs", json={**BODY, "includeCode": True}).json()["request"]
    assert only_code["includeCode"] is False      # 没勾“包含原文”，源码也不给


def test_csv_has_section_and_code_columns(client, demo):
    _seed(demo["server_id"])
    client.post("/api/auth/login", json={"loginName": "dev-test", "password": "dev-pass"})   # 日志申请只给开发者
    req = client.post("/api/teacher/logs", json={**BODY, "format": "csv", "includeText": True}).json()["request"]
    text = client.get(f"/api/teacher/logs/{req['id']}/download", params={"token": req["token"]}).content.decode("utf-8-sig")
    header = text.splitlines()[0].split(",")
    assert header[:6] == ["学号", "姓名", "时间", "类型", "交互", "栏目"] and header[-2:] == ["原文", "代码"]
    assert "FIRST_DRAFT_MARK" in text and "主动消息" in text
