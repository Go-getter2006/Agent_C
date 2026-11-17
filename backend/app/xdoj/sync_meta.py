"""元数据同步：班级 → 名单 → 练习 → 练习包含的题 → 题目和标签 → 测试点 → 题面。

每天凌晨跑一次，教师也可以手动“立即同步”。
比对哈希发现新题和改动：新题号就是新题；题面文件、测试点列表或测试点文件的哈希变了就是改动。
两种情况都重新抓取，并入队重新标注。
"""
from __future__ import annotations

import base64
import datetime as dt
import logging

from sqlalchemy import delete, select

from .. import accounts, db
from .. import models as m
from . import endpoints as ep
from .store import SyncContext, enqueue, get_time, set_time
from .util import detect_encoding, extract_problem_text, json_hash, sha256

log = logging.getLogger("xdoj.meta")

# 测试点文件每隔几天全部重新下载核对一次；平时只在测试点列表或题目信息变化时重抓。
FULL_ASSET_CHECK_DAYS = 7


def sync_classes_and_roster(ctx: SyncContext) -> None:
    classes = ep.list_classes(ctx.client)
    with db.session_scope() as s:
        for c in classes:
            row = s.get(m.XClass, (ctx.server_id, c.class_id))
            if row is None:
                s.add(m.XClass(server_id=ctx.server_id, class_id=c.class_id, class_name=c.class_name,
                               owner_id=c.owner_id, data_source_id=ctx.source_id, fetched_at=ctx.now()))
            else:
                row.class_name, row.owner_id, row.fetched_at = c.class_name, c.owner_id, ctx.now()
            if ctx.source_id is not None and s.get(m.ClassSource, (ctx.server_id, c.class_id, ctx.source_id)) is None:
                s.add(m.ClassSource(server_id=ctx.server_id, class_id=c.class_id, data_source_id=ctx.source_id))
    ctx.bump("classes", len(classes))

    seen: dict[str, int] = {}
    for c in classes:
        students = ep.list_students(ctx.client, c.class_id)
        with db.session_scope() as s:
            for st in students:
                seen[st.user_id] = c.class_id
                row = s.get(m.Student, st.user_id)
                if row is None:
                    s.add(m.Student(user_id=st.user_id, name=st.name, first_seen_at=ctx.now(), fetched_at=ctx.now()))
                else:
                    if st.name:
                        row.name = st.name
                    row.fetched_at = ctx.now()
                s.flush()
                accounts.ensure_student_user(s, st.user_id, st.name)
                _enroll(s, ctx, st.user_id, c.class_id)
        ctx.bump("students", len(students))

    # 这台服务器、这个数据源的班级里，名单上已经没有的学生：标为“已移出”，不删数据
    class_ids = [c.class_id for c in classes]
    if not class_ids:
        return
    with db.session_scope() as s:
        actives = s.scalars(select(m.Enrollment).where(
            m.Enrollment.server_id == ctx.server_id, m.Enrollment.status == "active",
            m.Enrollment.class_id.in_(class_ids))).all()
        for e in actives:
            if seen.get(e.user_id) != e.class_id:
                e.status = "moved" if e.user_id in seen else "removed"
                e.until = ctx.now()
                if e.user_id not in seen:
                    others = s.scalar(select(m.Enrollment).where(
                        m.Enrollment.user_id == e.user_id, m.Enrollment.status == "active",
                        m.Enrollment.id != e.id))
                    user = s.scalar(select(m.User).where(m.User.login_name == e.user_id))
                    if user is not None and others is None:
                        user.status = "removed"
                    ctx.bump("students_removed")


def _enroll(s, ctx: SyncContext, user_id: str, class_id: int) -> None:
    current = s.scalar(select(m.Enrollment).where(
        m.Enrollment.user_id == user_id, m.Enrollment.server_id == ctx.server_id,
        m.Enrollment.class_id == class_id, m.Enrollment.term == ctx.term))
    if current is None:
        s.add(m.Enrollment(user_id=user_id, server_id=ctx.server_id, class_id=class_id, term=ctx.term,
                           course=ctx.course, status="active", since=ctx.now()))
    elif current.status != "active":
        current.status, current.until = "active", None


