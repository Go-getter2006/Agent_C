from __future__ import annotations

import json

from app import config, db, llm
from app import models as m
from app.catalog import ProblemInfo
from app.chat import context, reply, teaching, workflow
from app.chat.policy import Decision, PolicyInput, decide


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
