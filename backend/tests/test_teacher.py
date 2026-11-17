"""教师端：权限按班级控制；图模型只在教师端出现；教师用 XDOJ 账号登录。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

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
    assert (row["userId"], row["name"], row["skill"]["code"], row["times"], row["lastProblem"]) == (
        UID, "林晓", "C06.03.02", 2, "成绩最高分")
    assert row["skill"]["name"].startswith("最值的初值设定")
    assert stuck["onceOnly"] >= 1 and stuck["common"][0]["students"] >= 1
    g = client.get(f"/api/teacher/classes/{key}/graph").json()
    assert g["trial"] is True and g["prereqStatus"] == "初稿，未审定"
    assert g["data"]["students"] == 12 and g["data"]["evidence"] > 100 and g["data"]["predictions"] > 50
    assert g["data"]["posteriors"] > 100 and g["data"]["updatedAt"]
    assert g["weekly"] and all(0 <= w["simpleAccuracy"] <= 1 and 0 <= w["graphAccuracy"] <= 1 for w in g["weekly"])
    assert g["edges"] and {"parent", "child", "n", "withParent", "withoutParent", "doubtful"} <= set(g["edges"][0])
    assert all(abs(x["simple"] - x["graph"]) >= config.GRAPH["big_gap"] - 0.006 for x in g["gaps"])
    assert all(x["name"] in ("林晓", "周航") or len(x["name"]) <= 4 for x in g["gaps"]) and all("code" in x["skill"] for x in g["gaps"])


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
        sources.ensure_server(s, "c-test", "C 语言（测试用的假服务器）", "http://fake.test/xdoj-ssm")
    listed = client.get("/api/teacher/servers").json()["servers"]
    assert {"code": "c", "name": "C 语言程序设计"} in listed      # 真实的 C 语言服务器写在代码里
    assert all(x["code"] != "ds" for x in listed)                 # 数据结构那台还没有地址，不能选
    body = {"server": "c-test", "userId": "t-wang", "password": "wrong"}
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
        user = s.scalar(select(m.User).where(m.User.login_name == "c-test:t-wang"))
        assert user.role == "teacher" and user.data_source_id == src.id
        assert s.scalars(select(m.Trigger).where(m.Trigger.kind == "sync_meta")).first() is not None
        # 密码明文不在任何表里
        for table in db.Base.metadata.sorted_tables:
            for row in s.execute(table.select()).all():
                assert "xdoj-real-password" not in repr(tuple(row)), table.name
    # 主循环能用这份加密保存的密码建立会话
    ctxs = sources.build_contexts()
    assert [c.source_id for c in ctxs] == [src.id]


def test_known_servers_guard(database):
    from app.servers import C_SERVER, WrongServer, ensure_known

    with db.session_scope() as s:
        known = ensure_known(s)
        assert known["c"].base_url == "http://47.92.139.74/xdoj-ssm" and known["c"].course == "C"
        assert known["ds"].base_url == "" and known["ds"].course == "D"
        with pytest.raises(WrongServer):
            sources.ensure_server(s, "ds", "数据结构", C_SERVER.base_url + "/", "D")   # 数据结构不能用 C 语言那台
        with pytest.raises(WrongServer):
            sources.ensure_server(s, "c", "C 语言", "http://1.2.3.4/xdoj-ssm", "C")   # C 语言那台的地址不能改
        assert sources.ensure_server(s, "ds", "数据结构", "http://10.1.2.3/xdoj-ssm", "D").course == "D"


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


# ------------------------------------------------------------ 概况、知识图谱
def test_overview_kpis_trend_attention(client, demo):
    login(client, "demo-teacher")
    ov = client.get(f"/api/teacher/classes/c{demo['server_id']}-31/overview").json()
    k = ov["kpis"]
    assert k["students"] == 12 and 0 <= k["active"] <= 12 and "firstTryDelta" in k and "review" in k
    assert len(ov["trend"]) == 8 and ov["trend"][-1]["label"] == "本周"
    assert sum(w["submissions"] for w in ov["trend"]) > 100
    names = {a["name"]: a["reasons"] for a in ov["attention"]}
    assert all(r for r in names.values())


def test_knowledge_map_two_courses_and_cross_links(client, demo):
    login(client, "demo-teacher")
    key = f"c{demo['server_id']}-31"
    g = client.get(f"/api/teacher/classes/{key}/knowledge-map").json()
    courses = {n["course"] for n in g["nodes"]}
    assert courses == {"C", "D"} and len(g["nodes"]) == 95
    cross = [e for e in g["edges"] if e["cross"]]
    assert cross and all(e["from"].startswith("C") and e["to"].startswith("D") for e in cross)
    node = {n["code"]: n for n in g["nodes"]}
    assert node["C06.03"]["covered"] and sum(node["C06.03"]["dist"]) == 12 and node["C06.03"]["mean"] is not None
    assert not node["D02.02"]["covered"] and node["D02.02"]["mean"] is None
    assert node["D02.01"]["readiness"] is not None   # 数据结构：由 C 语言推出来的准备度
    one = client.get(f"/api/teacher/classes/{key}/knowledge-map", params={"student": UID}).json()
    me = {n["code"]: n for n in one["nodes"]}
    assert me["C06.03"]["student"]["level"] in ("基本掌握", "待加强")
    assert client.get(f"/api/teacher/classes/{key}/knowledge-map", params={"student": "2500000101"}).status_code == 404
    d = client.get(f"/api/teacher/classes/{key}/knowledge-map/C06.03").json()
    assert d["name"] == "最值、求和、统计" and {p["title"] for p in d["problems"]} >= {"成绩最高分", "最大差值"}
    assert any(x["cross"] for x in client.get(f"/api/teacher/classes/{key}/knowledge-map/C10.01").json()["next"])


# ------------------------------------------------------------ 按需申请学生日志
def test_log_request_scoped_audited_and_minimal(client, demo):
    from app.chat import workflow

    sid = demo["server_id"]
    with db.session_scope() as s:
        workflow.handle(s, UID, (sid, 406), "我的手机号是 13800000000，帮我看看")   # 对话原文默认不导出
    login(client, "demo-teacher")
    meta = client.get("/api/teacher/logs").json()
    assert {k["code"] for k in meta["kinds"]} >= {"submission", "ask", "reply", "quiz", "review", "login", "view"}
    body = {"ids": f"{UID}, 2500000002 2500000101 9999999999", "dateFrom": "2026-06-01", "dateTo": "2027-05-31",
            "kinds": ["submission", "ask", "reply", "quiz"], "format": "csv", "purpose": "看看哪些同学卡在最值上"}
    r = client.post("/api/teacher/logs", json=body)
    assert r.status_code == 200, r.text
    req = r.json()["request"]
    assert req["granted"] == 3 and req["denied"] == ["9999999999"]   # 02 班也在他名下；不存在的学号不给
    assert req["rows"] > 20 and req["status"] == "ready" and r.json()["preview"]
    dl = client.get(f"/api/teacher/logs/{req['id']}/download", params={"token": req["token"]})
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    text = dl.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header.startswith("学号,姓名,时间,类型,交互") and "原文" not in header
    assert "13800000000" not in text and "林晓" in text and "周航" in text
    assert "没通过第" in text and "在 XDOJ 提交" in text and "智能体回复" in text
    with db.session_scope() as s:   # 测试点数据不导出
        for tc in s.scalars(select(m.Testcase)).all():
            line = " ".join(tc.input_bytes.decode("utf-8").split())
            if len(line.split()) >= 4:
                assert line not in " ".join(text.split())
    assert client.get(f"/api/teacher/logs/{req['id']}/download", params={"token": "wrong"}).status_code == 404
    # 勾选“包含对话原文”才有原文
    with_text = client.post("/api/teacher/logs", json={**body, "includeText": True, "format": "json"}).json()["request"]
    js = client.get(f"/api/teacher/logs/{with_text['id']}/download", params={"token": with_text["token"]}).json()
    assert any("13800000000" in (row.get("text") or "") for row in js["rows"])
    assert js["request"]["purpose"] == "看看哪些同学卡在最值上"
    # 作废之后不能再下载；记录还在（审计）
    client.post(f"/api/teacher/logs/{req['id']}/revoke")
    assert client.get(f"/api/teacher/logs/{req['id']}/download", params={"token": req["token"]}).status_code == 410
    listed = client.get("/api/teacher/logs").json()["requests"]
    assert [x["status"] for x in listed][:2] == ["ready", "revoked"] and listed[1]["downloads"] == 1


def test_other_teacher_cannot_get_logs_of_my_students(client, demo):
    with db.session_scope() as s:
        src = m.DataSource(server_id=demo["server_id"], teacher_user_id="someone-else", enabled=False)
        s.add(src)
        s.flush()
        s.add(m.User(role="teacher", login_name="other-teacher", display_name="别的老师", data_source_id=src.id))
    login(client, "other-teacher")
    r = client.post("/api/teacher/logs", json={"ids": UID, "dateFrom": "2026-11-01", "dateTo": "2026-11-30",
                                               "kinds": ["submission"], "purpose": "想看看别班"}).json()["request"]
    assert r["granted"] == 0 and r["denied"] == [UID] and r["rows"] == 0


def test_log_request_rules(client, demo):
    login(client, "demo-teacher")
    base = {"ids": UID, "dateFrom": "2026-11-01", "dateTo": "2026-11-30", "kinds": ["submission"], "purpose": "期中前看看"}
    assert client.post("/api/teacher/logs", json={**base, "purpose": ""}).status_code == 400          # 要写用途
    assert client.post("/api/teacher/logs", json={**base, "kinds": []}).status_code == 400
    assert client.post("/api/teacher/logs", json={**base, "dateFrom": "2025-01-01"}).status_code == 400  # 超过 366 天
    assert client.post("/api/teacher/logs", json={**base, "classKeys": ["c1-32"], "ids": ""}).status_code == 200
    assert client.post("/api/teacher/logs", json={**base, "classKeys": ["c9-99"]}).status_code == 403
    for _ in range(19):
        client.post("/api/teacher/logs", json=base)
    assert client.post("/api/teacher/logs", json=base).status_code == 400    # 每天最多 20 次
    client.post("/api/auth/logout")
    login(client, UID)
    assert client.get("/api/teacher/logs").status_code == 403                # 学生不能申请


def test_student_login_through_xdoj(client, monkeypatch):
    """学生账号来自老师同步下来的名单；登录时去 XDOJ 核对密码，密码不保存。"""
    import httpx

    from app import accounts

    fx = FakeXdoj()
    fx.student_passwords["2599000001"] = "stu-xdoj-pw"
    monkeypatch.setattr(teacher_api, "_test_transport", fx.transport())
    with db.session_scope() as s:
        srv = sources.ensure_server(s, "c-test", "C 语言（测试用的假服务器）", "http://fake.test/xdoj-ssm")
        s.add(m.Student(user_id="2599000001", name="测试生"))
        s.flush()
        accounts.ensure_student_user(s, "2599000001", "测试生")
        s.add(m.Enrollment(user_id="2599000001", server_id=srv.id, class_id=77))

    r = client.post("/api/auth/login", json={"loginName": "2599000099", "password": "x"})
    assert r.status_code == 401 and "名单" in r.json()["error"]          # 不在任何名单里
    assert client.post("/api/auth/login", json={"loginName": "2599000001", "password": "wrong"}).status_code == 401
    # 演示口令不能冒充真实名单里的学生
    assert client.post("/api/auth/login", json={"loginName": "2599000001", "password": "test-pass"}).status_code == 401
    r = client.post("/api/auth/login", json={"loginName": "2599000001", "password": "stu-xdoj-pw"})
    assert r.status_code == 200 and r.json() == {"role": "student", "name": "测试生"}
    assert client.get("/api/auth/me").json()["loginName"] == "2599000001"
    with db.session_scope() as s:
        for table in db.Base.metadata.sorted_tables:
            for row in s.execute(table.select()).all():
                assert "stu-xdoj-pw" not in repr(tuple(row)), table.name

    # 演示数据里的学生照旧用演示口令
    client.post("/api/auth/logout")
    assert login(client, "2500000001").status_code == 200

    # XDOJ 连不上：说清楚是连不上，不说密码错
    def down(request):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(teacher_api, "_test_transport", httpx.MockTransport(down))
    r = client.post("/api/auth/login", json={"loginName": "2599000001", "password": "stu-xdoj-pw"})
    assert r.status_code == 502


def test_teacher_login_checks_role_and_reads_name(client, monkeypatch):
    """XDOJ 登录返回 userType：只有教师（1）能登记成老师；姓名从教师首页右上角读。"""
    fx = FakeXdoj(teacher_id="t-li", password="pw")
    fx.teacher_name = "李老师"
    fx.student_passwords["2599000002"] = "stu-pw"
    monkeypatch.setattr(teacher_api, "_test_transport", fx.transport())
    with db.session_scope() as s:
        sources.ensure_server(s, "c-test", "C 语言（测试用的假服务器）", "http://fake.test/xdoj-ssm")
    r = client.post("/api/teacher/login", json={"server": "c-test", "userId": "2599000002", "password": "stu-pw"})
    assert r.status_code == 403
    with db.session_scope() as s:
        assert s.scalar(select(m.DataSource).where(m.DataSource.teacher_user_id == "2599000002")) is None
    r = client.post("/api/teacher/login", json={"server": "c-test", "userId": "t-li", "password": "pw"})
    assert r.status_code == 200 and r.json()["name"] == "李老师"


def test_teacher_links_accounts_of_both_courses(client, monkeypatch):
    """C 语言和数据结构是两台 XDOJ，同一位老师的账号密码可能不同：关联以后用哪个账号登录都进同一个账号。"""
    import httpx

    c_fx = FakeXdoj(teacher_id="t-zhang", password="c-pass")
    ds_fx = FakeXdoj(teacher_id="zhang-ds", password="ds-pass")
    stu = FakeXdoj(teacher_id="t-other", password="other-pass")
    hosts = {"c.test": c_fx, "ds.test": ds_fx}
    monkeypatch.setattr(teacher_api, "_test_transport",
                        httpx.MockTransport(lambda req: hosts.get(req.url.host, stu).handle(req)))
    with db.session_scope() as s:
        sources.ensure_server(s, "c-test", "C 语言（测试）", "http://c.test/xdoj-ssm")
        sources.ensure_server(s, "ds-test", "数据结构（测试）", "http://ds.test/xdoj-ssm", "D")
        sources.ensure_server(s, "o-test", "另一台（测试）", "http://other.test/xdoj-ssm")

    r = client.post("/api/teacher/login", json={"server": "c-test", "userId": "t-zhang", "password": "c-pass"})
    assert r.status_code == 200
    # 关联数据结构那台的账号（密码不同）；密码错不行
    bad = {"server": "ds-test", "userId": "zhang-ds", "password": "wrong"}
    assert client.post("/api/teacher/sources/link", json=bad).status_code == 401
    r = client.post("/api/teacher/sources/link", json={**bad, "password": "ds-pass"})
    assert r.status_code == 200
    codes = sorted(x["serverCode"] for x in client.get("/api/teacher/sources").json()["sources"])
    assert codes == ["c-test", "ds-test"]

    # 换用数据结构的账号登录：进的是同一个账号，两门课都在
    client.post("/api/auth/logout")
    r = client.post("/api/teacher/login", json={"server": "ds-test", "userId": "zhang-ds", "password": "ds-pass"})
    assert r.status_code == 200
    assert sorted(x["serverCode"] for x in client.get("/api/teacher/sources").json()["sources"]) == ["c-test", "ds-test"]
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(m.User).where(m.User.role == "teacher",
                                                                      m.User.login_name.like("%zhang%"))) == 1

    # 别的老师不能把已经关联的账号拿走
    client.post("/api/auth/logout")
    assert client.post("/api/teacher/login", json={"server": "o-test", "userId": "t-other", "password": "other-pass"}).status_code == 200
    r = client.post("/api/teacher/sources/link", json={"server": "ds-test", "userId": "zhang-ds", "password": "ds-pass"})
    assert r.status_code == 409

    # 取消关联：至少留一个
    client.post("/api/auth/logout")
    client.post("/api/teacher/login", json={"server": "c-test", "userId": "t-zhang", "password": "c-pass"})
    srcs = {x["serverCode"]: x["id"] for x in client.get("/api/teacher/sources").json()["sources"]}
    assert client.post(f"/api/teacher/sources/{srcs['ds-test']}/unlink").status_code == 200
    assert client.post(f"/api/teacher/sources/{srcs['c-test']}/unlink").status_code == 409
