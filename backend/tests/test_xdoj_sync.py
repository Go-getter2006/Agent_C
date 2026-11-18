"""XDOJ 同步程序的测试（《同步程序开发说明》第 9 节列出的各项）。"""
from __future__ import annotations

import datetime as dt
import logging

import pytest
from sqlalchemy import func, select, text

from app import config, db
from app import models as m
from app.xdoj import api, store, sync_meta, sync_submissions
from app.xdoj.assemble import assemble_submission
from app.xdoj.client import NotAllowed, RateLimiter, SessionError, XdojClient
from app.xdoj.daemon import Daemon, poll_tier
from app.xdoj.fake import FakeJudge, FakeRun, FakeSubmission

from .conftest import make_context, small_fake


def count(model) -> int:
    with db.session_scope() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def full_sync(ctx) -> Daemon:
    sync_meta.sync_metadata(ctx)
    daemon = Daemon([ctx])
    daemon.drain()
    return daemon


# ------------------------------------------------------------ 只读与限速
def test_whitelist_rejects_other_paths(ctx, fake):
    ctx.client.login()
    before = len(fake.requests)
    for method, path in [("POST", "/submission/rejudge.do"), ("POST", "/problem/delete.do"),
                         ("POST", "/submission/submit.do"), ("GET", "/user/login.do"),
                         ("POST", "/testcase/list.do"), ("GET", "/user/listStudents.do/../x")]:
        with pytest.raises(NotAllowed):
            ctx.client.request(method, path)
    assert len(fake.requests) == before, "白名单外的请求一个都不能发出去"


def test_only_whitelisted_paths_are_requested(ctx, fake):
    from app.xdoj.client import WHITELIST

    full_sync(ctx)
    assert fake.requests
    assert all((meth, path) in WHITELIST for meth, path in fake.requests)


def test_request_interval_not_below_config(ctx, fake):
    full_sync(ctx)
    times = fake.request_times
    assert len(times) > 20
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 0.5 - 1e-9


def test_interval_floor():
    assert RateLimiter(0.01).min_interval == config.XDOJ_MIN_INTERVAL_FLOOR


# ------------------------------------------------------------ 学生信息
def test_roster_secrets_never_stored_or_logged(ctx, fake, caplog):
    caplog.set_level(logging.DEBUG)
    full_sync(ctx)
    assert count(m.Student) == 3
    assert count(m.User) == 3
    secrets = ["SECRET", "pw-2500000001", "tok-2500000001", "10.9.8.7", fake.password]
    with db.session_scope() as s:
        for table in db.Base.metadata.sorted_tables:
            for row in s.execute(table.select()).all():
                blob = repr(tuple(row))
                for secret in secrets:
                    assert secret not in blob, f"{table.name} 里出现了敏感内容"
    for secret in secrets:
        assert secret not in caplog.text


def test_enrollment_and_accounts(ctx, fake):
    full_sync(ctx)
    with db.session_scope() as s:
        e = s.scalars(select(m.Enrollment)).all()
        assert {(x.user_id, x.class_id, x.status) for x in e} == {
            ("2500000001", 31, "active"), ("2500000002", 31, "active"), ("2500000003", 31, "active")}
        user = s.scalar(select(m.User).where(m.User.login_name == "2500000001"))
        assert user.role == "student" and user.student_id == "2500000001"
    # 名单里没了：标为已移出，不删数据
    fake.students = [st for st in fake.students if st["userId"] != "2500000003"]
    sync_meta.sync_classes_and_roster(ctx)
    with db.session_scope() as s:
        gone = s.scalar(select(m.Enrollment).where(m.Enrollment.user_id == "2500000003"))
        assert gone.status == "removed" and gone.until is not None
        assert s.get(m.Student, "2500000003") is not None
        assert s.scalar(select(m.User).where(m.User.login_name == "2500000003")).status == "removed"


