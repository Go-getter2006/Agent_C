"""教师端：权限按班级控制；图模型只在教师端出现；教师用 XDOJ 账号登录。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, crypto, db, graphmodel, sources
from app import models as m
from app.web import teacher as teacher_api
from app.web.app import create_app
from app.xdoj.fake import FakeXdoj

UID = "2500000001"


@pytest.fixture()
def client(demo):
    with TestClient(create_app()) as c:
        yield c


def login(c, name, password="test-pass"):
    return c.post("/api/auth/login", json={"loginName": name, "password": password})


def test_roles_are_separated(client):
    assert login(client, UID).json()["role"] == "student"
    for path in ("/api/teacher/classes", "/api/teacher/sources", f"/api/teacher/students/{UID}",
                 "/api/teacher/classes/c1-31/overview", "/api/teacher/classes/c1-31/graph"):
        assert client.get(path).status_code == 403, path
    client.post("/api/auth/logout")
    assert login(client, "demo-teacher").json() == {"role": "teacher", "name": "演示教师"}
    for path in ("/api/homeworks", "/api/progress", "/api/threads/general"):
        assert client.get(path).status_code == 403, path


def test_class_overview_and_students(client, demo):
    login(client, "demo-teacher")
    sid = demo["server_id"]
    classes = client.get("/api/teacher/classes").json()["classes"]
    assert [(c["name"], c["students"]) for c in classes] == [("C 语言程序设计 01 班", 12), ("C 语言程序设计 02 班", 1)]
    key = f"c{sid}-31"
    ov = client.get(f"/api/teacher/classes/{key}/overview").json()
    assert ov["class"]["students"] == 12 and ov["levels"] == ["已掌握", "基本掌握", "待加强", "还没练到"]
    assert all(sum(k["counts"]) == 12 for k in ov["knowledge"]) and len(ov["knowledge"]) > 15
    assert {"code", "name", "students", "count"} == set(ov["errors"][0])
    assert ov["errors"][0]["count"] >= ov["errors"][-1]["count"]
    assert [h["title"] for h in ov["homeworks"]] == ["上机练习：一维数组", "上机练习：循环结构", "上机练习：选择结构"]
    rows = client.get(f"/api/teacher/classes/{key}/students").json()
    me = next(r for r in rows["students"] if r["userId"] == UID)
    assert (me["name"], me["tried"], me["passed"], me["submissions"], me["firstTryRate"], me["stuck"]) == (
        "林晓", 12, 9, 22, 25, 2)
    assert rows["total"] == 14 and len(rows["students"]) == 12


def test_student_detail_has_internal_fields_for_teacher(client):
    login(client, "demo-teacher")
    d = client.get(f"/api/teacher/students/{UID}").json()
    assert d["student"]["name"] == "林晓" and d["student"]["class"]["name"].endswith("01 班")
    skill = next(x for x in d["skills"] if x["code"] == "C06.03.02")
    assert skill["level"] == "基本掌握" and 0 < skill["p"] < 1 and skill["graph"] is not None
    sub = next(x for x in d["submissions"] if x["title"] == "成绩最高分" and x["attemptNo"] == 3)
    assert sub["labels"][0]["code"] == "TY09" and sub["grid"] == [True, True, False, True, True]
    assert sub["problemId"] == 406
    assert [(x["code"], x["nth"], x["resolution"]) for x in d["stuck"]] == [
        ("C06.03.02", 1, "example_given"), ("C02.01.02", 1, "example_given")]
    assert {r["endReason"] for r in d["rounds"]} >= {"通过", "超时"}
    assert d["quizzes"][0]["outcome"] == "right_judge"
    # 教师端也不给测试点的原始数据
    blob = json.dumps(d, ensure_ascii=False)
    assert "-12 -7 -30" not in blob


def test_stuck_list_and_graph_page(client, demo):
    from app.chat import workflow

    sid = demo["server_id"]
    with db.session_scope() as s:   # 让演示学生在同一个技能上第二次卡住
        workflow.handle(s, UID, (sid, 406), action="hint")
    login(client, "demo-teacher")
    key = f"c{sid}-31"
    stuck = client.get(f"/api/teacher/classes/{key}/stuck").json()
    row = stuck["rows"][0]
    assert (row["userId"], row["code"], row["times"], row["lastProblem"]) == (UID, "C06.03.02", 2, "成绩最高分")
    assert stuck["onceOnly"] >= 1 and stuck["common"][0]["students"] >= 1
    g = client.get(f"/api/teacher/classes/{key}/graph").json()
    assert g["trial"] is True and g["prereqStatus"] == "初稿，未审定"
    assert g["data"]["students"] == 12 and g["data"]["evidence"] > 100 and g["data"]["predictions"] > 50
    assert g["data"]["posteriors"] > 100 and g["data"]["updatedAt"]
    assert g["weekly"] and all(0 <= w["simpleAccuracy"] <= 1 and 0 <= w["graphAccuracy"] <= 1 for w in g["weekly"])
    assert g["edges"] and {"parent", "child", "n", "withParent", "withoutParent", "doubtful"} <= set(g["edges"][0])
    assert all(abs(x["simple"] - x["graph"]) >= config.GRAPH["big_gap"] - 0.006 for x in g["gaps"])


def test_other_teacher_sees_nothing(client, demo):
    with db.session_scope() as s:
        src = m.DataSource(server_id=demo["server_id"], teacher_user_id="someone-else", enabled=False)
        s.add(src)
        s.flush()
        s.add(m.User(role="teacher", login_name="other-teacher", display_name="别的老师", data_source_id=src.id))
        s.add(m.User(role="admin", login_name="root", display_name="管理员"))
    login(client, "other-teacher")
    sid = demo["server_id"]
    assert client.get("/api/teacher/classes").json()["classes"] == []
    assert client.get(f"/api/teacher/classes/c{sid}-31/overview").status_code == 404
    assert client.get(f"/api/teacher/classes/c{sid}-31/students").status_code == 404
    assert client.get(f"/api/teacher/students/{UID}").status_code == 404
    mine = client.get("/api/teacher/sources").json()["sources"]
    assert [x["account"] for x in mine] == ["someone-else"]
    assert client.post("/api/teacher/sources/1/sync").status_code == 404
    client.post("/api/auth/logout")
    login(client, "root")   # 管理员看全部
    assert len(client.get("/api/teacher/classes").json()["classes"]) == 2
    assert len(client.get("/api/teacher/sources").json()["sources"]) == 2
    assert client.get(f"/api/teacher/students/{UID}").status_code == 200


def test_sources_page(client):
    login(client, "demo-teacher")
    data = client.get("/api/teacher/sources").json()
    src = data["sources"][0]
    assert (src["account"], src["demo"], src["enabled"], src["classes"], src["submissions"]) == (
        "demo-teacher", True, False, 2, 201)
    assert "password" not in json.dumps(data).lower()
    assert client.post(f"/api/teacher/sources/{src['id']}/sync").status_code == 409


def test_teacher_login_by_xdoj_account(client, monkeypatch):
    fx = FakeXdoj(teacher_id="t-wang", password="xdoj-real-password")
    monkeypatch.setattr(teacher_api, "_test_transport", fx.transport())
    with db.session_scope() as s:
        sources.ensure_server(s, "c", "C 语言", "http://fake.test/xdoj-ssm")
    assert client.get("/api/teacher/servers").json() == {"servers": [{"code": "c", "name": "C 语言"}]}
    body = {"server": "c", "userId": "t-wang", "password": "wrong"}
    assert client.post("/api/teacher/login", json=body).status_code == 401
    assert client.post("/api/teacher/login", json={**body, "server": "nope"}).status_code == 400
    assert client.post("/api/teacher/login", json={**body, "server": "demo"}).status_code == 400
    r = client.post("/api/teacher/login", json={**body, "password": "xdoj-real-password"})
    assert r.status_code == 200 and r.json()["role"] == "teacher"
    assert client.get("/api/teacher/classes").json()["classes"] == []   # 班级要等主循环同步进来
    with db.session_scope() as s:
        src = s.scalar(select(m.DataSource).where(m.DataSource.teacher_user_id == "t-wang"))
        assert src.enabled and src.password_enc and "xdoj-real-password" not in src.password_enc
        assert crypto.decrypt_secret(src.password_enc) == "xdoj-real-password"
        assert sources.password_of(src) == "xdoj-real-password"
        user = s.scalar(select(m.User).where(m.User.login_name == "c:t-wang"))
        assert user.role == "teacher" and user.data_source_id == src.id
        assert s.scalars(select(m.Trigger).where(m.Trigger.kind == "sync_meta")).first() is not None
        # 密码明文不在任何表里
        for table in db.Base.metadata.sorted_tables:
            for row in s.execute(table.select()).all():
                assert "xdoj-real-password" not in repr(tuple(row)), table.name
    # 主循环能用这份加密保存的密码建立会话
    ctxs = sources.build_contexts()
    assert [c.source_id for c in ctxs] == [src.id]


def test_graph_model_directions(database):
    weak = graphmodel.infer({"C06.03.02": (0.0, 3.0)})
    strong = graphmodel.infer({"C06.03.02": (4.0, 0.0)})
    assert weak["C06.03.02"] < 0.1 < 0.9 < strong["C06.03.02"]
    assert weak["C02.04.02"] < 0.5 < strong["C02.04.02"]          # 证据会传到先修技能上
    assert weak["C06.03.03"] < strong["C06.03.03"]
    assert graphmodel.infer({}) == {}
    again = graphmodel.infer({"C06.03.02": (0.0, 3.0)})
    assert again == weak, "同样的证据得到同样的结果（随机种子固定）"


def test_prediction_log_is_idempotent(demo):
    with db.session_scope() as s:
        before = len(s.scalars(select(m.PredictionLog)).all())
        assert before > 50 and graphmodel.log_predictions(s, UID) == 0
        rows = s.scalars(select(m.PredictionLog).where(m.PredictionLog.user_id == UID)).all()
        assert len(rows) == 12 and sum(r.outcome for r in rows) == 3   # 12 道题里 3 道一次通过
        assert all(0 <= r.p_simple <= 1 and 0 <= r.p_graph <= 1 for r in rows)
