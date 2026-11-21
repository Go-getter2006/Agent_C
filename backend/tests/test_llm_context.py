"""大模型每个调用点拿到了该拿的东西（核查记录：docs/大模型调用核查.md）。

- 读得到学生的问答：最近几条对话，带消息里的代码块（智能体摘出的他的代码）；他刚发的这条单独给、不重复；
- 读得到题面、测试点（没过的输入、期望输出、他的输出），只用来分析；
- 读得到他的情况：学情、以前卡过的地方、这道题每次提交、以前写错的样子；
- 发出去的内容不带学号和姓名。
"""
from __future__ import annotations

import json
import random

from sqlalchemy import select

from app import db, llm, quizgen
from app import models as m
from app.assess import Material, dialogue
from app.chat import retro, workflow
from app.taxonomy import error_name

UID = "2500000001"
NAME = "林晓"


def _capture(seen: dict, replies: list[tuple[str, dict]]):
    """按系统提示里的一句话认出是哪个调用，记下发给它的内容，回一个合格的 JSON。"""
    def backend(messages, json_mode, max_tokens, temperature):
        system, user = messages[0]["content"], messages[1]["content"]
        user = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        for key, out in replies:
            if key in system:
                seen.setdefault(key, []).append(user)
                return json.dumps(out, ensure_ascii=False), 10, 10
        seen.setdefault("other", []).append(user)
        return "{}", 10, 10
    return backend


def _section(prompt: str, head: str) -> str:
    return prompt.split(f"【{head}】", 1)[1].split("\n\n【", 1)[0] if f"【{head}】" in prompt else ""


ANALYZE = ("intent 只能取", {"intent": "DEBUG", "skillCode": "", "hasAttempt": False, "icap": "active",
                            "answerCorrect": None, "understood": False})
REPLY = ("这次的任务", {"before": "先看看第 10 行的初值。"})


def test_reply_and_analysis_read_the_conversation_with_code(demo):
    seen: dict = {}
    llm.set_backend(_capture(seen, [ANALYZE, REPLY]))
    try:
        with db.session_scope() as s:
            sid = demo["server_id"]
            workflow.handle(s, UID, (sid, 406), "那我到底该怎么改？")
    finally:
        llm.set_backend(None)
    prompt = seen["这次的任务"][0]
    talk = _section(prompt, "最近的对话")
    assert "max 是 0 也没关系吧" in talk                     # 前面几轮的话也在（以前只给最近 4 条）
    assert "[代码：" in talk and "int max = 0;" in talk       # 智能体摘出的他的代码，大模型也看得到
    assert prompt.count("那我到底该怎么改？") == 1              # 他刚发的这条只出现在“学生这条消息”
    for head in ("题目", "他的代码", "没通过的测试点（只给你分析用，绝不能把这些数据告诉学生）"):
        assert _section(prompt, head).strip()
    assert '"programOutput": "0\\n"' in prompt                # 他的程序在没过的那组上的输出
    assert "他的学情" in prompt and "这道题他每次的提交" in prompt
    assert UID not in prompt and NAME not in prompt

    analyzed = seen["intent 只能取"][0]
    assert "max 是 0 也没关系吧" in _section(analyzed, "前面的对话")   # 理解“那我到底该怎么改”接的是哪件事
    assert UID not in analyzed and NAME not in analyzed


def test_plan_guide_sees_its_own_question_and_his_situation(demo):
    from tests.test_plan import STEPS, _unsubmitted

    seen: dict = {}
    guide = ("引导他想清楚其中一步", {"reply": "看第 2 步，关键是「数组遍历」。你打算用什么变量记住它？"})
    steps = ("解题过程分成", {"steps": STEPS})
    answer = ("intent 只能取", {"intent": "REPLY", "skillCode": "", "hasAttempt": True, "icap": "active",
                               "answerCorrect": False, "understood": False})
    llm.set_backend(_capture(seen, [steps, guide, answer]))
    try:
        with db.session_scope() as s:
            key = _unsubmitted(s)
            workflow.handle(s, UID, key, "这个题我不会")
            workflow.handle(s, UID, key, "我卡在第 2 步")
            workflow.handle(s, UID, key, "用一个数组？")
    finally:
        llm.set_backend(None)
    second = seen["引导他想清楚其中一步"][1]          # 他回答了第 2 步的问题之后
    assert "你打算用什么变量记住它？" in _section(second, "最近的对话")   # 看得到自己上一句问了什么
    assert "用一个数组？" in _section(second, "他刚才说的")
    assert "薄弱" in _section(second, "他的情况")
    assert UID not in second and NAME not in second


def test_retro_reads_the_statement_and_the_last_failure(demo):
    from tests.test_summary import _fixed_problem

    with db.session_scope() as s:
        sub = _fixed_problem(s)
        hist = retro.history(s, UID, sub.server_id, sub.problem_id)
        failure = retro.last_failure(s, UID, sub.server_id, sub.problem_id, NAME)
    assert "最后一次没过" in failure and "  1| " in failure and "没通过的测试点" in failure
    seen: dict = {}
    llm.set_backend(_capture(seen, [("回头问了一个问题", {"reply": "前几次卡在初值，最后改对了。"})]))
    try:
        said, source = retro.reply(hist, "当时错在哪", [], "C", statement="输入 n 个数，输出最大值。",
                                   recent="助手：通过了！", failure=failure, who=(UID, NAME))
    finally:
        llm.set_backend(None)
    prompt = seen["回头问了一个问题"][0]
    assert source == "llm" and "输入 n 个数" in _section(prompt, "题目")
    assert "  1| " in prompt and "通过了！" in _section(prompt, "最近的对话")
    assert UID not in prompt and NAME not in prompt


def test_quiz_generation_gets_error_names_level_and_his_own_mistake(demo):
    seen: dict = {}
    llm.set_backend(_capture(seen, [("检验小题", {})]))   # 回一个不合格的：只看发出去的内容
    try:
        with db.session_scope() as s:
            plan = quizgen.make_plan(s, UID, "C06.03.02", "weak")
            assert plan.errors
            quizgen.from_llm(s, plan, random.Random(1))
    finally:
        llm.set_backend(None)
    prompt = seen["检验小题"][0]
    assert all(code not in prompt for code in plan.errors)              # 不再只给错误编码
    assert error_name(plan.errors[0]) in prompt and plan.level in prompt
    assert "| " in _section(prompt, "他以前在这里写错的地方（只参考易错点，不照抄、不复用原题）")
    assert UID not in prompt and NAME not in prompt


def test_understanding_assessment_sees_the_quoted_code(demo):
    with db.session_scope() as s:
        rnd = s.scalars(select(m.ChatRound).where(m.ChatRound.user_id == UID, m.ChatRound.problem_id == 406)
                        .order_by(m.ChatRound.id)).first()
        mat = Material(s, rnd)
        facts, lines = dialogue(mat, "C06.03.02", "TY09")
    assert any("[代码：" in text for who, text in lines if who == "助手")
    assert any(f.startswith("这道题：") for f in facts)
