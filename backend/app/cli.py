"""命令行。运行：python -m app.cli <命令>"""
from __future__ import annotations

import json
import logging

import typer
from sqlalchemy import func, select

from . import config, db, pipeline, quiz, sources
from . import models as m

cli = typer.Typer(add_completion=False, help="程序设计智能体：后台命令")


def _logging(quiet_requests: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx 的日志会带完整地址和参数，不要
    if quiet_requests:  # 网页进程里：每个 XDOJ 请求一行太吵，请求记录在 sync_request_log 表里
        logging.getLogger("xdoj.client").setLevel(logging.WARNING)


def _contexts():
    ctxs = sources.build_contexts()
    if not ctxs:
        typer.echo("没有可用的数据源。先在 .env 里设置 XDOJ_BASE_URL、XDOJ_USER、XDOJ_PASS。")
        raise typer.Exit(1)
    return ctxs


@cli.command("init-db")
def init_db() -> None:
    """建表或把库升到最新版本（以前用 create_all 建的库也能认出来再升级），并把小题题库写进库。"""
    from .cross import ensure_seed
    from .servers import ensure_known

    how = db.migrate()
    with db.session_scope() as s:
        n = quiz.load_bank(s)
        servers = ensure_known(s)
        ensure_seed(s)
    typer.echo(f"表已就绪（{how}）；新写入小题 {n} 道。数据库：{config.DATABASE_URL.split('@')[-1]}")
    for code, server in servers.items():
        typer.echo(f"服务器 {code}：{server.name}，{server.base_url or '地址还没有给出'}")


@cli.command("demo-seed")
def demo_seed() -> None:
    """把演示数据入库（虚构的一个班）。已经入过就跳过。"""
    from .demo.seed import seed

    typer.echo(json.dumps(seed(), ensure_ascii=False))


@cli.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """启动网页和接口（默认后台同步也在这个进程里跑，日志里能看到“名单：…”“作业：…”这样的进度）。"""
    import uvicorn

    _logging(quiet_requests=True)
    uvicorn.run("app.web.app:app", host=host, port=port, log_level="info")


@cli.command("login-test")
def login_test() -> None:
    """只测 XDOJ 登录。"""
    _logging()
    for ctx in _contexts():
        ctx.client.login()
        typer.echo(f"服务器 {ctx.server_id}：登录成功")


@cli.command("xdoj-probe")
def xdoj_probe() -> None:
    """核对接口字段：每个列表接口取第一行，只打印字段名和值的类型（不打印值）。第一次连真实服务器时用。"""
    _logging()
    from .xdoj.util import scrub

    for ctx in _contexts():
        c = ctx.client
        probes = [
            ("POST", "/class/list.do", {"page": 1, "rows": 1}),
            ("POST", "/contest/list.do", {"page": 1, "rows": 1}),
            ("POST", "/problem/list.do", {"page": 1, "rows": 1}),
            ("POST", "/submission/list.do", {"page": 1, "rows": 1, "sidx": "s.id", "sord": "desc"}),
        ]
        for method, path, params in probes:
            body = c.post_json(path, params) if method == "POST" else c.get_json(path, params)
            rows = body.get("data") if isinstance(body, dict) else body
            first = scrub(rows[0]) if rows else {}
            shape = {k: type(v).__name__ for k, v in first.items()}
            total = body.get("totalCount") if isinstance(body, dict) else None
            typer.echo(f"{path}  共 {total} 条\n  {json.dumps(shape, ensure_ascii=False)}")
        # 名单接口按哪个编号筛：班级列表的 classId 还是 id。两个都试，只打印条数和字段名
        body = c.post_json("/class/list.do", {"page": 1, "rows": 1})
        rows = body.get("data") if isinstance(body, dict) else body
        if rows:
            first = rows[0]
            for label in ("classId", "id"):
                fid = first.get(label)
                if fid is None:
                    continue
                filters = json.dumps({"groupOp": "AND", "rules": [{"field": "class_id", "op": "eq", "data": str(fid)}]})
                sb = c.post_json("/user/listStudents.do", {"page": 1, "rows": 1, "search": "true", "filters": filters})
                srows = sb.get("data") if isinstance(sb, dict) else sb
                shape = {k: type(v).__name__ for k, v in scrub(srows[0]).items()} if srows else {}
                total = sb.get("totalCount") if isinstance(sb, dict) else None
                typer.echo(f"/user/listStudents.do 按班级的 {label} 筛  共 {total} 人\n  {json.dumps(shape, ensure_ascii=False)}")
        _probe_judging(c)
        _probe_fetch(c)


def _probe_judging(c) -> None:
    """评测详情这一段：结果的取值（文字和数字的对应）、测试点运行记录的字段、getRunOutput 的字段名。

    只打印字段名、类型和评测结果的取值；测试点的输入输出、程序输出、源码一律不打印（只打印有没有、多长）。
    """
    from collections import Counter

    from .xdoj.util import pick, scrub

    def text_of(r: dict) -> str:   # 和 endpoints.py 取文字结果的顺序一样
        return str(pick(r, "judgementText", "judgementStr", "resultText", "resultStr", default="") or "")

    def rows_of(body):
        rows = body.get("data") if isinstance(body, dict) else body
        return rows if isinstance(rows, list) else []

    subs = rows_of(c.post_json("/submission/list.do", {"page": 1, "rows": 100, "sidx": "s.id", "sord": "desc"}))
    seen = Counter((text_of(r), r.get("judgement"), r.get("result")) for r in subs)
    typer.echo("提交的评测结果（文字, judgement, result）：")
    for (text, j, res), n in seen.most_common():
        typer.echo(f"  {text!r:12} judgement={j!r:6} result={res!r:6} × {n}")
    bad = next((r for r in subs if text_of(r) not in ("正确", "未评判", "")), None)
    if bad is None:
        typer.echo("最近 100 条里没有没通过的提交，评测详情跳过")
        return
    from .xdoj.endpoints import grid_json   # 子表：和网页一样带 _search、sidx、sord

    judges = rows_of(grid_json(c, "/submission/listJudge.do", {"submissionId": bad.get("id"), "page": 1, "rows": 20}))
    if not judges:
        typer.echo("/submission/listJudge.do  没有评测记录")
        return
    typer.echo(f"/submission/listJudge.do\n  {json.dumps({k: type(v).__name__ for k, v in scrub(judges[0]).items()}, ensure_ascii=False)}")
    runs = rows_of(c.post_json("/submission/listAllJudgeRun.do", {"judgeId": judges[0].get("id")}))
    if runs:
        typer.echo(f"/submission/listAllJudgeRun.do\n  {json.dumps({k: type(v).__name__ for k, v in scrub(runs[0]).items()}, ensure_ascii=False)}")
        values = Counter((r.get("result"), text_of(r)) for r in runs)
        typer.echo("  测试点运行结果（result, 文字）：" + "；".join(f"{a!r} {b!r} × {n}" for (a, b), n in values.items()))
        failed = next((r for r in runs if r.get("result") not in ("正确", 1)), runs[0])
        out = c.post_json("/submission/getRunOutput.do", {"id": failed.get("id")})
        body = out.get("data") if isinstance(out, dict) and isinstance(out.get("data"), dict) else out
        if isinstance(body, dict):
            # 只说有哪些字段、是不是空、有多长；内容是测试数据，不打印
            desc = {k: (f"{type(v).__name__}，长度 {len(v)}" if isinstance(v, str) else type(v).__name__) for k, v in body.items()}
            typer.echo(f"/submission/getRunOutput.do（只列字段，不打印内容）\n  {json.dumps(desc, ensure_ascii=False)}")


def _probe_fetch(c) -> None:
    """用同步程序自己的函数，把一道作业题、一条没通过的提交从头取一遍：题面、测试点、源码、评测记录、程序输出。

    每一步只打印成没成功、状态码、用了哪种参数写法、多长；题面只打印识别的结果和字数。内容一律不打印。
    同步哪一步取不到，这里就会在哪一步报错（和后台同步走的是同一段代码）。
    """
    from . import pdftext
    from .xdoj import endpoints as ep
    from .xdoj.client import SessionError, XdojError

    def step(name: str, fn):
        try:
            out = fn()
        except SessionError:
            raise
        except XdojError as exc:
            typer.echo(f"  ✗ {name}：{exc}")
            return None
        typer.echo(f"  ✓ {name}：{out[1] if isinstance(out, tuple) else out}")
        return out[0] if isinstance(out, tuple) else out

    typer.echo("\n从头取一道作业题（和后台同步同一段代码）：")
    contests = ep.list_contests(c)
    pid = next((p for ct in contests for p in (ct.problem_ids or [])), None)
    if pid is None and contests:   # 作业列表里没带题：从展开的子表取（也是一个 GET 子表）
        ids = step("作业里的题 /contest/problems.do", lambda: (r := ep.contest_problem_ids(c, contests[0].contest_id), f"{len(r)} 道"))
        pid = ids[0] if ids else None
    if pid is None:
        typer.echo("  没有作业里的题")
    else:
        typer.echo(f"  题号 {pid}")
        cases = step("测试点列表 /testcase/list.do", lambda: (r := ep.list_testcases(c, pid), f"{len(r)} 组"))
        if cases:
            step("测试点输入 downloadInputFile.do", lambda: (None, f"{len(ep.testcase_input(c, cases[0].testcase_id))} 字节"))
            step("测试点输出 downloadOutputFile.do", lambda: (None, f"{len(ep.testcase_output(c, cases[0].testcase_id))} 字节"))
        got = step("题面文件 viewTextFile.do", lambda: (r := ep.problem_text_file(c, pid), f"{r[1] or '没有类型'}，{len(r[0])} 字节"))
        if got:
            data, ctype = got
            raw, status, figures = pdftext.extract(ctype, data)
            typer.echo(f"    程序抽的文字：{pdftext.STATUS_NAMES.get(status, status)}，{len(raw)} 字，示意图 {len(figures)} 张"
                       + (f"（{pdftext.garbled(pdftext.FIG_MARK.sub('', raw))}）" if status == "garbled" else ""))
            from . import llm

            if llm.available() and status in ("layer", "garbled"):
                done = pdftext.tidy(raw, status)
                typer.echo(f"    大模型整理：{'没通过核对，沿用程序抽的文字' if done is None else pdftext.STATUS_NAMES[done[1]] + f'，{len(done[0])} 字'}")
            elif not llm.available():
                typer.echo("    大模型整理：没有配置大模型，先用程序抽的文字" + ("（乱码的不显示）" if status == "garbled" else ""))

    typer.echo("\n从头取一条没通过的提交：")
    body = c.post_json("/submission/list.do", {"page": 1, "rows": 100, "sidx": "s.id", "sord": "desc", "search": "false"})
    rows = body.get("data") if isinstance(body, dict) else body
    sub = next((r for r in rows or [] if str(r.get("judgementText") or r.get("judgementStr") or "") not in ("正确", "未评判", "")), None)
    if sub is None:
        typer.echo("  最近 100 条里没有没通过的提交")
    else:
        sid = sub.get("id")
        step("源码 downloadSource.do", lambda: (None, f"{len(ep.download_source(c, sid))} 字节"))
        judges = step("评测记录 /submission/listJudge.do", lambda: (r := ep.list_judges(c, sid), f"{len(r)} 条"))
        if judges:
            runs = step("测试点运行记录 listAllJudgeRun.do", lambda: (r := ep.list_judge_runs(c, judges[-1].judge_id), f"{len(r)} 组"))
            if runs:
                def output():
                    o = ep.run_output(c, runs[-1].run_id)
                    return None, "，".join(f"{k} {'有' if v is not None else '没有'}（{len(v or '')} 字）" for k, v in
                                          (("程序输出", o.program_output), ("标准输入", o.standard_input), ("标准输出", o.standard_output)))
                step("程序输出 getRunOutput.do", output)
    if c.grid_styles:
        typer.echo("\n子表用的参数写法：" + "，".join(f"{p} 第 {i + 1} 种" for p, i in c.grid_styles.items()))


@cli.command("sync-meta")
def sync_meta_cmd(max_requests: int = typer.Option(None, help="这次最多发多少个请求")) -> None:
    """手动同步一次元数据：班级、名单、作业、题目、测试点、题面。"""
    _logging()
    from .xdoj import sync_meta

    for ctx in _contexts():
        typer.echo(json.dumps(sync_meta.sync_metadata(ctx, max_requests=max_requests, inline_all=True), ensure_ascii=False))


@cli.command("backfill")
def backfill(max_requests: int = typer.Option(None, help="这次最多发多少个请求（控制一晚的请求量）")) -> None:
    """一次性回填历史提交。详情任务都放在 P2，不影响正在用的学生。"""
    _logging()
    from .xdoj import sync_submissions
    from .xdoj.daemon import Daemon

    for ctx in _contexts():
        n = sync_submissions.poll_new_submissions(ctx, backfill=True, max_requests=max_requests)
        typer.echo(f"服务器 {ctx.server_id}：新入库提交 {n} 条")
        daemon = Daemon([ctx], post_process=pipeline.local_task)
        start = ctx.client.request_count
        while daemon.step() is not None:
            if max_requests is not None and ctx.client.request_count - start >= max_requests:
                typer.echo("达到请求上限，剩下的下次接着做")
                break


@cli.command("daemon")
def daemon_cmd() -> None:
    """常驻主循环：同步、标注、学情证据。"""
    _logging()
    from .xdoj.daemon import Daemon

    from . import automation, graphmodel

    db.migrate()
    # 还没有数据源也能启动：教师在网页上登记后，一分钟内会被发现
    Daemon(sources.build_contexts(), post_process=pipeline.local_task, reload=sources.build_contexts,
           nightly=graphmodel.nightly, idle_work=automation.idle_work).run_forever()


@cli.command("assess-pending")
def assess_pending(limit: int = 50) -> None:
    """智能体评估刚结束、还没评的对话回合（平时后台空闲时会自动做）。"""
    _logging()
    from . import assess

    typer.echo(f"评了 {assess.run_pending(limit)} 个回合")


@cli.command("assemble")
def assemble(submission_id: int, server: int = typer.Option(None, help="服务器编号；只有一台时可以不填")) -> None:
    """打印一条提交组装好的证据 JSON（含测试点原始数据，只在后台看）。"""
    from .xdoj.assemble import assemble_submission

    with db.session_scope() as s:
        server_id = server or s.scalar(select(m.Server.id).order_by(m.Server.id))
        typer.echo(json.dumps(assemble_submission(s, server_id, submission_id), ensure_ascii=False, indent=2))


@cli.command("stats")
def stats() -> None:
    """各表行数、队列积压、轮询档位、最近请求成功率。"""
    from .xdoj import store
    from .xdoj.daemon import poll_tier

    with db.session_scope() as s:
        for name, model in [("班级", m.XClass), ("学生", m.Student), ("作业", m.Contest), ("题目", m.Problem),
                            ("测试点", m.Testcase), ("提交", m.Submission), ("评测记录", m.Judge),
                            ("源码", m.SourceSnapshot), ("错误标注", m.SubmissionLabel), ("题目标注", m.ProblemLabel),
                            ("对话消息", m.ChatMessage), ("学情证据", m.SkillEvidence)]:
            typer.echo(f"{name}\t{s.scalar(select(func.count()).select_from(model))}")
        for server in s.scalars(select(m.Server)).all():
            last_new = store.get_time(s, server.id, "last_new_submission_at")
            tier = poll_tier(db.now(), last_new, store.get_time(s, 0, "last_trigger_at"),
                             int(store.get_state(s, server.id, "poll_failures", "0") or 0))
            recent = s.scalars(select(m.RequestLog.ok).where(m.RequestLog.server_id == server.id)
                               .order_by(m.RequestLog.id.desc()).limit(200)).all()
            rate = f"{100 * sum(recent) / len(recent):.0f}%" if recent else "还没有请求"
            typer.echo(f"\n服务器 {server.id}（{server.name}）  进度 H={','.join(r.key.split(':')[-1] + '=' + r.value for r in s.scalars(select(m.SyncState).where(m.SyncState.server_id == server.id, m.SyncState.key.like('max_submission_id%'))).all()) or '0'}"
                       f"  轮询档位 {tier[0]}/{tier[1]} 秒  最近请求成功率 {rate}")
            typer.echo(f"  队列积压 {json.dumps(store.queue_backlog(s, server.id), ensure_ascii=False)}")


@cli.command("doctor")
def doctor() -> None:
    """同步出了什么问题：后台同步在不在跑、每个老师名下有几个班、每个班几个人、作业布置给了哪些班、队列、最近的错误。
    只读本地的库，不向 XDOJ 发请求，可以在网页运行时随时执行。"""
    from .xdoj import store
    from .xdoj.daemon import last_beat

    with db.session_scope() as s:
        beat, activity = last_beat(s)
        ago = f"{(db.now() - beat).total_seconds():.0f} 秒前" if beat else "从来没有"
        typer.echo(f"后台同步心跳：{ago}；正在：{activity or '（不知道）'}")
        for src in s.scalars(select(m.DataSource).order_by(m.DataSource.id)).all():
            server = s.get(m.Server, src.server_id)
            typer.echo(f"\n数据源 {src.id}：服务器 {server.code if server else src.server_id}，老师账号 {src.teacher_user_id}，"
                       f"{'启用' if src.enabled else '没启用'}，上次名单和作业 {store.get_time(s, src.server_id, 'last_meta_at') or '还没有'}")
            class_ids = [cs.class_id for cs in s.scalars(select(m.ClassSource).where(
                m.ClassSource.data_source_id == src.id)).all()]
            if not class_ids:
                typer.echo("  名下没有班级：班级列表还没同步下来，或者接口返回的字段和程序找的不一样（看日志里的 WARNING）")
            for cid in class_ids:
                cls = s.get(m.XClass, (src.server_id, cid))
                n = s.scalar(select(func.count()).select_from(m.Enrollment).where(
                    m.Enrollment.server_id == src.server_id, m.Enrollment.class_id == cid, m.Enrollment.status == "active"))
                hw = s.scalar(select(func.count()).select_from(m.ContestClass).where(
                    m.ContestClass.server_id == src.server_id, m.ContestClass.class_id == cid))
                typer.echo(f"  班级 {cid}（{cls.class_name if cls else ''}）：名单 {n} 人，布置给它的作业 {hw} 个"
                           + ("  ← 名单是空的" if not n else "") + ("  ← 没有作业对得上这个班" if not hw else ""))
        for server in s.scalars(select(m.Server).where(m.Server.code != "demo")).all():
            contests = s.scalar(select(func.count()).select_from(m.Contest).where(m.Contest.server_id == server.id))
            unmatched = s.scalar(select(func.count()).select_from(m.ContestClass).where(
                m.ContestClass.server_id == server.id,
                ~m.ContestClass.class_id.in_(select(m.XClass.class_id).where(m.XClass.server_id == server.id))))
            typer.echo(f"\n服务器 {server.code}：作业 {contests} 个，其中 {unmatched} 条“布置给某班”对不上已知的班级；"
                       f"题目 {s.scalar(select(func.count()).select_from(m.Problem).where(m.Problem.server_id == server.id))} 道；"
                       f"提交 {s.scalar(select(func.count()).select_from(m.Submission).where(m.Submission.server_id == server.id))} 条")
            typer.echo(f"  队列：{json.dumps(store.queue_backlog(s, server.id), ensure_ascii=False)}")
            failed = s.scalars(select(m.FetchTask).where(
                m.FetchTask.server_id == server.id, m.FetchTask.status.in_(("failed", "queued")),
                m.FetchTask.last_error.is_not(None), m.FetchTask.last_error != "")
                .order_by(m.FetchTask.id.desc()).limit(8)).all()
            for t in failed:
                state = "放弃了（每天夜里再试）" if t.status == "failed" else f"第 {t.attempts} 次没成功，等着重试"
                typer.echo(f"  出错的任务：{t.kind} {t.target_id}，{state}：{t.last_error[:120]}")
            _doctor_requests(s, server.id)
            _doctor_coverage(s, server.id)


def _doctor_requests(s, server_id: int) -> None:
    """最近 24 小时每个接口成功几次、失败的状态码。哪一步一直失败一眼就看得出来。"""
    import datetime as dt

    since = db.now() - dt.timedelta(hours=24)
    rows = s.execute(select(m.RequestLog.endpoint, m.RequestLog.ok, m.RequestLog.status_code, func.count())
                     .where(m.RequestLog.server_id == server_id, m.RequestLog.ts >= since)
                     .group_by(m.RequestLog.endpoint, m.RequestLog.ok, m.RequestLog.status_code)).all()
    if not rows:
        typer.echo("  最近 24 小时没有请求记录")
        return
    by: dict[str, dict] = {}
    for endpoint, ok, status, n in rows:
        d = by.setdefault(endpoint, {"ok": 0, "bad": {}})
        if ok:
            d["ok"] += n
        else:
            d["bad"][status] = d["bad"].get(status, 0) + n
    typer.echo("  最近 24 小时的请求：")
    for endpoint in sorted(by):
        d = by[endpoint]
        bad = "，".join(f"{code or '网络'}×{n}" for code, n in sorted(d["bad"].items()))
        typer.echo(f"    {endpoint:36} 成功 {d['ok']:>5}" + (f"  失败 {bad}" + ("  ← 一次都没成功" if not d["ok"] else "") if bad else ""))


def _doctor_coverage(s, server_id: int) -> None:
    """作业里的题有没有题面、测试点；提交有没有源码、详情。"""
    from . import pdftext

    pids = set(s.scalars(select(m.ContestProblem.problem_id).where(m.ContestProblem.server_id == server_id)).all())
    texts = {r.problem_id: r.extract_status for r in s.execute(
        select(m.ProblemText.problem_id, m.ProblemText.extract_status).where(m.ProblemText.server_id == server_id)).all()}
    with_cases = set(s.scalars(select(m.Testcase.problem_id).where(
        m.Testcase.server_id == server_id, m.Testcase.input_bytes.is_not(None))).all())
    usable = sum(1 for p in pids if texts.get(p) in pdftext.USABLE)
    statuses: dict[str, int] = {}
    for p in pids:
        st = texts.get(p, "还没下载")
        statuses[pdftext.STATUS_NAMES.get(st, st)] = statuses.get(pdftext.STATUS_NAMES.get(st, st), 0) + 1
    typer.echo(f"  作业里的题 {len(pids)} 道：有题面 {usable} 道，有测试点 {len(pids & with_cases)} 道；"
               f"题面：{json.dumps(statuses, ensure_ascii=False)}")
    total = s.scalar(select(func.count()).select_from(m.Submission).where(m.Submission.server_id == server_id)) or 0
    with_src = s.scalar(select(func.count()).select_from(m.SourceSnapshot).where(m.SourceSnapshot.server_id == server_id)) or 0
    done = s.scalar(select(func.count()).select_from(m.Submission).where(
        m.Submission.server_id == server_id, m.Submission.detail_status == "done")) or 0
    typer.echo(f"  提交 {total} 条：有源码 {with_src} 条，评测详情取齐 {done} 条")


@cli.command("graph-update")
def graph_update() -> None:
    """图模型全图更新一次，并补记预测（平时由主循环每天夜里做）。"""
    from . import graphmodel

    typer.echo(json.dumps(graphmodel.nightly(), ensure_ascii=False))


@cli.command("server-add")
def server_add(code: str, name: str, base_url: str, course: str = "C") -> None:
    """登记一台 XDOJ 服务器（教师登录时可以选它）。例：server-add c "C 语言" http://地址/xdoj-ssm"""
    from .servers import WrongServer

    db.create_all()
    try:
        with db.session_scope() as s:
            server = sources.ensure_server(s, code, name, base_url.rstrip("/"), course)
            typer.echo(f"服务器 {server.id}：{name}")
    except WrongServer as exc:
        typer.echo(f"没有登记：{exc}")
        raise typer.Exit(1)


@cli.command("admin-add")
def admin_add(login_name: str) -> None:
    """建一个管理员账号（开发用登录方式下，用统一口令登录）。"""
    db.create_all()
    with db.session_scope() as s:
        user = s.scalar(select(m.User).where(m.User.login_name == login_name))
        if user is None:
            s.add(m.User(role="admin", login_name=login_name, display_name="管理员"))
        else:
            user.role = "admin"
    typer.echo(f"管理员 {login_name} 已就绪")


@cli.command("quiz-verify")
def quiz_verify() -> None:
    """验证小题题库：有错的代码真的出错，没错的代码和标准改法真的通过。需要 gcc。"""
    with db.session_scope() as s:
        quiz.load_bank(s)
        bad = 0
        for code, ok, why in quiz.verify_bank(s):
            typer.echo(f"{'通过' if ok else '没通过'}\t{code}\t{'' if ok else why}")
            bad += 0 if ok else 1
    raise typer.Exit(1 if bad else 0)


@cli.command("label-pending")
def label_pending(limit: int = 200) -> None:
    """把还没标的题目和提交标一遍（需要配置大模型；规则能判的提交不需要）。"""
    _logging()
    from .labeling import labeler, problems

    with db.session_scope() as s:
        todo = s.execute(select(m.Problem.server_id, m.Problem.problem_id)).all()
    n = 0
    for server_id, problem_id in todo[:limit]:
        with db.session_scope() as s:
            if labeler.current_problem_label(s, server_id, problem_id) is None:
                n += 1 if problems.annotate_problem(s, server_id, problem_id) is not None else 0
    typer.echo(f"新标注题目 {n} 道")
    with db.session_scope() as s:
        subs = s.execute(select(m.Submission.server_id, m.Submission.submission_id).where(
            m.Submission.judgement.notin_(("AC", "PENDING")), m.Submission.detail_status == "done")
            .order_by(m.Submission.submission_id.desc()).limit(limit)).all()
    n = 0
    for server_id, submission_id in subs:
        with db.session_scope() as s:
            if not labeler.current_labels(s, server_id, submission_id):
                n += 1 if labeler.label_submission(s, server_id, submission_id) else 0
    typer.echo(f"新标注提交 {n} 条")


if __name__ == "__main__":
    cli()
