"""新提交、详情、评测未完成的跟进、回读。"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, update

from .. import config, db, pdftext
from .. import models as m
from ..chat import proactive
from ..taxonomy import normalize_judgement
from . import endpoints as ep
from .api import request_meta_sync_if_stale
from .client import SessionError, XdojError
from .store import SyncContext, enqueue, get_state, set_state, set_time
from .util import decode_bytes, detect_encoding

log = logging.getLogger("xdoj.submissions")


# ------------------------------------------------------------ 新提交
def poll_new_submissions(ctx: SyncContext, backfill: bool = False, max_requests: int | None = None) -> int:
    """从进度 H 往后取提交，直到取不满一页。每一批和 H 的前移在同一个事务里。"""
    rows_per_page = config.SYNC["page_rows"]
    total_new = 0
    start_count = ctx.client.request_count
    # 进度按数据源（老师）记：每位老师能看到的提交不一样，共用一个进度会漏掉后来的老师更早的提交
    key = f"max_submission_id:{ctx.source_id or 0}"
    while True:
        with db.session_scope() as s:
            cursor = int(get_state(s, ctx.server_id, key, "0") or 0)
        batch, _total = ep.list_submissions_after(ctx.client, cursor, rows_per_page)
        with db.session_scope() as s:
            live = not backfill and get_state(s, ctx.server_id, key + ":caught_up") == "1"
            for row in batch:
                total_new += _store_submission(s, ctx, row, backfill, live)
            if batch:
                set_state(s, ctx.server_id, key, batch[-1].submission_id)
                set_time(s, ctx.server_id, "last_new_submission_at", ctx.now())
            if len(batch) < rows_per_page:
                set_state(s, ctx.server_id, key + ":caught_up", "1")
            set_time(s, ctx.server_id, "last_poll_at", ctx.now())
        if len(batch) < rows_per_page:
            ctx.stats["poll_has_more"] = False
            break
        if max_requests is not None and ctx.client.request_count - start_count >= max_requests:
            ctx.stats["poll_has_more"] = True
            break
    ctx.bump("new_submissions", total_new)
    return total_new


def _store_submission(s, ctx: SyncContext, row: ep.SubmissionRow, backfill: bool, live: bool = False) -> int:
    if s.get(m.Submission, (ctx.server_id, row.submission_id)) is not None:
        return 0
    if row.user_id and s.get(m.Student, row.user_id) is None:
        # 名单里还没有这个学号：先建一行，并且马上自动同步一次名单（他多半是刚加进班的，要能登录）
        s.add(m.Student(user_id=row.user_id, name=row.user_name, first_seen_at=ctx.now(), fetched_at=ctx.now()))
        s.flush()
        if not backfill:
            request_meta_sync_if_stale(s, ctx.server_id)
    if row.contest_id and not backfill and s.get(m.Contest, (ctx.server_id, row.contest_id)) is None \
            and not get_state(s, ctx.server_id, f"meta_for_contest:{row.contest_id}"):
        # 交到了一个还不认识的作业里：多半是刚布置的，自动同步一次作业（每个作业只因此同步一次）
        set_state(s, ctx.server_id, f"meta_for_contest:{row.contest_id}", "1")
        request_meta_sync_if_stale(s, ctx.server_id)
    code = normalize_judgement(row.judgement_text)
    if code == "UNKNOWN" and row.judgement_text:
        log.warning("不认识的评测结果，已原样保存（提交 %s）", row.submission_id)
    sub = m.Submission(
        notice_eligible=(not backfill and (live or (row.submit_time is not None and
                         ctx.now() - row.submit_time <= dt.timedelta(minutes=config.PROACTIVE["recent_minutes"])))),
        server_id=ctx.server_id, submission_id=row.submission_id, user_id=row.user_id, problem_id=row.problem_id,
        contest_id=row.contest_id, class_id=row.class_id, submit_time=row.submit_time, language=row.language,
        judgement_text=row.judgement_text, judgement=code, points=row.points, fetched_at=ctx.now())
    s.add(sub)
    s.flush()
    proactive.on_failed_submission(s, sub, ctx.now())
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
    """取齐一条提交的详情。返回 done；还没评测完时抛 NotJudgedYet。每一步都按 XDOJ 的编号写入，可重复执行。

    源码最先取，而且不依赖评测记录（只要提交编号）：以前源码排在最后，评测记录一取不到，源码也跟着没有。
    每组测试点的输出单独取，一组出错不耽误别的组。有一步出错，拿到的先存下，最后把错误抛出去，任务过一会儿重试。
    """
    with db.session_scope() as s:
        sub = s.get(m.Submission, (ctx.server_id, submission_id))
        if sub is None:
            raise LookupError(f"库里没有提交 {submission_id}")
        problem_id = sub.problem_id
        list_code, list_text = sub.judgement, sub.judgement_text
    errors: list[XdojError] = []
    try:
        _fetch_source(ctx, submission_id)
    except SessionError:
        raise
    except XdojError as exc:
        log.warning("提交 %s 的源码没取到：%s", submission_id, exc)
        errors.append(exc)

    if source_first:
        with db.session_scope() as s:
            doc = s.get(m.ProblemText, (ctx.server_id, problem_id))
            missing_text = doc is None or not doc.file_bytes
            if not missing_text and (pdftext.needs_redo(doc.extract_status, doc.file_bytes, doc.content_type)
                                     or (doc.extract_status == "garbled" and "\uf0e8" in (doc.raw_text or ""))):
                if pdftext.store(s, doc, doc.content_type, doc.file_bytes):
                    enqueue(s, ctx.server_id, "label_problem", problem_id, 1, when=ctx.now())
        if missing_text:
            from .sync_meta import _sync_problem_text

            try:
                _sync_problem_text(ctx, problem_id)
            except SessionError:
                raise
            except XdojError as exc:
                errors.append(exc)

    judges = ep.list_judges(ctx.client, submission_id)
    if not judges:
        raise NotJudgedYet()
    latest = judges[-1]
    code = normalize_judgement(latest.result_text)
    judge_text_ok = code != "UNKNOWN"
    if not judge_text_ok and list_code not in ("UNKNOWN", ""):
        code = list_code  # 评测记录里的结果认不出（比如只有数字），用提交列表里的文字结果
    with db.session_scope() as s:
        if code not in ("UNKNOWN", "PENDING"):
            sub = s.get(m.Submission, (ctx.server_id, submission_id))
            sub.judgement = code
            proactive.on_failed_submission(s, sub, ctx.now())
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
        if not runs:
            raise NotJudgedYet()
    with db.session_scope() as s:
        for r in runs:
            row = s.get(m.JudgeRun, (ctx.server_id, r.run_id))
            if row is None:
                row = m.JudgeRun(server_id=ctx.server_id, run_id=r.run_id, judge_id=latest.judge_id,
                                 testcase_id=r.testcase_id)
                s.add(row)
            row.result_text, row.result = r.result_text, normalize_judgement(r.result_text)
            if row.result == "UNKNOWN" and code == "AC":
                row.result = "AC"  # 整条提交通过，每组测试点都通过
            row.passed = row.result == "AC"
            row.run_time_ms, row.memory_kb, row.fetched_at = r.run_time_ms, r.memory_kb, ctx.now()
            if row.result == "UNKNOWN" and r.result_text:
                log.warning("不认识的测试点结果，已原样保存（运行记录 %s）", r.run_id)

    for r in runs:
        if normalize_judgement(r.result_text) == "AC" or code == "AC":
            continue  # 通过的测试点不取输出
        with db.session_scope() as s:
            if s.get(m.RunOutput, (ctx.server_id, r.run_id)) is not None:
                continue
        try:
            out = ep.run_output(ctx.client, r.run_id)
        except SessionError:
            raise
        except XdojError as exc:
            errors.append(exc)
            continue
        with db.session_scope() as s:
            if out.program_output is not None:
                s.add(m.RunOutput(server_id=ctx.server_id, run_id=r.run_id,
                                  program_output_bytes=out.program_output.encode("utf-8"),
                                  encoding="utf-8", truncated=False, fetched_at=ctx.now()))
            _crosscheck_testcase(s, ctx, problem_id, r.testcase_id, out)

    if errors:
        raise errors[0]
    with db.session_scope() as s:
        sub = s.get(m.Submission, (ctx.server_id, submission_id))
        sub.detail_status = "done"
        sub.judgement = code
        if latest.result_text and judge_text_ok:
            sub.judgement_text = latest.result_text
        elif list_text:
            sub.judgement_text = list_text
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
    data_in = out.standard_input.encode("utf-8") if out.standard_input is not None else None
    data_out = out.standard_output.encode("utf-8") if out.standard_output is not None else None
    if t is None:
        s.add(m.Testcase(server_id=ctx.server_id, testcase_id=testcase_id, problem_id=problem_id, seq=0,
                         input_bytes=data_in, input_encoding="utf-8", output_bytes=data_out,
                         output_encoding="utf-8", fetched_at=ctx.now()))
        return
    if t.input_bytes is None and data_in is not None:
        t.input_bytes, t.input_encoding = data_in, "utf-8"
    if t.output_bytes is None and data_out is not None:
        t.output_bytes, t.output_encoding = data_out, "utf-8"

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
