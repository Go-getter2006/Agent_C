"""知识问答：多个问答主题；不自动绑到作业题上，学生选了“结合当前题”才带上；各看各的、按课程分开。"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import assess, config, db, llm, logs
from app import models as m
from app.chat import workflow
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


def test_delete_topic_removes_saved_messages_steps_and_assessment(client, monkeypatch):
    login(client)
    monkeypatch.setattr(llm, "_backend", lambda *_: ('{"before":"解释。"}', 10, 10))
    first = client.post("/api/threads/new/messages", json={"text": "保留这条问答"}).json()
    deleted = client.post("/api/threads/new/messages", json={"text": "删除这条问答"}).json()
    key = deleted["thread"]
    topic_id = int(key[1:])
    ids = [msg["id"] for msg in deleted["messages"]]
    with db.session_scope() as s:
        round_id = s.get(m.ChatMessage, ids[0]).round_id
        first_round_id = s.get(m.ChatMessage, first["messages"][0]["id"]).round_id
        assert first_round_id != round_id
        s.add(m.AgentAssessment(user_id=UID, course="C", round_id=round_id, skill_code="C01.01.01",
                                evidence=["删除这条问答"]))
        s.add(m.SkillEvidence(user_id=UID, skill_code="C01.01.01", event_type="agent_understanding",
                              success=True, weight=1, source_key=f"assess:{round_id}"))
        s.add(m.UiEvent(user_id=UID, course="C", kind="open_knowledge", target=key))

    client.post("/api/auth/logout")
    login(client, "2500000003")
    assert client.delete(f"/api/topics/{key}").status_code == 404
    client.post("/api/auth/logout")
    login(client)
    with db.session_scope() as s:
        s.get(m.ChatTopic, topic_id).course = "D"
    assert client.delete(f"/api/topics/{key}").status_code == 404
    with db.session_scope() as s:
        s.get(m.ChatTopic, topic_id).course = "C"
    assert client.delete("/api/topics/new").status_code == 404

    result = client.delete(f"/api/topics/{key}")
    assert result.status_code == 200
    assert all(topic["key"] != key for topic in result.json()["topics"])
    assert client.get(f"/api/threads/{key}").status_code == 404
    assert client.post(f"/api/threads/{key}/messages", json={"text": "旧标签页续问"}).status_code == 404
    assert client.get(f"/api/threads/{first['thread']}").json()["messages"] == first["messages"]
    with db.session_scope() as s:
        assert s.get(m.ChatTopic, topic_id) is None
        assert all(s.get(m.ChatMessage, mid) is None for mid in ids)
        assert s.scalar(select(m.ChatStep).where(m.ChatStep.message_id.in_(ids))) is None
        assert s.scalar(select(m.AgentAssessment).where(m.AgentAssessment.round_id == round_id)) is None
        assert s.scalar(select(m.SkillEvidence).where(m.SkillEvidence.source_key == f"assess:{round_id}")) is None
        assert s.scalar(select(m.UiEvent).where(m.UiEvent.target == key)) is None
        assert s.get(m.ChatRound, round_id) is None
        assert s.get(m.ChatRound, first_round_id) is not None

    new = client.post("/api/threads/new/messages", json={"text": "新建会话"}).json()
    assert int(new["thread"][1:]) > topic_id
    assert new["messages"][0]["id"] > ids[-1]


def test_delete_legacy_chat_leaves_homework_and_other_course(client):
    login(client)
    with db.session_scope() as s:
        round_ = m.ChatRound(user_id=UID, course="C", status="closed")
        s.add(round_)
        s.flush()
        legacy = m.ChatMessage(user_id=UID, course="C", role="student", round_id=round_.id)
        other = m.ChatMessage(user_id=UID, course="D", role="student")
        homework = m.ChatMessage(user_id=UID, course="C", role="student", server_id=1, problem_id=1)
        s.add_all((legacy, other, homework))
        s.flush()
        ids = legacy.id, other.id, homework.id, round_.id
    assert client.get("/api/threads/general").json()["messages"]
    result = client.delete("/api/topics/general")
    assert result.status_code == 200
    assert all(topic["key"] != "general" for topic in result.json()["topics"])
    assert client.get("/api/threads/general").json() == {"messages": []}
    with db.session_scope() as s:
        assert s.get(m.ChatRound, ids[3]) is None
        assert [s.get(m.ChatMessage, mid) is not None for mid in ids[:3]] == [False, True, True]


def test_rounds_follow_the_conversation_even_when_linked_to_same_homework(client, demo, monkeypatch):
    login(client)
    monkeypatch.setattr(llm, "_backend", lambda *_: ('{"before":"先看这个概念。"}', 10, 10))
    homework = f"p{demo['server_id']}-406"
    now = db.now()
    with db.session_scope() as s:
        if prior := workflow.open_round(s, UID, "C", homework):
            workflow.close_round(s, prior, "超时", now)
    pin = {"problemKey": homework}
    first = client.post("/api/threads/new/messages", json={**pin, "text": "第一段会话"}).json()
    second = client.post("/api/threads/new/messages", json={**pin, "text": "第二段会话"}).json()
    follow = client.post(f"/api/threads/{first['thread']}/messages", json={**pin, "text": "第一段追问"}).json()
    legacy = client.post("/api/threads/general/messages", json={**pin, "text": "以前的会话"}).json()
    result = client.post(f"/api/threads/{homework}/messages", json={"action": "hint"}).json()

    with db.session_scope() as s:
        messages = [s.get(m.ChatMessage, data["messages"][-2]["id"])
                    for data in (first, second, follow, legacy, result)]
        one, two, continued, old, hw = [s.get(m.ChatRound, msg.round_id) for msg in messages]
        assert one.id == continued.id
        assert len({one.id, two.id, old.id, hw.id}) == 4
        assert [r.conversation_key for r in (one, two, old, hw)] == [first["thread"], second["thread"], "general", homework]
        assert all(r.problem_id == 406 for r in (one, two, old, hw))
        assert assess.Material(s, one).student_texts == ["第一段会话", "第一段追问"]
        assert assess.Material(s, two).student_texts == ["第二段会话"]
        assert hw.help_count == 1 and all(r.help_count == 0 for r in (one, two, old))
        s.add(m.AgentAssessment(user_id=UID, course="C", round_id=two.id, skill_code="C01.01.01"))
        s.flush()
        rows = logs.collect(s, [UID], now, db.now() + dt.timedelta(seconds=1), ["round", "assess"],
                            include_text=False, course="C")
        records = {(row.kind, row.data["conversation"]): row.section for row in rows}
        assert records["round", first["thread"]] == records["assess", second["thread"]] == "知识问答"
        assert records["round", homework] == "作业引导"

        passed_at = db.now()
        workflow.after_pass(s, m.Submission(user_id=UID, server_id=demo["server_id"], problem_id=406, submit_time=passed_at))
        assert all((r.status, r.end_reason, r.ended_at) == ("closed", "通过", passed_at) for r in (one, two, old, hw))


def test_model_failure_keeps_knowledge_history_unchanged(client):
    login(client)
    topics = client.get("/api/topics").json()
    assert client.post("/api/threads/new/messages", json={"text": "for 循环怎么用"}).status_code == 502
    assert client.get("/api/topics").json() == topics


def test_standalone_array_question_accepts_a_multiline_example(client, monkeypatch):
    login(client)
    code = "int a[2][3] = {{1,2,3},{4,5,6}};\nfor (int i = 0; i < 2; ++i)\n    for (int j = 0; j < 3; ++j)\n        printf(\"%d \", a[i][j]);"
    calls = []

    def answer(*args):
        calls.append(args)
        return json.dumps({"before": "用两个下标分别访问行和列。", "code": code}), 10, 10

    monkeypatch.setattr(llm, "_backend", answer)
    result = client.post("/api/threads/new/messages", json={"text": "二维数组如何使用"})
    assert result.status_code == 200
    assert result.json()["messages"][-1]["blocks"][-1]["lines"] == code.splitlines()
    assert len(calls) == 1
    assert calls[0][2] is None


def test_provider_balance_error_is_reported_without_retry(client, monkeypatch):
    login(client)
    calls = []

    def reject(url, **kwargs):
        calls.append(kwargs["json"])
        return llm.httpx.Response(402)

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(config, "OFFICIAL_DEEPSEEK", True)
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(llm.httpx, "post", reject)
    result = client.post("/api/threads/new/messages", json={"text": "二维数组如何使用"})
    assert result.status_code == 502
    assert "额度不足" in result.json()["error"]
    assert len(calls) == 1
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert "max_tokens" not in calls[0]


def test_knowledge_does_not_reject_long_explanations_or_multiple_code_examples(client, monkeypatch):
    login(client)
    intro = "用两个下标分别表示行和列。" * 28
    body = intro + "\n```c\nint a[2][3];\n```\n另一个例子如下。\n```c\nint b[3][5];\n```"
    monkeypatch.setattr(llm, "_backend", lambda *_: (json.dumps({"before": body}), 10, 10))
    result = client.post("/api/threads/new/messages", json={"text": "二维数组可以举两个例子吗"})
    assert result.status_code == 200
    blocks = result.json()["messages"][-1]["blocks"]
    assert blocks[0]["text"] == intro
    assert [b["lines"] for b in blocks if b["t"] == "code"] == [["int a[2][3];"], ["int b[3][5];"]]


def test_knowledge_preserves_markdown_spacing_on_send_and_reopen(client, monkeypatch):
    login(client)
    markdown = (
        "## 二维数组\n\n- **行**\n  - 行下标\n\n"
        "    ```c\n    int row = 0;\n    ```\n\n"
        "| 下标 | 含义 |\n| --- | --- |\n| `i` | 行 |"
    )
    monkeypatch.setattr(llm, "_backend", lambda *_: (json.dumps({"before": markdown}), 10, 10))
    result = client.post("/api/threads/new/messages", json={"text": "二维数组的下标怎么用？"})
    assert result.status_code == 200
    data = result.json()
    blocks = [{"t": "p", "text": markdown}]
    assert data["messages"][-1]["blocks"] == blocks
    assert client.get(f"/api/threads/{data['thread']}").json()["messages"][-1]["blocks"] == blocks


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
