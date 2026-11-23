"""全部自动：名单和作业定时同步、发现不认识的学生和作业时马上同步、回合自动结束并评估、评语自动写。"""
from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import assess, automation, db
from app import models as m
from app.chat import workflow
from app.web.app import create_app
from app.xdoj import sync_meta
from app.xdoj.daemon import Daemon
from app.xdoj.fake import FakeJudge, FakeRun, FakeSubmission, FakeXdoj

UID = "2500000001"


def _meta_triggers(s) -> int:
    return s.scalar(select(func.count()).select_from(m.Trigger).where(m.Trigger.kind == "sync_meta",
                                                                      m.Trigger.handled_at.is_(None))) or 0


def test_roster_syncs_every_two_hours(ctx, fake, clock):
    daemon = Daemon([ctx])
    assert daemon.step() == "meta"            # 启动后第一件事：同步名单
    daemon.drain()
    clock.advance(60 * 60)                    # 一小时后：还不用
    assert not daemon._meta_due(ctx)
    clock.advance(61 * 60)                    # 两小时后：自动再同步一次，不用谁去点
    assert daemon._meta_due(ctx)
    assert daemon.step() == "meta"


def test_unknown_homework_and_student_trigger_sync(ctx, fake, clock):
    sync_meta.sync_metadata(ctx)
    daemon = Daemon([ctx])
    daemon.drain()
    with db.session_scope() as s:
        assert _meta_triggers(s) == 0
    # 一个刚加进班的学生，交到了一个刚布置的作业里
    fake.submissions[537900] = FakeSubmission(
        id=537900, user_id="2500000099", user_name="新同学", problem_id=406, contest_id=777, class_id=31,
        submit_time="2026-11-16 21:30:00", source=b"int main(){return 0;}\n",
        judges=[FakeJudge(id=538900, result="答案错误", points=0, runs=[FakeRun(2154900, 1396, "错误", program_output="")])])
    clock.advance(120)
    daemon.drain()
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537900)) is not None
        handled = s.scalars(select(m.Trigger).where(m.Trigger.kind == "sync_meta")).all()
        assert handled and all(t.handled_at is not None for t in handled)   # 自动同步过了，不用谁去点
        assert len(handled) == 1                                            # 新学生、新作业合并成一次


def test_unknown_student_login_triggers_roster_sync(database, monkeypatch):
    """XDOJ 上有这个学生、密码也对，只是名单还没同步到：马上自动再同步一次。"""
    from app.web import teacher as teacher_api

    fx = FakeXdoj()
    fx.student_passwords["2599001234"] = "x"
    monkeypatch.setattr(teacher_api, "_test_transport", fx.transport())
    with db.session_scope() as s:
        srv = m.Server(code="c-t", name="C 语言（测试）", course="C", base_url="http://fake.test/xdoj-ssm")
        s.add(srv)
        s.flush()
        s.add(m.DataSource(server_id=srv.id, teacher_user_id="t", enabled=True, last_login_at=db.now()))
    with TestClient(create_app()) as c:
        r = c.post("/api/auth/login", json={"loginName": "2599001234", "password": "x", "course": "C"})
        assert r.status_code == 401 and "自动重新同步名单" in r.json()["error"]
        c.post("/api/auth/login", json={"loginName": "2599001234", "password": "x", "course": "C"})
    with db.session_scope() as s:
        assert _meta_triggers(s) == 1             # 重复登录不重复请求


def test_rounds_close_and_get_assessed_without_the_student(demo):
    """学生问到一半走开了：后台自动结束回合，接着自动评估，不用等他再打开页面。"""
    with db.session_scope() as s:
        open_rounds = s.scalars(select(m.ChatRound).where(m.ChatRound.status == "open")).all()
        assert open_rounds
        later = db.now() + dt.timedelta(hours=1)
        assert workflow.close_stale_rounds(s, later) == len(open_rounds)
        assert all(r.status == "closed" for r in open_rounds)
    assert assess.run_pending() >= 1
    with db.session_scope() as s:
        rid = open_rounds[0].id
        assert s.scalar(select(m.AgentAssessment.id).where(m.AgentAssessment.round_id == rid)) is not None


def test_idle_work_runs_everything(demo):
    automation._last.clear()
    with db.session_scope() as s:
        for r in s.scalars(select(m.ChatRound).where(m.ChatRound.status == "open")).all():
            r.last_message_at = db.now() - dt.timedelta(hours=2)
    assert automation.idle_work() >= 1          # 结束了回合、评了理解
    assert automation.idle_work() == 0          # 刚做过：不会空转
