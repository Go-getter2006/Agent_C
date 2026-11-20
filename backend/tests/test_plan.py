"""还没做的题问“怎么做”：先给解题流程图，问卡在哪一步，再一步一步引导（app/chat/plan.py）。"""
from __future__ import annotations

import json

from sqlalchemy import select

from app import catalog, db, llm
from app import models as m
from app.chat import plan, workflow

UID = "2500000001"


def _unsubmitted(s):
    """林晓看得到、还没交过的一道题。"""
    done = {(x.server_id, x.problem_id) for x in s.scalars(select(m.Submission).where(m.Submission.user_id == UID))}
    for key in catalog.visible_problems(s, UID, "C"):
        if key not in done:
            return key
    raise AssertionError("演示数据里没有还没交过的题")


def _flow(msg):
    return next((b for b in msg.content["blocks"] if b.get("t") == "flow"), None)


def test_unsubmitted_problem_gets_a_flowchart_then_step_by_step(demo):
    with db.session_scope() as s:
        key = _unsubmitted(s)
        out = workflow.handle(s, UID, key, "这个题我不会")
        reply = out[1]
        flow = _flow(reply)
        assert flow is not None and flow["pick"] and plan.MIN_STEPS <= len(flow["steps"]) <= plan.MAX_STEPS
        assert not any(b.get("t") == "code" for b in reply.content["blocks"])        # 不先给代码
        assert all(st["title"] for st in flow["steps"]) and flow["steps"][0]["title"] == "读入数据"
        row = s.get(m.ProblemPlan, key)
        assert row is not None and row.steps == flow["steps"] and row.version == plan.VERSION   # 按题存一份
        step = s.scalars(select(m.ChatStep).where(m.ChatStep.message_id == out[0].id)).one()
        assert (step.reply["flow"], step.help_level) == ("plan", "H0")

        # 选了卡在第 2 步：从第 2 步讲，流程图上第 2 步高亮
        out = workflow.handle(s, UID, key, "我卡在第 2 步")
        flow = _flow(out[1])
        assert flow["current"] == 1 and not flow["pick"] and "第 2 步" in workflow.message_text(out[1])
        # 没答上：讲得更具体一点，还在第 2 步
        out = workflow.handle(s, UID, key, "不知道")
        assert _flow(out[1])["current"] == 1
        # 第二次还没答上：把这一步讲清楚，只选了这一步，流程图结束
        out = workflow.handle(s, UID, key, "还是不会")
        flow = _flow(out[1])
        assert flow["current"] is None and 1 in flow["done"] and out[1].content["plan"]["ended"]
        assert "XDOJ 上交一次" in workflow.message_text(out[1])
        # 流程图结束以后就是平常的对话
        out = workflow.handle(s, UID, key, "好的")
        assert _flow(out[1]) is None


def test_all_steps_and_correct_answers_move_forward(demo):
    """“都不会”就从第一步开始；答上了换下一步。大模型分步和引导；分步按题存一份，别的学生不再生成。"""
    calls = []

    def backend(messages, json_mode, max_tokens, temperature):
        system, user = messages[0]["content"], messages[1]["content"]
        if "解题过程分成" in system:
            calls.append("steps")
            return json.dumps({"steps": [{"title": "读入数据", "how": "读入 n 和 n 个成绩", "kp": "scanf 格式化输入"},
                                         {"title": "找最大值", "how": "用一个变量记住目前最大的", "kp": "最值、求和、统计"},
                                         {"title": "输出结果", "how": "按格式输出最大值", "kp": "printf 格式化输出"}]},
                              ensure_ascii=False), 50, 20
        if "引导他想清楚其中一步" in system:
            calls.append("guide")
            return json.dumps({"reply": "这一步关键是「最值、求和、统计」。你打算用什么变量记住目前最大的？"},
                              ensure_ascii=False), 50, 20
        # 消息分析：他说的这一步答到点上了
        return json.dumps({"intent": "REPLY", "skillCode": "", "hasAttempt": True, "icap": "active",
                           "answerCorrect": True, "understood": False}), 50, 20

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            key = _unsubmitted(s)
            out = workflow.handle(s, UID, key, "这题怎么做")
            assert [x["title"] for x in _flow(out[1])["steps"]] == ["读入数据", "找最大值", "输出结果"]
            out = workflow.handle(s, UID, key, "都不会")
            assert _flow(out[1])["current"] == 0
            out = workflow.handle(s, UID, key, "用 scanf 先读 n 再循环读成绩")
            flow = _flow(out[1])
            assert flow["current"] == 1 and flow["done"] == [0] and workflow.message_text(out[1]).startswith("对，这一步就是这样")
            assert calls.count("steps") == 1
            # 别的学生问同一道题：用存好的步骤，不再生成
            other = "2500000002"
            if key in catalog.visible_problems(s, other, "C"):
                workflow.handle(s, other, key, "这题怎么做")
                assert calls.count("steps") == 1
    finally:
        llm.set_backend(None)


def test_picked_steps_are_recognised():
    assert plan.picked("我卡在第 2 步", 4) == [1]
    assert plan.picked("第1步和第3步不会", 4) == [0, 2]
    assert plan.picked("第二步", 4) == [1]
    assert plan.picked("我卡在第 1、2、3 步", 4) == [0, 1, 2]      # 学生端多选发的就是这种
    assert plan.picked("第二步和第三步", 4) == [1, 2] and plan.picked("2 和 4 步不会", 4) == [1, 3]
    assert plan.picked("我想一步一步来", 4) is None
    assert plan.picked("都不会", 3) == [0, 1, 2]
    assert plan.picked("第 9 步", 4) is None and plan.picked("我想想", 4) is None
    assert plan.wants_plan("这个题我不会", "STUCK") and plan.wants_plan("这题怎么做啊", "TASK")
    assert not plan.wants_plan("为什么输出不对", "DEBUG")
