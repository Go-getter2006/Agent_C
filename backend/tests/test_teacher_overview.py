"""班级总览：需要留意的学生分三类、误区（本周 / 全部）、提交记录、先给上次算好的再在后台重算。"""
from __future__ import annotations

import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, profile
from app import models as m
from app.web import cache
from app.web import teacher as teacher_api
from app.web.app import create_app

UID = "2500000001"


@pytest.fixture()
def client(demo):
    with TestClient(create_app()) as c:
        c.post("/api/auth/login", json={"loginName": "demo-teacher", "password": "test-pass"})
        yield c


def _key(demo) -> str:
    return f"c{demo['server_id']}-31"


def test_attention_is_grouped_into_idle_stuck_and_other(client, demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        # 一个从没交过题的学生
        s.add(m.Student(user_id="2599009901", name="新来的"))
        s.flush()
        s.add(m.Enrollment(user_id="2599009901", server_id=sid, class_id=31, course="C"))
        # 一个学生在一道作业题上交了 6 次还没过
        pid = teacher_api.class_problems(s, s.scalar(select(m.XClass).where(m.XClass.server_id == sid, m.XClass.class_id == 31)))[0][1]
        title = s.get(m.Problem, (sid, pid)).title
        for i in range(6):
            s.add(m.Submission(server_id=sid, submission_id=990000 + i, user_id="2500000005", problem_id=pid,
                               judgement="WA", submit_time=db.now() - dt.timedelta(hours=6 - i)))
        s.execute(m.Submission.__table__.delete().where((m.Submission.user_id == "2500000005")
                                                         & (m.Submission.problem_id == pid) & (m.Submission.judgement == "AC")))
    ov = client.get(f"/api/teacher/classes/{_key(demo)}/overview").json()
    groups = {g["key"]: g for g in ov["attentionGroups"]}
    assert [g["key"] for g in ov["attentionGroups"]] == ["idle", "stuck", "other"]
    assert all(g["title"] and g["rule"] for g in groups.values())
    idle = {r["userId"]: r for r in groups["idle"]["students"]}
    assert "还没有交过题" in idle["2599009901"]["reasons"]
    stuck = {r["userId"]: r for r in groups["stuck"]["students"]}
    assert any(f"「{title}」交了" in reason and "还没通过" in reason for reason in stuck["2500000005"]["reasons"])
    # 以前的平铺列表还在（所有理由合在一起）
    flat = {a["userId"]: a for a in ov["attention"]}
    assert "还没有交过题" in flat["2599009901"]["reasons"]


def test_explain_gaps_match_the_full_profile(client, demo):
    """班级页用的“会做但讲不清”和完整画像的 explain_gap 一样（只是少取几样用不上的数据）。"""
    from app import mastery

    with db.session_scope() as s:
        uids = [u for u, in s.execute(select(m.Enrollment.user_id).where(m.Enrollment.class_id == 31)).all()]
        states = {u: {c: v for c, v in mastery.skill_states(s, u).items() if c.startswith("C")} for u in uids}
        fast = profile.explain_gaps(s, states, "C")
        for u in uids:
            full = sorted(kp for kp, d in profile.kp_profile(s, u, "C").items() if "explain_gap" in d.flags)
            assert fast[u] == full, u


def test_misconceptions_this_week_and_all(client, demo):
    ov = client.get(f"/api/teacher/classes/{_key(demo)}/overview").json()
    week, every = ov["misconceptions"]["week"], ov["misconceptions"]["all"]
    assert every["total"] >= week["total"] > 0 and len(every["items"]) <= 10
    top = every["items"][0]
    assert top["students"] >= every["items"][-1]["students"]
    # 最值的初值：演示数据里很多人犯，也有人说出了错误想法
    init = next(x for x in every["items"] if x["skill"] == "C06.03.02")
    assert init["students"] >= 5 and init["submissions"] >= init["students"] - 1
    assert init["problems"] and init["who"] and all(w["name"] for w in init["who"])
    assert 0 <= init["unresolved"] <= init["fromSubmissions"]
    full = client.get(f"/api/teacher/classes/{_key(demo)}/misconceptions", params={"period": "all"}).json()
    assert len(full["items"]) == every["total"] and full["version"]
    assert client.get(f"/api/teacher/classes/{_key(demo)}/misconceptions", params={"period": "year"}).status_code == 400
    assert all(not x["error"] or x["error"] != "OT99" for x in full["items"])


def test_class_submissions_and_detail(client, demo):
    key = _key(demo)
    page = client.get(f"/api/teacher/classes/{key}/submissions").json()
    assert page["total"] >= 100 and len(page["items"]) == page["pageSize"] == 50
    times = [x["time"] for x in page["items"] if x["time"]]
    assert times == sorted(times, reverse=True)                       # 最近的在前
    mine = client.get(f"/api/teacher/classes/{key}/submissions", params={"student": UID, "result": "wrong"}).json()
    assert mine["items"] and all(x["userId"] == UID and x["result"] in ("WA", "PE", "OLE") for x in mine["items"])
    one = next(x for x in mine["items"] if x["labels"])
    d = client.get(f"/api/teacher/submissions/{one['key']}").json()
    assert d["lines"] and d["labels"] and d["cases"]
    # 每组测试点：输入、标准输出、他的输出（没过的才有他的输出；通过的和标准输出一样）；第几组和列表的格子一样
    assert [c["no"] for c in d["cases"]] == list(range(1, len(d["cases"]) + 1))
    assert all({"input", "expected", "output", "passed", "result", "firstDiffLine"} <= set(c) for c in d["cases"])
    bad = [c for c in d["cases"] if c["passed"] is False]
    assert bad and all(c["input"] is not None and c["expected"] is not None for c in bad)
    assert all(c["output"] is None for c in d["cases"] if c["passed"])
    assert client.get(f"/api/teacher/classes/{key}/submissions", params={"student": "9999999999"}).status_code == 404
    # 别的老师看不到
    with db.session_scope() as s:
        src = m.DataSource(server_id=demo["server_id"], teacher_user_id="someone-else", enabled=False)
        s.add(src)
        s.flush()
        s.add(m.User(role="teacher", login_name="other-teacher", display_name="别的老师", data_source_id=src.id))
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"loginName": "other-teacher", "password": "test-pass"})
    assert client.get(f"/api/teacher/submissions/{one['key']}").status_code == 404
    assert client.get(f"/api/teacher/classes/{key}/submissions").status_code == 404


