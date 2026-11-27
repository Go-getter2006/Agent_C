"""相同登录身份下，两门课的班级页、后台刷新和缓存使用相同流程。"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import crypto, db, llm, mastery, models as m
from app.web import cache, teacher
from app.web.app import create_app


@pytest.fixture()
def classes(tmp_path, monkeypatch):
    # 后台线程和 HTTP 请求各用自己的连接，模拟正式环境，而非共用内存 SQLite 的单一连接。
    engine = db.init_engine("sqlite:///" + str(tmp_path / "courses.db"))
    db.create_all()
    now = db.now()
    monkeypatch.setattr(db, "now", lambda: now)
    with db.session_scope() as s:
        users = {role: m.User(role=role, login_name=f"parity-{role}", display_name=role)
                 for role in ("teacher", "admin")}
        s.add_all(users.values())
        s.flush()
        for i in range(20):
            s.add(m.Student(user_id=f"parity-{i}", name=f"学生{i}"))
        s.flush()
        servers = {}
        for course, skill in (("C", "C10.01.01"), ("D", "D02.02.01")):
            server = m.Server(code=f"parity-{course}", name=course, course=course, base_url="http://fake.invalid")
            s.add(server)
            s.flush()
            sid = servers[course] = server.id
            source = m.DataSource(server_id=sid, teacher_user_id="parity-teacher", enabled=False)
            s.add(source)
            s.flush()
            s.add(m.TeacherSource(user_id=users["teacher"].id, data_source_id=source.id))
            s.add(m.ClassSource(server_id=sid, class_id=31, data_source_id=source.id))
            s.add(m.XClass(server_id=sid, class_id=31, class_name=f"{course}班", data_source_id=source.id))
            s.add(m.Contest(server_id=sid, contest_id=11, title=f"{course}作业"))
            s.add(m.ContestClass(server_id=sid, class_id=31, contest_id=11))
            s.add(m.Problem(server_id=sid, problem_id=101, title=f"{course}题"))
            s.add(m.ContestProblem(server_id=sid, contest_id=11, problem_id=101))
            s.add(m.ProblemLabel(server_id=sid, problem_id=101, primary_skill=skill, secondary_skills=[]))
            for i in range(20):
                uid, good = f"parity-{i}", i % 2 == 0
                s.add(m.Enrollment(user_id=uid, server_id=sid, class_id=31, course=course))
                s.add(m.Submission(server_id=sid, submission_id=i + 1, problem_id=101, user_id=uid,
                                   contest_id=11, class_id=31, judgement="AC" if good else "WA",
                                   submit_time=now - dt.timedelta(hours=1), detail_status="done"))
                mastery.add_evidence(s, uid, skill, "pass_first_try" if good else "stuck_again", good, 1,
                                     f"parity:{course}:{i}", now, independent=good)
        ids = {role: user.id for role, user in users.items()}
    yield servers, ids
    cache.wait_idle()
    engine.dispose()


@pytest.mark.parametrize("course", ["C", "D"])
@pytest.mark.parametrize("role", ["teacher", "admin"])
def test_both_courses_prewarm_and_refresh_in_the_original_login_course(classes, course, role, monkeypatch, caplog):
    servers, ids = classes
    sid = servers[course]
    monkeypatch.setattr(cache, "BACKGROUND", True)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("读取班级页不能调用大模型")

    monkeypatch.setattr(llm, "call_json", forbidden)
    pages = ("overview", "students", "knowledge-map")
    with TestClient(create_app()) as client:
        client.cookies.set("pda_session", crypto.sign_session(ids[role], course))
        listed = client.get("/api/teacher/classes").json()["classes"]
        assert [c["key"] for c in listed] == [f"c{sid}-31"]
        cache.wait_idle()
        initial = {}
        for page in pages:
            response = client.get(f"/api/teacher/classes/c{sid}-31/{page}")
            assert response.status_code == 200, response.text
            initial[page] = response.json()
            assert "refreshing" not in initial[page]  # 预计算失败时不能靠前台重算掩盖问题。
        assert initial["overview"]["kpis"]["submissions"] == 20
        # 在一台服务器更新，另一门课的提交不应混进统计。
        with db.session_scope() as s:
            s.add(m.Submission(server_id=sid, submission_id=999, user_id="parity-0", problem_id=101,
                               judgement="WA", submit_time=db.now(), detail_status="done"))
        for page in pages:
            path = f"/api/teacher/classes/c{sid}-31/{page}"
            stale = client.get(path).json()
            assert stale["refreshing"] is True
            cache.wait_idle()
            fresh = client.get(path).json()
            assert "refreshing" not in fresh, (course, role, page)
            if page == "overview":
                assert fresh["kpis"]["submissions"] == 21
        other = servers["D" if course == "C" else "C"]
        assert client.get(f"/api/teacher/classes/c{other}-31/overview").status_code == 404
        # 同一个账号切换登录课程，两份后台结果和浏览器请求仍各归自己的班。
        client.cookies.set("pda_session", crypto.sign_session(ids[role], "D" if course == "C" else "C"))
        result = client.get(f"/api/teacher/classes/c{other}-31/overview").json()
        assert result["kpis"]["submissions"] == 20
    assert not calls
    assert not [r for r in caplog.records if r.name == "web.cache" and r.levelno >= 40]


@pytest.mark.parametrize("course", ["C", "D"])
def test_overview_cross_links_are_not_queried_once_per_student(classes, course):
    servers, ids = classes
    statements = []

    def count(_conn, _cursor, sql, *_args):
        if "from cross_links" in sql.lower():
            statements.append(sql)

    with db.session_scope() as s:
        user = s.get(m.User, ids["admin"])
        user.course = course
        mastery.memoize(s)
        event.listen(db.engine(), "before_cursor_execute", count)
        try:
            result = teacher.overview.__wrapped__(key=f"c{servers[course]}-31", user=user, s=s)
        finally:
            event.remove(db.engine(), "before_cursor_execute", count)
    assert len(statements) <= 2  # 初稿尚未入库时，空表检查也是一次查询。
    assert result["class"]["students"] == 20 and result["kpis"]["submissions"] == 20


def test_cross_link_changes_remain_visible_outside_readonly_requests(classes):
    from app import cross

    with db.session_scope() as s:
        rows = cross.accepted(s)
        assert rows  # 空表使用既有初稿。
        link = m.CrossLink(c_skill="C10.01.01", d_skill="D02.02.01", relation="prerequisite", status="accepted")
        s.add(link)
        s.flush()
        assert cross.accepted(s) == [link]
        link.status = "rejected"
        s.flush()
        assert cross.accepted(s) == []
    with db.session_scope() as s:
        mastery.memoize(s)
        assert cross.accepted(s) == []
