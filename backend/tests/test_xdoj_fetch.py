"""题面、测试点、源码、程序输出取不到的那次问题：

- 子表（作业里的题、测试点、评测记录）要照网页发 _search、sidx、sord、nd；
- 某个接口拒绝请求（400 错误页、不是 JSON）只让这个任务失败、过一会儿重试，不能当成掉线、让整个同步停下来；
- 题面和测试点、源码和评测记录互不依赖；
- 失败的任务每天自动重新入队。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app import db
from app import models as m
from app.xdoj import endpoints as ep
from app.xdoj import store, sync_meta
from app.xdoj.client import ResponseError, SessionError
from app.xdoj.daemon import Daemon


def _tasks(kind: str) -> list[m.FetchTask]:
    with db.session_scope() as s:
        rows = s.scalars(select(m.FetchTask).where(m.FetchTask.kind == kind)).all()
        s.expunge_all()
        return rows


def test_grid_requests_carry_the_page_parameters(ctx, fake):
    """和网页里的 jqGrid 一样：子表带 _search、page、rows、sidx、sord、nd，并且是 AJAX 请求。"""
    ep.list_testcases(ctx.client, 406)
    ep.list_judges(ctx.client, 537101)
    for path in ("/testcase/list.do", "/submission/listJudge.do"):
        args = fake.last_args[path]
        assert {"_search", "page", "rows", "sidx", "sord", "nd"} <= set(args), path
        assert args["_search"] == "false" and args["__ajax"] == "XMLHttpRequest"
    ep.download_source(ctx.client, 537101)
    assert fake.last_args["/submission/downloadSource.do"]["__ajax"] == ""    # 下载文件是直接打开链接
    ep.list_classes(ctx.client)
    assert fake.last_args["/class/list.do"]["search"] == "false"             # 顶层表格的开关叫 search


def test_old_style_request_is_what_the_server_rejects(ctx, fake):
    """以前评测记录只带页码：真实服务器（假服务器照它）回 400 错误页。"""
    ctx.client.login()
    with pytest.raises(Exception) as err:
        ctx.client.get_json("/submission/listJudge.do", {"submissionId": 537101, "page": 1, "rows": 20})
    assert not isinstance(err.value, SessionError)      # 是这个接口拒绝了，不是掉线
    assert "400" in str(err.value)


def test_grid_uses_verified_parameters_without_guessing(ctx, fake):
    fake.grid_reject_sidx["/submission/listJudge.do"] = "id"
    judges = ep.list_judges(ctx.client, 537101)
    assert [j.judge_id for j in judges] == [537601]
    assert fake.last_args["/submission/listJudge.do"]["sidx"] == ""
    assert fake.last_args["/submission/listJudge.do"]["_search"] == "false"
    assert "search" not in fake.last_args["/submission/listJudge.do"]
    n = len(fake.requests)
    ep.list_judges(ctx.client, 537101)
    assert len(fake.requests) == n + 1


def test_rejected_endpoint_does_not_stop_the_sync(ctx, fake):
    """测试点列表和评测记录都被拒绝：同步不停；题面、源码照样取到；任务记下错误、过一会儿重试。"""
    fake.fail_paths["/testcase/list.do"] = 400
    fake.fail_paths["/submission/listJudge.do"] = 400
    sync_meta.sync_metadata(ctx)
    daemon = Daemon([ctx])
    daemon.drain()                                              # 以前这里抛会话错误，整个后台同步停下来
    with db.session_scope() as s:
        text = s.get(m.ProblemText, (ctx.server_id, 406))
        assert text is not None and text.extracted_text.startswith("输入两个整数")   # 题面不受测试点影响
        assert not s.get(m.Problem, (ctx.server_id, 406)).assets_fetched            # 测试点没取到，不算下载过
        assert s.get(m.SourceSnapshot, (ctx.server_id, 537101)) is not None           # 源码不受评测记录影响
        assert s.get(m.Submission, (ctx.server_id, 537101)).detail_status != "done"
    assets = [t for t in _tasks("problem_assets") if t.target_id == 406]
    assert assets and "400" in assets[-1].last_error and assets[-1].status == "queued"   # 退避后重试，没有放弃
    details = [t for t in _tasks("detail") if t.target_id == 537101]
    assert details and "400" in details[-1].last_error

    # XDOJ 那边好了：等到重试的时候自己补上
    fake.fail_paths.clear()
    with db.session_scope() as s:
        for t in s.scalars(select(m.FetchTask).where(m.FetchTask.status == "queued")).all():
            t.next_try_at = ctx.now()
    daemon.drain()
    with db.session_scope() as s:
        assert s.get(m.Problem, (ctx.server_id, 406)).assets_fetched
        assert s.get(m.Submission, (ctx.server_id, 537101)).detail_status == "done"
        outs = s.scalars(select(m.RunOutput).where(m.RunOutput.server_id == ctx.server_id)).all()
        assert outs                                              # 没通过的那组的程序输出


def test_html_instead_of_json_is_not_a_logout(ctx, fake):
    """200 但不是 JSON（也不是登录页）：报这个接口回的不对，不重新登录、不当成会话失效。"""
    ctx.client.login()
    logins = fake.login_count
    fake.fail_paths["/testcase/list.do"] = 200
    with pytest.raises(ResponseError):
        ep.list_testcases(ctx.client, 406)
    assert fake.login_count == logins


def test_login_page_still_means_logged_out(ctx, fake):
    ctx.client.login()
    fake.expire_sessions()
    assert [t.testcase_id for t in ep.list_testcases(ctx.client, 406)] == [1396, 1397]   # 重新登录后照常


def test_failed_tasks_are_requeued(ctx, fake):
    fake.fail_paths["/testcase/list.do"] = 404
    sync_meta.sync_metadata(ctx)
    with db.session_scope() as s:
        for t in s.scalars(select(m.FetchTask).where(m.FetchTask.kind == "problem_assets")).all():
            t.status = "failed"   # 退避次数用完
    fake.fail_paths.clear()
    with db.session_scope() as s:
        assert store.requeue_failed(s, ctx.server_id, at=ctx.now()) == 2       # 两道题都没取齐：重新入队（启动时和每天夜里）
    Daemon([ctx]).drain()
    with db.session_scope() as s:
        assert all(s.get(m.Problem, (ctx.server_id, pid)).assets_fetched for pid in (406, 407))


def test_doctor_shows_which_step_is_stuck(ctx, fake):
    """doctor 按接口列出成功几次、失败的状态码，题面、测试点、源码各有多少。只读本地的库。"""
    from typer.testing import CliRunner

    from app.cli import cli

    fake.fail_paths["/testcase/list.do"] = 400
    sync_meta.sync_metadata(ctx)
    Daemon([ctx]).drain()
    out = CliRunner().invoke(cli, ["doctor"]).output
    line = next(x for x in out.splitlines() if x.strip().startswith("/testcase/list.do"))
    assert "400×" in line and "一次都没成功" in line
    assert "作业里的题 2 道：有题面 2 道" in out     # 题面不受测试点列表影响
    assert "提交 4 条：有源码 4 条" in out and "等着重试" in out
