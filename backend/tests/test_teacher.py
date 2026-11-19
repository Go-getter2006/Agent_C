"""教师端：权限按班级控制；图模型只在教师端出现；教师用 XDOJ 账号登录。"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import config, crypto, db, graphmodel, mastery, sources
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
    assert login(client, "demo-teacher").json() == {"role": "teacher", "name": "演示教师", "course": "C"}
    for path in ("/api/homeworks", "/api/progress", "/api/threads/general"):
        assert client.get(path).status_code == 403, path


def _avg_to_pass(server_id: int, uid: str) -> float:
    """独立算一遍：每道通过的题交到第几次第一次通过，取平均。"""
    with db.session_scope() as s:
        subs = s.scalars(select(m.Submission).where(m.Submission.server_id == server_id, m.Submission.user_id == uid)
                         .order_by(m.Submission.submission_id)).all()
    by: dict[int, list[str]] = {}
    for x in subs:
        by.setdefault(x.problem_id, []).append(x.judgement)
    tries = [js.index("AC") + 1 for js in by.values() if "AC" in js]
    return round(sum(tries) / len(tries), 1)


def test_class_overview_and_students(client, demo):
    login(client, "demo-teacher")
    sid = demo["server_id"]
    classes = client.get("/api/teacher/classes").json()["classes"]
    assert [(c["name"], c["students"]) for c in classes] == [("C 语言程序设计 01 班", 12), ("C 语言程序设计 02 班", 1)]
    key = f"c{sid}-31"
    ov = client.get(f"/api/teacher/classes/{key}/overview").json()
    assert ov["class"]["students"] == 12 and "knowledge" not in ov and "overconfident" not in ov
    # 要讲的知识点：待加强、卡住过、没来问的人多的排前面，带最常见的错误；全部知识点在知识图谱里看
    focus = ov["focus"]
    assert 0 < len(focus) <= 6 and {"code", "name", "weak", "stuck", "silent", "error"} == set(focus[0])
    load = [f["weak"] + f["stuck"] + f["silent"] for f in focus]
    assert load == sorted(load, reverse=True) and all(x > 0 for x in load)
    top = next(f for f in focus if f["code"] == "C06.03")   # 林晓等人在“最值、求和、统计”上待加强、卡住过
    assert top["weak"] >= 1 and top["stuck"] >= 1 and top["error"]["name"]
    assert {"code", "name", "students", "count"} == set(ov["errors"][0])
    assert ov["errors"][0]["count"] >= ov["errors"][-1]["count"]
    assert [h["title"] for h in ov["homeworks"]] == ["上机练习：一维数组", "上机练习：循环结构", "上机练习：选择结构"]
    rows = client.get(f"/api/teacher/classes/{key}/students").json()
    me = next(r for r in rows["students"] if r["userId"] == UID)
    assert (me["name"], me["tried"], me["passed"], me["submissions"], me["stuck"]) == ("林晓", 12, 9, 22, 2)
    assert me["avgToPass"] == _avg_to_pass(demo["server_id"], UID)
    assert rows["total"] == 14 and len(rows["students"]) == 12
    detail = client.get(f"/api/teacher/students/{UID}", params={"classKey": key}).json()["assignmentMastery"]
    assert all(me[k] == value for k, value in detail.items()) and me["coveredProblems"] == 12


def test_student_detail_has_internal_fields_for_teacher(client):
    login(client, "demo-teacher")
    d = client.get(f"/api/teacher/students/{UID}").json()
    assert d["student"]["name"] == "林晓" and d["student"]["class"]["name"].endswith("01 班")
    # 概况的几个数和学生列表一致
    rows = client.get(f"/api/teacher/classes/{d['student']['class']['key']}/students").json()
    me = next(r for r in rows["students"] if r["userId"] == UID)
    f = d["facts"]
    assert (f["score"], f["passed"], f["total"], f["avgToPass"], f["weak"]) == (
        me["score"], me["passed"], rows["total"], me["avgToPass"], me["weak"])
    assert f["stuck"] == len(d["stuck"]) and f["lastActive"]
    skill = next(x for x in d["skills"] if x["code"] == "C06.03.02")
    # 演示情节中反复出现最值初始化错误，v2 与学生端一致判为待加强。
    assert skill["level"] == "待加强" and 0 < skill["p"] < 1 and skill["graph"] is not None
    sub = next(x for x in d["submissions"] if x["title"] == "成绩最高分" and x["attemptNo"] == 3)
    assert sub["labels"][0]["code"] == "TY09" and sub["grid"] == [True, True, False, True, True]
    assert sub["problemId"] == 406
    assert [(x["code"], x["nth"], x["resolution"]) for x in d["stuck"]] == [
        ("C06.03.02", 1, "example_given"), ("C02.01.02", 1, "example_given")]
    assert {r["endReason"] for r in d["rounds"]} >= {"通过", "超时"}
    # 最新一项也可能尚未完成；核对接口与实际记录一致，不能假定最近一项已答对。
    with db.session_scope() as s:
        latest_quiz = s.scalars(select(m.QuizAttempt).where(
            m.QuizAttempt.user_id == UID, m.QuizAttempt.skill_code.like("C%"))
            .order_by(m.QuizAttempt.id.desc())).first()
        assert latest_quiz is not None
        assert (d["quizzes"][0]["outcome"], d["quizzes"][0]["state"]) == (latest_quiz.outcome, latest_quiz.state)
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
    # 研究 · 求助效果：按知识点的比例，不列学生
    help_ = client.get(f"/api/teacher/classes/{key}/help").json()
    row = next(r for r in help_["rows"] if r["code"] == "C06.03")   # 按知识点汇总
    assert (row["name"], row["episodes"], row["repeat"]) == ("最值、求和、统计", 2, 1)
    assert row["skills"][0]["code"] == "C06.03.02" and row["skills"][0]["episodes"] == 2
    assert row["passed"]["d"] >= 1 and help_["version"] == "stuck-kp-1"
    assert all(set(r) >= {"recall", "passed", "transfer", "silent"} for r in help_["rows"])
    assert not any("studentRows" in r for r in help_["rows"])
    assert help_["totals"]["stuck"] >= 1 and {"students", "constructive", "answerSeeking"} == set(help_["dialogue"])
    # 每次帮助之后（app/helpeffect.py）：按帮助级别，最后一行是全部，比例都有分子分母
    nxt = help_["helpNext"]
    assert nxt["version"] == "help-next-1" and nxt["rows"] and nxt["rows"][-1]["level"] == "all"
    assert all(set(r) >= {"n", "resubmitted", "nextPass", "errorGone", "passed", "medianMinutes", "medianTries"}
               for r in nxt["rows"])
    # 上课用：谁卡在这里，在知识图谱的知识点详情里
    node = client.get(f"/api/teacher/classes/{key}/knowledge-map/C06.03").json()
    me = node["stuck"]["students"][0]
    assert (me["userId"], me["name"], me["times"], me["rounds"], me["lastProblem"]) == (UID, "林晓", 2, 2, "成绩最高分")
    assert me["skills"][0].startswith("最值的初值设定")
    nodes = {n["code"]: n for n in client.get(f"/api/teacher/classes/{key}/knowledge-map").json()["nodes"]}
    assert nodes["C06.03"]["stuck"] >= 1 and "silent" in nodes["C06.03"]
    assert client.get(f"/api/teacher/classes/{key}/stuck").status_code == 404   # 旧接口不再有
    g = client.get(f"/api/teacher/classes/{key}/graph").json()
    assert g["trial"] is True and g["prereqStatus"] == "初稿，未审定"
    assert g["data"]["students"] == 12 and g["data"]["evidence"] > 100 and g["data"]["predictions"] > 50
    assert g["data"]["posteriors"] > 100 and g["data"]["updatedAt"]
    assert g["weekly"] and all(0 <= w["simpleAccuracy"] <= 1 and 0 <= w["graphAccuracy"] <= 1 for w in g["weekly"])
    assert g["edges"] and {"parent", "child", "n", "withParent", "withoutParent", "doubtful"} <= set(g["edges"][0])
    assert all(abs(x["v2"] - x["graph"]) >= config.GRAPH["big_gap"] - 0.006 for x in g["gaps"])
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
    # 同步全部自动：没有“立即同步”这个手动入口
    assert client.post(f"/api/teacher/sources/{src['id']}/sync").status_code in (404, 405)


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


def test_graph_prior_and_oj_failures_do_not_overstate_mastery(database):
    skill = "C06.03.02"
    parents = ("C02.04.02", "C06.03.03")
    supported_parents = {code: (20.0, 0.0) for code in parents}
    no_local_evidence = graphmodel.infer({**supported_parents, skill: (0.0, 0.0)})
    assert 0.65 < no_local_evidence[skill] < 0.75

    with db.session_scope() as s:
        at = db.now()
        for code in parents:
            mastery.add_evidence(s, UID, code, "pass_first_try", True, 20, f"fixture:{code}", at)
        mastery.add_evidence(s, UID, skill, "pass_first_try", True, 1.0, "pass:1:10", at,
                             independent=True, reliability=0.4)
        for sid in (11, 12):
            sub = m.Submission(server_id=1, submission_id=sid, user_id=UID, problem_id=1,
                               submit_time=at, judgement="WA")
            s.add(sub)
            assert mastery.record_failed_submission(s, sub, skill)
        assert mastery.skill_states(s, UID, at=at, model="v1")[skill].f == 0
        weights = graphmodel.evidence_of(s, UID, at)
        assert weights[skill] == (0.4, 0.75)
        assert graphmodel.infer(weights)[skill] < no_local_evidence[skill]


def test_graph_looks_for_prerequisites_with_direct_evidence_after_repeated_failures():
    skill, parent = "C06.03.02", "C02.04.02"
    state = mastery.SkillState(skill, f=0.65, unresolved_submissions=2)
    assert graphmodel.root_causes({skill: state}, {skill: 0.32}) == []
    assert graphmodel.root_causes({skill: state}, {skill: 0.32, parent: 0.65}) == []
    assert graphmodel.root_causes({skill: state}, {skill: 0.32, parent: 0.27}) == [
        {"skill": skill, "root": parent, "p": 0.27}]


def test_graph_refresh_uses_current_version_and_keeps_old_predictions(database):
    skill = "C01.02.01"
    with db.session_scope() as s:
        mastery.add_evidence(s, UID, skill, "pass_first_try", True, 1.0, "fixture:printf")
        s.add(m.GraphPosterior(user_id=UID, skill_code=skill, p=0.99, has_evidence=True, model_version="old"))
        for model_version in ("old", config.GRAPH_VERSION):
            s.add(m.PredictionLog(user_id=UID, skill_code=skill, source_key=f"fixture:{model_version}", ts=db.now(),
                                  outcome=True, p_simple=0.6, p_v2=0.7, p_graph=0.99, model_version=model_version))
        assert graphmodel.current_posteriors(s, UID) == {}
    assert graphmodel.refresh_old_posteriors() == 1
    assert graphmodel.refresh_old_posteriors() == 0
    with db.session_scope() as s:
        rows = graphmodel.current_posteriors(s, UID)
        assert skill in rows and rows[skill].p < 0.99 and rows[skill].has_evidence
        assert graphmodel.weekly_accuracy(s, [UID], version=config.GRAPH_VERSION)[0]["n"] == 1
        assert graphmodel.weekly_accuracy(s, [UID])[0]["n"] == 2


def test_prediction_log_is_idempotent(demo):
    with db.session_scope() as s:
        before = len(s.scalars(select(m.PredictionLog)).all())
        assert before > 50 and graphmodel.log_predictions(s, UID) == 0
        rows = s.scalars(select(m.PredictionLog).where(m.PredictionLog.user_id == UID)).all()
        assert len(rows) == 12 and sum(r.outcome for r in rows) == 3   # 12 道题里 3 道一次通过
        assert all(0 <= r.p_simple <= 1 and 0 <= r.p_graph <= 1 for r in rows)


def test_graph_page_preserves_historical_predictions_and_shows_repeated_failures(client, demo):
    sid = demo["server_id"]
    now = db.now()
    skill = "C01.01.01"   # 无先修技能，失败记录也应该显示
    with db.session_scope() as s:
        for row in s.scalars(select(m.PredictionLog)):
            row.model_version = "2026-11-17a"
            row.ts = now - dt.timedelta(weeks=8)
        for submission_id in (900_001, 900_002):
            sub = m.Submission(server_id=sid, submission_id=submission_id, user_id=UID, problem_id=900,
                               submit_time=now, judgement="WA")
            s.add(sub)
            assert mastery.record_failed_submission(s, sub, skill)

    login(client, "demo-teacher")
    url = f"/api/teacher/classes/c{sid}-31/graph"
    before = client.get(url).json()
    assert before["weekly"] == []
    archive, = before["predictionHistory"]
    assert archive["version"] == "2026-11-17a" and archive["legacyGraph"]
    assert sum(w["n"] for w in archive["weekly"]) == before["data"]["predictions"] > 50
    row = next(r for r in before["failureRows"] if r["userId"] == UID and r["skill"]["code"] == skill)
    assert row["failures"] >= 2 and row["root"] is None

    with db.session_scope() as s:
        for code in (skill, "D02.01.01"):
            s.add(m.PredictionLog(user_id=UID, skill_code=code, source_key=f"fixture:new:{code}", ts=now,
                                  outcome=True, p_simple=0.6, p_v2=0.7, p_graph=0.8, model_version=config.GRAPH_VERSION))
    after = client.get(url).json()
    assert after["data"]["predictions"] == before["data"]["predictions"] + 1
    week, = after["weekly"]
    assert week["n"] == 1 and week["graphBrier"] == 0.04
    assert after["predictionHistory"] == before["predictionHistory"]


# ------------------------------------------------------------ 概况、知识图谱
def test_overview_kpis_trend_attention(client, demo):
    login(client, "demo-teacher")
    ov = client.get(f"/api/teacher/classes/c{demo['server_id']}-31/overview").json()
    k = ov["kpis"]
    assert k["students"] == 12 and 0 <= k["active"] <= 12 and "avgToPassDelta" in k and "review" in k
    assert "firstTryRate" not in k                      # 不再把“第一次就通过的比例”当成主要指标
    assert len(ov["trend"]) == 8 and ov["trend"][-1]["label"] == "本周"
    assert sum(w["submissions"] for w in ov["trend"]) > 100
    names = {a["name"]: a["reasons"] for a in ov["attention"]}
    assert all(r for r in names.values())


def test_knowledge_map_of_c_class_has_no_data_structures(client, demo):
    """C 语言的班只有 C 语言这张图：没有数据结构的知识点、没有跨课程的边。"""
    login(client, "demo-teacher")
    key = f"c{demo['server_id']}-31"
    g = client.get(f"/api/teacher/classes/{key}/knowledge-map").json()
    assert {n["course"] for n in g["nodes"]} == {"C"} and [c["code"] for c in g["courses"]] == ["C"]
    assert g["edges"] and not any(e["cross"] for e in g["edges"])
    assert all(t["course"] == "C" for t in g["topics"])
    node = {n["code"]: n for n in g["nodes"]}
    assert node["C06.03"]["covered"] and sum(node["C06.03"]["dist"]) == 12 and node["C06.03"]["mean"] is not None
    one = client.get(f"/api/teacher/classes/{key}/knowledge-map", params={"student": UID}).json()
    me = {n["code"]: n for n in one["nodes"]}
    assert me["C06.03"]["student"]["level"] in ("基本掌握", "待加强")
    assert client.get(f"/api/teacher/classes/{key}/knowledge-map", params={"student": "2500000101"}).status_code == 404
    d = client.get(f"/api/teacher/classes/{key}/knowledge-map/C06.03").json()
    assert d["name"] == "最值、求和、统计" and {p["title"] for p in d["problems"]} >= {"成绩最高分", "最大差值"}
    assert not any(x["cross"] for x in client.get(f"/api/teacher/classes/{key}/knowledge-map/C10.01").json()["next"])
    assert client.get(f"/api/teacher/classes/{key}/knowledge-map/D02.02").status_code == 404
    detail = client.get(f"/api/teacher/students/{UID}").json()
    assert detail["readiness"] == [] and detail["foundation"] == []
    assert all(k["code"].startswith("C") for k in detail["knowledge"])
    assert "数据结构" not in json.dumps(detail, ensure_ascii=False)


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


def test_research_export_keeps_student_ids(client, demo):
    """研究用的几类记录也带学号和姓名（老师要和期末考试系统按学号比对），不做化名；
    卡点、求助回合、理解评估、独立作答的字段和指标版本都在。"""
    login(client, "demo-teacher")
    body = {"ids": UID, "dateFrom": "2026-06-01", "dateTo": "2027-05-31", "format": "json",
            "kinds": ["submission", "ask", "quiz", "review", "round", "stuck", "help", "assess"], "purpose": "论文数据分析",
            "deidentify": True}   # 以前的“去标识”参数：现在不认，照样带学号
    req = client.post("/api/teacher/logs", json=body).json()["request"]
    assert "deidentified" not in req
    js = client.get(f"/api/teacher/logs/{req['id']}/download", params={"token": req["token"]}).json()
    assert js["request"]["students"] == [UID]
    assert {r["user_id"] for r in js["rows"]} == {UID} and {r["name"] for r in js["rows"]} == {"林晓"}
    kinds = {r["kind"] for r in js["rows"]}
    assert {"卡点", "求助回合", "理解评估", "在 XDOJ 提交"} <= kinds
    assert any(r["submission_id"] for r in js["rows"] if r["kind"] == "在 XDOJ 提交")
    stuck = next(r for r in js["rows"] if r["kind"] == "卡点")["data"]
    assert stuck["version"] == "stuck-kp-1" and stuck["kp"] == stuck["skill"][:6] and "passed" in stuck
    rnd = next(r for r in js["rows"] if r["kind"] == "求助回合")["data"]
    assert {"maxHelp", "askedBack", "failedReplies", "studentMessages", "minutes", "endReason"} <= set(rnd)
    quiz = next(r for r in js["rows"] if r["kind"] in ("小题", "复习"))["data"]
    assert {"protocol", "judgedHasBug", "firstLines", "locateHit", "firstFixPass", "firstFixClean", "hints",
            "sampleRuns", "events"} <= set(quiz)
    assert js["request"]["versions"]["stuck"] == "stuck-kp-1" and js["request"]["versions"]["helpNext"] == "help-next-1"
    helped = next(r for r in js["rows"] if r["kind"].startswith("帮助之后"))["data"]
    assert {"level", "helpsBefore", "nextSubmission", "minutes", "nextPass", "errorGone", "passed", "triesToPass"} <= set(helped)
    again = client.post("/api/teacher/logs", json={**body, "format": "csv"}).json()["request"]
    text = client.get(f"/api/teacher/logs/{again['id']}/download", params={"token": again["token"]}).content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("学号,姓名,时间,类型,交互") and UID in text and "林晓" in text


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
    assert r.status_code == 401 and "不对" in r.json()["error"]          # 不在名单里，XDOJ 也登不上
    fx.student_passwords["2599000099"] = "new-pw"
    r = client.post("/api/auth/login", json={"loginName": "2599000099", "password": "new-pw"})
    assert r.status_code == 401 and "名单" in r.json()["error"]          # XDOJ 上有、名单还没同步到
    assert client.post("/api/auth/login", json={"loginName": "2599000001", "password": "wrong"}).status_code == 401
    # 演示口令不能冒充真实名单里的学生
    assert client.post("/api/auth/login", json={"loginName": "2599000001", "password": "test-pass"}).status_code == 401
    r = client.post("/api/auth/login", json={"loginName": "2599000001", "password": "stu-xdoj-pw"})
    assert r.status_code == 200 and r.json() == {"role": "student", "name": "测试生", "course": "C"}
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


def test_one_entry_for_teachers_and_students(client, monkeypatch):
    """登录页只有一个入口（学号 / 工号）：不在名单里的账号去这门课的 XDOJ 试登，XDOJ 说是教师就进教师端。"""
    fx = FakeXdoj(teacher_id="t-zhao", password="t-pw")
    fx.teacher_name = "赵老师"
    monkeypatch.setattr(teacher_api, "_test_transport", fx.transport())
    with db.session_scope() as s:
        sources.ensure_server(s, "c-test", "C 语言（测试用的假服务器）", "http://fake.test/xdoj-ssm")
    assert client.post("/api/auth/login", json={"loginName": "t-zhao", "password": "nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"loginName": "t-zhao", "password": "t-pw", "course": "C"})
    assert r.status_code == 200 and r.json() == {"role": "teacher", "name": "赵老师", "course": "C"}
    me = client.get("/api/auth/me").json()
    assert me["role"] == "teacher" and me["course"] == "C"
    with db.session_scope() as s:
        src = s.scalar(select(m.DataSource).where(m.DataSource.teacher_user_id == "t-zhao"))
        assert src.enabled and crypto.decrypt_secret(src.password_enc) == "t-pw"   # 后台同步要用，加密保存
        assert s.scalar(select(m.User).where(m.User.login_name.like("%:t-zhao"))).role == "teacher"
    # 第二次登录还是同一个账号，不重复建
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"loginName": "t-zhao", "password": "t-pw"}).status_code == 200
    with db.session_scope() as s:
        assert len(s.scalars(select(m.User).where(m.User.role == "teacher", m.User.login_name.like("%t-zhao"))).all()) == 1
    # 数据结构还没接入：同一个工号登录数据结构，不去 C 语言那台试
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"loginName": "t-zhao", "password": "t-pw", "course": "D"})
    assert r.status_code == 401


def test_all_courses_always_listed(client):
    """首页一直有 C 语言和数据结构两个选项；数据结构没接入时标成还不能登录。"""
    info = client.get("/api/auth/info").json()
    assert [(c["code"], c["name"]) for c in info["allCourses"]] == [("C", "C 语言程序设计"), ("D", "数据结构")]
    avail = {c["code"]: c["available"] for c in info["allCourses"]}
    assert avail == {"C": True, "D": False}
    with db.session_scope() as s:
        sources.ensure_server(s, "ds", "数据结构", "http://10.1.2.3/xdoj-ssm", "D")   # .env 填上 XDOJ_DS_URL 以后
    info = client.get("/api/auth/info").json()
    d = next(c for c in info["allCourses"] if c["code"] == "D")
    assert d["available"] and d["server"] == "ds"


def _two_courses(monkeypatch):
    """两台假的 XDOJ：C 语言（c.test）和数据结构（ds.test），同一位老师在两边的账号密码不同。"""
    import httpx

    from app import accounts

    c_fx = FakeXdoj(teacher_id="t-zhang", password="c-pass")
    ds_fx = FakeXdoj(teacher_id="zhang-ds", password="ds-pass")
    for fx in (c_fx, ds_fx):
        fx.student_passwords["2599000003"] = "both-pw"
    c_fx.student_passwords["2599000004"] = "c-only-pw"
    hosts = {"c.test": c_fx, "ds.test": ds_fx}
    monkeypatch.setattr(teacher_api, "_test_transport", httpx.MockTransport(lambda req: hosts[req.url.host].handle(req)))
    with db.session_scope() as s:
        c_srv = sources.ensure_server(s, "c-test", "C 语言（测试）", "http://c.test/xdoj-ssm")
        d_srv = sources.ensure_server(s, "ds-test", "数据结构（测试）", "http://ds.test/xdoj-ssm", "D")
        for uid, name in (("2599000003", "两门都上"), ("2599000004", "只上C")):
            s.add(m.Student(user_id=uid, name=name))
            s.flush()
            accounts.ensure_student_user(s, uid, name)
        for srv, cid, title, pid, ptitle in ((c_srv, 501, "C 语言第 1 次作业", 9001, "求最大值"),
                                             (d_srv, 601, "数据结构第 1 次作业", 9101, "单链表逆置")):
            s.add(m.XClass(server_id=srv.id, class_id=cid, class_name=f"{srv.name}一班"))
            s.add(m.Contest(server_id=srv.id, contest_id=cid, title=title))
            s.add(m.ContestClass(server_id=srv.id, contest_id=cid, class_id=cid))
            s.add(m.Problem(server_id=srv.id, problem_id=pid, title=ptitle))
            s.add(m.ContestProblem(server_id=srv.id, contest_id=cid, problem_id=pid, seq=1))
            s.add(m.Enrollment(user_id="2599000003", server_id=srv.id, class_id=cid))
        s.add(m.Enrollment(user_id="2599000004", server_id=c_srv.id, class_id=501))
        return c_srv.id, d_srv.id


def _own_class(server_id: int, class_id: int) -> None:
    """名单同步的结果：这个班挂到登录的这位老师在这台服务器上的数据源下。"""
    with db.session_scope() as s:
        src = s.scalar(select(m.DataSource).where(m.DataSource.server_id == server_id))
        if s.get(m.ClassSource, (server_id, class_id, src.id)) is None:
            s.add(m.ClassSource(data_source_id=src.id, server_id=server_id, class_id=class_id))


def test_courses_are_logged_in_and_shown_separately(client, monkeypatch):
    """C 语言和数据结构分开登录：各看各的，C 语言里不出现数据结构；数据结构里没有 C 语言的答题记录。"""
    c_id, d_id = _two_courses(monkeypatch)
    info = client.get("/api/auth/info").json()
    assert [c["code"] for c in info["courses"]] == ["C", "D"]

    # 老师：用 C 语言的账号登录，只有 C 语言
    r = client.post("/api/teacher/login", json={"server": "c-test", "userId": "t-zhang", "password": "c-pass"})
    assert r.status_code == 200 and r.json()["course"] == "C"
    _own_class(c_id, 501)
    assert client.get("/api/auth/me").json()["course"] == "C"
    classes = client.get("/api/teacher/classes").json()
    assert [c["name"] for c in classes["classes"]] == ["C 语言（测试）一班"] and classes["course"] == "C"
    srcs = client.get("/api/teacher/sources").json()
    assert [x["serverCode"] for x in srcs["sources"]] == ["c-test"] and all(k["course"] == "C" for k in srcs["known"])
    # 关联另一门课的入口没有了
    assert client.post("/api/teacher/sources/link", json={"server": "ds-test", "userId": "zhang-ds",
                                                           "password": "ds-pass"}).status_code in (404, 405)

    # 用数据结构的账号登录（密码不同）：只有数据结构
    client.post("/api/auth/logout")
    bad = {"server": "ds-test", "userId": "zhang-ds", "password": "c-pass"}
    assert client.post("/api/teacher/login", json=bad).status_code == 401
    r = client.post("/api/teacher/login", json={**bad, "password": "ds-pass"})
    assert r.status_code == 200 and r.json()["course"] == "D"
    _own_class(d_id, 601)
    classes = client.get("/api/teacher/classes").json()["classes"]
    assert [c["name"] for c in classes] == ["数据结构（测试）一班"]
    assert [x["serverCode"] for x in client.get("/api/teacher/sources").json()["sources"]] == ["ds-test"]
    assert client.get(f"/api/teacher/classes/c{c_id}-501/overview").status_code == 404
    g = client.get(f"/api/teacher/classes/c{d_id}-601/knowledge-map").json()
    assert {c["code"] for c in g["courses"]} == {"C", "D"} and any(e["cross"] for e in g["edges"])
    node = {n["code"]: n for n in g["nodes"]}
    assert node["C10.01"]["foundation"] and not node["D02.02"]["foundation"]
    c_node = client.get(f"/api/teacher/classes/c{d_id}-601/knowledge-map/C10.01").json()
    assert c_node["foundation"] and c_node["errors"] == [] and c_node["problems"] == []   # 只有掌握程度
    detail = client.get("/api/teacher/students/2599000003").json()
    assert detail["course"] == "D" and detail["foundation"] and detail["submissions"] == []
    assert all(k["code"].startswith("D") for k in detail["knowledge"])
    assert client.get("/api/teacher/students/2599000004").status_code == 404   # 只上 C 的学生不在数据结构的班里

    # 学生：两门都上的，选哪门进哪门
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"loginName": "2599000003", "password": "both-pw", "course": "D"})
    assert r.status_code == 200 and r.json()["course"] == "D"
    assert [h["title"] for h in client.get("/api/homeworks").json()["homeworks"]] == ["数据结构第 1 次作业"]
    assert client.get(f"/api/problems/p{c_id}-9001").status_code == 404     # C 语言的题在数据结构里打不开
    assert client.get(f"/api/problems/p{d_id}-9101").status_code == 200
    assert client.post("/api/threads/general/messages", json={"text": "链表逆置要注意什么？"}).status_code == 200
    assert client.get("/api/threads/general").json()["messages"]
    prog = client.get("/api/progress").json()
    assert prog["knowledge"] and "C 语言" not in json.dumps(prog["knowledge"], ensure_ascii=False)

    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"loginName": "2599000003", "password": "both-pw", "course": "C"})
    assert r.status_code == 200 and r.json()["course"] == "C"
    assert [h["title"] for h in client.get("/api/homeworks").json()["homeworks"]] == ["C 语言第 1 次作业"]
    assert client.get(f"/api/problems/p{d_id}-9101").status_code == 404
    assert client.get("/api/threads/general").json()["messages"] == []       # 综合对话两门课各一个
    page = json.dumps([client.get(p).json() for p in ("/api/progress", "/api/review", "/api/status", "/api/homeworks")],
                      ensure_ascii=False)
    assert "数据结构" not in page and "链表" not in page

    # 只上 C 的学生选了数据结构：说清楚是选错了课程
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"loginName": "2599000004", "password": "c-only-pw", "course": "D"})
    assert r.status_code == 401 and "选错了课程" in r.json()["error"]
    assert client.post("/api/auth/login", json={"loginName": "2599000004", "password": "c-only-pw",
                                                "course": "X"}).status_code == 400


def test_old_session_tokens_need_login_again(client):
    """旧格式（不带课程）的登录凭证不再认，重新登录一次就好。"""
    import hashlib
    import hmac
    import time

    body = f"1.{int(time.time()) + 3600}"
    sig = hmac.new(crypto._derive("session"), body.encode("ascii"), hashlib.sha256).hexdigest()
    client.cookies.set("pda_session", f"{body}.{sig}")
    assert client.get("/api/auth/me").status_code == 401
    assert crypto.verify_session(crypto.sign_session(5, "D")) == (5, "D")
    assert crypto.verify_session(crypto.sign_session(5, "C")[:-1] + "x") is None


def test_version_tells_pages_when_to_refetch(client, demo):
    """教师端页面定时只问 /version：数据没变就不重取；有了新数据 data 变；班级、名单没变时 classes 不变。"""
    from app.chat import workflow

    login(client, "demo-teacher")
    v1 = client.get("/api/teacher/version").json()
    assert set(v1) == {"data", "classes"} and client.get("/api/teacher/version").json() == v1
    with db.session_scope() as s:   # 学生在对话里卡住了：有新数据
        workflow.handle(s, UID, (demo["server_id"], 406), action="hint")
    v2 = client.get("/api/teacher/version").json()
    assert v2["data"] != v1["data"] and v2["classes"] == v1["classes"]
    with db.session_scope() as s:   # 名单变了：班级列表要重取
        s.add(m.Student(user_id="2599999999", name="新同学"))
        s.flush()
        s.add(m.Enrollment(user_id="2599999999", server_id=demo["server_id"], class_id=31))
    assert client.get("/api/teacher/version").json()["classes"] != v2["classes"]
    client.post("/api/auth/logout")
    login(client, UID)
    assert client.get("/api/teacher/version").status_code == 403
