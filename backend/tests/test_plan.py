"""还没做的题问“怎么做”：先给解题流程图，问卡在哪一步，再一步一步引导（app/chat/plan.py）。"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select

from app import automation, catalog, db, llm
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


STEPS = [{"title": "读入数据", "how": "读入 n 和 n 个数", "kp": "scanf 格式化输入"},
         {"title": "逐个比较", "how": "用一个变量记住到目前为止要找的那个数", "kp": "数组遍历"},
         {"title": "处理找不到", "how": "想好一个都没找到时输出什么", "kp": "顺序查找"},
         {"title": "输出结果", "how": "按格式输出结果", "kp": "printf 格式化输出"}]


def _same(steps: list[dict]) -> bool:
    """和大模型给的一样；知识点只留这门课里有的名称（没有的写成空）。"""
    from app.taxonomy import KNOWLEDGE_POINTS

    names = {v for k, v in KNOWLEDGE_POINTS.items() if k.startswith("C")}
    return [(x["title"], x["how"]) for x in steps] == [(x["title"], x["how"]) for x in STEPS] \
        and all(x["kp"] == (y["kp"] if y["kp"] in names else "") for x, y in zip(steps, STEPS))


def _backend(calls: list, correct: bool | None = False):
    import re

    def backend(messages, json_mode, max_tokens, temperature):
        system, user = messages[0]["content"], messages[1]["content"]
        if "解题过程分成" in system:
            calls.append("steps")
            return json.dumps({"steps": STEPS}, ensure_ascii=False), 50, 20
        if "引导他想清楚其中一步" in system:
            calls.append("guide")
            n = re.search(r"【现在引导第 (\d+) 步】", user).group(1)
            if "这是最后一次" in user:
                return json.dumps({"reply": f"第 {n} 步是这样：先定好变量，再一个一个比。"}, ensure_ascii=False), 50, 20
            return json.dumps({"reply": f"看第 {n} 步，关键是「数组遍历」。你打算用什么变量记住它？"}, ensure_ascii=False), 50, 20
        return json.dumps({"intent": "REPLY", "skillCode": "", "hasAttempt": True, "icap": "active",
                           "answerCorrect": correct, "understood": False}), 50, 20
    return backend


def test_unsubmitted_problem_gets_a_flowchart_then_step_by_step(demo):
    calls: list = []
    llm.set_backend(_backend(calls, correct=False))
    try:
        with db.session_scope() as s:
            key = _unsubmitted(s)
            out = workflow.handle(s, UID, key, "这个题我不会")
            reply = out[1]
            flow = _flow(reply)
            assert flow is not None and flow["pick"] and _same(flow["steps"])
            assert not any(b.get("t") == "code" for b in reply.content["blocks"])        # 不先给代码
            row = s.get(m.ProblemPlan, key)
            assert row is not None and _same(row.steps) and row.source == "llm" and row.version == plan.VERSION
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
            assert calls.count("steps") == 1
    finally:
        llm.set_backend(None)


def test_no_flowchart_without_model_made_steps(demo):
    """没有大模型（或给不出合格的步骤）：不出流程图，按平常的引导走；不拿技能拼步骤。"""
    with db.session_scope() as s:
        key = _unsubmitted(s)
        out = workflow.handle(s, UID, key, "这个题我不会")
        assert _flow(out[1]) is None and "plan" not in (out[1].content or {})
        assert s.get(m.ProblemPlan, key) is None


def test_idle_worker_does_not_spend_requests_on_unasked_plans(demo, monkeypatch):
    monkeypatch.setattr(automation, "_due", lambda name: name == "plans")
    calls: list = []
    monkeypatch.setattr(llm, "_backend", _backend(calls))
    assert automation.idle_work() == 0
    with db.session_scope() as s:
        assert not s.scalars(select(m.ProblemPlan)).all()
    assert calls == []


def test_invalid_steps_are_attempted_once_per_problem_statement(demo, monkeypatch):
    calls = []

    def broken(messages, *_a):
        calls.append(messages)
        return json.dumps({"steps": [{"title": "写代码", "how": "for (i = 0; i < n; i++)", "kp": ""}] * 3}), 10, 10

    monkeypatch.setattr(llm, "_backend", broken)
    with db.session_scope() as s:
        key = _unsubmitted(s)
        assert plan.steps_for(s, *key, "题面", None, "C", []) is None
        row = s.get(m.ProblemPlan, key)
        assert row.source == "invalid"
        row.created_at = db.now() - dt.timedelta(days=1)
    with db.session_scope() as s:
        assert plan.steps_for(s, *key, "题面", None, "C", []) is None
        assert len(calls) == 1
        assert plan.steps_for(s, *key, "更新后的题面", None, "C", []) is None
        assert len(calls) == 2


def test_all_steps_and_correct_answers_move_forward(demo):
    """“都不会”就从第一步开始；答上了换下一步。大模型分步和引导；分步按题存一份，别的学生不再生成。"""
    calls = []

    def backend(messages, json_mode, max_tokens, temperature):
        system, user = messages[0]["content"], messages[1]["content"]
        if "解题过程分成" in system:
            calls.append("steps")
            return json.dumps({"steps": [{"title": "读入数据", "how": "读入 n 和 n 个成绩", "kp": "scanf 格式化输入"},
                                         {"title": "找最大值", "how": "用一个变量记住目前最大的", "kp": "求最值"},
                                         {"title": "输出结果", "how": "按格式输出最大值", "kp": "printf 格式化输出"}]},
                              ensure_ascii=False), 50, 20
        if "引导他想清楚其中一步" in system:
            calls.append("guide")
            return json.dumps({"reply": "这一步关键是「求最值」。你打算用什么变量记住目前最大的？"},
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
