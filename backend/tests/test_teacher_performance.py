"""只读班级汇总：批量读取不改变统计、课程隔离或历史截止时间。"""
from __future__ import annotations

import datetime as dt
import threading
from dataclasses import asdict

import pytest
from sqlalchemy import event, select

from app import db, llm, mastery
from app import models as m
from app.labeling import labeler
from app.web import cache, teacher


@pytest.fixture()
def threaded_database(tmp_path):
    # 并发请求用独立连接；内存 SQLite 的 StaticPool 只有一条连接，不适合并发事务。
    engine = db.init_engine("sqlite:///" + str(tmp_path / "overview.db"))
    db.create_all()
    yield engine
    cache.wait_idle()
    engine.dispose()


def test_overview_batch_matches_original_without_model_calls(demo, monkeypatch):
    now = db.now()
    monkeypatch.setattr(db, "now", lambda: now)

    def forbidden(*args, **kwargs):
        raise AssertionError("班级总览不应调用大模型")

    monkeypatch.setattr(llm, "call_json", forbidden)
    sid = demo["server_id"]
    with db.session_scope() as s:
        # 增加学生和作业题，能测到逐人、逐题查询的增长，而非只测空班。
        for i in range(25):
            uid = f"performance-{i}"
            s.add(m.Student(user_id=uid, name=f"测试{i}"))
            s.flush()
            s.add(m.Enrollment(user_id=uid, server_id=sid, class_id=31, course="C"))
        for i in range(80):
            s.add(m.ContestProblem(server_id=sid, contest_id=99990, problem_id=10000 + i, seq=i))
        s.add(m.ContestClass(server_id=sid, contest_id=99990, class_id=31))

    def read():
        statements = []

        def count(_conn, _cursor, sql, *_args):
            statements.append(sql)

        event.listen(db.engine(), "before_cursor_execute", count)
        try:
            with db.session_scope() as s:
                mastery.memoize(s)
                user = s.scalar(select(m.User).where(m.User.login_name == "demo-teacher"))
                result = teacher.overview.__wrapped__(key=f"c{sid}-31", user=user, s=s)
            return result, len(statements)
        finally:
            event.remove(db.engine(), "before_cursor_execute", count)

    with monkeypatch.context() as plain:
        plain.setattr(mastery, "preload", lambda *_: None)
        plain.setattr(labeler, "preload_problem_labels", lambda *_: None)
        original, original_queries = read()
    batched, batch_queries = read()
    assert batched == original
    assert batch_queries < original_queries / 2
    assert batch_queries < 90
    # 同班新提交仍须更新；只在一次只读会话里复用，不缓存过期的学生数据。
    with db.session_scope() as s:
        s.add(m.Submission(server_id=sid, submission_id=991555, user_id="2500000001", problem_id=406,
                           judgement="WA", submit_time=now))
    updated, _ = read()
    assert updated["kpis"]["submissions"] == batched["kpis"]["submissions"] + 1


def test_mastery_preload_preserves_cutoffs_cross_course_and_low_confidence(demo):
    uid = "2500000001"
    now = db.now()
    earlier, future = now - dt.timedelta(days=2), now + dt.timedelta(days=2)
    with db.session_scope() as s:
        server = m.Server(code="batch-d", course="D", name="数据结构", base_url="http://example.invalid")
        s.add(server)
        s.flush()
        sid = server.id
        for no, when in ((1, earlier), (2, now), (3, future), (4, None)):
            s.add(m.Submission(server_id=sid, submission_id=no, user_id=uid, problem_id=12,
                               judgement="WA" if no == 1 else "AC", submit_time=when))
            s.add(m.SubmissionLabel(server_id=sid, submission_id=no, role="primary", error_code="OT99",
                                    skill_code="D01.01.01", confidence=0.3 if no == 2 else 0.9))
        for i, when in enumerate((earlier, now, future)):
            s.add(m.SkillEvidence(user_id=uid, skill_code="D01.01.01", event_type="pass_first_try",
                                source_key=f"batch:{i}", success=True, weight=1, ts=when, independent=True))

    def snapshot(batched):
        with db.session_scope() as s:
            if batched:
                mastery.memoize(s)
                mastery.preload(s, [uid, "no-records"])
            return [(
                mastery.attempt_history(s, uid, cutoff, historical=historical),
                {code: asdict(st) for code, st in mastery.skill_states(
                    s, uid, at=cutoff, model=model, with_predicted=True, replay=historical).items()},
                mastery.attempt_history(s, "no-records", cutoff),
            ) for cutoff in (earlier, now, future) for historical in (False, True) for model in ("v1", "v2")]

    assert snapshot(True) == snapshot(False)
    assert snapshot(True)[0][0][sid, 12] == [(1, "WA", "D01.01.01")]