def test_stale_result_first_then_background_refresh(client, demo):
    """数据变了：先给上次算好的（标 refreshing），后台算新的；算好以后再取就是新的。登录后取班级列表时总览已经在算。"""
    cache.clear()
    cache.BACKGROUND = True
    key = _key(demo)
    client.get("/api/teacher/classes")                                     # 先在后台算上总览
    cache.wait_idle()
    t = time.monotonic()
    first = client.get(f"/api/teacher/classes/{key}/overview").json()
    assert "refreshing" not in first and time.monotonic() - t < 1.0     # 已经算好：直接给
    with db.session_scope() as s:
        s.add(m.Submission(server_id=demo["server_id"], submission_id=991234, user_id=UID, problem_id=406,
                           judgement="WA", submit_time=db.now()))
    stale = client.get(f"/api/teacher/classes/{key}/overview").json()
    assert stale["refreshing"] is True and stale["kpis"]["submissions"] == first["kpis"]["submissions"]
    cache.wait_idle()
    fresh = client.get(f"/api/teacher/classes/{key}/overview").json()
    assert "refreshing" not in fresh and fresh["kpis"]["submissions"] == first["kpis"]["submissions"] + 1
    # “数据变没变”的询问一发现变了，最近看过的页面就在后台先算
    with db.session_scope() as s:
        s.add(m.Submission(server_id=demo["server_id"], submission_id=991235, user_id=UID, problem_id=406,
                           judgement="WA", submit_time=db.now()))
    client.get("/api/teacher/version")
    cache.wait_idle()
    after = client.get(f"/api/teacher/classes/{key}/overview").json()
    assert "refreshing" not in after and after["kpis"]["submissions"] == first["kpis"]["submissions"] + 2


# ------------------------------------------------------------ 开发者：几个班一起看、每一页都能导出
def _dev(client):
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"loginName": "dev-test", "password": "dev-pass"})


