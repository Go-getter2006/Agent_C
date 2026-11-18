"""每次提交改了什么、通过后的总结（怎么改对的；大改时对比两种做法、问他为什么换）、大模型拿到的完整情况。"""
from __future__ import annotations

import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import catalog, changes, db, llm
from app import models as m
from app.chat import summary, workflow
from app.web.app import create_app

UID = "2500000001"


def _fixed_problem(s):
    """演示数据里林晓一道“没过、后来改对”的题：返回第一次通过的那次提交。"""
    subs = s.scalars(select(m.Submission).where(m.Submission.user_id == UID).order_by(m.Submission.submission_id)).all()
    seen = {}
    for x in subs:
        key = (x.server_id, x.problem_id)
        seen.setdefault(key, []).append(x)
        if x.judgement == "AC" and len(seen[key]) > 1 and all(y.judgement != "AC" for y in seen[key][:-1]):
            if changes.code_lines(s, x) and changes.code_lines(s, seen[key][-2]):
                return x
    return None


def _clear_summary(s, sub) -> None:
    """演示数据入库时已经自动发过这道题的总结了：先删掉，再看这一次。"""
    for msg in s.scalars(select(m.ChatMessage).where(m.ChatMessage.server_id == sub.server_id,
                                                     m.ChatMessage.problem_id == sub.problem_id,
                                                     m.ChatMessage.user_id == UID)).all():
        if (msg.content or {}).get("summary"):
            s.delete(msg)
    s.flush()


def test_changes_between_submissions(demo):
    with db.session_scope() as s:
        sub = _fixed_problem(s)
        out = changes.problem_changes(s, UID, sub.server_id, sub.problem_id)
        ch = out[catalog.submission_key(sub.server_id, sub.submission_id)]
        assert ch["available"] and ch["changed"] >= 1 and ch["passedNow"]
        assert ch["excerpt"] and {e["type"] for e in ch["excerpt"]} <= {"add", "del"}
        assert all(not any(c.isdigit() and len(n) > 6 for c in n) for n in ch["fixed"])   # 只有错误名称，没有编码
        first = catalog.user_submissions(s, UID, sub.server_id, sub.problem_id)[0]
        assert catalog.submission_key(first.server_id, first.submission_id) not in out    # 第一次没有“上一次”
    assert changes.compare(["a", "b"], ["x", "y", "z"]).big and not changes.compare(["a"] * 30, ["a"] * 29 + ["b"]).big


def test_summary_after_pass(demo):
    with db.session_scope() as s:
        sub = _fixed_problem(s)
        assert summary._sent(s, sub)                                    # 演示数据入库时已经自动发过
        _clear_summary(s, sub)
        now = sub.submit_time + dt.timedelta(minutes=5)
        msg = summary.on_pass(s, sub, now)
        text = workflow.message_text(msg)
        assert text.startswith("通过了") and "哪里卡住" not in text and summary.REFLECT in text
        assert msg.content["summary"]["submission"] == sub.submission_id
        assert summary.on_pass(s, sub, now) is None                    # 每道题只发一次
        summary.drop_reflection(msg)
        assert summary.REFLECT not in workflow.message_text(msg)
        late = summary.on_pass(s, sub, sub.submit_time + dt.timedelta(days=5))
        assert late is None                                             # 很久以前的通过不发


def test_summary_compares_two_approaches_on_big_rewrite(demo, monkeypatch):
    monkeypatch.setattr(changes, "BIG_LINES", 1)                       # 把任何改动都当成大改
    seen = []

    def backend(messages, json_mode, max_tokens, temperature):
        seen.append(messages[-1]["content"])
        return json.dumps({"before": "先把 max 设成 0 再比较", "after": "用第一个数当初值再比较",
                           "compare": "原来的初值在全是负数时比任何数都大，所以错了；现在不会有这个问题。",
                           "question": "你是怎么想到换成用第一个数当初值的？"}, ensure_ascii=False), 100, 50

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            sub = _fixed_problem(s)
            _clear_summary(s, sub)
            msg = summary.on_pass(s, sub, sub.submit_time + dt.timedelta(minutes=5))
            text = workflow.message_text(msg)
            assert "大幅改了代码" in text and "原来：" in text and "现在：" in text and text.endswith("？")
            assert msg.content["summary"]["big"] is True
        assert "【原来的代码】" in seen[0] and UID not in seen[0] and "林晓" not in seen[0]
    finally:
        llm.set_backend(None)


def test_tutor_sees_everything_but_student_never_sees_tests(demo):
    sid = demo["server_id"]
    seen = []

    def backend(messages, json_mode, max_tokens, temperature):
        seen.append("\n".join(x["content"] for x in messages))
        if "分析" in messages[0]["content"][:20]:
            return json.dumps({"intent": "DEBUG", "skillCode": "", "hasAttempt": False, "icap": "active",
                               "answerCorrect": None, "understood": False}), 50, 20
        # 写回复的大模型想把一组测试输入原样告诉学生：要被拦下，退回模板
        with db.session_scope() as s:
            tc = s.scalars(select(m.Testcase).where(m.Testcase.server_id == sid, m.Testcase.problem_id == 406)).first()
            leaked = tc.input_bytes.decode("utf-8").strip()
        return json.dumps({"before": f"试试这组输入：{leaked}", "quote": None, "code": None, "after": ""},
                          ensure_ascii=False), 200, 60

    with db.session_scope() as s:
        workflow.close_round(s, workflow.open_round(s, UID), "换题", db.now())
    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            out = workflow.handle(s, UID, (sid, 406), "还是有一组不过，我检查了循环，没看出问题。")
            step = s.scalars(select(m.ChatStep).order_by(m.ChatStep.id.desc())).first()
            assert step.reply_source == "template"                       # 泄露的回复没有用
            assert "试试这组输入" not in workflow.message_text(out[1])
        prompt = next(x for x in seen if "【这次的任务】" in x)
        assert "【题目】" in prompt and "【这道题的测试点" in prompt and "这道题他每次的提交" in prompt
        assert UID not in prompt and "林晓" not in prompt
    finally:
        llm.set_backend(None)


def test_problem_page_has_statement_and_changes(demo):
    with db.session_scope() as s:
        sub = _fixed_problem(s)
        key = catalog.problem_key(sub.server_id, sub.problem_id)
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": UID, "password": "test-pass"})
        d = c.get(f"/api/problems/{key}").json()
        assert "statement" in d and d["changes"]
        assert all({"changed", "excerpt", "fixed", "still", "new"} <= set(v) for v in d["changes"].values())
