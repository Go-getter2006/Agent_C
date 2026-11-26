"""数据结构独立地址、旧库升级和登录登记，全部使用假 XDOJ。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, db, models as m, servers, sources
from app.web import teacher
from app.web.app import create_app
from app.xdoj.fake import FakeXdoj
from app.xdoj import api, sync_meta
from app.xdoj.daemon import Daemon
from .conftest import FakeClock, make_context, small_fake


def test_ds_address_upgrade_and_teacher_login(database, monkeypatch):
    with db.session_scope() as s:
        known = servers.ensure_known(s)
        sid = known["ds"].id
        assert known["ds"].base_url == ""  # 通用测试配置模拟接入前的旧库。
    monkeypatch.setattr(config, "XDOJ_DS_URL", config.DEFAULT_XDOJ_DS_URL)
    fake = FakeXdoj(teacher_id="ds-teacher", password="fake-test-password")
    monkeypatch.setattr(teacher, "_test_transport", fake.transport())
    with TestClient(create_app()) as client:
        info = client.get("/api/auth/info").json()
        ds = next(c for c in info["allCourses"] if c["code"] == "D")
        assert ds["available"] and ds["server"] == "ds"
        r = client.post("/api/auth/login", json={"loginName": "ds-teacher", "password": "fake-test-password", "course": "D"})
        assert r.status_code == 200 and r.json()["course"] == "D" and r.json()["role"] == "teacher"
    with db.session_scope() as s:
        ds = s.get(m.Server, sid)
        assert ds.base_url == "http://39.100.78.5/xdoj-ssm" and ds.course == "D"
        source = s.scalar(select(m.DataSource).where(m.DataSource.teacher_user_id == "ds-teacher"))
        assert source.server_id == sid
        trigger = s.scalar(select(m.Trigger).where(m.Trigger.kind == "sync_meta", m.Trigger.target_id == str(source.id)))
        assert trigger is not None and trigger.server_id == sid
        assert s.scalar(select(m.Server.base_url).where(m.Server.code == "c")) == servers.C_SERVER.base_url
    contexts = sources.build_contexts()
    try:
        assert [(c.server_id, c.course) for c in contexts] == [(sid, "D")]
    finally:
        for ctx in contexts:
            ctx.client.close()
    # 第一次登录，即使原主循环还没有任何教师会话，也立即加载新老师并同步。
    sync_fake = small_fake()
    sync_fake.teacher_id, sync_fake.password = fake.teacher_id, fake.password
    fresh = make_context(sync_fake, FakeClock(), server_id=sid)
    fresh.source_id, fresh.course = source.id, "D"
    reloads, bank_counts = [], []
    original_bank = sync_meta.sync_problems

    def bank(ctx, **kwargs):
        with db.session_scope() as s:
            from sqlalchemy import func
            bank_counts.append(s.scalar(select(func.count()).select_from(m.Submission)))
        return original_bank(ctx, **kwargs)

    monkeypatch.setattr(sync_meta, "sync_problems", bank)
    daemon = Daemon([], reload=lambda: reloads.append(1) or [fresh])
    assert daemon.step() == "meta"
    assert len(reloads) == 1 and daemon.contexts == [fresh]
    assert bank_counts == [len(sync_fake.submissions)]  # 首批提交先落库，再开始题库列表。
    with db.session_scope() as s:
        assert s.get(m.Trigger, trigger.id).handled_at is not None
        assert s.scalar(select(m.XClass.class_id).where(m.XClass.server_id == sid)) is not None


def test_new_teacher_trigger_is_not_lost_when_context_not_loaded(ctx, registered_source):
    with db.session_scope() as s:
        api.request_meta_sync(s, ctx.server_id, source_id=ctx.source_id)
    daemon = Daemon([], reload=lambda: [])
    assert daemon.step() == "trigger"
    assert daemon.step() is None  # 仍加载失败时，不反复空转或吞掉触发。
    with db.session_scope() as s:
        trigger = s.scalar(select(m.Trigger).where(m.Trigger.kind == "sync_meta"))
        assert trigger.handled_at is None
    # 下一次加载成功，原触发仍能工作，不需要老师退出重登。
    daemon._reload = lambda: [ctx]
    daemon._last_trigger_reload = None
    assert daemon.step() == "meta"
    with db.session_scope() as s:
        assert s.get(m.Trigger, trigger.id).handled_at is not None


def test_ds_environment_override_and_course_guard(database, monkeypatch):
    monkeypatch.setattr(config, "XDOJ_DS_URL", "http://ds.test/xdoj-ssm")
    with db.session_scope() as s:
        assert servers.ensure_known(s)["ds"].base_url == config.XDOJ_DS_URL
        with pytest.raises(servers.WrongServer):
            sources.ensure_server(s, "wrong-d", "数据结构", servers.C_SERVER.base_url, "D")
        with pytest.raises(servers.WrongServer):
            sources.ensure_server(s, "wrong-c", "C", config.XDOJ_DS_URL, "C")