# ------------------------------------------------------------ 原样保存
def test_source_and_output_bytes_kept_verbatim(ctx, fake):
    full_sync(ctx)
    with db.session_scope() as s:
        snap = s.get(m.SourceSnapshot, (1, 537101))
        assert snap.source_bytes == fake.submissions[537101].source  # \r\n、制表符、连续空格都在
        gbk = s.get(m.SourceSnapshot, (1, 537102))
        assert gbk.source_bytes == fake.submissions[537102].source
        assert gbk.source_bytes.decode(gbk.encoding).startswith("// 两数之和")
        tc = s.get(m.Testcase, (1, 1397))
        assert tc.input_bytes == b"2 3\n" and tc.output_bytes == b"5\n"
        assert s.get(m.RunOutput, (1, 2153402)).program_output_bytes == b"7\n"


def test_program_output_null_vs_empty(ctx):
    full_sync(ctx)
    with db.session_scope() as s:
        wrong = assemble_submission(s, 1, 537101)
        by_tc = {t["testcaseId"]: t for t in wrong["testCases"]}
        assert by_tc[1396]["programOutput"] is None       # 通过的测试点：没取
        assert by_tc[1397]["programOutput"] == "7\n"
        assert by_tc[1397]["standardInput"] == "2 3\n" and by_tc[1397]["standardOutput"] == "5\n"
        assert wrong["attemptNo"] == 1 and wrong["prevSubmissionId"] is None
        silent = assemble_submission(s, 1, 537104)
        assert [t["programOutput"] for t in silent["testCases"]] == ["", ""]  # 程序确实什么都没输出
        assert s.get(m.RunOutput, (1, 2153401)) is None


def test_missing_program_output_is_not_an_empty_output(ctx, fake):
    fake.submissions[537101].judges[-1].runs[-1].program_output = None
    full_sync(ctx)
    with db.session_scope() as s:
        evidence = assemble_submission(s, 1, 537101)
        failed = next(row for row in evidence["testCases"] if not row["passed"])
        assert failed["programOutput"] is None


@pytest.mark.parametrize("value,expected", [(1, "正确"), (2, "错误"), (3, "输出超限"),
                                           (4, "超时"), (5, "运行时错误"), (6, "格式错误")])
def test_real_numeric_run_results(ctx, monkeypatch, value, expected):
    from app.xdoj import endpoints as ep

    monkeypatch.setattr(ctx.client, "post_json", lambda *args: {
        "data": [{"id": 1, "testcaseId": 2, "result": value, "runTime": 3, "memory": 0}]})
    assert ep.list_judge_runs(ctx.client, 10)[0].result_text == expected


def test_priority_detail_repairs_cached_statement_before_reply(ctx, fake, monkeypatch):
    from app import pdftext

    sync_meta.sync_metadata(ctx)
    sync_submissions.poll_new_submissions(ctx)
    with db.session_scope() as s:
        s.add(m.ProblemText(server_id=ctx.server_id, problem_id=406, content_type="application/pdf",
                            file_bytes=b"%PDF-1.4\n", raw_text="字符转换：a\uf0e8d，B\uf0e8Z",
                            extract_status="garbled", extracted_text="", tidied_at=ctx.now()))
    monkeypatch.setattr(pdftext, "read_pdf", lambda data: ("字符转换：a→d，B→Z", []))
    before = len(fake.requests)
    sync_submissions.fetch_detail(ctx, 537101, source_first=True)
    with db.session_scope() as s:
        assert assemble_submission(s, ctx.server_id, 537101)["problemText"] == "字符转换：a→d，B→Z"
    assert not any(path == "/problem/viewTextFile.do" for _, path in fake.requests[before:])


