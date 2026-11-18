"""把演示数据入库。

数据不是直接写表：先用内容和 fixture.json 搭一台假的 XDOJ，再让真实的同步程序去“同步”它，
然后按时间顺序跑标注、学情证据和几段对话。这样演示环境里看到的每一项都是真实流程产生的。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import select

from .. import config, db, llm, pipeline, quiz
from .. import models as m
from ..chat import workflow
from ..labeling.problems import reference_solution, save_problem_label
from ..sandbox import sandbox_available
from ..xdoj import sync_meta, sync_submissions
from ..xdoj.client import RateLimiter, XdojClient
from ..xdoj.daemon import Daemon
from ..xdoj.fake import FakeJudge, FakeRun, FakeSubmission, FakeXdoj
from ..xdoj.store import SyncContext
from . import content as c

FIXTURE = Path(__file__).with_name("fixture.json")
DEMO_SERVER_CODE = "demo"
FIRST_SUBMISSION_ID = 537001


def _fmt(t: dt.datetime) -> str:
    return t.astimezone(db.BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def build_fake(now: dt.datetime) -> tuple[FakeXdoj, dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fx = FakeXdoj(teacher_id=c.TEACHER, password="demo")
    fx.classes = [{"id": k["id"], "classId": k["id"], "className": k["name"], "ownerId": c.TEACHER} for k in c.CLASSES]
    for uid, name in [c.DEMO_STUDENT, *c.CLASSMATES]:
        fx.add_student(uid, name, 31)
    fx.add_student(*c.OTHER_CLASS_STUDENT, 32)
    for cid, title, start, end, classes, pids in c.CONTESTS:
        fx.contests.append({
            "id": cid, "title": title, "startTime": _fmt(now - dt.timedelta(minutes=start)),
            "endTime": _fmt(now + dt.timedelta(minutes=end)), "type": "练习",
            "classes": [{"id": x} for x in classes], "_problem_ids": list(pids)})
    testcase_ids: dict[int, list[int]] = {}
    next_tc = 1396
    for pid, p in sorted(c.PROBLEMS.items()):
        fx.problems.append({"id": pid, "title": p["title"], "categoryName": p["category"],
                            "tags": [{"name": t} for t in p["tags"]], "difficulty": "易",
                            "timeLimit": 1000, "memoryLimit": 65536})
        fx.problem_files[pid] = (p["text"].encode("utf-8"), "text/plain; charset=utf-8")
        testcase_ids[pid] = []
        for (data_in, _skill, _sit, _ord), want in zip(p["tests"], data["expected"][str(pid)]):
            fx.add_testcase(pid, next_tc, data_in, want, points=round(100 / len(p["tests"])))
            testcase_ids[pid].append(next_tc)
            next_tc += 1
    run_id = 2153001
    for i, sub in enumerate(data["submissions"]):
        sid = FIRST_SUBMISSION_ID + i
        when = now - dt.timedelta(minutes=sub["ago"])
        j = data["judges"][sub["source"]]
        runs = []
        for tid, r in zip(testcase_ids[sub["problem"]], j["runs"]):
            runs.append(FakeRun(run_id, tid, r["result"], r["time"], 1024, r["output"]))
            run_id += 1
        fx.submissions[sid] = FakeSubmission(
            id=sid, user_id=sub["user"], user_name=sub["name"], problem_id=sub["problem"],
            contest_id=sub["contest"], class_id=sub["class"], submit_time=_fmt(when),
            source=data["sources"][sub["source"]].encode("utf-8"),
            judges=[FakeJudge(id=sid + 500, result=j["result"], points=j["points"],
                              start=_fmt(when + dt.timedelta(seconds=1)), end=_fmt(when + dt.timedelta(seconds=2)),
                              compile_output=j["compile_output"], runs=runs)])
    return fx, {"testcase_ids": testcase_ids, "data": data}


def ensure_demo_server(s) -> m.Server:
    server = s.scalar(select(m.Server).where(m.Server.code == DEMO_SERVER_CODE))
    if server is None:
        server = m.Server(code=DEMO_SERVER_CODE, name="演示数据（不是真实的 XDOJ）", course="C",
                          base_url="http://demo.invalid/xdoj-ssm")
        s.add(server)
        s.flush()
        source = m.DataSource(server_id=server.id, teacher_user_id=c.TEACHER, from_env=False, enabled=False)
        s.add(source)
        s.flush()
        # 演示用的教师账号：用开发口令登录，名下是演示的两个班
        s.add(m.User(role="teacher", login_name=c.TEACHER, display_name="演示教师", data_source_id=source.id))
    return server


def seed(now: dt.datetime | None = None, with_story: bool = True, verify_quiz: bool = True) -> dict:
    now = now or db.now()
    db.create_all()
    with db.session_scope() as s:
        server_id = ensure_demo_server(s).id
        source_id = s.scalar(select(m.DataSource.id).where(m.DataSource.server_id == server_id))
        if s.scalar(select(m.Submission.submission_id).where(m.Submission.server_id == server_id).limit(1)):
            return {"skipped": "演示数据已经在库里了"}
    fx, extra = build_fake(now)

    # 1 走真实的同步流程（限速器用假时钟，不真的等）
    tick = {"t": 0.0}
    limiter = RateLimiter(0.5, clock=lambda: tick["t"], sleep=lambda sec: tick.__setitem__("t", tick["t"] + sec))
    client = XdojClient("http://demo.invalid/xdoj-ssm", fx.teacher_id, fx.password, limiter=limiter,
                        transport=fx.transport(), sleep=lambda sec: None)
    ctx = SyncContext(server_id=server_id, client=client, source_id=source_id, term="演示学期", clock=lambda: now)
    sync_meta.sync_metadata(ctx)
    sync_submissions.poll_new_submissions(ctx, backfill=True)
    Daemon([ctx]).drain()

    llm_was = llm._backend, config.DEEPSEEK_API_KEY
    llm.set_backend(None)
    config.DEEPSEEK_API_KEY = ""  # 入库过程不调用大模型，结果可重复
    quiz._rng.seed(7)
    try:
        with db.session_scope() as s:
            _seed_labels(s, server_id, extra)
            quiz.load_bank(s)
            if verify_quiz and sandbox_available():
                bad = [code for code, ok, _why in quiz.verify_bank(s) if not ok]
                assert not bad, f"小题没通过验证：{bad}"
            else:  # 没有 gcc：题库在提交前已经验证过，这里直接沿用
                for item in s.scalars(select(m.QuizItem)).all():
                    item.verified = True
        _run_timeline(server_id, now, with_story)
        from .. import graphmodel

        graphmodel.nightly()  # 图模型全图更新一次，教师端才有东西可看
    finally:
        llm.set_backend(llm_was[0])
        config.DEEPSEEK_API_KEY = llm_was[1]
        quiz._rng.seed()
    with db.session_scope() as s:
        return {
            "server_id": server_id,
            "students": len(c.CLASSMATES) + 2,
            "submissions": len(fx.submissions),
            "requests": client.request_count,
            "demo_student": c.DEMO_STUDENT[0],
            "evidence": len(s.scalars(select(m.SkillEvidence.id)).all()),
            "messages": len(s.scalars(select(m.ChatMessage.id)).all()),
        }


def _seed_labels(s, server_id: int, extra: dict) -> None:
    """演示环境没有大模型来标题目，这里把内容里写好的标注存进去（来源记为 seed）。"""
    for pid, p in c.PROBLEMS.items():
        tests = [{"id": tid, "skill": t[1], "situation": t[2], "ordinary": t[3]}
                 for tid, t in zip(extra["testcase_ids"][pid], p["tests"])]
        save_problem_label(s, server_id, pid, p["brief"], p["primary"], p["secondary"], tests, "seed")
        reference_solution(s, server_id, pid)  # 取同学的通过提交当参考
    by_source = {b["source"]: b for b in c.BUGGY.values() if b.get("label")}
    subs = s.scalars(select(m.Submission).where(m.Submission.server_id == server_id)).all()
    for sub in subs:
        snap = s.get(m.SourceSnapshot, (server_id, sub.submission_id))
        bug = by_source.get(snap.source_bytes.decode("utf-8")) if snap else None
        if bug is None or sub.judgement == "AC":
            continue
        error, skill, line, why = bug["label"]
        judge = s.scalar(select(m.Judge).where(m.Judge.server_id == server_id,
                                               m.Judge.submission_id == sub.submission_id, m.Judge.is_latest.is_(True)))
        failed = [r.testcase_id for r in s.scalars(select(m.JudgeRun).where(
            m.JudgeRun.server_id == server_id, m.JudgeRun.judge_id == judge.judge_id)).all() if not r.passed]
        s.add(m.SubmissionLabel(
            server_id=server_id, submission_id=sub.submission_id, error_code=error, skill_code=skill,
            role="primary", confidence=0.9, line=line, evidence=why, testcase_ids=failed, source="seed",
            library_version=config.ERROR_LIBRARY_VERSION, labeler_version=config.LABELER_VERSION,
            problem_label_version=1))


def _run_timeline(server_id: int, now: dt.datetime, with_story: bool) -> None:
    """按时间顺序：处理每条提交（标注、学情证据、通过后的小题），中间穿插演示学生的几段对话。"""
    uid = c.DEMO_STUDENT[0]
    events: list[tuple[dt.datetime, int, object]] = []
    with db.session_scope() as s:
        for sub in s.scalars(select(m.Submission).where(m.Submission.server_id == server_id)).all():
            events.append((sub.submit_time, 0, sub.submission_id))

    def say(minutes_ago: float, problem: int, text: str = "", action: str | None = None):
        def run(s, at):
            workflow.handle(s, uid, (server_id, problem), text, action, now=at)
        events.append((now - dt.timedelta(minutes=minutes_ago), 1, run))

    def answer_quiz(minutes_ago: float, confidence: str):
        def run(s, at):
            attempt = s.scalars(select(m.QuizAttempt).where(
                m.QuizAttempt.user_id == uid, m.QuizAttempt.state != "done").order_by(m.QuizAttempt.id.desc())).first()
            if attempt is None:
                return
            item = s.get(m.QuizItem, attempt.item_id)
            # 独立作答：判断对、选中出错的那一行（没错的题选第一行当依据）、有错的改对
            quiz.judge2(s, attempt, item.has_bug, confidence, 2600)
            start = item.start_line or 1
            quiz.locate(s, attempt, [start + (item.bug_line or 1) - 1])
            if attempt.state == "edit":
                quiz.submit_fix(s, attempt, item.standard_fix)
            attempt.finished_at = at
            s.flush()
            for ev in s.scalars(select(m.SkillEvidence).where(
                    m.SkillEvidence.source_key.in_([f"quiz:{attempt.id}", f"streak:{attempt.id}"]))).all():
                ev.ts = at
        events.append((now - dt.timedelta(minutes=minutes_ago), 1, run))

    if with_story:
        d = c.DAY
        # 九天前，「阶乘求和」：第一次卡在“估计结果范围”上，拿到了示例代码，之后还是没通过
        say(9 * d + 17, 301, "n 大的时候结果就不对，是哪里的问题？")
        say(9 * d + 14, 301, "不知道怎么改。")
        # 昨天，「最大差值」：第一次卡在“最值的初值”上，拿到示例代码后通过，顺手做了一道小题
        say(1 * d + 22, 410, "有几组过不了，我看不出哪里有问题。")
        say(1 * d + 18, 410, "还是不会，不知道该怎么写。")
        answer_quiz(1 * d - 3, "一半")
        # 今天，「成绩最高分」：同一个地方又错了，对话进行到一半
        say(5, 406, "有一组一直错，我觉得逻辑没问题，是不是比较那里写错了？")
        say(4, 406, "max 是 0 也没关系吧，反正后面会比较。")

    for at, kind, payload in sorted(events, key=lambda e: (e[0], e[1])):
        with db.session_scope() as s:
            if kind == 0:
                pipeline.process_submission(s, server_id, payload, now=at + dt.timedelta(seconds=30))
            else:
                payload(s, at)
    # 九天前那个卡点进了复习，现在已经到期：复习页和学情页“接下来做什么”里会出现