def test_developer_sees_several_classes_together(client, demo):
    """开发者勾几个班（同一台服务器）当一个班看：学生、题目取并集；不在名下的班不行；单个班照旧。"""
    sid = demo["server_id"]
    one, two, both = f"c{sid}-31", f"c{sid}-32", f"c{sid}-31_32"
    _dev(client)
    a = client.get(f"/api/teacher/classes/{one}/students").json()
    b = client.get(f"/api/teacher/classes/{two}/students").json()
    ab = client.get(f"/api/teacher/classes/{both}/students").json()
    ids = {r["userId"] for r in ab["students"]}
    assert ids == {r["userId"] for r in a["students"]} | {r["userId"] for r in b["students"]}
    assert len(ids) == len(ab["students"])                                    # 两个班都有的人只算一次
    assert ab["class"]["name"] == f'{a["class"]["name"]}、{b["class"]["name"]}'
    assert all(r["className"] in (a["class"]["name"], b["class"]["name"]) for r in ab["students"])
    assert a["students"][0]["className"] is None                              # 单个班不标
    ov = client.get(f"/api/teacher/classes/{both}/overview").json()
    assert ov["class"]["students"] == len(ids)
    for page in ("help", "graph", "rating", "knowledge-map", "misconceptions", "submissions"):
        assert client.get(f"/api/teacher/classes/{both}/{page}").status_code == 200, page
    assert client.get(f"/api/teacher/classes/c{sid}-31_99/overview").status_code == 404   # 有一个不在名下
    # 从几个班一起看的学生列表点进学生明细
    assert client.get(f"/api/teacher/students/{UID}", params={"classKey": both}).status_code == 200


def test_every_page_can_be_exported_by_developer(client, demo):
    key = f"c{demo['server_id']}-31_32"
    assert client.get("/api/teacher/export/students", params={"key": key}).status_code == 403   # 老师不能导
    _dev(client)
    for page in ("overview", "students", "submissions", "misconceptions", "map", "models", "rating", "help"):
        r = client.get(f"/api/teacher/export/{page}", params={"key": key})
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"], page
        js = r.json()
        assert js["data"] and js["class"]["classes"] == [f"c{demo['server_id']}-31", f"c{demo['server_id']}-32"], page
    csv = client.get("/api/teacher/export/students", params={"key": key, "format": "csv"})
    head = csv.content.decode("utf-8-sig").splitlines()[0]
    assert "userId" in head and "className" in head
    subs = client.get("/api/teacher/export/submissions", params={"key": key, "format": "csv"}).content.decode("utf-8-sig")
    assert UID in subs and len(subs.splitlines()) > 100
    assert client.get("/api/teacher/export/models", params={"key": key, "format": "csv"}).status_code == 400
    assert client.get("/api/teacher/export/nothing", params={"key": key}).status_code == 404


def test_help_page_compares_asked_and_silent(client, demo):
    _dev(client)
    d = client.get(f"/api/teacher/classes/c{demo['server_id']}-31/help").json()
    u = d["usage"]
    assert u["students"] >= u["used"] >= u["askedHomework"] >= u["stuck"] >= 0
    av = d["askVsSilent"]
    assert av["version"] == "ask-silent-1" and av["threshold"] == 3
    for g in (av["asked"], av["silent"]):
        assert set(g) == {"cases", "students", "passed", "medianTries"}
        assert g["passed"] is None or g["passed"]["d"] == g["cases"]
    assert av["asked"]["cases"] + av["silent"]["cases"] > 0
    assert all("silentPassed" in r for r in d["rows"])


def test_candidate_errors_are_grouped(client, demo):
    """错误库里没有的错误：叫法不同的同一种错误归在一起，给次数、学生、题目、现在被归到的编码的名字。"""
    sid = demo["server_id"]
    with db.session_scope() as s:
        for i, name in enumerate(["源文件编码与评测环境不一致导致中文输出乱码", "源文件编码不匹配导致中文输出乱码",
                                  "源文件编码与评测环境不一致导致中文乱码", "数组下标从1开始越界"]):
            s.add(m.LabelCandidate(kind="error", name=name, description="d", nearest=["IO04", "IO05"] if i < 3 else ["CE01"],
                                   server_id=sid, submission_id=1000 + i, problem_id=406, user_id=f"25000000{i:02d}"))
    _dev(client)
    groups = client.get("/api/teacher/sources").json()["candidates"]
    assert [g["count"] for g in groups][:2] == [3, 1]
    top = groups[0]
    assert top["students"] == 3 and top["names"] == 3 and top["problems"][0]["count"] == 3
    assert [n["code"] for n in top["nearest"]] == ["IO04", "IO05"] and all(n["name"] for n in top["nearest"])
