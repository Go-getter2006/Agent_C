"""大模型每个调用点拿到了该拿的东西（核查记录：docs/大模型调用核查.md）。

- 读得到学生的问答：最近几条对话，带消息里的代码块（智能体摘出的他的代码）；他刚发的这条单独给、不重复；
- 读得到题面、测试点（没过的输入、期望输出、他的输出），只用来分析；
- 读得到他的情况：学情、以前卡过的地方、这道题每次提交、以前写错的样子；
- 发出去的内容不带学号和姓名。
"""
from __future__ import annotations

import json
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, llm, quizgen
from app import models as m
from app.assess import Material, dialogue
from app.chat import retro, workflow
from app.taxonomy import error_name

UID = "2500000001"
NAME = "林晓"


def _capture(seen: dict, replies: list[tuple[str, dict]]):
    """认出调用，记下发给它的内容，回一个合格的 JSON。"""
    def backend(messages, json_mode, max_tokens, temperature):
        system, user = messages[0]["content"], messages[1]["content"]
        user = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
        for key, out in replies:
            if key in system or (key.startswith("【") and key in user):
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
    for head in ("题目", "共用测试点", "第 3 次提交"):
        assert _section(prompt, head).strip()
    assert '"programOutput":"0\\n"' in prompt                 # 他的程序在没过的那组上的输出
    assert "他的学情" in prompt and "这道题他每次的提交" in prompt
    assert UID not in prompt and NAME not in prompt

    analyzed = seen["intent 只能取"][0]
    assert "max 是 0 也没关系吧" in _section(analyzed, "前面的对话")   # 理解“那我到底该怎么改”接的是哪件事
    assert UID not in analyzed and NAME not in analyzed


@pytest.mark.parametrize("scenario", ["complete", "missing_output", "changed_tests", "leak_old_output",
                                      "switch_focus", "switch_missing_output", "switch_first_fallback",
                                      "switch_luck", "different_input_claim", "switch_named_progress",
                                      "switch_named_wrong_line"])
