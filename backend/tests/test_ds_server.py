"""数据结构独立地址、旧库升级和登录登记，全部使用假 XDOJ。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, db, models as m, servers, sources
from app.web import teacher
from app.web.app import create_app
from app.xdoj.fake import FakeXdoj


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


def test_ds_environment_override_and_course_guard(database, monkeypatch):
    monkeypatch.setattr(config, "XDOJ_DS_URL", "http://ds.test/xdoj-ssm")
    with db.session_scope() as s:
        assert servers.ensure_known(s)["ds"].base_url == config.XDOJ_DS_URL
        with pytest.raises(servers.WrongServer):
            sources.ensure_server(s, "wrong-d", "数据结构", servers.C_SERVER.base_url, "D")
        with pytest.raises(servers.WrongServer):
            sources.ensure_server(s, "wrong-c", "C", config.XDOJ_DS_URL, "C")
