"""xdoj-probe 的评测详情部分：只打印字段名、类型和评测结果的取值，不打印测试数据和程序输出。"""
from __future__ import annotations

from app.cli import _probe_fetch, _probe_judging


def test_probe_prints_shapes_not_data(ctx, capsys):
    ctx.client.login()
    _probe_judging(ctx.client)
    out = capsys.readouterr().out
    assert "答案错误" in out and "/submission/listAllJudgeRun.do" in out
    assert "getRunOutput" in out and "standardInput" in out and "programOutput" in out and "长度" in out
    # 测试点 1397 的输入是 “2 3”、期望输出 5、程序输出 7：一个都不能出现
    assert "2 3" not in out and '"5' not in out and '"7' not in out
    assert "2500000001" not in out and "学生甲" not in out


def test_probe_fetches_like_the_sync_and_says_where_it_breaks(ctx, fake, capsys):
    """xdoj-probe 用同步程序自己的函数把题面、测试点、源码、评测记录、程序输出各取一遍；只打印成没成功和长度。"""
    ctx.client.login()
    _probe_fetch(ctx.client)
    out = capsys.readouterr().out
    for step in ("测试点列表", "测试点输入", "题面文件", "源码", "评测记录", "测试点运行记录", "程序输出"):
        assert f"✓ {step}" in out, step
    assert "识别结果：文字文件" in out and "✗" not in out
    assert "2 3" not in out and "两个整数" not in out and "scanf" not in out    # 不打印内容
    fake.fail_paths["/testcase/list.do"] = 400
    _probe_fetch(ctx.client)
    out = capsys.readouterr().out
    assert "✗ 测试点列表" in out and "400" in out and "✓ 源码" in out
