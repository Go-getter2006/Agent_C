from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import db, llm, quiz, quizgen, review, pdftext
from app import models as m
from app.chat.analyze import analyze, analyze_rules
from app.chat import context, reply
from app.chat.policy import Decision
from app.catalog import ProblemInfo
from app.web.app import create_app
from fastapi.testclient import TestClient

UID = "test-learner"
SKILL = "C06.03.02"


def setup(s, at, skill=SKILL, origin="failed"):
    if s.get(m.Student, UID) is None:
        s.add(m.Student(user_id=UID, name="测试同学"))
    quiz.load_bank(s)
    q = s.scalars(select(m.QuizItem).where(m.QuizItem.skill_code == skill)).first()
    assert q is not None
    item = review.enqueue(s, UID, skill, origin, at=at-dt.timedelta(days=8))
    return item, q


def test_pending_review_does_not_hide_remaining_daily_slot(database, monkeypatch):
    now = db.now()
    with db.session_scope() as s:
        first, q = setup(s, now)
        other = review.enqueue(s, UID, "C02.01.02", "corrected", at=now-dt.timedelta(days=8))
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:q)
        _, attempt = review.start(s, UID, first.id, now, "C")
        assert attempt is not None
        assert {r.id for r in review.due_items(s,UID,now,"C")} == {first.id,other.id}
        assert review.summary(s,UID,now,"C")["doneToday"] == 0
        quiz.judge2(s, attempt, True)
        quiz.skip(s, attempt)
        assert first.attempt_id is None and first.due_at > now
        assert review.summary(s, UID, now, "C")["doneToday"] == 0


def test_offer_is_automatic_idempotent_and_stays_in_general_chat(database, monkeypatch):
    now = db.now()
    calls = []
    with db.session_scope() as s:
        item,q = setup(s,now)
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:(calls.append(1) or q))
        assert review.offer_in_chat(s,UID,"C",now) == "ready"
        assert review.offer_in_chat(s,UID,"C",now) == "ready"
        assert len(calls) == 1
        messages = s.scalars(select(m.ChatMessage).where(m.ChatMessage.user_id==UID)).all()
        assert len(messages)==1 and messages[0].kind=="quiz"
        assert messages[0].server_id is None and messages[0].problem_id is None
        assert s.get(m.QuizAttempt,messages[0].quiz_attempt_id).review_item_id == item.id
        assert review.offer_in_chat(s,UID,"D",now) == "idle"


def test_unavailable_quiz_keeps_due_and_backs_off(database, monkeypatch):
    now=db.now()
    calls=[]
    with db.session_scope() as s:
        item,q=setup(s,now)
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:(calls.append(1) or None))
        assert review.offer_in_chat(s,UID,"C",now)=="waiting"
        assert review.offer_in_chat(s,UID,"C",now+dt.timedelta(seconds=30))=="waiting"
        assert calls == [1]
        assert [r.id for r in review.due_items(s,UID,now,"C")]==[item.id]
        assert item.attempt_id is None


def test_recent_student_message_defers_automatic_question(database, monkeypatch):
    now=db.now()
    with db.session_scope() as s:
        _,q=setup(s,now)
        s.add(m.ChatMessage(user_id=UID,course="C",role="student",kind="text",created_at=now-dt.timedelta(seconds=20),content={}))
        s.flush()
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:pytest.fail("不能打断正在进行的对话"))
        assert review.offer_in_chat(s,UID,"C",now)=="busy"


def test_failures_are_prioritized_over_spot_checks(database):
    now=db.now()
    with db.session_scope() as s:
        wrong,_=setup(s,now)
        spot=review.enqueue(s,UID,"C02.01.02","spot_check",at=now-dt.timedelta(days=60))
        assert review.due_items(s,UID,now,"C")[0].id==wrong.id


def test_fixed_submission_retains_review_and_upgrades_reason(database):
    now=db.now()
    with db.session_scope() as s:
        item=review.enqueue(s,UID,SKILL,"failed",1,406,now-dt.timedelta(hours=2))
        sub=SimpleNamespace(user_id=UID,server_id=1,problem_id=406,submit_time=now)
        label=SimpleNamespace(primary_skill=SKILL,secondary_skills=[])
        review.on_independent_practice(s,sub,label,1,"NA",1.0)
        assert item.origin=="corrected" and item.state=="active" and item.streak==0


def test_missing_historical_error_is_backfilled_once(demo):
    actual_uid="2500000001"
    with db.session_scope() as s:
        item=s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id==actual_uid,m.ReviewItem.skill_code==SKILL)).first()
        assert item is not None
        s.delete(item)
        s.flush()
        now=db.now()+dt.timedelta(minutes=10)
        assert review.seed_missing(s,actual_uid,"C",now)>=1
        replacement=s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id==actual_uid,m.ReviewItem.skill_code==SKILL)).first()
        assert replacement is not None and replacement.origin in ("failed","corrected")
        assert review.seed_missing(s,actual_uid,"C",now)==0


def test_api_generation_failure_does_not_claim_review_is_unnecessary(demo,monkeypatch):
    with TestClient(create_app()) as client:
        assert client.post('/api/auth/login',json={'loginName':'2500000001','password':'test-pass'}).status_code==200
        monkeypatch.setattr(quizgen,'generate',lambda *_a,**_k:None)   # 进复习提升会当场备题：先让出题失败
        page=client.get('/api/review').json()
        assert 'current' not in page and page['due']
        due=page['due'][0]
        result=client.post(f"/api/review/{due['id']}/start")
        assert result.status_code==503 and "仍需复习" in result.json()['error']
        assert due['id'] in {r['id'] for r in client.get('/api/review').json()['due']}


@pytest.mark.parametrize("text",["什么是指针？","这题里的 scanf 是什么意思？","int 占多少字节？","我不懂，sizeof 是什么？","break 和 continue 有什么区别？"])
def test_factual_questions_bypass_reflective_analysis(text):
    assert analyze_rules(text,False,True,bound=True).intent=="CONCEPT"
    llm.set_backend(lambda *_a:pytest.fail("明确事实提问不用再次让模型分类"))
    try:
        assert analyze(text,False,True,bound=True).intent=="CONCEPT"
    finally:
        llm.set_backend(None)


def test_debugging_and_task_questions_keep_their_intent():
    assert analyze_rules("我的代码为什么错了？",False,False,True).intent=="DEBUG"
    assert analyze_rules("这题是什么意思？",False,False,True).intent=="TASK"


def test_tidy_keeps_samples_separate_and_preserves_tokens():
    raw="输出说明\n输出英文。\n输入示例1：2020 11 5\n输出示例1：Thursday\n样例说明：\n这是星期四。"
    tidy="输出格式\n输出英文。\n样例输入 1\n2020 11 5\n样例输出 1\nThursday\n样例说明\n这是星期四。"
    assert pdftext.check_tidy(raw,tidy,False) is None
    assert "样例" in pdftext.check_tidy(raw,tidy.replace("Thursday","Friday"),False)
    assert "样例" in pdftext.check_tidy(raw,tidy.replace("样例说明\n",""),False)