def test_missing_standard_data_remains_unknown(ctx):
    from app.xdoj import endpoints as ep

    with db.session_scope() as s:
        sync_submissions._crosscheck_testcase(s, ctx, 406, 999, ep.RunOutputRow(None, "", None))
    with db.session_scope() as s:
        testcase = s.get(m.Testcase, (ctx.server_id, 999))
        assert testcase.input_bytes is None and testcase.output_bytes == b""
        sync_submissions._crosscheck_testcase(s, ctx, 406, 999, ep.RunOutputRow("input", None, None))
    with db.session_scope() as s:
        testcase = s.get(m.Testcase, (ctx.server_id, 999))
        assert testcase.input_bytes == b"input" and testcase.output_bytes == b""


def test_run_output_rejects_incomplete_response(ctx, monkeypatch):
    from app.xdoj import endpoints as ep
    from app.xdoj.client import XdojError

    monkeypatch.setattr(ctx.client, "post_json", lambda *args: {"error": "unavailable"})
    with pytest.raises(XdojError, match="输入输出字段"):
        ep.run_output(ctx.client, 1)


def test_judge_request_uses_page_parameters_and_reads_all_pages(ctx, fake, monkeypatch):
    from app.xdoj import endpoints as ep

    judges = fake.submissions[537101].judges
    judges.extend(FakeJudge(id=600000 + i, result="正确") for i in range(25))
    original = ctx.client.get_json

    def check_parameters(path, params):
        if path == "/submission/listJudge.do":
            assert params["sord"] == "asc"
            assert "sidx" in params and params["_search"] == "false"
        return original(path, params)

    monkeypatch.setattr(ctx.client, "get_json", check_parameters)
    parsed = ep.list_judges(ctx.client, 537101)
    assert len(parsed) == 26
    assert parsed[-1].judge_id == 600024


def test_compile_error_assembles_without_runs(ctx):
    full_sync(ctx)
    with db.session_scope() as s:
        out = assemble_submission(s, 1, 537102)
        assert out["judgementCode"] == "CE" and "expected ';'" in out["compileOutput"]
        assert out["testCases"] == []
        assert "两数之和" in out["sourceCode"]
        allcases = assemble_submission(s, 1, 537102, include_all_testcases=True)
        assert [t["testcaseId"] for t in allcases["testCases"]] == [1396, 1397]
        assert all(t["runId"] is None and t["programOutput"] is None for t in allcases["testCases"])


def test_max_output_chars_reports_first_diff(ctx):
    full_sync(ctx)
    with db.session_scope() as s:
        out = assemble_submission(s, 1, 537101, max_output_chars=1)
        failed = [t for t in out["testCases"] if not t["passed"]][0]
        assert failed["firstDiffLine"] == 1 and failed["outputTruncated"] is True
        assert failed["programOutput"] == "7"


# ------------------------------------------------------------ 重判、未评测完
def test_rejudge_inserts_new_row_and_switches_latest(ctx, fake, clock):
    daemon = full_sync(ctx)
    fake.submissions[537101].judges.append(FakeJudge(
        id=537699, result="正确", points=100,
        runs=[FakeRun(2153490, 1396, "正确"), FakeRun(2153491, 1397, "正确")]))
    clock.advance(3700)  # 过了一小时：回读
    daemon.drain()
    with db.session_scope() as s:
        judges = s.scalars(select(m.Judge).where(m.Judge.submission_id == 537101).order_by(m.Judge.judge_id)).all()
        assert [(j.judge_id, j.is_latest) for j in judges] == [(537601, False), (537699, True)]
        assert s.get(m.Submission, (1, 537101)).judgement == "AC"
        assert assemble_submission(s, 1, 537101)["judgeId"] == 537699
        assert s.get(m.JudgeRun, (1, 2153402)) is not None, "旧评测的运行记录保留"


