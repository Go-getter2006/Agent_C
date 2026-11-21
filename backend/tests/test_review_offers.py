from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import db, llm, mastery, quiz, quizgen, review, pdftext
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


def test_preparation_is_idempotent_and_stays_in_review(database, monkeypatch):
    now = db.now()
    calls = []
    with db.session_scope() as s:
        item,q = setup(s,now)
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:(calls.append(1) or q))
        assert review.prepare(s,UID,"C",now) == "ready"
        assert review.prepare(s,UID,"C",now) == "ready"
        assert len(calls) == 1
        messages = s.scalars(select(m.ChatMessage).where(m.ChatMessage.user_id==UID)).all()
        assert not messages
        assert s.get(m.QuizAttempt,item.attempt_id).review_item_id == item.id
        assert review.prepare(s,UID,"D",now) == "idle"


def test_unavailable_quiz_keeps_due_and_backs_off(database, monkeypatch):
    now=db.now()
    calls=[]
    with db.session_scope() as s:
        item,q=setup(s,now)
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:(calls.append(1) or None))
        assert review.prepare(s,UID,"C",now)=="waiting"
        assert review.prepare(s,UID,"C",now+dt.timedelta(seconds=30))=="waiting"
        assert calls == [1]
        assert [r.id for r in review.due_items(s,UID,now,"C")]==[item.id]
        assert item.attempt_id is None


def test_background_preparation_during_chat_does_not_interrupt(database, monkeypatch):
    now=db.now()
    with db.session_scope() as s:
        _,q=setup(s,now)
        s.add(m.ChatMessage(user_id=UID,course="C",role="student",kind="text",created_at=now-dt.timedelta(seconds=20),content={}))
        s.flush()
        monkeypatch.setattr(quizgen,"generate",lambda *_a,**_k:q)
        assert review.prepare(s,UID,"C",now)=="ready"
        assert all(msg.role == "student" for msg in s.scalars(select(m.ChatMessage)))


def test_failures_are_more_likely_picked_than_spot_checks(database):
    """后验抽样：有失败证据的（掌握分低）比只做对过、被抽查的更可能先出；概率算得出来。"""
    now=db.now()
    with db.session_scope() as s:
        wrong,_=setup(s,now)
        mastery.record_stuck(s,UID,wrong.skill_code,1,"stuck:1",ts=now-dt.timedelta(days=3))
        spot=review.enqueue(s,UID,"C02.01.02","spot_check",at=now-dt.timedelta(days=60))
        mastery.add_evidence(s,UID,"C02.01.02","pass_first_try",True,1.0,"pass:9:1",now-dt.timedelta(days=60),True)
        due=review.due_items(s,UID,now,"C",candidates=True)
        states=mastery.skill_states(s,UID,at=now,model="v2")
        probs=dict(zip([r.id for r in due],review.lowest_probabilities([review.beta_of(states.get(r.skill_code)) for r in due])))
        assert probs[wrong.id]>probs[spot.id]>0


def test_fixed_submission_retains_review_and_upgrades_reason(database):
    now=db.now()
    with db.session_scope() as s:
        item=review.enqueue(s,UID,SKILL,"failed",1,406,now-dt.timedelta(hours=2))
        sub=SimpleNamespace(user_id=UID,submission_id=10,server_id=1,problem_id=406,submit_time=now)
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
        monkeypatch.setattr(quizgen,'generate',lambda *_a,**_k:pytest.fail("读取页面或点击入口不能触发出题"))
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


def test_real_wait_is_measured_at_first_judgement(database, monkeypatch):
    at = db.now()
    with db.session_scope() as s:
        item, q = setup(s, at)
        item.origin_server_id, item.origin_problem_id = 1, 6
        item = review.enqueue(s, UID, SKILL, "corrected", 1, 6, at, 901)
        monkeypatch.setattr(quizgen, "generate", lambda *_a, **_k: q)
        _, a = review.start(s, UID, item.id, at + dt.timedelta(days=2), "C")
        assert a.events[0]["phase"] == "delayed" and a.events[0]["submissionId"] == 901
        monkeypatch.setattr(db, "now", lambda: at + dt.timedelta(days=2, seconds=300))
        quiz.judge2(s, a, q.has_bug)
        quiz.judge2(s, a, not q.has_bug)
        assert [e["secondsSinceTrigger"] for e in a.events if e["kind"] == "judge"] == [173100]


