"""新提交、详情、评测未完成的跟进、回读。"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, update

from .. import config, db
from .. import models as m
from ..taxonomy import normalize_judgement
from . import endpoints as ep
from .store import SyncContext, enqueue, get_state, set_state, set_time
from .util import decode_bytes, detect_encoding

log = logging.getLogger("xdoj.submissions")


# ------------------------------------------------------------ 新提交
def poll_new_submissions(ctx: SyncContext, backfill: bool = False, max_requests: int | None = None) -> int:
    """从进度 H 往后取提交，直到取不满一页。每一批和 H 的前移在同一个事务里。"""
    rows_per_page = config.SYNC["page_rows"]
    total_new = 0
    start_count = ctx.client.request_count
    while True:
        with db.session_scope() as s:
            cursor = int(get_state(s, ctx.server_id, "max_submission_id", "0") or 0)
        batch, _total = ep.list_submissions_after(ctx.client, cursor, rows_per_page)
        with db.session_scope() as s:
            for row in batch:
                total_new += _store_submission(s, ctx, row, backfill)
            if batch:
                set_state(s, ctx.server_id, "max_submission_id", batch[-1].submission_id)
                set_time(s, ctx.server_id, "last_new_submission_at", ctx.now())
            set_time(s, ctx.server_id, "last_poll_at", ctx.now())
        if len(batch) < rows_per_page:
            break
        if max_requests is not None and ctx.client.request_count - start_count >= max_requests:
            break
    ctx.bump("new_submissions", total_new)
    return total_new


def _store_submission(s, ctx: SyncContext, row: ep.SubmissionRow, backfill: bool) -> int:
    if s.get(m.Submission, (ctx.server_id, row.submission_id)) is not None:
        return 0
    if row.user_id and s.get(m.Student, row.user_id) is None:
        # 名单里还没有这个学号：先建一行，下次名单同步时补全
        s.add(m.Student(user_id=row.user_id, name=row.user_name, first_seen_at=ctx.now(), fetched_at=ctx.now()))
        s.flush()
    code = normalize_judgement(row.judgement_text)
    if code == "UNKNOWN" and row.judgement_text:
        log.warning("不认识的评测结果，已原样保存（提交 %s）", row.submission_id)
    s.add(m.Submission(
        server_id=ctx.server_id, submission_id=row.submission_id, user_id=row.user_id, problem_id=row.problem_id,
        contest_id=row.contest_id, class_id=row.class_id, submit_time=row.submit_time, language=row.language,
        judgement_text=row.judgement_text, judgement=code, points=row.points, fetched_at=ctx.now()))
    s.flush()
    if code == "PENDING" or not row.judgement_text:
        enqueue(s, ctx.server_id, "pending_judge", row.submission_id, 1,
                delay_seconds=config.SYNC["pending_judge_delays"][0], when=ctx.now())
    else:
        enqueue(s, ctx.server_id, "detail", row.submission_id, _detail_priority(s, ctx, row, code, backfill),
                when=ctx.now())
    return 1


def _detail_priority(s, ctx: SyncContext, row: ep.SubmissionRow, code: str, backfill: bool) -> int:
    """P1：没通过的提交；之前失败过、这次第一次通过的提交。P2：其他通过的提交、回填的提交。"""
    if backfill:
        return 2
    if code != "AC":
        return 1
    earlier = s.scalars(select(m.Submission.judgement).where(
        m.Submission.server_id == ctx.server_id, m.Submission.user_id == row.user_id,
        m.Submission.problem_id == row.problem_id, m.Submission.submission_id < row.submission_id)).all()
    return 1 if earlier and "AC" not in earlier else 2


# ------------------------------------------------------------ 详情
class NotJudgedYet(Exception):
    pass


def fetch_detail(ctx: SyncContext, submission_id: int, source_first: bool = False) -> str:
    """取齐一条提交的详情。返回 done；还没评测完时抛 NotJudgedYet。每一步都按 XDOJ 的编号写入，可重复执行。"""
    with db.session_scope() as s:
        sub = s.get(m.Submission, (ctx.server_id, submission_id))
        if sub is None:
            raise LookupError(f"库里没有提交 {submission_id}")
        problem_id = sub.problem_id
    if source_first:  # P0：先拿到源码
        _fetch_source(ctx, submission_id)

    judges = ep.list_judges(ctx.client, submission_id)
    if not judges:
        raise NotJudgedYet()
    latest = judges[-1]
    code = normalize_judgement(latest.result_text)
    with db.session_scope() as s:
        s.execute(update(m.Judge).where(
            m.Judge.server_id == ctx.server_id, m.Judge.submission_id == submission_id).values(is_latest=False))
        for j in judges:
            row = s.get(m.Judge, (ctx.server_id, j.judge_id))
            if row is None:
                row = m.Judge(server_id=ctx.server_id, judge_id=j.judge_id, submission_id=submission_id)
                s.add(row)
            row.result_text, row.result, row.points = j.result_text, normalize_judgement(j.result_text), j.points
            row.judge_start, row.judge_end, row.compile_output = j.start, j.end, j.compile_output
            row.is_latest = j.judge_id == latest.judge_id
            row.fetched_at = ctx.now()
    if code == "PENDING":
        raise NotJudgedYet()

    runs: list[ep.RunRow] = []
    if code != "CE":
        runs = ep.list_judge_runs(ctx.client, latest.judge_id)
        if not runs and latest.compile_output:
            code = "CE"  # 有编译输出且没有测试点运行：编译错误
    with db.session_scope() as s:
        for r in runs:
            row = s.get(m.JudgeRun, (ctx.server_id, r.run_id))
            if row is None:
                row = m.JudgeRun(server_id=ctx.server_id, run_id=r.run_id, judge_id=latest.judge_id,
                                 testcase_id=r.testcase_id)
                s.add(row)
            row.result_text, row.result = r.result_text, normalize_judgement(r.result_text)
            row.passed = row.result == "AC"
            row.run_time_ms, row.memory_kb, row.fetched_at = r.run_time_ms, r.memory_kb, ctx.now()
            if row.result == "UNKNOWN" and r.result_text:
                log.warning("不认识的测试点结果，已原样保存（运行记录 %s）", r.run_id)

    for r in runs:
        if normalize_judgement(r.result_text) == "AC":
            continue  # 通过的测试点不取输出
        with db.session_scope() as s:
            if s.get(m.RunOutput, (ctx.server_id, r.run_id)) is not None:
                continue
        out = ep.run_output(ctx.client, r.run_id)
        with db.session_scope() as s:
            data = (out.program_output or "").encode("utf-8")
            s.add(m.RunOutput(server_id=ctx.server_id, run_id=r.run_id, program_output_bytes=data,
                              encoding="utf-8", truncated=False, fetched_at=ctx.now()))
            _crosscheck_testcase(s, ctx, problem_id, r.testcase_id, out)

    _fetch_source(ctx, submission_id)
    with db.session_scope() as s:
        sub = s.get(m.Submission, (ctx.server_id, submission_id))
        sub.detail_status = "done"
        sub.judgement = code
        if latest.result_text:
            sub.judgement_text = latest.result_text
        if latest.points is not None:
            sub.points = latest.points
        enqueue(s, ctx.server_id, "post_process", submission_id, 1 if code != "AC" else 2, when=ctx.now())
    ctx.bump("details")
    return "done"


def _fetch_source(ctx: SyncContext, submission_id: int) -> None:
    with db.session_scope() as s:
        if s.get(m.SourceSnapshot, (ctx.server_id, submission_id)) is not None:
            return
    data = ep.download_source(ctx.client, submission_id)
    with db.session_scope() as s:
        if s.get(m.SourceSnapshot, (ctx.server_id, submission_id)) is None:
            s.add(m.SourceSnapshot(server_id=ctx.server_id, submission_id=submission_id, source_bytes=data,
                                   encoding=detect_encoding(data), fetched_at=ctx.now()))


def _crosscheck_testcase(s, ctx: SyncContext, problem_id: int, testcase_id: int, out: ep.RunOutputRow) -> None:
    """getRunOutput 顺带返回测试点的输入和标准输出：库里没有就补上；已有的核对，不一致时告警并安排重抓。"""
    if out.standard_input is None and out.standard_output is None:
        return
    t = s.get(m.Testcase, (ctx.server_id, testcase_id))
    data_in = (out.standard_input or "").encode("utf-8")
    data_out = (out.standard_output or "").encode("utf-8")
    if t is None:
        s.add(m.Testcase(server_id=ctx.server_id, testcase_id=testcase_id, problem_id=problem_id, seq=0,
                         input_bytes=data_in, input_encoding="utf-8", output_bytes=data_out,
                         output_encoding="utf-8", fetched_at=ctx.now()))
        return
    if t.input_bytes is None:
        t.input_bytes, t.input_encoding = data_in, "utf-8"
        t.output_bytes, t.output_encoding = data_out, "utf-8"
        return

    def same(stored: bytes | None, enc: str, fresh: str | None) -> bool:
        text, ok = decode_bytes(stored, enc)
        if not ok or text is None or fresh is None:
            return True
        return text.replace("\r\n", "\n").rstrip() == fresh.replace("\r\n", "\n").rstrip()

    if not (same(t.input_bytes, t.input_encoding, out.standard_input)
            and same(t.output_bytes, t.output_encoding, out.standard_output)):
        log.warning("测试点 %s 的内容和库里的不一致，已安排重新抓取这道题", testcase_id)
        prob = s.get(m.Problem, (ctx.server_id, problem_id))
        if prob is not None:
            prob.assets_fetched = False
            prob.testcase_list_hash = ""


# ------------------------------------------------------------ 回读
def enqueue_recheck(ctx: SyncContext) -> int:
    """最近 24 小时内的提交，重新看一遍评测记录（发现重判）。"""
    since = ctx.now() - dt.timedelta(hours=config.SYNC["recheck_window_hours"])
    with db.session_scope() as s:
        ids = s.scalars(select(m.Submission.submission_id).where(
            m.Submission.server_id == ctx.server_id, m.Submission.submit_time >= since)).all()
        for sid in ids:
            enqueue(s, ctx.server_id, "recheck", sid, 3, when=ctx.now())
        set_time(s, ctx.server_id, "last_recheck_at", ctx.now())
    return len(ids)


def recheck_submission(ctx: SyncContext, submission_id: int) -> bool:
    """出现新的评测记录（重判），或上次还没评测完：按详情流程再取一遍。返回是否有变化。"""
    judges = ep.list_judges(ctx.client, submission_id)
    with db.session_scope() as s:
        known = set(s.scalars(select(m.Judge.judge_id).where(
            m.Judge.server_id == ctx.server_id, m.Judge.submission_id == submission_id)).all())
        sub = s.get(m.Submission, (ctx.server_id, submission_id))
        pending = sub is not None and sub.detail_status != "done"
    fresh = {j.judge_id for j in judges} - known
    if not fresh and not pending:
        return False
    with db.session_scope() as s:
        enqueue(s, ctx.server_id, "detail", submission_id, 1, when=ctx.now())
    return True