def test_pending_submission_followed_until_judged(ctx, fake, clock):
    daemon = full_sync(ctx)
    fake.submissions[537200] = FakeSubmission(
        id=537200, user_id="2500000002", user_name="学生乙", problem_id=407, contest_id=501, class_id=31,
        submit_time="2026-11-16 21:05:00", source=b"int main(){}\n", judges=[])
    clock.advance(61)
    daemon.drain()
    with db.session_scope() as s:
        sub = s.get(m.Submission, (1, 537200))
        assert sub.judgement == "PENDING" and sub.detail_status == "pending"
    clock.advance(2)
    daemon.drain()  # 第一次跟进：还没结果
    fake.submissions[537200].judges.append(FakeJudge(
        id=537700, result="正确", points=100,
        runs=[FakeRun(2153501, 1401, "正确"), FakeRun(2153502, 1402, "正确")]))
    clock.advance(5)
    daemon.drain()
    with db.session_scope() as s:
        sub = s.get(m.Submission, (1, 537200))
        assert sub.detail_status == "done" and sub.judgement == "AC"
        assert s.get(m.SourceSnapshot, (1, 537200)) is not None


# ------------------------------------------------------------ 轮询与触发
def test_poll_tiers():
    night = dt.datetime(2026, 11, 16, 19, 0, tzinfo=db.UTC)   # 北京时间 3:00
    day = dt.datetime(2026, 11, 16, 7, 0, tzinfo=db.UTC)      # 北京时间 15:00
    recent = lambda t, minutes: t - dt.timedelta(minutes=minutes)  # noqa: E731
    assert poll_tier(day, recent(day, 3), None) == ("active", 10)
    assert poll_tier(day, None, recent(day, 9)) == ("active", 10)
    assert poll_tier(day, recent(day, 20), None) == ("normal", 60)
    assert poll_tier(night, recent(night, 40), None) == ("night", 300)
    assert poll_tier(night, recent(night, 20), None) == ("normal", 60)   # 夜里但刚有人交过
    assert poll_tier(night, recent(night, 5), None) == ("active", 10)
    assert poll_tier(day, recent(day, 1), None, fail_streak=10) == ("degraded", 300)


def test_trigger_polls_immediately_and_raises_priority(ctx, fake, clock):
    daemon = full_sync(ctx)
    clock.advance(1200)   # 20 分钟没动静：平时档
    daemon.drain()
    assert daemon.tier(ctx)[0] == "normal"
    fake.submissions[537300] = FakeSubmission(
        id=537300, user_id="2500000001", user_name="学生甲", problem_id=407, contest_id=501, class_id=31,
        submit_time="2026-11-16 21:30:00", source=b"int main(){return 1;}\n",
        judges=[FakeJudge(id=537800, result="答案错误", points=50,
                          runs=[FakeRun(2153601, 1401, "正确"), FakeRun(2153602, 1402, "错误", program_output="0\n")])])
    clock.advance(1)  # 还没到下一次轮询的时间
    assert daemon.step() is None
    with db.session_scope() as s:
        api.notify_student_active(s, "2500000001")
    assert daemon.step() == "trigger"
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537300)) is not None, "触发后立即查到新提交"
        task = s.scalar(select(m.FetchTask).where(m.FetchTask.target_id == 537300, m.FetchTask.kind == "detail"))
        assert task.priority == 0
    before = len(fake.requests)
    assert daemon.step() == "p0"
    # P0 先拿源码
    assert fake.requests[before][1] == "/submission/downloadSource.do"
    assert daemon.tier(ctx)[0] == "active"


def test_request_priority_for_unknown_submission(ctx, fake, clock):
    daemon = full_sync(ctx)
    fake.submissions[537400] = FakeSubmission(
        id=537400, user_id="2500000003", user_name="学生丙", problem_id=407, contest_id=501, class_id=31,
        submit_time="2026-11-16 21:40:00", source=b"int main(){}\n",
        judges=[FakeJudge(id=537900, result="正确", points=100,
                          runs=[FakeRun(2153701, 1401, "正确"), FakeRun(2153702, 1402, "正确")])])
    with db.session_scope() as s:
        api.request_priority(s, 1, 537400)
    daemon.step()
    daemon.step()
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537400)).detail_status == "done"


