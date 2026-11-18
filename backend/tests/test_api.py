"""学生端接口：能用、不越权、不泄露。"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db
from app import models as m
from app.web.app import create_app

UID = "2500000001"
OTHER = "2500000003"


@pytest.fixture()
def client(demo):
    with TestClient(create_app()) as c:
        yield c


def login(c: TestClient, name: str = UID, password: str = "test-pass"):
    return c.post("/api/auth/login", json={"loginName": name, "password": password})


def test_login_required_and_wrong_password(client):
    for path in ("/api/homeworks", "/api/progress", "/api/threads/general", "/api/problems/p1-406"):
        assert client.get(path).status_code == 401
    assert login(client, password="nope").status_code == 401
    assert login(client, name="9999999999").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.json() == {"role": "student", "name": "林晓", "course": "C"}
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["loginName"] == UID
    info = client.get("/api/auth/info").json()
    assert "password" not in json.dumps(info).lower() and "test-pass" not in json.dumps(info)
    client.post("/api/auth/logout")
    assert client.get("/api/homeworks").status_code == 401


def test_login_throttle(client):
    for _ in range(5):
        assert login(client, name="2500000009", password="x").status_code == 401
    assert login(client, name="2500000009", password="x").status_code == 429


def test_homeworks_only_for_my_class(client, demo):
    login(client)
    hws = client.get("/api/homeworks").json()["homeworks"]
    assert [h["title"] for h in hws] == ["上机练习：循环结构", "上机练习：一维数组", "上机练习：选择结构"]
    assert [h["ended"] for h in hws] == [False, False, True]          # 已过截止的放在后面
    assert (hws[1]["passed"], hws[1]["total"]) == (3, 7)
    sid = demo["server_id"]
    detail = client.get(f"/api/homeworks/h{sid}-501").json()
    rows = {p["title"]: p for p in detail["problems"]}
    assert list(rows) == ["成绩最高分", "最低气温", "数组逆序", "统计字母", "最大差值", "高于平均分的人数", "查找元素"]
    last = rows["成绩最高分"]["last"]
    assert (last["attemptNo"], last["resultText"], last["grid"]) == (3, "答案错误", [True, True, False, True, True])
    assert rows["统计字母"]["last"] is None and rows["统计字母"]["attempts"] == 0
    assert rows["数组逆序"]["last"]["resultText"] == "运行错误" and rows["数组逆序"]["last"]["passed"] == 2
    # 别的班的作业、别的班的题、没布置的题：一律看不到
    assert client.get(f"/api/homeworks/h{sid}-504").status_code == 404
    for pid in (601, 999, 12345):
        assert client.get(f"/api/problems/p{sid}-{pid}").status_code == 404
        assert client.get(f"/api/threads/p{sid}-{pid}").status_code == 404
        assert client.post(f"/api/threads/p{sid}-{pid}/messages", json={"text": "怎么做"}).status_code == 404


def test_problem_detail_and_compile_error_has_no_grid(client, demo):
    login(client)
    sid = demo["server_id"]
    d = client.get(f"/api/problems/p{sid}-406").json()
    assert d["title"] == "成绩最高分" and d["brief"] == "输入 n 个学生的成绩，输出最高分"
    subs = d["submissions"]
    assert [(x["attemptNo"], x["resultText"], x["passed"]) for x in subs] == [
        (3, "答案错误", 4), (2, "答案错误", 3), (1, "编译错误", None)]
    assert subs[2]["grid"] is None        # 编译错误不显示格子
    assert [k["name"] for k in d["knowledge"]][0] == "最值、求和、统计"
    assert all(set(k) == {"name", "level", "segments"} for k in d["knowledge"])


def test_cannot_see_other_students_data(client, demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        theirs = s.scalars(select(m.Submission).where(m.Submission.user_id == OTHER)).first()
        mine = s.scalars(select(m.Submission).where(m.Submission.user_id == UID, m.Submission.problem_id == 406)
                         .order_by(m.Submission.submission_id)).all()
        their_key = f"s{sid}-{theirs.submission_id}"
        my_keys = [f"s{sid}-{x.submission_id}" for x in mine]
        their_attempt = None
    login(client)
    assert client.get(f"/api/submissions/{their_key}/source").status_code == 404
    assert client.get(f"/api/problems/p{sid}-406/diff", params={"a": my_keys[1], "b": their_key}).status_code == 404
    assert client.post(f"/api/threads/p{sid}-406/messages",
                       json={"text": "为什么错", "submissionKey": their_key}).status_code == 404
    # 自己的可以看
    src = client.get(f"/api/submissions/{my_keys[2]}/source").json()
    assert src["lines"][9] == "    int max = 0;"
    diff = client.get(f"/api/problems/p{sid}-406/diff", params={"a": my_keys[1], "b": my_keys[2]}).json()
    changed = [r for r in diff["rows"] if r["type"] != "same"]
    assert len(changed) == 1 and changed[0]["left"]["text"].strip() == "for (i = 1; i < n; i++)"
    assert changed[0]["right"]["text"].strip() == "for (i = 0; i < n; i++)" and diff["changed"] == 1
    # 别人登录后看不到我的对话，也动不了我的小题
    with db.session_scope() as s:
        my_attempt = s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.user_id == UID)).first().id
    client.post("/api/auth/logout")
    login(client, name=OTHER)
    assert client.get(f"/api/threads/p{sid}-406").json()["messages"] == []
    assert client.get(f"/api/submissions/{my_keys[2]}/source").status_code == 404
    assert client.post(f"/api/quiz/{my_attempt}/skip").status_code == 404
    del their_attempt


def test_chat_roundtrip_and_buttons(client, demo):
    login(client)
    sid = demo["server_id"]
    th = client.get(f"/api/threads/p{sid}-406").json()["messages"]
    # 第一条是智能体主动发的：第 3 次又犯了以前在「最大差值」里改正过的错，摘出当时改对的代码
    assert [x["role"] for x in th] == ["agent", "student", "agent", "student", "agent"]
    first = th[0]["blocks"]
    assert "最大差值" in first[0]["text"]
    code = next(b for b in first if b["t"] == "code")
    assert code["caption"].startswith("你在「最大差值」里改对后的代码") and code["mark"]
    assert any("a[0]" in line for line in code["lines"])
    assert th[2]["actions"] == []   # 按钮只在最新一条回复下面
    assert th[4]["actions"] == ["think", "hint", "tell"]
    assert set(th[4]) == {"id", "role", "kind", "time", "blocks", "actions"}
    out = client.post(f"/api/threads/p{sid}-406/messages", json={"action": "think"}).json()["messages"]
    assert out[-2]["blocks"][0]["text"] == "我想自己想想" and out[-1]["actions"] == ["hint", "tell"]
    out = client.post(f"/api/threads/p{sid}-406/messages", json={"text": "那我把初值改成 a[0] 是不是就行了？\nint max = a[0];\nfor (i = 1; i < n; i++)\n    if (a[i] > max) max = a[i];"}).json()["messages"]
    assert out[-2]["blocks"][-1]["t"] == "code" and out[-2]["blocks"][-1]["caption"] == "我贴的代码"
    assert client.post(f"/api/threads/p{sid}-406/messages", json={"text": "  "}).status_code == 400
    assert client.post("/api/events", json={"kind": "copy_code", "target": "x"}).status_code == 200
    assert client.post("/api/events", json={"kind": "whatever"}).status_code == 400
    with db.session_scope() as s:
        kinds = [e.kind for e in s.scalars(select(m.UiEvent)).all()]
        assert kinds == ["login", "help_think", "copy_code"]
        assert s.scalars(select(m.Trigger).where(m.Trigger.kind == "student_active")).first() is not None


def test_ask_why_from_submission(client, demo):
    login(client)
    sid = demo["server_id"]
    subs = client.get(f"/api/problems/p{sid}-408").json()["submissions"]
    out = client.post(f"/api/threads/p{sid}-408/messages",
                      json={"text": "这次提交为什么错了？", "submissionKey": subs[0]["key"]}).json()["messages"]
    code = [b for b in out[-1]["blocks"] if b["t"] == "code"][0]
    assert code["caption"].startswith("你的第 2 次提交") and out[-1]["actions"] == ["think", "hint", "tell"]


def test_review_flow_over_api(client, demo):
    login(client)
    page = client.get("/api/review").json()
    assert [d["title"] for d in page["due"]] == ["整型与取值范围"] and page["due"][0]["dueNow"]
    due = page["due"][0]
    assert due["why"] == "卡住过" and due["origin"]["title"] == "阶乘求和" and due["memory"]["segments"] == 1
    assert "%" not in json.dumps(page, ensure_ascii=False)  # 学生端不给概率
    assert "%" not in json.dumps(client.get("/api/progress").json()["next"], ensure_ascii=False)
    assert "C02" not in str(page) and "skill" not in str(page).lower()
    up = [u["title"] for u in page["upcoming"]]
    assert "最值、求和、统计" in up
    r = client.post(f"/api/review/{due['id']}/start")
    assert r.status_code == 200
    body = r.json()
    q = body["quiz"]
    assert body["lead"] and q["state"] == "confidence"
    assert set(q) >= {"attemptId", "stem", "caption", "code", "state", "startLine"}
    assert client.post(f"/api/review/{due['id']}/start").json()["quiz"]["attemptId"] == q["attemptId"]  # 不重复出题
    aid = q["attemptId"]
    assert client.post(f"/api/quiz/{aid}/judge", json={"hasBug": True}).json()["state"] == "confidence"
    q = client.post(f"/api/quiz/{aid}/confidence", json={"value": "很有把握", "elapsedMs": 2100}).json()
    assert q["state"] == "judge"
    with db.session_scope() as s:
        item = s.get(m.QuizItem, s.get(m.QuizAttempt, aid).item_id)
        has_bug, fix, owner = item.has_bug, item.standard_fix, item.owner_user_id
        assert owner in (UID, None) and item.verified   # 他没通过过用到这个技能的题：退回题库
    q = client.post(f"/api/quiz/{aid}/judge", json={"hasBug": has_bug}).json()
    if q["state"] == "edit":
        q = client.post(f"/api/quiz/{aid}/run", json={"code": fix}).json()
    assert q["state"] == "done" and q["result"]["outcome"] == "right"
    with db.session_scope() as s:
        it = s.get(m.ReviewItem, due["id"])
        assert it.streak == 1 and it.half_life > 2.5 and it.due_at > db.now() and it.attempt_id is None
    page = client.get("/api/review").json()
    assert all(d["id"] != due["id"] for d in page["due"])
    assert client.post(f"/api/review/{due['id']}/start").status_code == 409   # 今天这一项已经做过了


def test_progress_page(client):
    login(client)
    p = client.get("/api/progress").json()
    assert set(p) == {"summary", "knowledge", "errors", "activity", "next", "foundation"} and p["foundation"] == []
    names = [i["name"] for g in p["knowledge"] for i in g["items"]]
    assert "最值、求和、统计" in names and len(names) == len(set(names)) and len(names) == 65
    assert all(not n.startswith("D") for n in names) and "单链表" not in names   # 跨课程的内容学生看不到
    for g in p["knowledge"]:
        for i in g["items"]:
            assert set(i) == {"name", "level", "segments", "basis", "practice", "covered", "dims", "insight"}
            assert i["level"] in ("已掌握", "基本掌握", "待加强", "还没练到")
    covered = [i for g in p["knowledge"] for i in g["items"] if i["covered"]]
    assert 15 < len(covered) < 65
    errs = {e["name"]: e for e in p["errors"]}
    assert 3 <= len(p["errors"]) <= 5
    assert errs["变量没给合适的初值就使用"]["status"] == "改正后又出现" and errs["变量没给合适的初值就使用"]["count"] == 3
    assert errs["缺分号或括号、花括号不匹配"]["status"] == "已改正"
    assert errs["数组开小了"]["status"] == "还没解决"
    act = p["activity"]
    assert (act["tried"], act["passed"]) == (12, 9) and 1 < act["avgToPass"] < 22 / 9 + 1
    assert "firstTryRate" not in act and "firstTryRate" not in p["summary"]
    assert len(act["weeks"]) == 8 and act["weeks"][-1]["label"] == "本周"
    summ = p["summary"]
    assert (summ["passed"], summ["total"], summ["reviewDue"]) == (9, 14, 1)
    assert 2 <= len(p["next"]) <= 3 and p["next"][0]["kind"] == "review"
    assert p["next"][1]["problem"]["title"] == "成绩最高分"


def test_student_api_never_leaks(client, demo):
    """把学生端能拿到的东西全拿一遍：不含测试点数据、内部编码、概率、别人的信息。"""
    login(client)
    sid = demo["server_id"]
    bodies = []

    def get(path, **kw):
        r = client.get(path, **kw)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        bodies.append(r.text)
        return r.json()

    get("/api/auth/me")
    get("/api/status")
    get("/api/progress")
    for hw in get("/api/homeworks")["homeworks"]:
        get(f"/api/homeworks/{hw['key']}/submissions")
        for prob in get(f"/api/homeworks/{hw['key']}")["problems"]:
            detail = get(f"/api/problems/{prob['key']}")
            get(f"/api/threads/{prob['key']}")
            keys = [x["key"] for x in detail["submissions"]]
            for k in keys:
                get(f"/api/submissions/{k}/source")
            if len(keys) >= 2:
                get(f"/api/problems/{prob['key']}/diff", params={"a": keys[1], "b": keys[0]})
            if detail["submissions"] and detail["submissions"][0]["result"] != "AC":
                for body in ({"text": "为什么错了？"}, {"action": "hint"}, {"action": "tell"}, {"text": "不会"}):
                    r = client.post(f"/api/threads/{prob['key']}/messages", json=body)
                    assert r.status_code == 200
                    bodies.append(r.text)
    get("/api/threads/general")
    blob = " ".join(json.loads(json.dumps(" ".join(bodies))).split())
    text = " ".join(" ".join(json.dumps(json.loads(b), ensure_ascii=False) for b in bodies).split())

    with db.session_scope() as s:
        # 1 测试点的原始输入、标准输出（学生自己的源码里不会有这些）
        checked = 0
        for tc in s.scalars(select(m.Testcase)).all():
            for raw in (tc.input_bytes, tc.output_bytes):
                for line in raw.decode("utf-8").splitlines():
                    line = " ".join(line.split())
                    if len(line.split()) >= 3:
                        checked += 1
                        assert line not in text, f"泄露了测试点数据：{line[:40]}"
        assert checked > 20
        # 2 测试点编号、运行记录编号
        for tid in s.scalars(select(m.Testcase.testcase_id)).all():
            assert not re.search(rf"\b{tid}\b", text), f"出现了测试点编号 {tid}"
        # 3 程序在测试点上的输出
        for ro in s.scalars(select(m.RunOutput)).all():
            out = " ".join(ro.program_output_bytes.decode("utf-8").split())
            if len(out.split()) >= 3:
                assert out not in text
        # 4 别的同学
        for st in s.scalars(select(m.Student).where(m.Student.user_id != UID)).all():
            assert st.user_id not in text and st.name not in text
    # 5 内部编码、帮助级别、规则编号、概率
    assert not re.search(r"\b[A-Z]{2}\d{2}\b", text), "出现了错误类型编码"
    assert not re.search(r"\bC\d{2}\.\d{2}", text), "出现了知识点或技能编码"
    assert not re.search(r'"(help_?[Ll]evel|rule_?[Ii]d|reveal|willingness|intent|confidence_?score|p|prob|probability|mastery)"\s*:', text)
    assert not re.search(r"\bH[0-3]\b|\bR\d{2}\b", text)
    assert '"flow"' not in text and "evidence" not in text.lower()
    assert "situation" not in text and "testcase" not in text.lower()
    del blob


def test_status_reports_demo(client):
    login(client)
    st = client.get("/api/status").json()
    assert st["xdoj"] == {"ok": True, "dataTime": None, "demo": True} and st["dueReviews"] == 1
