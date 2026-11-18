"""跨课程：带类型的联系、数据检验、联合状态（η）、智能体提出联系要老师采用、C 基础预警只在数据结构这边。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import accounts, cross, db, llm, mastery
from app import models as m
from app.web.app import create_app

C_PTR, D_LIST = "C10.01.01", "D02.02.01"     # 指针定义与初始化 → 链表结点（初稿里的一条先修）


def _c_evidence(s, uid: str, skill: str, good: bool, n: int = 3) -> None:
    for i in range(n):
        mastery.add_evidence(s, uid, skill, "pass_first_try" if good else "stuck_again", good, 1.0,
                             f"t:{uid}:{skill}:{i}", db.now(), independent=good)


def _outcomes(s, uid: str, skill: str, results: list[bool]) -> None:
    for i, ok in enumerate(results):
        s.add(m.PredictionLog(user_id=uid, skill_code=skill, source_key=f"first:{uid}:{skill}:{i}", ts=db.now(),
                              outcome=ok, p_simple=0.5, p_v2=0.5, p_graph=0.5))


def test_posterior_comparison():
    assert cross.prob_greater(9, 10, 2, 10) > 0.99
    assert abs(cross.prob_greater(5, 10, 5, 10) - 0.5) < 1e-9
    assert cross.verdict_of(9, 10, 2, 10)[1] == "supported"
    assert cross.verdict_of(3, 10, 7, 10)[1] == "unsupported"
    assert cross.verdict_of(2, 2, 0, 1) == (None, "unknown")        # 样本不够不下结论


def test_seed_validate_and_weights(database):
    with db.session_scope() as s:
        assert cross.ensure_seed(s) == 28 and cross.ensure_seed(s) == 0
        for i in range(8):
            strong = f"s{i}"
            weak = f"w{i}"
            _c_evidence(s, strong, C_PTR, True)
            _c_evidence(s, weak, C_PTR, False)
            _outcomes(s, strong, D_LIST, [True, True])
            _outcomes(s, weak, D_LIST, [False, i % 4 == 0])
        cross.validate_links(s)
        link = s.scalar(select(m.CrossLink).where(m.CrossLink.c_skill == C_PTR, m.CrossLink.d_skill == D_LIST))
        assert link.verdict == "supported" and link.rate_with == 1.0 and link.rate_without < 0.3
        assert link.n_with == 16 and link.n_without == 16 and link.prob_support > 0.9
        other = s.scalar(select(m.CrossLink).where(m.CrossLink.c_skill == "C11.03.01", m.CrossLink.d_skill == D_LIST))
        assert other.verdict == "unknown"                              # C 那头没有数据
        parents, strength = cross.weighted_parents(s)
        w = dict(parents[D_LIST])
        assert w[C_PTR] == 1.0 and w["C11.03.01"] == 0.7               # 数据支持的分量更大
        assert strength[D_LIST] > 1.0

        # 先修扎实的学生：数据结构这头还没做题，也能推出较高的准备度；做了题以后 η 变小
        before = {r["code"]: r for r in cross.joint_state(s, "s0")}["D02.02"]
        assert before["predicted"] and before["pC"] is not None and before["pC"] > 0.6
        for i in range(4):
            mastery.add_evidence(s, "s0", D_LIST, "stuck_again", False, 1.0, f"d:{i}", db.now())
        after = {r["code"]: r for r in cross.joint_state(s, "s0")}["D02.02"]
        assert not after["predicted"] and after["eta"] < 0.5 and after["p"] < after["pC"]

        # 老师不用这条联系：它就不再进先验
        link.status = "rejected"
        s.flush()
        parents, _ = cross.weighted_parents(s)
        assert C_PTR not in dict(parents[D_LIST])


def test_llm_proposals_need_review(database):
    sent = []
    answers = [{"links": [{"c": "C99.99.99", "d": D_LIST, "type": "prerequisite", "why": "瞎编的"}]},
               {"links": [{"c": "C10.03.01", "d": D_LIST, "type": "used_for", "why": "遍历链表要移动指针", "confidence": 0.8},
                          {"c": C_PTR, "d": D_LIST, "type": "prerequisite", "why": "已有的"}]}]

    def backend(messages, json_mode, max_tokens, temperature):
        sent.append(messages[-1]["content"])
        return json.dumps(answers[min(len(sent) - 1, 1)], ensure_ascii=False), 100, 50

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            cross.ensure_seed(s)
            rows = cross.propose_links(s, "D02.02")
            assert [(r.c_skill, r.relation, r.status, r.source) for r in rows] == [("C10.03.01", "used_for", "proposed", "llm")]
            assert "不是 C 语言的技能编码" in sent[-1]           # 编造的编码被退回重写
            parents, _ = cross.weighted_parents(s)
            assert "C10.03.01" not in dict(parents[D_LIST])  # 待审的不进先验
            rows[0].status = "accepted"
            s.flush()
            parents, _ = cross.weighted_parents(s)
            assert abs(dict(parents[D_LIST])["C10.03.01"] - 0.8 * 0.7) < 1e-9   # 被用于 0.8 × 还没检验 0.7
    finally:
        llm.set_backend(None)


def _ds_class(s) -> tuple[int, int]:
    c_srv = m.Server(code="c-t", name="C 语言（测试）", course="C", base_url="")
    d_srv = m.Server(code="ds-t", name="数据结构（测试）", course="D", base_url="")
    s.add_all([c_srv, d_srv])
    s.flush()
    s.add(m.XClass(server_id=d_srv.id, class_id=601, class_name="数据结构一班"))
    s.add(m.Contest(server_id=d_srv.id, contest_id=601, title="链表"))
    s.add(m.ContestClass(server_id=d_srv.id, contest_id=601, class_id=601))
    s.add(m.Problem(server_id=d_srv.id, problem_id=9101, title="单链表逆置"))
    s.add(m.ContestProblem(server_id=d_srv.id, contest_id=601, problem_id=9101, seq=1))
    s.add(m.ProblemLabel(server_id=d_srv.id, problem_id=9101, primary_skill=D_LIST, secondary_skills=[], brief="逆置"))
    for uid, name, good in (("2599000011", "基础好", True), ("2599000012", "基础弱", False)):
        s.add(m.Student(user_id=uid, name=name))
        s.flush()
        accounts.ensure_student_user(s, uid, name)
        s.add(m.Enrollment(user_id=uid, server_id=d_srv.id, class_id=601))
        _c_evidence(s, uid, C_PTR, good)
    s.add(m.User(role="admin", login_name="admin-t", display_name="管理员"))
    return c_srv.id, d_srv.id


def test_ds_pages(database):
    with db.session_scope() as s:
        _c_id, d_id = _ds_class(s)
    with TestClient(create_app()) as c:
        # C 语言登录：没有跨课程页
        c.post("/api/auth/login", json={"loginName": "admin-t", "password": "test-pass", "course": "C"})
        assert c.get(f"/api/teacher/classes/c{d_id}-601/cross").status_code == 404
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"loginName": "admin-t", "password": "test-pass", "course": "D"})
        page = c.get(f"/api/teacher/classes/c{d_id}-601/cross").json()
        assert len(page["links"]) == 28 and page["focus"] == [{"code": "D02.02", "name": page["focus"][0]["name"]}]
        warn = page["warning"]
        assert [x["name"] for x in warn["students"]] == ["基础弱"] and warn["topics"][0]["code"] == "C10.01"
        link = next(x for x in page["links"] if x["c"]["code"] == C_PTR and x["d"]["code"] == D_LIST)
        assert link["status"] == "accepted" and link["relationName"] == "先修"
        r = c.post(f"/api/teacher/cross/{link['id']}/review", json={"action": "reject"}).json()
        assert r["status"] == "rejected"
        assert c.post("/api/teacher/cross/propose", json={"kp": "D02.02"}).status_code == 409   # 没有大模型
        assert c.post("/api/teacher/cross/validate").json()["checked"] == 27
        detail = c.get("/api/teacher/students/2599000012").json()
        assert detail["joint"] and detail["foundation"] and detail["submissions"] == []
        ov = c.get(f"/api/teacher/classes/c{d_id}-601/overview").json()
        assert any("C 语言基础薄弱" in reason for a in ov["attention"] for reason in a["reasons"])
        c.post(f"/api/teacher/cross/{link['id']}/review", json={"action": "accept"})
        c.post("/api/auth/logout")
        # 数据结构的学生：学情页提示要补的 C 基础，只有档位，没有数字和 C 语言的记录
        c.post("/api/auth/login", json={"loginName": "2599000012", "password": "test-pass", "course": "D"})
        prog = c.get("/api/progress").json()
        assert prog["foundation"] and prog["foundation"][0]["supports"]
        assert set(prog["foundation"][0]) == {"name", "level", "segments", "supports", "text"}
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"loginName": "2599000011", "password": "test-pass", "course": "D"})
        assert c.get("/api/progress").json()["foundation"] == []