def test_explicit_submission_precedes_other_student_history(ctx):
    sync_meta.sync_metadata(ctx)
    sync_submissions.poll_new_submissions(ctx)
    with db.session_scope() as s:
        api.notify_student_active(s, "2500000001")
        api.request_priority(s, ctx.server_id, 537104)
    assert Daemon([ctx]).step() == "trigger"
    with db.session_scope() as s:
        assert s.get(m.Submission, (ctx.server_id, 537104)).detail_status == "done"
        assert s.get(m.Submission, (ctx.server_id, 537101)).detail_status != "done"


def test_explicit_request_repairs_missing_source_of_completed_detail(ctx):
    from sqlalchemy import delete

    daemon = full_sync(ctx)
    with db.session_scope() as s:
        s.execute(delete(m.SourceSnapshot).where(m.SourceSnapshot.submission_id == 537101))
        api.request_priority(s, ctx.server_id, 537101)
    assert daemon.step() == "trigger"
    with db.session_scope() as s:
        assert s.get(m.SourceSnapshot, (ctx.server_id, 537101)) is not None


def test_daemon_yields_between_submission_pages(ctx, monkeypatch):
    monkeypatch.setitem(config.SYNC, "page_rows", 2)
    daemon = Daemon([ctx])
    daemon._poll(ctx)
    assert count(m.Submission) == 2
    assert daemon._poll_due(ctx)
    with db.session_scope() as s:
        api.request_priority(s, ctx.server_id, 537101)
    daemon.step()
    with db.session_scope() as s:
        assert s.get(m.Submission, (ctx.server_id, 537101)).detail_status == "done"
    assert count(m.Submission) == 2


# ------------------------------------------------------------ 续跑与出错
def test_crash_midway_then_rerun_no_dup_no_miss(database, clock, monkeypatch):
    fx = small_fake()
    for i in range(250):  # 多于两页
        sid = 600000 + i
        fx.submissions[sid] = FakeSubmission(
            id=sid, user_id="2500000002", user_name="学生乙", problem_id=406, contest_id=501, class_id=31,
            submit_time="2026-11-16 21:10:00", source=b"int main(){}\n",
            judges=[FakeJudge(id=700000 + i, result="正确", points=100,
                              runs=[FakeRun(3000000 + 2 * i, 1396, "正确"), FakeRun(3000001 + 2 * i, 1397, "正确")])])
    ctx = make_context(fx, clock)
    sync_meta.sync_metadata(ctx)

    calls = {"n": 0}
    real = sync_submissions._store_submission

    def flaky(s, c, row, backfill):
        calls["n"] += 1
        if calls["n"] == 150:  # 第二页写到一半时崩溃
            raise RuntimeError("模拟崩溃")
        return real(s, c, row, backfill)

    monkeypatch.setattr(sync_submissions, "_store_submission", flaky)
    with pytest.raises(RuntimeError):
        sync_submissions.poll_new_submissions(ctx, backfill=True)
    with db.session_scope() as s:
        assert store.get_state(s, 1, "max_submission_id:0") == str(sorted(fx.submissions)[99])
    assert count(m.Submission) == 100, "崩溃的那一批整批回滚，进度没有前移"

    monkeypatch.setattr(sync_submissions, "_store_submission", real)
    sync_submissions.poll_new_submissions(ctx, backfill=True)
    assert count(m.Submission) == len(fx.submissions)
    # 详情取到一半崩溃：重跑后补齐，不重复
    daemon = Daemon([ctx])
    for _ in range(40):
        daemon.step()
    ctx2 = make_context(fx, clock)
    daemon2 = Daemon([ctx2])
    daemon2.startup()
    daemon2.drain()
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(m.Submission).where(m.Submission.detail_status == "done")) \
            == len(fx.submissions)
    assert count(m.SourceSnapshot) == len(fx.submissions)
    assert count(m.Judge) == sum(len(x.judges) for x in fx.submissions.values())


def test_session_expiry_relogin(ctx, fake):
    sync_meta.sync_metadata(ctx)
    assert fake.login_count == 1
    fake.expire_sessions()
    sync_submissions.poll_new_submissions(ctx)
    assert fake.login_count == 2
    assert count(m.Submission) == 4


