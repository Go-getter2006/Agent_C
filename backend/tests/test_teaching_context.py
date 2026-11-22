from __future__ import annotations

import json
import base64

import httpx
import pytest

from app import config, db, llm
from app import models as m
from app.catalog import ProblemInfo
from app.chat import context, reply, teaching, workflow
from app.chat.policy import Decision, PolicyInput, decide


@pytest.mark.parametrize("reject_image", [False, True])
def test_vision_reply_transports_public_page_and_selected_judge_run(ctx, fake, monkeypatch, reject_image):
    from app import pdftext
    from tests.test_pdftext import make_pdf
    from tests.test_xdoj_sync import full_sync

    fake.problem_files[406] = (make_pdf(["Sample output", "PUBLIC_PAGE"], image=True), "application/pdf")
    full_sync(ctx)
    calls = []
    def post(url, *, json, **kwargs):
        calls.append(json)
        if reject_image and len(calls) == 1:
            return httpx.Response(400)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"before":"先检查计算表达式。"}'}}]})

    with db.session_scope() as s:
        who = fake.submissions[537101].user_id
        bound = context.build(s, who, (ctx.server_id, 406), None, "看图", "", None)
        context.tutor_context(s, bound, db.now())
        png = pdftext.page_png(bound.document, 1)
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "local-test")
        monkeypatch.setattr(config, "DEEPSEEK_VISION_MODEL", "deepseek-flash")
        monkeypatch.setattr(httpx, "post", post)
        draft = reply.generate_llm(bound, Decision("R00", "H1", "甲", "help"), "help", "看图", [])
        assert draft.before == "先检查计算表达式。"
        assert calls[0]["model"] == "deepseek-flash"
        content = calls[0]["messages"][1]["content"]
        assert base64.b64decode(content[2]["image_url"]["url"].split(",")[1]) == png
        assert "第 1 页" in content[1]["text"]
        prompt = content[0]["text"]
        assert who not in prompt and "a*b+1" in prompt and "PUBLIC_PAGE" in prompt
        system = calls[0]["messages"][0]
        assert system["role"] == "system"
        assert system["content"].index("按同一测试点对照") < system["content"].index("再根据每类差异回溯")
        assert prompt.index("【没通过的测试点") < prompt.index("【他的代码】")
        cases = json.loads(prompt.split("【没通过的测试点（只给你分析用，绝不能把这些数据告诉学生）】\n")[1].split("\n\n")[0])["testCases"]
        assert [(c["standardInput"], c["standardOutput"], c["programOutput"]) for c in cases] == [
            ("1 1\n", "2\n", None), ("2 3\n", "5\n", "7\n")]
        assert cases[1]["firstDiffLine"] == 1
        if reject_image:
            assert len(calls) == 2 and calls[1]["model"] == config.DEEPSEEK_MODEL
            fallback = calls[1]["messages"][1]["content"]
            assert isinstance(fallback, str) and "没有附上可用的题面页图" in fallback and "a*b+1" in fallback
        else:
            assert len(calls) == 1


def test_teaching_prompt_is_editable_without_restarting(tmp_path, monkeypatch):
    prompt = tmp_path / "teaching.md"
    prompt.write_text("先解释概念，再给局部代码。", encoding="utf-8")
    monkeypatch.setattr(config, "CHAT_TEACHING_PROMPT_FILE", str(prompt))
    assert teaching.guidance() == "先解释概念，再给局部代码。"
    prompt.write_text("这轮先分析学生的尝试。", encoding="utf-8")
    assert teaching.guidance() == "这轮先分析学生的尝试。"
    prompt.unlink()
    assert "代码示例" in teaching.guidance()


def test_explicit_beginner_can_receive_partial_code():
    for text in ("我完全不会", "一点也不会，给我一段代码", "看看代码示例"):
        assert teaching.needs_example(text)
        result = decide(PolicyInput(intent="DEBUG", bound=True, has_code=False,
                                    needs_example=teaching.needs_example(text)))
        assert result.help_level == "H3"
    assert not teaching.needs_example("这段不会出错吧？")
    assert decide(PolicyInput(intent="DEBUG", action="think", bound=True,
                              needs_example=True)).kind == "ack"