def test_slow_background_question_does_not_hold_homework_or_review_requests(demo, monkeypatch):
    from threading import Event, Thread

    entered, release = Event(), Event()
    errors = []
    uid = "2500000001"
    with TestClient(create_app()) as client:
        client.post("/api/auth/login", json={"loginName": uid, "password": "test-pass"})
        with db.session_scope() as s:
            review.seed_missing(s, uid, "C", db.now())
        def slow(*_args, **_kwargs):
            entered.set()
            if not release.wait(8):
                raise TimeoutError("前台请求等待了出题")
            return None
        monkeypatch.setattr(quizgen, "generate", slow)
        def work():
            try:
                with db.session_scope() as s:
                    review.prepare(s, uid, "C")
            except Exception as error:
                errors.append(error)
        worker = Thread(target=work)
        worker.start()
        try:
            assert entered.wait(5)
            th = client.get(f"/api/threads/p{demo['server_id']}-406")
            assert th.status_code == 200 and th.json()["messages"]
            page = client.get("/api/review")
            assert page.status_code == 200 and "current" not in page.json()
            assert client.get("/api/status").status_code == 200 and worker.is_alive()
        finally:
            release.set()
            worker.join(10)
        assert not errors and not worker.is_alive()


def test_worker_waits_before_preparing_corrections(database, monkeypatch):
    at = db.now()
    with db.session_scope() as s:
        _, q = setup(s, at)
        item = review.enqueue(s, UID, SKILL, "corrected", 1, 406, at, 901)
        assert review.available_at(item) >= at + dt.timedelta(days=1)
        qid = q.id
    monkeypatch.setattr(quizgen, "generate", lambda s, *_a, **_k: s.get(m.QuizItem, qid))
    assert review.prepare_active(at=at) == 0
    later = at + dt.timedelta(days=2)
    assert review.prepare_active(at=later) == 1
    assert review.prepare_active(at=later) == 0
    with db.session_scope() as s:
        assert review.ready_count(s, UID, "C") == 1
        assert s.scalar(select(m.ChatMessage.id)) is None



def test_review_samples_by_evidence_and_waits_before_rechecking(database):
    now = db.now()
    with db.session_scope() as s:
        oldest = review.enqueue(s, UID, "C02.01.02", "stuck", at=now - dt.timedelta(days=60))
        repeated = review.enqueue(s, UID, SKILL, "repeated_fix", 1, 406, now - dt.timedelta(days=2), 100)
        weak = review.enqueue(s, UID, "C03.03.01", "failed", at=now - dt.timedelta(days=2))
        verify = review.enqueue(s, UID, "C04.03.01", "verify", 1, 402, now - dt.timedelta(days=2), 200)
        recent = review.enqueue(s, UID, "C05.02.01", "repeated_fix", 1, 405, now - dt.timedelta(hours=10), 300)
        mastery.record_stuck(s, UID, weak.skill_code, 2, "stuck:201", ts=now - dt.timedelta(days=2))
        due = review.due_items(s, UID, now, "C", candidates=True)
        assert {r.id for r in due} == {weak.id, repeated.id, verify.id, oldest.id}   # 刚记的还没到期
        assert due[0].id == oldest.id                                                # 列出来按到期先后
        # 先出哪个由后验抽样定：有卡住证据的掌握分最低，被选中的概率最大
        states = mastery.skill_states(s, UID, at=now, model="v2")
        probs = review.lowest_probabilities([review.beta_of(states.get(r.skill_code)) for r in due])
        assert max(zip(probs, [r.id for r in due]))[1] == weak.id
        assert not review.item_view(s, UID, recent, now)["dueNow"]
        assert "雷同" not in review.ORIGIN_TEXT[verify.origin]
