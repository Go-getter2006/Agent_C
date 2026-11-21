"""小题做完、反馈解释过以后，回顾他以前在这里出过的错（app/recap.py）：程序按题整理没过的提交和做错的小题，
大模型分析这几次错在同一件事的什么地方、和这道小题的关系。每次作答只做一次，存下来。"""
from __future__ import annotations

import json

from sqlalchemy import select

from app import db, llm, quiz, recap
from app import models as m

UID = "2500000001"


def _finish_ok(s, skill: str) -> m.QuizAttempt:
    quiz.load_bank(s)
    item = s.scalars(select(m.QuizItem).where(m.QuizItem.skill_code == skill, m.QuizItem.has_bug.is_(True))).first()
    a = quiz.new_attempt(s, UID, item, "review")
    quiz.judge2(s, a, False)
    quiz.challenge_reply(s, a, keep=True)          # 有错的代码判成满足要求，追问后仍坚持：判断错
    return a


def test_recap_lists_failed_problems_on_the_same_skill(demo):
    with db.session_scope() as s:
        a = _finish_ok(s, "C06.03.02")              # 最值的初值
        rc = recap.build(s, a)
        assert rc is not None and rc["intro"] == recap.INTRO and rc["outro"] == recap.OUTRO
        problems = [x for x in rc["items"] if x["kind"] == "problem"]
        titles = [x["title"] for x in problems]
        assert "成绩最高分" in titles and len(problems) <= recap.MAX_PROBLEMS
        top = next(x for x in problems if x["title"] == "成绩最高分")
        assert top["key"].startswith("p") and top["text"].startswith("没过 ") and "变量没给合适的初值" in top["text"]
        assert all("第 " in x["text"] for x in problems if "改对了" in x["text"])
        assert UID not in repr(rc)


def test_recap_includes_earlier_wrong_quizzes_and_nothing_when_clean(demo):
    with db.session_scope() as s:
        first = _finish_ok(s, "C06.03.02")
        second = _finish_ok(s, "C06.03.02")
        quizzes = [x for x in recap.build(s, second)["items"] if x["kind"] == "quiz"]
        assert quizzes and quizzes[0]["attemptId"] == first.id and quizzes[0]["text"] == "没看出代码里的错。"
        clean = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.id == first.id)).one()
        clean.skill_code = "C11.04.01"              # 没在这里出过错的技能：不回顾
        assert recap.build(s, clean) is None


def test_model_analyses_the_past_mistakes_once(demo):
    """大模型拿到那几道题当时出错的代码和后来改对的代码来分析；存下来，第二次不再调。没写出合格的就只列事实。"""
    seen: list[str] = []

    def backend(messages, *_a):
        seen.append(messages[1]["content"])
        return json.dumps({"analysis": "这几次都是 `max` 的初值给了 0：数全是负数时就不对。下次先看最值变量的初值。"},
                          ensure_ascii=False), 10, 10

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            a = _finish_ok(s, "C06.03.02")
            rc = recap.for_attempt(s, a)
            assert rc["source"] == "llm" and "初值" in rc["analysis"] and rc["items"]
            assert all("_ref" not in x for x in rc["items"])         # 内部编号不下发
            prompt = seen[0]
            assert "没过时出错的地方" in prompt and "int max = 0;" in prompt and "后来改对的代码" in prompt
            assert UID not in prompt
            assert recap.for_attempt(s, a) == rc and len(seen) == 1  # 存下来了
    finally:
        llm.set_backend(None)

    def leaky(messages, *_a):
        return json.dumps({"analysis": "```c\nint max = a[0];\n```"}), 10, 10

    llm.set_backend(leaky)
    try:
        with db.session_scope() as s:
            b = _finish_ok(s, "C06.03.02")
            rc = recap.for_attempt(s, b)
            assert rc["source"] == "facts" and rc["analysis"] == "" and rc["items"]   # 不拿模板凑分析
    finally:
        llm.set_backend(None)


def test_recap_endpoint_only_after_feedback(demo):
    from fastapi.testclient import TestClient

    from app.web.app import create_app

    with TestClient(create_app()) as client:
        _endpoint(client)


def _endpoint(client):
    client.post("/api/auth/login", json={"loginName": UID, "password": "test-pass"})
    with db.session_scope() as s:
        quiz.load_bank(s)
        item = s.scalars(select(m.QuizItem).where(m.QuizItem.skill_code == "C06.03.02", m.QuizItem.has_bug.is_(True))).first()
        a = quiz.new_attempt(s, UID, item, "review")
        aid = a.id
    assert client.get(f"/api/quiz/{aid}/recap").json() == {"recap": None}       # 还没做完：不回顾
    client.post(f"/api/quiz/{aid}/judge", json={"hasBug": False})
    client.post(f"/api/quiz/{aid}/challenge", json={"keep": True})
    rc = client.get(f"/api/quiz/{aid}/recap").json()["recap"]
    assert rc and any(x["title"] == "成绩最高分" for x in rc["items"])
