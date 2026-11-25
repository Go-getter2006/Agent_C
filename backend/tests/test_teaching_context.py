from __future__ import annotations

import json
import base64

import httpx
import pytest
from sqlalchemy.orm import Session

from app import config, db, llm
from app import models as m
from app.catalog import ProblemInfo
from app.chat import context, prompt_context, reply, teaching, workflow
from app.chat.policy import Decision, PolicyInput, decide


@pytest.mark.parametrize("display", ["figure", "page", "rejected"])
@pytest.mark.usefixtures("registered_source")
def test_vision_reply_reuses_sidebar_and_selected_judge_run(ctx, fake, monkeypatch, display):
    from app import pdftext
    from tests.test_pdftext import make_pdf
    from tests.test_xdoj_sync import full_sync

    fake.problem_files[406] = (make_pdf(["Sample output", "PUBLIC_PAGE"], image=True), "application/pdf")
    full_sync(ctx)
    calls = []
    def post(url, *, json, **kwargs):
        calls.append(json)
        if display == "rejected" and len(calls) == 1:
            return httpx.Response(400)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"before":"先检查计算表达式。"}'}}]})

    with db.session_scope() as s:
        who = fake.submissions[537101].user_id
        question = "这次为什么错？"
        bound = context.build(s, who, (ctx.server_id, 406), None, question, "", None)
        context.tutor_context(s, bound, db.now())
        if display == "figure":
            png = s.get(m.ProblemFigure, (ctx.server_id, 406, 1)).png
        else:
            bound.document.extracted_text += "\n[图999]"
            bound.statement = pdftext.usable_text(bound.document)
            png = pdftext.page_png(bound.document, 1)
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "local-test")
        monkeypatch.setattr(config, "DEEPSEEK_VISION_MODEL", "deepseek-flash")
        monkeypatch.setattr(httpx, "post", post)
        materials = prompt_context.build(s, bound)
        draft = reply.generate_llm(bound, Decision("R00", "H1", "甲", "help"), "help", question, [],
                                   materials=materials)
        assert draft.before == "先检查计算表达式。"
        assert calls[0]["model"] == "deepseek-flash"
        content = calls[0]["messages"][1]["content"]
        assert base64.b64decode(content[2]["image_url"]["url"].split(",")[1]) == png
        assert ("[图1]" if display == "figure" else "第 1 页") in content[1]["text"]
        assert content[0]["text"] == "【题目】\n" + bound.statement
        prompt = content[-1]["text"]
        assert who not in prompt and "a*b+1" in prompt and prompt.endswith(question)
        system = calls[0]["messages"][0]
        assert system["role"] == "system"
        assert system["content"].index("按同一测试点对照") < system["content"].index("再根据每类差异回溯")
        key = f"【第 {bound.attempt_no} 次提交】"
        assert prompt.index("【共用测试点】") < prompt.index(key)
        standard = json.loads(prompt.split("【共用测试点】\n")[1].split("\n\n")[0])
        expected = {c["testcaseId"]: c for c in standard}
        cases = json.loads(prompt.split(key + "\n")[1].split("\n\n")[0])["testCases"]
        assert [(expected[c["testcaseId"]]["standardInput"], expected[c["testcaseId"]]["standardOutput"],
                 c["programOutput"]) for c in cases] == [
            ("1 1\n", "2\n", None), ("2 3\n", "5\n", "7\n")]
        assert cases[1]["firstDiffLine"] == 1
        if display == "rejected":
            assert len(calls) == 2 and calls[1]["model"] == config.DEEPSEEK_MODEL
            fallback = calls[1]["messages"][1]["content"]
            assert isinstance(fallback, str) and "a*b+1" in fallback and fallback.endswith(question)
            assert "没有附上可用的题面图片" in calls[1]["messages"][0]["content"]
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


def test_old_model_settings_cannot_override_deepseek_flash(monkeypatch):
    import runpy

    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://compatible.example")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_VISION_MODEL", "deepseek-reasoner")
    settings = runpy.run_path(config.__file__)
    assert settings["DEEPSEEK_MODEL"] == settings["DEEPSEEK_VISION_MODEL"] == "deepseek-flash"
    monkeypatch.setenv("DEEPSEEK_VISION_MODEL", "off")
    assert runpy.run_path(config.__file__)["DEEPSEEK_VISION_MODEL"] == "off"


