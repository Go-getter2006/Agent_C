"""命令行。运行：python -m app.cli <命令>"""
from __future__ import annotations

import json
import logging

import typer
from sqlalchemy import func, select

from . import config, db, pipeline, quiz, sources
from . import models as m

cli = typer.Typer(add_completion=False, help="程序设计智能体：后台命令")


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx 的日志会带完整地址和参数，不要


def _contexts():
    ctxs = sources.build_contexts()
    if not ctxs:
        typer.echo("没有可用的数据源。先在 .env 里设置 XDOJ_BASE_URL、XDOJ_USER、XDOJ_PASS。")
        raise typer.Exit(1)
    return ctxs


@cli.command("init-db")
def init_db() -> None:
    """建表，并把小题题库写进库。"""
    db.create_all()
    with db.session_scope() as s:
        n = quiz.load_bank(s)
    typer.echo(f"表已就绪；新写入小题 {n} 道。数据库：{config.DATABASE_URL.split('@')[-1]}")


@cli.command("demo-seed")
def demo_seed() -> None:
    """把演示数据入库（虚构的一个班）。已经入过就跳过。"""
    from .demo.seed import seed

    typer.echo(json.dumps(seed(), ensure_ascii=False))


@cli.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """启动网页和接口。"""
    import uvicorn

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


@cli.command("sync-meta")
def sync_meta_cmd(max_requests: int = typer.Option(None, help="这次最多发多少个请求")) -> None:
    """手动同步一次元数据：班级、名单、作业、题目、测试点、题面。"""
    _logging()
    from .xdoj import sync_meta

    for ctx in _contexts():
        typer.echo(json.dumps(sync_meta.sync_metadata(ctx, max_requests=max_requests), ensure_ascii=False))


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

    db.create_all()
    Daemon(_contexts(), post_process=pipeline.local_task).run_forever()


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
            typer.echo(f"\n服务器 {server.id}（{server.name}）  进度 H={store.get_state(s, server.id, 'max_submission_id', '0')}"
                       f"  轮询档位 {tier[0]}/{tier[1]} 秒  最近请求成功率 {rate}")
            typer.echo(f"  队列积压 {json.dumps(store.queue_backlog(s, server.id), ensure_ascii=False)}")


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