def test_login_failure_stops(database, fake, clock):
    fake.password = "changed"
    bad = XdojClient("http://fake.test/xdoj-ssm", fake.teacher_id, "wrong", transport=fake.transport(),
                     limiter=RateLimiter(0.5, clock=clock.monotonic, sleep=clock.sleep), sleep=clock.sleep)
    with pytest.raises(SessionError):
        bad.get_json("/testcase/list.do", {"problemId": 406})


def test_retry_on_5xx_then_success(ctx, fake, clock):
    ctx.client.login()
    fake.fail_queue = [500, 502]
    rows, _ = __import__("app.xdoj.endpoints", fromlist=["x"]).list_submissions_after(ctx.client, 0)
    assert len(rows) == 4
    assert clock.slept.count(1.0) >= 1 and 2.0 in clock.slept  # 指数退避


def test_request_log_has_no_bodies(ctx, fake):
    full_sync(ctx)
    with db.session_scope() as s:
        logs = s.scalars(select(m.RequestLog)).all()
        assert len(logs) == len(fake.requests)
        assert {l.endpoint for l in logs} <= {p for _m, p in fake.requests}
        cols = {c.name for c in m.RequestLog.__table__.columns}
        assert cols == {"id", "server_id", "ts", "endpoint", "status_code", "duration_ms", "ok"}


# ------------------------------------------------------------ 题库更新
def test_changed_problem_detected_and_archived(ctx, fake, clock):
    full_sync(ctx)
    with db.session_scope() as s:
        s.execute(text("DELETE FROM sync_fetch_queue WHERE kind = 'label_problem'"))
    # 题面改了、测试点文件改了、加了一道新题
    fake.problem_files[406] = ("输入两个整数，输出它们的和（改过）。".encode("utf-8"), "text/plain; charset=utf-8")
    fake.testcases[407][1]["output"] = b"-4\n\n"
    fake.problems.append({"id": 408, "title": "新题", "categoryName": "循环", "tags": [], "difficulty": "易",
                          "timeLimit": 1000, "memoryLimit": 65536})
    fake.problem_files[408] = (b"new", "text/plain")
    fake.add_testcase(408, 1501, "1\n", "1\n")
    clock.advance(8 * 86400)  # 过了一周：测试点文件全部重新核对
    changed = sync_meta.sync_problems(ctx, inline_all=True)
    assert sorted(changed) == [406, 407, 408]
    with db.session_scope() as s:
        kinds = {(a.kind, a.target_id) for a in s.scalars(select(m.Archive)).all()}
        assert ("problem_text", 406) in kinds and ("testcase", 1402) in kinds
        assert "改过" in s.get(m.ProblemText, (1, 406)).extracted_text
        queued = set(s.scalars(select(m.FetchTask.target_id).where(
            m.FetchTask.kind == "label_problem", m.FetchTask.status == "queued")).all())
        assert queued == {406, 407, 408}


def test_contest_visibility_data(ctx):
    full_sync(ctx)
    with db.session_scope() as s:
        assert s.get(m.ContestClass, (1, 501, 31)) is not None
        assert [p.problem_id for p in s.scalars(select(m.ContestProblem).order_by(m.ContestProblem.seq)).all()] \
            == [406, 407]
        assert {t.tag for t in s.scalars(select(m.ProblemTag)).all()} == {"输入输出", "一维数组"}


def test_two_servers_do_not_collide(database, clock):
    fa, fb = small_fake(), small_fake()
    fb.submissions[537101].user_id = "2500000003"
    ca, cb = make_context(fa, clock, server_id=1), make_context(fb, clock, server_id=2)
    for c in (ca, cb):
        full_sync(c)
    assert count(m.Submission) == 8 and count(m.Student) == 3   # 编号相同，各存各的；学号是同一批学生
    with db.session_scope() as s:
        assert s.get(m.Submission, (1, 537101)).user_id == "2500000001"
        assert s.get(m.Submission, (2, 537101)).user_id == "2500000003"
        assert store.get_state(s, 1, "max_submission_id:0") == store.get_state(s, 2, "max_submission_id:0") == "537104"


