"""决策记录（app/decisions.py）：复习先出哪个技能、出正确还是有错的代码、发不发主动消息。
记下当时的候选和依据、规则、选了哪个、选中的概率、结果在哪；学生端看不到。"""
from __future__ import annotations

import datetime as dt
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, review
from app import models as m
from app.chat import proactive
from app.web.app import create_app

UID = "2500000001"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="没有 gcc")
def test_review_pick_and_quiz_plan_are_recorded(demo):
    with db.session_scope() as s:
        before = s.scalar(select(m.DecisionLog.id).order_by(m.DecisionLog.id.desc())) or 0   # 演示数据入库时也记过
        # 演示环境没有大模型：只让题库里有题的复习项到期，抽到哪一项都出得了题（选哪项是随机的）
        bank = set(s.scalars(select(m.QuizItem.skill_code).where(m.QuizItem.verified.is_(True))).all())
        for item in s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == UID)).all():
            if item.skill_code not in bank:
                item.due_at = db.now() + dt.timedelta(days=30)
        assert review.prepare(s, UID, "C") == "ready"
        new = select(m.DecisionLog).where(m.DecisionLog.id > before)
        pick = s.scalars(new.where(m.DecisionLog.kind == "review_pick")).one()
        attempt = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.reason == "review",
                                                        m.QuizAttempt.state != "done")).one()
        # 后验抽样（thompson-1）：每个候选当时被选中的概率都记下，加起来是 1；选中的那个的概率就是记录的概率
        assert pick.rule == "thompson-1" and 0 < pick.probability <= 1
        assert pick.ref["attempt"] == attempt.id and pick.chosen == f"review:{pick.ref['reviewItem']}"
        ranks = [c["rank"] for c in pick.candidates]
        assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) >= 1
        first = pick.candidates[0]
        assert {"skill", "origin", "weak", "betaA", "betaB", "score", "overdueDays", "halfLife", "pickProbability"} <= set(first)
        draw = pick.context["draws"][-1]
        assert abs(sum(draw["probabilities"]) - 1) < 1e-6 and draw["chosen"] == pick.ref["reviewItem"]
        assert pick.probability == draw["p"]
        assert pick.context["chosenRank"] >= 1 and pick.context["versions"]["decisions"] == "decision-1"

        plan = s.scalars(new.where(m.DecisionLog.kind == "quiz_plan")).one()
        item = s.get(m.QuizItem, attempt.item_id)
        assert plan.ref == {"reviewItem": pick.ref["reviewItem"], "attempt": attempt.id, "quizItem": item.id}
        assert plan.chosen == ("has_bug" if plan.context["plannedHasBug"] else "no_bug")   # 记的是计划
        assert plan.context["itemHasBug"] == item.has_bug                                   # 实际给的另记
        assert plan.context["planMet"] == (plan.context["plannedHasBug"] == item.has_bug)
        assert abs(sum(c["p"] for c in plan.candidates) - 1) < 1e-9
        assert plan.context["bugRule"] in ("random", "ratio_low", "ratio_high")
        assert plan.probability == (0.5 if plan.context["bugRule"] == "random" else 1.0)
        assert plan.context["madeBy"] in plan.context["tried"] and plan.context["source"] == item.source
        # 已经备好题：不再备、也不再记
        assert review.prepare(s, UID, "C") == "ready"
        assert len(s.scalars(new.where(m.DecisionLog.kind == "review_pick")).all()) == 1