@pytest.mark.parametrize(("count", "output_length"), [(3, 10), (9, 10), (3, 6000)])
def test_stable_evidence_prefix_appends_new_attempts(monkeypatch, count, output_length):
    tests = [{"testcaseId": 11, "standardInput": "PRIVATE_INPUT", "standardOutput": "PRIVATE_EXPECTED"}]
    versions = {
        no: {"attemptNo": no, "sourceCode": f"int n = {no};",
             "testCases": [{**tests[0], "programOutput": f"ACTUAL_{no}" + "x" * output_length, "passed": False}]}
        for no in range(1, count + 1)
    }
    attempts = [
        m.Submission(server_id=1, submission_id=no, judgement="WA", detail_status="done")
        for no in versions
    ]

    def assemble(_session, _server_id, submission_id, *, include_problem_text):
        assert include_problem_text is False
        return versions[submission_id]

    monkeypatch.setattr(prompt_context, "assemble_submission", assemble)
    with Session() as session:
        bound = context.Ctx(user_id="masked-student", statement="公开题面", all_tests=tests,
                            attempts=attempts[:-1], attempt_no=count - 1, evidence=versions[count - 1])
        original = prompt_context.build(session, bound)
        bound.attempts, bound.attempt_no, bound.evidence = attempts, count, versions[count]
        updated = prompt_context.build(session, bound)

    assert original.document == updated.document
    assert updated.prefix.startswith(original.prefix + f"\n\n【第 {count} 次提交】")
    assert updated.prefix.count("PRIVATE_INPUT") == updated.prefix.count("PRIVATE_EXPECTED") == 1
    assert [row["programOutput"] for row in updated.tests] == [
        f"ACTUAL_{no}" + "x" * output_length for no in versions
    ]


def test_homework_stages_cover_logic_and_format_without_early_code(demo):
    from app import catalog
    from app.chat import talk

    uid, sid = "2500000001", demo["server_id"]
    with db.session_scope() as s:
        sub = s.get(m.Submission, (sid, 537201))
        pid = sub.problem_id
        if (rnd := workflow.open_round(s, uid)) is not None:
            workflow.close_round(s, rnd, "换题", db.now())
        quoted = catalog.submission_key(sid, sub.submission_id)
        messages = []

        def backend(turns, *_args):
            system, user = turns[0]["content"], turns[1]["content"]
            if "intent 只能取" in system:
                return '{"intent":"DEBUG","icap":"active"}', 10, 10
            messages.append(user)
            if "issues 只填写" in user:
                output = {"before": "几组的输出分隔有问题。", "issues": ["logic", "io"],
                          "steps": ["读入数据", "比较并更新", "输出结果"], "gap": 1,
                          "after": "什么时候初值会一直保留下来？"}
            elif "源码中最值得检查" in user:
                output = {"before": "先看更新结果之前的初始化。", "quote": {"from": 9, "to": 12, "mark": [10]}}
            else:
                output = {"before": "先把初值设成读到的第一个数。", "code": "int max = a[0];"}
            return json.dumps(output, ensure_ascii=False), 10, 10

        llm.set_backend(backend)
        try:
            ask = workflow.handle(s, uid, (sid, pid), "这题哪儿有问题？", submission_key=quoted)[-1]
            blocks = ask.content["blocks"]
            assert "处理逻辑、输入输出" in workflow.message_text(ask)
            assert next(b for b in blocks if b["t"] == "flow")["current"] == 1
            assert not any(b["t"] == "code" for b in blocks)
            assert "比较并更新（标出的步骤）" in talk.message_for_model(ask)
            locate = workflow.handle(s, uid, (sid, pid), action="hint", submission_key=quoted)[-1]
            assert next(b for b in locate.content["blocks"] if b["t"] == "code")["mark"] == [10]
            fix = workflow.handle(s, uid, (sid, pid), action="hint", submission_key=quoted)[-1]
            assert any(b.get("caption") == "示例" and b["lines"] == ["int max = a[0];"] for b in fix.content["blocks"])
            assert ["issues 只填写" in messages[0], "源码中最值得检查" in messages[1]] == [True, True]
        finally:
            llm.set_backend(None)


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
        assert '"programOutputAvailable":false' in user
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


def test_failure_notice_starts_with_the_kind_and_situation(demo):
    """提交没过时先给检查方向，隐藏输入和源码定位留在后台。"""
    from app.chat import proactive
    from app.xdoj.assemble import assemble_submission

    sid = demo["server_id"]
    with db.session_scope() as s:
        sub = s.get(m.Submission, (sid, 537200))
        blocks = proactive._failure_blocks(s, sub, 2)
        text = "\n".join(b["text"] for b in blocks if b["t"] == "p")
        assert text.startswith("你的第 2 次提交没有通过（5 组过 3 组）。") and "全是负数" in text and "边界条件" in text
        assert not any(b["t"] == "code" for b in blocks)
        for case in assemble_submission(s, sid, 537200)["testCases"]:
            for value in (case.get("standardInput"), case.get("standardOutput"), case.get("programOutput")):
                if value and len(value.strip()) > 2:
                    assert value.strip() not in text
        ce = proactive._failure_blocks(s, s.get(m.Submission, (sid, 537199)), 1)[0]["text"]
        assert ce.startswith("你的第 1 次提交编译没有通过。") and ce.count("编译没有通过") == 1
