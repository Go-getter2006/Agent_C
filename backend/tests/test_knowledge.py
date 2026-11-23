"""知识问答：多个问答主题；不自动绑到作业题上，学生选了“结合当前题”才带上；各看各的、按课程分开。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, llm
from app import models as m
from app.web.app import create_app
from app.web.student import topic_title

UID = "2500000001"


@pytest.fixture()
def client(demo):
    with TestClient(create_app()) as c:
        yield c


def login(c: TestClient, name: str = UID):
    assert c.post("/api/auth/login", json={"loginName": name, "password": "test-pass"}).status_code == 200


def _last_step():
    with db.session_scope() as s:
        step = s.scalars(select(m.ChatStep).order_by(m.ChatStep.id.desc())).first()
        return step.bind_by, step.problem_id


def test_reading_thread_never_starts_quiz_generation(client, monkeypatch):
    login(client)

    def unexpected(*args, **kwargs):
        raise AssertionError("读取会话时触发了作业收尾或出题")

    monkeypatch.setattr("app.chat.workflow.refresh_rounds", unexpected)
    assert client.get("/api/threads/general").status_code == 200


def test_topic_title():
    assert topic_title("为什么 5 / 2 存进 double 还是 2？后面还有") == "为什么 5 / 2 存进 double 还是 2"
    assert topic_title("数组下标到底是从 0 还是从 1 开始的呢呢呢呢") == "数组下标到底是从 0 还是从 1 开始…"
    assert topic_title("   ") == "新的问答"


def test_model_failure_keeps_knowledge_history_unchanged(client):
    login(client)
    topics = client.get("/api/topics").json()
    assert client.post("/api/threads/new/messages", json={"text": "for 循环怎么用"}).status_code == 502
    assert client.get("/api/topics").json() == topics


def test_homework_list_reports_own_thread_even_without_submission(client):
    login(client)
    hw = client.get("/api/homeworks").json()["homeworks"][0]["key"]
    rows = client.get(f"/api/homeworks/{hw}").json()["problems"]
    row = next(r for r in rows if r["lastMessageId"] == 0)
    sid, pid = map(int, row["key"][1:].split("-"))
    with db.session_scope() as s:
        for uid in (UID, "2500000003"):
            msg = m.ChatMessage(user_id=uid, server_id=sid, problem_id=pid, course="C", role="agent",
                                kind="text", content={"blocks": [{"t": "p", "text": "讲解"}]})
            s.add(msg)
            s.flush()
            if uid == UID:
                own = msg.id
    rows = client.get(f"/api/homeworks/{hw}").json()["problems"]
    assert next(r for r in rows if r["key"] == row["key"])["lastMessageId"] == own
    assert client.get(f"/api/threads/{row['key']}").json()["messages"][-1]["id"] == own


def test_knowledge_code_block_is_renderable_on_send_and_reopen(client, monkeypatch):
    login(client)
    before = "用 `struct` 定义结构体。"
    after = "用 `.` 访问成员。"
    lines = ["struct Student { int age; };", "struct Student s = {18};"]
    fenced = [before, "```c", *lines, "```", after]
    calls = []

    def answer(*args):
        calls.append(args)
        return json.dumps({"before": "\n".join(fenced), "code": None}), 10, 10

    monkeypatch.setattr(llm, "_backend", answer)
    sent = client.post("/api/threads/new/messages", json={"text": "什么是结构体？"})
    assert sent.status_code == 200
    key = sent.json()["thread"]
    blocks = sent.json()["messages"][-1]["blocks"]
    assert blocks == [
        {"t": "p", "text": before},
        {"t": "code", "caption": "示例", "start": 1, "lines": lines, "mark": []},
        {"t": "p", "text": after},
    ]
    assert len(calls) == 1

    # 模拟修复前已保存的回复，旧围栏和每行代码各自存成段落。
    msg_id = sent.json()["messages"][-1]["id"]
    old_blocks = [{"t": "p", "text": line} for line in fenced]
    with db.session_scope() as s:
        msg = s.get(m.ChatMessage, msg_id)
        assert msg.content["blocks"] == blocks
        msg.content = {**msg.content, "blocks": old_blocks}
    assert client.get(f"/api/threads/{key}").json()["messages"][-1]["blocks"] == blocks
    assert len(calls) == 1
    with db.session_scope() as s:
        assert s.get(m.ChatMessage, msg_id).content["blocks"] == old_blocks


def test_topics_are_separate_threads_and_do_not_bind_homework(client, monkeypatch):
    monkeypatch.setattr(llm, "_backend", lambda *_: ('{"before":"整数除法先算出 2，写成 5.0 / 2 才得到 2.5。"}', 10, 10))
    login(client)
    before = client.get("/api/threads/general").json()["messages"]
    r = client.post("/api/threads/new/messages", json={"text": "为什么 5 / 2 存进 double 还是 2？"})
    assert r.status_code == 200
    key = r.json()["thread"]
    assert key.startswith("t") and len(r.json()["messages"]) == 2
    topics = client.get("/api/topics").json()["topics"]
    assert topics[0]["key"] == key and topics[0]["title"] == "为什么 5 / 2 存进 double 还是 2"
    assert client.get(f"/api/threads/{key}").json()["messages"][0]["role"] == "student"
    assert client.get("/api/threads/general").json()["messages"] == before      # 以前的综合对话不受影响
    assert _last_step() == ("", None)                                         # 不自动绑到最近没过的题
    reply = r.json()["messages"][1]["blocks"][0]["text"]
    assert "整数除法" in reply and "2.5" in reply
    assert "哪一道" not in reply and "结合当前题" not in reply                   # 概念问题直接答，不反问是哪道题

    # 提到作业题名也不自动绑；选了“结合当前题”才带上那道题
    hw = client.get("/api/homeworks").json()["homeworks"][0]["key"]
    problem = client.get(f"/api/homeworks/{hw}").json()["problems"][0]
    client.post(f"/api/threads/{key}/messages", json={"text": f"{problem['title']}里 for 循环怎么写？"})
    assert _last_step() == ("", None)
    r = client.post(f"/api/threads/{key}/messages", json={"text": "这道题的循环条件对吗？", "problemKey": problem["key"]})
    assert r.status_code == 200
    by, pid = _last_step()
    assert by == "chosen" and pid is not None
    assert len(client.get(f"/api/threads/{key}").json()["messages"]) == 6
    assert client.post(f"/api/threads/{key}/review").json()["status"] == "busy"   # 知识问答的主题里不插小题

    # 新建第二个问答：两个主题各是各的
    r2 = client.post("/api/threads/new/messages", json={"text": "数组下标从哪里开始"})
    assert r2.json()["thread"] != key
    assert [t["key"] for t in client.get("/api/topics").json()["topics"]][:2] == [r2.json()["thread"], key]

    # 别人看不到；换到另一门课也看不到
    client.post("/api/auth/logout")
    login(client, "2500000003")
    assert client.get(f"/api/threads/{key}").status_code == 404
    assert all(t["key"] != key for t in client.get("/api/topics").json()["topics"])
    with db.session_scope() as s:
        s.get(m.ChatTopic, int(key[1:])).course = "D"
    client.post("/api/auth/logout")
    login(client)
    assert client.get(f"/api/threads/{key}").status_code == 404
    assert client.get("/api/threads/t999999").status_code == 404
    assert client.get("/api/threads/tx").status_code == 404