def test_notice_decisions(demo):
    """刚交的没过的提交：发了哪种主动消息记一条；再处理同一次提交记“已经发过”；很久以前的、批量补同步的不记。"""
    sid = demo["server_id"]
    with db.session_scope() as s:
        old = s.get(m.Submission, (sid, 537201))
        new_id = s.scalar(select(m.Submission.submission_id).order_by(m.Submission.submission_id.desc())) + 1
        now = db.now()
        sub = m.Submission(server_id=sid, submission_id=new_id, user_id=UID, problem_id=old.problem_id, contest_id=old.contest_id,
                           class_id=old.class_id, submit_time=now - dt.timedelta(minutes=1), judgement="WA",
                           judgement_text="答案错误", detail_status="done")
        s.add(sub)
        s.add(m.SubmissionLabel(server_id=sid, submission_id=new_id, error_code="LP01", skill_code="C05.04.03",
                                role="primary", confidence=0.9, is_current=True))
        s.flush()
        before = s.scalar(select(m.DecisionLog.id).order_by(m.DecisionLog.id.desc())) or 0   # 演示数据入库时也记过
        msg = proactive.on_failed_submission(s, sub, now)
        notices = select(m.DecisionLog).where(m.DecisionLog.kind == "notice", m.DecisionLog.id > before)
        rows = s.scalars(notices).all()
        assert len(rows) == 1 and rows[0].ref["submission"] == new_id
        if msg is not None:
            assert rows[0].chosen.startswith("send:") and rows[0].ref["message"] == msg.id
            assert rows[0].context["attemptNo"] >= 1 and rows[0].context["error"] == "LP01"
            proactive.on_failed_submission(s, sub, now)
            again = s.scalars(notices.order_by(m.DecisionLog.id)).all()
            assert again[-1].chosen.startswith(("skip:", "update:"))
        n = len(s.scalars(notices).all())
        old.notice_eligible = None                                                       # 没标过“该提醒”的
        proactive.on_failed_submission(s, old, old.submit_time + dt.timedelta(hours=5))   # 很久以前的：不记
        old.notice_eligible = False
        proactive.on_failed_submission(s, old, db.now())                                 # 批量补同步的：不记
        assert len(s.scalars(notices).all()) == n


def test_decisions_are_exported_but_not_shown_to_students(demo):
    with db.session_scope() as s:
        s.add(m.DecisionLog(user_id=UID, course="C", kind="quiz_plan", rule="quiz-plan-1", chosen="has_bug",
                            probability=0.5, candidates=[{"hasBug": True, "p": 0.5}, {"hasBug": False, "p": 0.5}],
                            context={"bugRule": "random"}, ref={"attempt": 1}))
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "dev-test", "password": "dev-pass"})   # 日志申请只给开发者
        body = {"ids": UID, "dateFrom": "2026-06-01", "dateTo": "2027-05-31", "format": "json",
                "kinds": ["decision"], "purpose": "论文数据分析"}
        resp = c.post("/api/teacher/logs", json=body)
        assert resp.status_code == 200, resp.text
        req = resp.json()["request"]
        js = c.get(f"/api/teacher/logs/{req['id']}/download", params={"token": req["token"]}).json()
        row = next(r for r in js["rows"] if r["data"].get("kind") == "quiz_plan")
        assert row["user_id"] == UID and row["data"]["probability"] == 0.5 and row["data"]["candidates"]
        assert js["request"]["versions"]["decisions"] == "decision-1"
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": UID, "password": "test-pass"})
        for path in ("/api/review", "/api/progress", "/api/homework"):
            assert "quiz-plan-1" not in c.get(path).text and "evidence-priority" not in c.get(path).text


@pytest.mark.skipif(shutil.which("gcc") is None, reason="没有 gcc")
def test_student_can_request_a_review_now(demo, monkeypatch):
    """复习安排里点“去复习”：不等到期、不受每天两道的限制，马上出一道；决策记录记成学生自己选的；一天有上限。"""
    from app import config

    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": UID, "password": "test-pass"})
        page = c.get("/api/review").json()
        items = page["due"] + page["upcoming"]
        assert items
        pick = items[-1]["id"]
        r = c.post(f"/api/review/{pick}/request")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["itemId"] == pick and body["quiz"]["state"] == "judge" and body["lead"]
        again = c.post(f"/api/review/{pick}/request").json()          # 已经备好的：同一道
        assert again["quiz"]["attemptId"] == body["quiz"]["attemptId"]
        with db.session_scope() as s:
            rows = s.scalars(select(m.DecisionLog).where(m.DecisionLog.rule == "student-pick",
                                                         m.DecisionLog.chosen == f"review:{pick}")).all()
            assert len(rows) == 1                                           # 点了两次（“继续”）只记一次
            row = rows[0]
            assert row.chosen == f"review:{pick}" and row.context["by"] == "student"
            assert row.ref["attempt"] == body["quiz"]["attemptId"]
        monkeypatch.setitem(config.REVIEW, "self_daily_cap", 0)
        other = next((x["id"] for x in items if x["id"] != pick), None)
        if other is not None:
            blocked = c.post(f"/api/review/{other}/request")
            assert blocked.status_code == 409 and "明天" in blocked.json()["error"]
        assert c.post("/api/review/99999999/request").status_code == 409