def sync_contests(ctx: SyncContext) -> None:
    contests = ep.list_contests(ctx.client)
    for c in contests:
        problem_ids = c.problem_ids if c.problem_ids is not None else ep.contest_problem_ids(ctx.client, c.contest_id)
        with db.session_scope() as s:
            row = s.get(m.Contest, (ctx.server_id, c.contest_id))
            if row is None:
                row = m.Contest(server_id=ctx.server_id, contest_id=c.contest_id)
                s.add(row)
            row.title, row.start_time, row.end_time, row.type = c.title, c.start_time, c.end_time, c.type
            row.raw_json, row.fetched_at = c.raw, ctx.now()
            s.execute(delete(m.ContestClass).where(
                m.ContestClass.server_id == ctx.server_id, m.ContestClass.contest_id == c.contest_id))
            for cid in dict.fromkeys(c.class_ids):
                s.add(m.ContestClass(server_id=ctx.server_id, contest_id=c.contest_id, class_id=cid))
            s.execute(delete(m.ContestProblem).where(
                m.ContestProblem.server_id == ctx.server_id, m.ContestProblem.contest_id == c.contest_id))
            for seq, pid in enumerate(dict.fromkeys(problem_ids), start=1):
                s.add(m.ContestProblem(server_id=ctx.server_id, contest_id=c.contest_id, problem_id=pid, seq=seq))
    ctx.bump("contests", len(contests))


def sync_problems(ctx: SyncContext, max_requests: int | None = None) -> list[int]:
    """同步题目、标签、测试点和题面。返回新题或有改动的题号（已入队重新标注）。"""
    start_count = ctx.client.request_count
    problems = ep.list_problems(ctx.client)
    changed: list[int] = []
    with db.session_scope() as s:
        last_full = get_time(s, ctx.server_id, "last_full_asset_check_at")
    full_check = last_full is None or ctx.now() - last_full >= dt.timedelta(days=FULL_ASSET_CHECK_DAYS)

    for p in problems:
        if max_requests is not None and ctx.client.request_count - start_count >= max_requests:
            log.info("达到本次请求上限，剩下的题下次接着同步")
            full_check = False
            break
        meta_hash = json_hash(p.raw)
        with db.session_scope() as s:
            row = s.get(m.Problem, (ctx.server_id, p.problem_id))
            is_new = row is None
            if row is None:
                row = m.Problem(server_id=ctx.server_id, problem_id=p.problem_id)
                s.add(row)
            meta_changed = (not is_new) and row.meta_hash != meta_hash
            if meta_changed:
                s.add(m.Archive(server_id=ctx.server_id, kind="problem_meta", target_id=p.problem_id,
                                content_hash=row.meta_hash, payload=row.raw_json or {}))
            row.title, row.category, row.difficulty = p.title, p.category, p.difficulty
            row.time_limit, row.memory_limit = p.time_limit, p.memory_limit
            row.raw_json, row.meta_hash, row.fetched_at = p.raw, meta_hash, ctx.now()
            s.execute(delete(m.ProblemTag).where(
                m.ProblemTag.server_id == ctx.server_id, m.ProblemTag.problem_id == p.problem_id))
            for tag in dict.fromkeys(p.tags):
                s.add(m.ProblemTag(server_id=ctx.server_id, problem_id=p.problem_id, tag=tag[:64]))
            need_assets = is_new or meta_changed or not row.assets_fetched
        tc_changed = _sync_testcases(ctx, p.problem_id, force_files=need_assets or full_check)
        text_changed = _sync_problem_text(ctx, p.problem_id)
        with db.session_scope() as s:
            row = s.get(m.Problem, (ctx.server_id, p.problem_id))
            row.assets_fetched = True
            if is_new or tc_changed or text_changed:
                changed.append(p.problem_id)
                enqueue(s, ctx.server_id, "label_problem", p.problem_id, 2)
        ctx.bump("problems")
    if full_check:
        with db.session_scope() as s:
            set_time(s, ctx.server_id, "last_full_asset_check_at", ctx.now())
    ctx.bump("problems_changed", len(changed))
    return changed


