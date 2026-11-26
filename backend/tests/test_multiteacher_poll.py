"""同一服务器的教师查询时钟互不覆盖，历史提交分页公平轮转。"""
from sqlalchemy import select

from app import config, db
from app import models as m
from app.web import teacher
from app.xdoj import store, sync_meta, sync_submissions
from app.xdoj.client import XdojError
from app.xdoj.daemon import Daemon

from .conftest import make_context, small_fake


def teachers(clock):
    return _teachers(clock)


def _teachers(clock, distinct_students=False):
    first, second = small_fake(), small_fake()
    first.submissions = {k: v for k, v in first.submissions.items() if k == 537104}
    second.submissions = {k: v for k, v in second.submissions.items() if k != 537104}
    second.teacher_id = "teacher02"
    second.classes[0].update(id=32, classId=32, className="测试二班", ownerId=second.teacher_id)
    second.contests[0].update(id=502, classes=[{"id": 32}])
    for student in second.students:
        student["classId"] = 32
        if distinct_students:
            student["userId"] = "26" + student["userId"][2:]
    for sub in second.submissions.values():
        sub.class_id, sub.contest_id = 32, 502
        if distinct_students:
            sub.user_id = "26" + sub.user_id[2:]
    contexts = [make_context(fake, clock) for fake in (first, second)]
    for ctx, fake in zip(contexts, (first, second)):
        with db.session_scope() as s:
            source = m.DataSource(server_id=ctx.server_id, teacher_user_id=fake.teacher_id)
            s.add(source)
            s.flush()
            ctx.source_id = source.id
        sync_meta.sync_metadata(ctx)
    return contexts


def test_second_teacher_catches_older_submissions_and_class_query(database, clock):
    first, second = teachers(clock)
    daemon = Daemon([first, second])
    assert daemon.step() == "poll"
    assert not daemon._poll_due(first)
    assert daemon._poll_due(second), "前一位老师刚查完不能压住第二位老师"
    assert daemon.step() == "poll"
    with db.session_scope() as s:
        assert set(s.scalars(select(m.Submission.submission_id))) == {537101, 537102, 537103, 537104}
        assert store.get_state(s, 1, f"max_submission_id:{first.source_id}") == "537104"
        assert store.get_state(s, 1, f"max_submission_id:{second.source_id}") == "537103"
        # 实际教师提交接口也应能读出第二位老师同步到的历史记录。
        user = m.User(role="teacher", login_name="teacher02", data_source_id=second.source_id, course="C")
        s.add(user)
        s.flush()
        s.add(m.TeacherSource(user_id=user.id, data_source_id=second.source_id))
        s.flush()
        page = teacher.class_submissions("c1-32", user=user, s=s)
        assert page["total"] == 4  # 名单中的学生的全部提交，包含第一位老师已同步的那条。
        assert {row["key"] for row in page["items"]} == {f"s1-{i}" for i in (537101, 537102, 537103, 537104)}
        assert teacher._source_view(s, s.get(m.DataSource, second.source_id))["lastPoll"] is not None
    assert not daemon._poll_due(second)
    # 重启不重复查询，第二位老师独立到期时继续同步。
    assert not Daemon([first, second])._poll_due(second)
    clock.advance(config.SYNC["poll_normal_seconds"] + 1)
    assert daemon._poll_due(first) and daemon._poll_due(second)


def test_backlog_yields_to_other_teacher(database, clock, monkeypatch):
    first, second = teachers(clock)
    monkeypatch.setitem(config.SYNC, "page_rows", 1)
    daemon = Daemon([first, second])
    assert daemon.step() == "poll"
    assert first.stats["poll_has_more"]
    assert daemon.step() == "poll"
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537101)) is not None
    # 第一个老师补完，第二个老师仍能继续分页。
    assert daemon.step() == "poll"
    assert daemon.step() == "poll"
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537102)) is not None


def test_failure_and_retry_time_are_per_teacher(database, clock, monkeypatch):
    first, second = teachers(clock)
    daemon = Daemon([first, second])
    real_poll = sync_submissions.poll_new_submissions

    def fail_first(ctx, **kwargs):
        if ctx is first:
            raise XdojError("模拟接口异常")
        return real_poll(ctx, **kwargs)

    monkeypatch.setattr(sync_submissions, "poll_new_submissions", fail_first)
    for _ in range(config.SYNC["fail_streak_slowdown"]):
        daemon._poll(first)
    assert daemon.tier(first)[0] == "degraded"
    assert daemon.tier(second)[0] != "degraded"
    assert daemon._poll_due(second)
    daemon._poll(second)
    assert daemon.tier(first)[0] == "degraded", "别的老师成功不能清掉这位老师的失败计数"
    assert not daemon._poll_due(first)
    with db.session_scope() as s:
        bad = teacher._source_view(s, s.get(m.DataSource, first.source_id))
        good = teacher._source_view(s, s.get(m.DataSource, second.source_id))
        assert bad["pollFailures"] == config.SYNC["fail_streak_slowdown"]
        assert bad["lastPoll"] is None
        assert good["pollFailures"] == 0 and good["lastPoll"] is not None


def test_detail_uses_teacher_who_owns_submission_class(database, clock):
    first, second = _teachers(clock, distinct_students=True)
    daemon = Daemon([first, second])
    daemon._poll(first)
    daemon._poll(second)
    requests = first.client.request_count
    # 队列是共用的，即便入口是第一位老师，也不能用他的账号取第二位老师的详情。
    assert daemon._run_task(first, ("detail",), 1, target_id=537101)
    assert first.client.request_count == requests
    with db.session_scope() as s:
        sub = s.get(m.Submission, (1, 537101))
        assert sub.detail_status == "done"
        assert s.get(m.SourceSnapshot, (1, 537101)) is not None
        task = s.scalar(select(m.FetchTask).where(m.FetchTask.target_id == 537101, m.FetchTask.kind == "detail"))
        assert task.status == "done" and task.attempts == 0
        # 提交没班号时，也可以沿学生名单找到可用教师。
        sub.class_id = None
        s.flush()
        routed = daemon._submission_context(s, first, sub.submission_id)
        assert routed.server_id == sub.server_id
        assert routed.source_id == second.source_id


def test_analysis_runs_before_historical_detail_backlog(database, clock):
    first, _second = teachers(clock)
    processed = []
    daemon = Daemon([first], post_process=lambda *task: processed.append(task))
    daemon._poll(first)
    with db.session_scope() as s:
        store.enqueue(s, 1, "post_process", 123, 1, when=clock.now())
    requests = first.client.request_count
    assert daemon.step() == "local"
    assert processed == [(1, "post_process", 123)]
    assert first.client.request_count == requests
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537104)).detail_status != "done"
