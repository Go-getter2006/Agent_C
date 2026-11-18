"""AI 出题：规划、用学生自己的代码变异出题、自造检查数据、个性化模板、大模型出题。都要在沙箱里验证。"""
from __future__ import annotations

import json
import random
import shutil

import pytest
from sqlalchemy import select

from app import db, llm, quiz, quizgen
from app import models as m
from app.xdoj.util import decode_bytes

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="没有 gcc")
UID = "2500000001"


def plan_for(s, skill="C06.03.02", **kw):
    p = quizgen.make_plan(s, UID, skill, "after_pass", origin=None, rng=random.Random(1))
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_make_inputs_are_ours_and_valid(demo):
    sid = demo["server_id"]
    with db.session_scope() as s:
        tests = {" ".join(decode_bytes(t.input_bytes, t.input_encoding)[0].split())
                 for t in s.scalars(select(m.Testcase).where(m.Testcase.problem_id == 410)).all()}
        ref = s.scalar(select(m.ReferenceSolution).where(m.ReferenceSolution.problem_id == 410)).source_code
        checks = quizgen.make_inputs(s, sid, 410, ref, random.Random(3))
        assert len(checks) >= 4
        for c in checks:
            assert " ".join(c["input"].split()) not in tests, "不能和测试点一样"
            assert c["expected"] and c["show"] and len(c["input"]) <= 160
        assert any(int(x) < 0 for c in checks for x in c["input"].split()[1:]), "要有全是负数这类容易出错的数据"


@pytest.mark.parametrize("quiz_type,has_bug", [("JUDGE", False), ("JUDGE", True), ("FIX", True), ("FIX", False)])
def test_own_code_quiz(demo, quiz_type, has_bug):
    with db.session_scope() as s:
        plan = plan_for(s, quiz_type=quiz_type, has_bug=has_bug, origin=(demo["server_id"], 410))
        item = quizgen.from_own_code(s, plan, random.Random(5))
        if item is None and quiz_type == "FIX" and not has_bug:
            pytest.skip("这段代码里没找到改了还对的写法")
        assert item is not None and item.verified and item.source == "own_code" and item.owner_user_id == UID
        assert item.has_bug == has_bug and item.quiz_type == quiz_type
        assert item.source_problem_id == 410 and item.start_line > 1 and len(item.shown_code.split("\n")) <= 10
        assert "最大差值" in item.caption and len(item.stem) <= 40
        ok, why = quiz.verify_item(item)
        assert ok, why
        if has_bug:
            assert item.standard_fix and item.bug_line and "原来是" in item.explanation
            r = quiz.run_checks(item, item.shown_code)
            assert r["status"] == "fail" and "应该是" in r["message"]
        else:
            assert item.explanation.startswith("还对")
            assert quiz.run_checks(item, item.shown_code)["status"] == "pass"
        if quiz_type == "FIX" and has_bug:
            assert len(item.choices) >= 3 and quiz.run_checks(item, quiz.apply_choice(item, 0))["status"] == "pass"


def test_own_code_attempt_view_uses_original_line_numbers(demo):
    with db.session_scope() as s:
        plan = plan_for(s, quiz_type="JUDGE", has_bug=True, origin=(demo["server_id"], 410))
        item = quizgen.from_own_code(s, plan, random.Random(5))
        attempt = quiz.new_attempt(s, UID, item, "after_pass")
        attempt.protocol, attempt.state = 1, "confidence"   # 以前的作答协议（新协议见 test_independent.py）
        v = quiz.view(s, attempt)
        assert v["startLine"] == item.start_line and v["mark"] == [item.start_line + item.origin["mark_line"] - 1]
        assert "result" not in v and "origin" not in json.dumps(v)
        quiz.set_confidence(s, attempt, "很有把握", 2000)
        quiz.judge(s, attempt, False)   # 说“对”，其实不对
        v = quiz.view(s, attempt)
        assert v["result"]["outcome"] == "wrong" and v["result"]["fix"]["start"] == item.start_line
        assert v["result"]["bugLine"] == v["mark"][0]


def test_generate_prefers_own_code_and_respects_ratio(demo):
    with db.session_scope() as s:
        item = quizgen.generate(s, UID, "C06.03.02", "after_pass", origin=(demo["server_id"], 410), rng=random.Random(2))
        assert item is not None and item.source == "own_code"
        # 最近的小题全是有错的：下一道一定出正确的
        bank = s.scalars(select(m.QuizItem).where(m.QuizItem.has_bug.is_(True), m.QuizItem.source == "bank")).all()
        for b in bank[:5]:
            a = quiz.new_attempt(s, UID, b, "after_pass")
            a.state = "done"
        plan = quizgen.make_plan(s, UID, "C06.03.02", "after_pass")
        assert plan.has_bug is False and any("正确代码只占" in n for n in plan.notes)


def test_template_uses_student_names(demo):
    with db.session_scope() as s:
        plan = plan_for(s, has_bug=True, quiz_type="FIX")
        item = quizgen.from_template(s, plan, random.Random(1))
        assert item is not None and item.source == "template" and item.verified
        assert item.origin["names"] and all(v in item.shown_code + item.template for v in item.origin["names"].values())


def test_llm_quiz_is_verified_by_running(demo):
    good = {"stem": "这段想求平均分，对吗？", "caption": "这段代码想求平均分",
            "code": "int s = 0;\nfor (i = 0; i < n; i++) s += a[i];\nprintf(\"%.2f\\n\", s / n);",
            "correct_code": "int s = 0;\nfor (i = 0; i < n; i++) s += a[i];\nprintf(\"%.2f\\n\", (double)s / n);",
            "template": "#include <stdio.h>\nint main(void) {\n    int n, i, a[100];\n    scanf(\"%d\", &n);\n"
                        "    for (i = 0; i < n; i++) scanf(\"%d\", &a[i]);\n/*<<CODE>>*/\n    return 0;\n}\n",
            "inputs": ["2\n1 2\n", "3\n1 1 2\n", "1\n5\n"], "explanation": "整数相除丢掉小数。",
            "expected_outputs_should_be_ignored": ["9", "9", "9"]}
    seen = []

    def backend(messages, json_mode, max_tokens, temperature):
        seen.append(messages[-1]["content"])
        return json.dumps(good, ensure_ascii=False), 300, 200

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            plan = plan_for(s, skill="C06.03.05", has_bug=True, quiz_type="FIX")
            item = quizgen.from_llm(s, plan, random.Random(1))
            assert item is not None and item.source == "llm" and item.verified and item.has_bug
            assert [c["expected"] for c in item.checks] == ["1.50", "1.33", "5.00"], "期望输出是程序运行算的"
            # 大模型说有错、实际没错：作废
            plan2 = plan_for(s, skill="C06.03.05", has_bug=False, quiz_type="FIX")
            assert quizgen.from_llm(s, plan2, random.Random(1)) is None
        assert UID not in "\n".join(seen) and "林晓" not in "\n".join(seen)
    finally:
        llm.set_backend(None)