def test_bank_problems_are_queued_not_downloaded_inline(ctx, fake):
    """老师第一次登录：只拉班级、名单、作业和题库列表；测试点排队（作业里的题优先），主循环接着下载。"""
    from app.xdoj import daemon as daemon_mod

    fake.problems.append({"id": 499, "title": "题库里的题", "categoryName": "循环", "tags": [], "difficulty": "易",
                          "timeLimit": 1000, "memoryLimit": 65536})
    fake.problem_files[499] = (b"bank", "text/plain")
    fake.add_testcase(499, 1901, "1\n", "1\n")
    sync_meta.sync_metadata(ctx)
    assert not any("testcase" in p or "viewTextFile" in p for _m, p in fake.requests)
    with db.session_scope() as s:
        prio = {t.target_id: t.priority for t in s.scalars(select(m.FetchTask).where(m.FetchTask.kind == "problem_assets"))}
        assert prio == {406: 1, 407: 1, 499: 3}   # 作业里的题排在前面
    Daemon([ctx]).drain()
    with db.session_scope() as s:
        assert all(p.assets_fetched for p in s.scalars(select(m.Problem)).all())
        assert s.get(m.Testcase, (1, 1901)).input_bytes is not None
        assert daemon_mod.claim("test-owner")
        _when, activity = daemon_mod.last_beat(s)
    assert activity  # 心跳里带着“正在做什么”


def test_bank_synced_once_per_server_per_day(ctx, fake, clock):
    """所有老师共用一个题库：同一天第二次同步（另一位老师登录）不再拉题库，也不再下载测试点。"""
    full_sync(ctx)
    before = len(fake.requests)
    sync_meta.sync_metadata(ctx)
    paths = [p for _m, p in fake.requests[before:]]
    assert "/problem/list.do" not in paths
    assert not any("testcase" in p or "viewTextFile" in p for p in paths)
    # 第二天：题库列表拉一次；作业里的题只轻量核对（测试点列表和题面），文件没变就不重新下载
    clock.advance(86400)
    before = len(fake.requests)
    sync_meta.sync_metadata(ctx)
    Daemon([ctx]).drain()
    paths = [p for _m, p in fake.requests[before:]]
    assert "/problem/list.do" in paths
    assert not any("download" in p.lower() and "testcase" in p for p in paths)


def test_roster_filter_falls_back_to_other_class_id(ctx, fake):
    """班级列表里 id 和 classId 不一样，名单接口按 id 筛：先按 classId 取不到人，换 id 再取；作业里的班级也对上。"""
    fake.classes = [{"id": 9031, "classId": 31, "className": "测试一班", "ownerId": fake.teacher_id}]
    for st in fake.students:
        st["classId"] = 9031           # 名单接口认的是 id
    fake.contests[0]["classes"] = [{"id": 9031}]
    sync_meta.sync_metadata(ctx)
    with db.session_scope() as s:
        names = {e.user_id for e in s.scalars(select(m.Enrollment).where(m.Enrollment.class_id == 31)).all()}
        assert names == {"2500000001", "2500000002", "2500000003"}
        assert s.get(m.ContestClass, (1, 501, 31)) is not None