def test_compares_same_testcases_across_two_pinned_submissions(demo, scenario):
    """复现左边追问第 1、2 次，右边同时已有第 3 次的情况。"""
    from app import catalog

    sid, pid = demo["server_id"], 9100001
    question = "那为啥第一次全错第二次对三个"
    first_question, follow_up = "这次怎么能全错呢", "那这次呢"
    switches_focus = scenario.startswith("switch_")
    missing_output = scenario.endswith("missing_output")
    names_baseline = scenario in ("switch_named_progress", "switch_named_wrong_line")
    progress_question = scenario == "switch_named_progress"
    if names_baseline:
        first_question = "第三次为什么通过了？"
        follow_up = "和第一次比 这次有没有好处" if progress_question else "和第一次比 为什么这次多过三组"
    seen = {}
    model_reply = "先看两版的输出格式改动，再对照还没有修好的条件判断。"
    if scenario == "leak_old_output":
        model_reply = "你第一次输出了 PRIVATE_OUTPUT_1_0。"
    elif scenario == "switch_luck":
        model_reply = "前一次的输入更难，第二次碰巧过了。"
    elif scenario == "different_input_claim":
        model_reply = "两次测试数据不一样，第二次更简单。"
    elif scenario == "switch_named_wrong_line":
        model_reply = "第 2 次把第 11 行的 || 改成了 &&，所以多过三组。"
    comparison_section = "【已确认的评测事实】"
    reply = (comparison_section, {"reply": model_reply})
    first_fallback = scenario == "switch_first_fallback"
    single_reply = (REPLY[0], {"before": ""}) if first_fallback else REPLY
    prior_reply = ("回头问了一个问题", {"reply": "第 3 次把条件中的 || 改成了 &&。"})
    llm.set_backend(_capture(seen, [ANALYZE, reply, single_reply, prior_reply]))
    try:
        with db.session_scope() as s:
            uid = UID
            cid = catalog.problem_contest(s, uid, sid, 406)
            tids = list(range(9100011, 9100016))
            s.add_all([
                m.Problem(server_id=sid, problem_id=pid, title="两次评测对照用题"),
                m.ContestProblem(server_id=sid, contest_id=cid, problem_id=pid),
            ])
            for index, tid in enumerate(tids):
                s.add(m.Testcase(server_id=sid, problem_id=pid, testcase_id=tid, seq=index + 1,
                                 input_bytes=f"PRIVATE_INPUT_{index}".encode("utf-8"), input_encoding="utf-8",
                                 output_bytes=f"PRIVATE_EXPECTED_{index}".encode("utf-8"), output_encoding="utf-8"))
            now = db.now()
            if scenario == "changed_tests":
                s.add(m.Archive(server_id=sid, kind="testcase", target_id=tids[0], archived_at=now))
            for no in (1, 2, 3):
                sub_id = 8000000 + no
                result = "WA" if no < 3 else "AC"
                source = f"int main() {{ return {no}; }}"
                if names_baseline:
                    lines = [f"/* 第 {line} 行 */" for line in range(1, 20)]
                    space = "" if no == 1 else " "
                    op = "&&" if no == 3 else "||"
                    lines[9] = f'printf("yes{space}1");'
                    lines[10] = f"else if (a + b > c {op} a + c > b {op} b + c > a) {{"
                    lines[13] = f'printf("yes{space}2");'
                    lines[15] = f'printf("no{space}3");'
                    source = "\n".join(lines)
                s.add_all([
                    m.Submission(server_id=sid, submission_id=sub_id, user_id=uid, problem_id=pid,
                                 submit_time=now, judgement=result, detail_status="done"),
                    m.SourceSnapshot(server_id=sid, submission_id=sub_id, encoding="utf-8",
                                     source_bytes=source.encode("utf-8")),
                    m.Judge(server_id=sid, submission_id=sub_id, judge_id=sub_id, is_latest=True, result=result),
                ])
                for index, tid in enumerate(tids):
                    passed = no == 3 or (no == 2 and index >= 2)
                    run_id = sub_id * 100 + index
                    s.add(m.JudgeRun(server_id=sid, judge_id=sub_id, run_id=run_id, testcase_id=tid,
                                     result="AC" if passed else "WA", passed=passed))
                    if not passed and not (missing_output and no == 1 and index == 0):
                        output = f"PRIVATE_OUTPUT_{no}_{index}"
                        if (first_fallback or names_baseline or scenario == "switch_luck") and no == 1 and index >= 2:
                            output = f"PRIVATE_ EXPECTED_{index}"
                        s.add(m.RunOutput(server_id=sid, run_id=run_id, encoding="utf-8",
                                          program_output_bytes=output.encode("utf-8")))
            s.flush()
            if not switches_focus:
                messages = workflow.handle(s, uid, (sid, pid), question,
                                           submission_key=catalog.submission_key(sid, 8000002), now=now)
                shown = workflow.message_text(messages[-1])
        if switches_focus:
            from app.web.app import create_app

            key = catalog.problem_key(sid, pid)
            with TestClient(create_app()) as client:
                assert client.post("/api/auth/login", json={"loginName": uid, "password": "test-pass"}).status_code == 200
                for no, text in [(3 if names_baseline else 1, first_question), (2, follow_up)]:
                    result = client.post(f"/api/threads/{key}/messages", json={
                        "text": text, "submissionKey": catalog.submission_key(sid, 8000000 + no),
                    })
                    assert result.status_code == 200, result.text
                    if names_baseline and no == 3:
                        assert "第 3 次把条件中的 || 改成了 &&" in result.text
                    if first_fallback and no == 1:
                        assert "3 组的输出只差空格或换行" in result.text
                        assert "2 组的输出内容也有差异" in result.text
                        assert "PRIVATE_" not in result.text
                tail = result.json()["messages"][-1]
                assert tail["role"] == "agent"
                shown = json.dumps(tail, ensure_ascii=False)
    finally:
        llm.set_backend(None)

    assert "PRIVATE_OUTPUT_" not in shown
    if scenario != "changed_tests":
        assert "两次使用同一组测试点" in shown
    if names_baseline:
        assert "第 1 次通过 0/5 组，第 2 次通过 3/5 组" in shown
        assert "改动涉及第 2 次提交的第 10、14、16 行" in shown
        assert "第 11 行" not in shown and "改成了 &&" not in shown
    if progress_question:
        assert "其中 3 组原来的实际输出与标准输出只差空白字符" in shown
        assert comparison_section not in seen
    elif missing_output or scenario == "changed_tests":
        assert "第 1 次通过 0/5 组，第 2 次通过 3/5 组" in shown
        assert ("测试点有更新记录" if scenario == "changed_tests" else "无法确定通过数变化的原因") in shown
        assert comparison_section not in seen
    else:
        prompt = seen[comparison_section][0]
        if switches_focus and not names_baseline:
            assert "上一条消息是第 1 次，本轮是第 2 次" in prompt
        if switches_focus:
            assert first_question in _section(prompt, "最近的对话")
            assert follow_up in _section(prompt, "他问的")
        assert json.loads(_section(prompt, "本轮相关提交次数")) == [1, 2]
        versions = [json.loads(_section(prompt, f"第 {n} 次提交")) for n in (1, 2, 3)]
        assert [v["judgementCode"] for v in versions] == ["WA", "WA", "AC"]
        if names_baseline:
            assert all("||" in row["sourceCode"] and "&&" not in row["sourceCode"] for row in versions[:2])
            assert "改成了 &&" not in _section(prompt, "最近的对话")
            delta = json.loads(_section(prompt, "源码改动（del 为原行，add 为新行）"))
            assert {row["n"] for row in delta} == {10, 14, 16}
        else:
            assert all(f"return {n};" in row["sourceCode"] for n, row in enumerate(versions, 1))
        standard = json.loads(_section(prompt, "共用测试点"))
        assert prompt.index("【共用测试点】") < prompt.index("【第 1 次提交】") < prompt.index("【第 2 次提交】")
        assert [row["testcaseId"] for row in standard] == tids
        assert standard[0]["standardInput"] == "PRIVATE_INPUT_0"
        assert standard[0]["standardOutput"] == "PRIVATE_EXPECTED_0"
        assert prompt.count("PRIVATE_INPUT_0") == 1
        assert all([row["testcaseId"] for row in v["testCases"]] == tids for v in versions)
        assert versions[0]["testCases"][0]["programOutput"] == "PRIVATE_OUTPUT_1_0"
        assert versions[1]["testCases"][0]["programOutput"] == "PRIVATE_OUTPUT_2_0"
        assert all(row["passed"] and row["programOutput"] is None for row in versions[1]["testCases"][2:])
        assert uid not in prompt and NAME not in prompt
        rejected = scenario in ("leak_old_output", "switch_luck", "different_input_claim", "switch_named_wrong_line")
        assert ("先对照两版改动" if rejected else "输出格式改动") in shown
        assert "有 3 组由未通过变为通过" in shown
        if scenario in ("switch_luck", "switch_first_fallback"):
            assert "其中 3 组原来的实际输出与标准输出只差空白字符" in shown
        assert "碰巧" not in shown and "测试数据不一样" not in shown
        assert len(seen[comparison_section]) == 1


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
