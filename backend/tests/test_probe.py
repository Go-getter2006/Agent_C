"""xdoj-probe 的评测详情部分：只打印字段名、类型和评测结果的取值，不打印测试数据和程序输出。"""
from __future__ import annotations

from app.cli import _probe_judging


def test_probe_prints_shapes_not_data(ctx, capsys):
    ctx.client.login()
    _probe_judging(ctx.client)
    out = capsys.readouterr().out
    assert "答案错误" in out and "/submission/listAllJudgeRun.do" in out
    assert "getRunOutput" in out and "standardInput" in out and "programOutput" in out and "长度" in out
    # 测试点 1397 的输入是 “2 3”、期望输出 5、程序输出 7：一个都不能出现
    assert "2 3" not in out and '"5' not in out and '"7' not in out
    assert "2500000001" not in out and "学生甲" not in out