@pytest.mark.parametrize("class_code", ["COURSE-2026", "000031"])
def test_roster_keeps_original_class_filter(ctx, fake, class_code):
    """文本班级编号和带前导零的编号都按原值查询，数据库继续使用数字主键。"""
    from app.xdoj import endpoints as ep

    fake.classes = [{"id": 9031, "classId": class_code, "className": "测试一班", "ownerId": fake.teacher_id}]
    for student in fake.students:
        student["classId"] = class_code
    fake.contests[0]["classes"] = [{"id": 9031, "classId": class_code}]
    with db.session_scope() as s:
        source = m.DataSource(server_id=ctx.server_id, teacher_user_id=fake.teacher_id)
        s.add(source)
        s.flush()
        ctx.source_id = source.id
    rows = ep.list_classes(ctx.client)
    assert len(rows) == 1
    local_id = int(class_code) if class_code.isdigit() else 9031
    assert rows[0].class_id == local_id
    assert rows[0].roster_filter_id == class_code
    sync_meta.sync_metadata(ctx)
    sync_meta.sync_metadata(ctx)
    with db.session_scope() as s:
        assert s.get(m.XClass, (ctx.server_id, local_id)) is not None
        assert s.get(m.ClassSource, (ctx.server_id, local_id, ctx.source_id)) is not None
        enrollments = s.scalars(select(m.Enrollment).where(m.Enrollment.class_id == local_id)).all()
        assert len(enrollments) == 3
        assert all(row.status == "active" for row in enrollments)
        assert s.get(m.ContestClass, (ctx.server_id, 501, local_id)) is not None
        assert s.scalar(select(func.count()).select_from(m.User)) == 3


def test_unjudged_submission_waits_for_judging(ctx, fake, caplog):
    fake.submissions[537101].judges[-1].result = "未评判"
    sync_submissions.poll_new_submissions(ctx)
    with db.session_scope() as s:
        submission = s.get(m.Submission, (ctx.server_id, 537101))
        assert submission.judgement == "PENDING"
        pending = s.scalar(select(m.FetchTask).where(
            m.FetchTask.kind == "pending_judge", m.FetchTask.target_id == 537101))
        assert pending is not None
    assert not any("不认识的评测结果" in record.message for record in caplog.records)


def test_real_response_shapes_numeric_results_and_language_object(ctx, fake):
    """真实接口：评测记录只给数字结果、语言是对象。正确的提交不能被详情同步改成“结果未知”，语言要取名字。"""
    fake.numeric_results = True
    fake.language_object = True
    full_sync(ctx)
    with db.session_scope() as s:
        subs = s.scalars(select(m.Submission)).all()
        assert subs and all(len(x.language) <= 32 and x.language == "C" for x in subs)
        ac = [x for x in subs if x.judgement_text == "正确"]
        assert ac and all(x.judgement == "AC" for x in ac)
        assert "UNKNOWN" not in {x.judgement for x in subs}
        for x in ac:
            judge = s.scalar(select(m.Judge).where(m.Judge.submission_id == x.submission_id, m.Judge.is_latest.is_(True)))
            runs = s.scalars(select(m.JudgeRun).where(m.JudgeRun.judge_id == judge.judge_id)).all()
            assert runs and all(r.passed for r in runs)


def test_submission_cursor_is_per_teacher(ctx, fake, clock):
    """两位老师在同一台服务器上：一位把进度推到最后，另一位仍要从头取到自己的提交。"""
    from dataclasses import replace

    sync_submissions.poll_new_submissions(ctx)
    n = count(m.Submission)
    with db.session_scope() as s:
        s.execute(text("DELETE FROM xdoj_submissions"))
    other = replace(ctx, source_id=99)
    assert sync_submissions.poll_new_submissions(other) == n


def test_homework_assigned_by_text_class_code(ctx, fake):
    """作业里的“布置给哪些班”写的是文本 classId（不是数字 id），也要对上本地的班级。"""
    fake.classes = [{"id": 9031, "classId": "COURSE-2026", "className": "测试一班", "ownerId": fake.teacher_id}]
    for st in fake.students:
        st["classId"] = "COURSE-2026"
    fake.contests[0]["classes"] = ["COURSE-2026"]
    sync_meta.sync_metadata(ctx)
    with db.session_scope() as s:
        assert s.get(m.ContestClass, (1, 501, 9031)) is not None
        assert len(s.scalars(select(m.Enrollment).where(m.Enrollment.class_id == 9031)).all()) == 3