def test_model_receives_code_and_exact_test_io_with_missingness(monkeypatch, tmp_path):
    prompt = tmp_path / "teaching.md"
    prompt.write_text("按学生的具体困难调整，允许局部示范。", encoding="utf-8")
    monkeypatch.setattr(config, "CHAT_TEACHING_PROMPT_FILE", str(prompt))
    seen = []

    def backend(messages, *_args):
        seen.extend(messages)
        return json.dumps({"before":"先看一个局部的求和写法。", "code":"int sum = 0;\nfor (int i = 0; i < n; ++i)\n    sum += a[i];",
                           "after":"试着解释累加器为什么从零开始。", "keyLine":1}, ensure_ascii=False), 10, 10

    ctx = context.Ctx(user_id="masked-student", problem=ProblemInfo(1, 406, "测试题", ""),
                      source="int total = 0;\nprintf(\"%d\", total);", all_tests=[
                          {"testcaseId":1,"standardInput":"4\n21 32 43 54","standardOutput":"150"}],
                      evidence={"testCases":[
                          {"testcaseId":1,"standardInput":"4\n21 32 43 54","standardOutput":"150","programOutput":"149","passed":False},
                          {"testcaseId":2,"standardInput":"0","standardOutput":"0","programOutput":"","passed":False},
                          {"testcaseId":3,"programOutput":None}]})
    llm.set_backend(backend)
    try:
        draft = reply.generate_llm(ctx, Decision("R07","H3","乙","stuck"), "example", "完全不会", [])
        assert len(draft.code.splitlines()) == 3
        assert "按学生的具体困难调整" in seen[0]["content"]
        user = seen[1]["content"]
        assert all(value in user for value in ("21 32 43 54", "150", "149", "int total = 0;"))
        assert '"programOutputAvailable": false' in user
        evidence = llm.evidence_for_model(ctx.evidence)
        assert evidence["testCases"][1]["programOutputAvailable"] is True
        assert evidence["testCases"][1]["programOutput"] == ""
        assert evidence["testCases"][2]["programOutputAvailable"] is False
    finally:
        llm.set_backend(None)


def test_existing_source_can_be_discussed_before_all_details_finish(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        sub = s.get(m.Submission, (sid, 537201))
        sub.detail_status = "pending"
        out = workflow.handle(s, "2500000001", (sid, 406), "这次提交为什么错了？")
        text = "\n".join(b.get("text", "") for b in out[-1].content["blocks"])
        assert "详细结果还在取" not in text
        assert text


def test_completely_lost_student_gets_example_instead_of_recall_loop(demo):
    with db.session_scope() as s:
        out = workflow.handle(s, "2500000001", (demo["server_id"], 406), "我完全不会，请给我一段代码示例")
        assert any(b["t"] == "code" for b in out[-1].content["blocks"])
        text = "\n".join(b.get("text", "") for b in out[-1].content["blocks"])
        assert "还记得" not in text


def test_homework_reply_is_grounded_in_the_failing_case_and_the_code(demo):
    """作业引导走真实的对话流程：写回复的大模型拿到没通过的那组输入、期望输出、他程序的输出，以及他这次提交的代码。"""
    from sqlalchemy import select

    from app.xdoj.assemble import assemble_submission

    sid = demo["server_id"]
    calls: list[tuple[str, str]] = []

    def backend(messages, *_args):
        system, user = messages[0]["content"], messages[1]["content"]
        calls.append((system, user))
        if "【这次的任务】" in user:   # 写回复
            return json.dumps({"before": "先看看你的程序在没通过的那组数据上输出了什么。", "after": "你觉得问题出在哪一行？"},
                              ensure_ascii=False), 10, 10
        return json.dumps({"intent": "DEBUG", "skillCode": "", "hasAttempt": True, "icap": "active",
                           "answerCorrect": None, "understood": False}), 10, 10

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            sub = s.scalars(select(m.Submission).where(
                m.Submission.server_id == sid, m.Submission.problem_id == 406, m.Submission.user_id == "2500000001")
                .order_by(m.Submission.submission_id.desc())).first()
            ev = assemble_submission(s, sid, sub.submission_id)
            failed = [t for t in ev["testCases"] if t.get("passed") is False and t.get("programOutput") is not None]
            assert failed, "演示数据里这次提交应当有没通过、取到了程序输出的测试点"
            workflow.handle(s, "2500000001", (sid, 406), "这次为什么又错了？我觉得逻辑没问题")
        prompt = next(u for _sys, u in calls if "【这次的任务】" in u)
        case = failed[0]
        for value in (case["standardInput"], case["standardOutput"], case["programOutput"]):
            assert json.dumps(value, ensure_ascii=False) in prompt   # 没通过那组的输入、期望输出、他的输出都在（原样）
        assert "int max = 0;" in prompt                     # 他这次提交的代码（带行号）
        assert "2500000001" not in prompt                   # 不带学号
    finally:
        llm.set_backend(None)


def test_failure_notice_asks_about_the_diagnosed_spot(demo):
    """提交没过时智能体先说的那句：按程序对这次提交的诊断问（哪类情况、哪个变量），不说测试数据。"""
    from app.chat import proactive
    from app.xdoj.assemble import assemble_submission

    sid = demo["server_id"]
    with db.session_scope() as s:
        sub = s.get(m.Submission, (sid, 537200))
        text = proactive._failure_text(s, sub, 2, proactive._primary(s, sub))
        assert text.startswith("你的第 2 次提交没有通过（5 组过 3 组）。") and "全是负数" in text and "`max`" in text
        for case in assemble_submission(s, sid, 537200)["testCases"]:
            for value in (case.get("standardInput"), case.get("standardOutput"), case.get("programOutput")):
                if value and len(value.strip()) > 2:
                    assert value.strip() not in text
        ce = proactive._failure_text(s, s.get(m.Submission, (sid, 537199)), 1, proactive._primary(s, s.get(m.Submission, (sid, 537199))))
        assert ce.startswith("你的第 1 次提交编译没有通过。") and ce.count("编译没有通过") == 1