def test_class_list_only_prewarms_default_class(demo, monkeypatch):
    seen = []
    monkeypatch.setattr(cache, "prewarm", lambda key, rebuild: seen.append(key))
    with db.session_scope() as s:
        user = m.User(role="admin", login_name="perf-admin", course="C", display_name="开发者")
        s.add(user)
        s.flush()
        result = teacher.classes(user=user, s=s)
    assert len(result["classes"]) > 1
    assert seen == [("overview", "C", ("key", result["classes"][0]["key"]))]


def test_background_refresh_is_bounded_and_can_retry(threaded_database, monkeypatch):
    monkeypatch.setattr(cache, "BACKGROUND", True)
    release = threading.Event()
    started = threading.Event()
    counts = []

    def build(s):
        counts.append(1)
        if len(counts) == 2:
            started.set()
        assert release.wait(5)
        return {"value": len(counts)}

    try:
        assert cache._refresh(("first",), build)
        assert cache._refresh(("second",), build)
        assert started.wait(5)
        assert not cache._refresh(("third",), build)
        assert not cache._refresh(("first",), build)
    finally:
        release.set()
        cache.wait_idle()
    assert cache._refresh(("third",), build)
    cache.wait_idle()
    assert len(counts) == 3


def test_simultaneous_cold_requests_share_one_build(threaded_database, monkeypatch):
    monkeypatch.setattr(cache, "BACKGROUND", True)
    entered, release, checked = threading.Event(), threading.Event(), threading.Event()
    calls, versions, results, errors = [], [], [], []
    original_version = cache.data_version

    def version(s):
        value = original_version(s)
        versions.append(value)
        if len(versions) == 2:
            checked.set()
        return value

    monkeypatch.setattr(cache, "data_version", version)

    def build():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"students": 90}

    def request():
        try:
            with db.session_scope() as s:
                results.append(cache.cached(s, ("overview", "C", ("key", "c1-31")), build,
                                            rebuild=lambda _: build()))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=request) for _ in range(2)]
    try:
        threads[0].start()
        assert entered.wait(5)
        threads[1].start()
        assert checked.wait(5)
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(5)
    assert not errors and all(not thread.is_alive() for thread in threads)
    assert calls == [1] and results == [{"students": 90}, {"students": 90}]


def test_weekly_buckets_match_calendar_boundaries(database):
    """UTC 提交按北京时间周一分周；首次通过的次数包含更早的失败，空时间不计入趋势。"""
    from types import SimpleNamespace

    monday = dt.datetime(2026, 11, 23, tzinfo=db.BEIJING)
    times = [monday - dt.timedelta(weeks=8), monday - dt.timedelta(weeks=7),
             monday - dt.timedelta(microseconds=1), monday, monday + dt.timedelta(days=1),
             monday + dt.timedelta(weeks=1), None]
    subs = [SimpleNamespace(submit_time=t, judgement="AC" if i == 3 else "WA")
            for i, t in enumerate(times)]
    data = SimpleNamespace(ids=["a"], course="C", subs={"a": {(1, 12): subs}})
    with db.session_scope() as s:
        rows = teacher._weekly(s, data, monday + dt.timedelta(days=2))
    assert [row["submissions"] for row in rows] == [1, 0, 0, 0, 0, 0, 1, 2]
    assert rows[-1]["passed"] == 1 and rows[-1]["avgToPass"] == 4
    assert rows[-1]["active"] == 1 and all(row["rounds"] == 0 for row in rows)