def _sync_testcases(ctx: SyncContext, problem_id: int, force_files: bool) -> bool:
    rows = ep.list_testcases(ctx.client, problem_id)
    list_hash = json_hash([(r.testcase_id, r.description, r.points) for r in rows])
    with db.session_scope() as s:
        prob = s.get(m.Problem, (ctx.server_id, problem_id))
        list_changed = prob.testcase_list_hash != list_hash
        had_list = bool(prob.testcase_list_hash)
        prob.testcase_list_hash = list_hash
        existing = {t.testcase_id: t for t in s.scalars(select(m.Testcase).where(
            m.Testcase.server_id == ctx.server_id, m.Testcase.problem_id == problem_id)).all()}
        for seq, r in enumerate(rows, start=1):
            t = existing.get(r.testcase_id)
            if t is None:
                s.add(m.Testcase(server_id=ctx.server_id, testcase_id=r.testcase_id, problem_id=problem_id,
                                 seq=seq, description=r.description, points=r.points, fetched_at=ctx.now()))
            else:
                t.seq, t.description, t.points = seq, r.description, r.points
        missing = {r.testcase_id for r in rows
                   if r.testcase_id not in existing or existing[r.testcase_id].input_bytes is None}
    changed = list_changed and had_list
    for r in rows:
        if not (force_files or list_changed or r.testcase_id in missing):
            continue
        data_in = ep.testcase_input(ctx.client, r.testcase_id)
        data_out = ep.testcase_output(ctx.client, r.testcase_id)
        io_hash = sha256(data_in + b"\x00" + data_out)
        with db.session_scope() as s:
            t = s.get(m.Testcase, (ctx.server_id, r.testcase_id))
            if t.io_hash and t.io_hash != io_hash:
                changed = True
                s.add(m.Archive(
                    server_id=ctx.server_id, kind="testcase", target_id=r.testcase_id, content_hash=t.io_hash,
                    payload={"input_b64": base64.b64encode(t.input_bytes or b"").decode("ascii"),
                             "output_b64": base64.b64encode(t.output_bytes or b"").decode("ascii")}))
            t.input_bytes, t.input_encoding = data_in, detect_encoding(data_in)
            t.output_bytes, t.output_encoding = data_out, detect_encoding(data_out)
            t.io_hash, t.fetched_at = io_hash, ctx.now()
    return changed


def _sync_problem_text(ctx: SyncContext, problem_id: int) -> bool:
    data, content_type = ep.problem_text_file(ctx.client, problem_id)
    file_hash = sha256(data)
    with db.session_scope() as s:
        row = s.get(m.ProblemText, (ctx.server_id, problem_id))
        if row is not None and row.file_hash == file_hash:
            return False
        changed = row is not None
        if row is None:
            row = m.ProblemText(server_id=ctx.server_id, problem_id=problem_id)
            s.add(row)
        else:
            s.add(m.Archive(server_id=ctx.server_id, kind="problem_text", target_id=problem_id,
                            content_hash=row.file_hash,
                            payload={"content_type": row.content_type,
                                     "file_b64": base64.b64encode(row.file_bytes or b"").decode("ascii")}))
        text, status = extract_problem_text(content_type, data)
        row.content_type, row.file_bytes, row.file_hash = content_type[:64], data, file_hash
        row.extracted_text, row.extract_status, row.fetched_at = text, status, ctx.now()
        return changed


def sync_metadata(ctx: SyncContext, max_requests: int | None = None) -> dict:
    sync_classes_and_roster(ctx)
    sync_contests(ctx)
    sync_problems(ctx, max_requests=max_requests)
    with db.session_scope() as s:
        set_time(s, ctx.server_id, "last_meta_at", ctx.now())
    return dict(ctx.stats)
