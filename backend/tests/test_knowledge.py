"""知识问答：多个问答主题；不自动绑到作业题上，学生选了“结合当前题”才带上；各看各的、按课程分开。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
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


def test_topics_are_separate_threads_and_do_not_bind_homework(client):
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
