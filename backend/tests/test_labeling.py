"""错误标注：规则先行；大模型只能从库里选，编码不在库里就作废。"""
from __future__ import annotations

import json

from sqlalchemy import select

from app import db, llm
from app import models as m
from app.labeling import labeler
from app.labeling.problems import annotate_problem, save_problem_label
from app.labeling.rules import label_by_rules, label_compile_error, label_wrong_answer
from app.xdoj import sync_meta
from app.xdoj.daemon import Daemon


def case(want, got, tid=1):
    return {"testcaseId": tid, "standardOutput": want, "programOutput": got, "passed": False}


def test_rules_from_output():
    assert label_wrong_answer([case("1 2 3\n", "1\n2\n3\n")]).error_code == "IO04"
    assert label_wrong_answer([case("5\n", "请输入两个数：5\n")]).error_code == "IO05"
    assert label_wrong_answer([case("78.40 3\n", "78.00 3\n"), case("90.50\n", "90.00\n", 2)]).error_code == "TY01"
    assert label_wrong_answer([case("3.14\n", "3.140000\n")]).error_code == "IO06"
    assert label_wrong_answer([case("3.14\n", "3.1\n")]).error_code == "IO06"
    assert label_wrong_answer([case("-4\n", "0\n")]) is None          # 规则判不了：交给大模型
    assert label_wrong_answer([case("5\n", "5\n\n"), case("7\n", "8\n", 2)]) is None  # 不是所有失败点都符合


def test_rules_from_compile_output():
    cases = {
        "main.c:3:15: error: expected ';' before 'printf'": "CE01",
        "main.c:5:5: error: 'sum' undeclared (first use in this function)": "CE02",
        "main.c:4:5: error: implicit declaration of function 'prinft'": "CE04",
        "main.c:3:20: error: stray '\\357' in program": "CE05",
        "main.c:6:9: error: redefinition of 'i'": "CE07",
        "main.c:7:5: error: too few arguments to function 'f'": "CE03",
        "main.c:2:1: error: unknown type name 'itn'": "CE06",
    }
    for diag, code in cases.items():
        draft = label_compile_error(diag, "int main(){}")
        assert draft.error_code == code, diag
        assert draft.line is not None and draft.confidence > 0.9
    assert label_compile_error("x", "   \n").error_code == "OT02"
    assert label_by_rules({"judgementCode": "CE", "compileOutput": "main.c:1:1: error: expected ';' before '}'",
                           "sourceCode": "int main(){ return 0 }", "testCases": []}).error_code == "CE01"


def synced(ctx):
    sync_meta.sync_metadata(ctx)
    Daemon([ctx]).drain()


def test_label_submission_rule_then_saved(ctx):
    synced(ctx)
    with db.session_scope() as s:
        rows = labeler.label_submission(s, 1, 537102)
        assert [(r.error_code, r.source, r.role) for r in rows] == [("CE01", "rule", "primary")]
        assert rows[0].skill_code.startswith("C01.01") and rows[0].library_version
        assert labeler.label_submission(s, 1, 537103) == []            # 通过的提交不标
        assert labeler.label_submission(s, 1, 537101) == []            # 规则判不了，又没有大模型：先不标


def test_llm_label_must_come_from_library(ctx):
    synced(ctx)
    prompts = []
    answers = [
        {"labels": [{"errorCode": "XX99", "skillCode": "C03.01.03", "role": "primary", "confidence": 0.9}]},
        {"labels": [{"errorCode": "TY10", "skillCode": "C03.01.03", "role": "primary", "confidence": 0.8,
                     "line": 3, "evidence": "把加法写成了乘法再加一"}]},
    ]

    def backend(messages, json_mode, max_tokens, temperature):
        prompts.append(messages)
        return json.dumps(answers[len(prompts) - 1], ensure_ascii=False), 100, 20

    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            rows = labeler.label_submission(s, 1, 537101)
            assert [(r.error_code, r.skill_code, r.source, r.line) for r in rows] == [("TY10", "C03.01.03", "llm", 3)]
            assert rows[0].testcase_ids == [1397]
        assert len(prompts) == 2, "第一次的编码不在库里：整条作废，重标一次"
        sent = json.dumps(prompts, ensure_ascii=False)
        assert "2500000001" not in sent and "学生甲" not in sent, "发给大模型的内容不带学号和姓名"
        with db.session_scope() as s:
            calls = s.scalars(select(m.LlmCall)).all()
            assert len(calls) == 1 and calls[0].ok and calls[0].attempts == 2 and calls[0].prompt_version
    finally:
        llm.set_backend(None)


def test_llm_garbage_twice_becomes_ot99(ctx):
    synced(ctx)
    llm.set_backend(lambda *a: ('{"labels":[{"errorCode":"自己编的","role":"primary"}]}', 10, 5))
    try:
        with db.session_scope() as s:
            rows = labeler.label_submission(s, 1, 537101)
            assert [r.error_code for r in rows] == ["OT99"]
    finally:
        llm.set_backend(None)


def test_strip_identity():
    src = "// 学号 2500000001 张三\n/* author: zhangsan */\nint main(){ /* 求和 */ return 0; } // 张三写的\n"
    out = llm.strip_identity(src, "2500000001", "张三")
    assert "2500000001" not in out and "张三" not in out and "zhangsan" not in out
    assert "求和" in out and "int main()" in out


def test_problem_label_versions_and_llm_annotation(ctx):
    synced(ctx)
    with db.session_scope() as s:
        save_problem_label(s, 1, 407, "输入 n 个整数，输出最大值", "C06.03.02", ["C06.02.01"],
                           [{"id": 1401, "skill": "C06.03.03", "situation": "普通数据", "ordinary": True},
                            {"id": 1402, "skill": "C06.03.02", "situation": "全是负数"}], "seed")
        save_problem_label(s, 1, 407, "输入 n 个整数，输出最大值", "C06.03.02", [], [], "seed")
        rows = s.scalars(select(m.ProblemLabel).where(m.ProblemLabel.problem_id == 407)
                         .order_by(m.ProblemLabel.version)).all()
        assert [(r.version, r.is_current) for r in rows] == [(1, False), (2, True)]

    answer = {"brief": "输入两个整数，输出它们的和", "primary": "C01.03.01", "secondary": ["C01.02.01"],
              "testcases": [{"id": 1396, "skill": "C01.03.01", "situation": "普通数据", "ordinary": True},
                            {"id": 1397, "skill": "C01.03.01", "situation": "普通数据", "ordinary": True}]}
    llm.set_backend(lambda *a: (json.dumps(answer, ensure_ascii=False), 10, 5))
    try:
        with db.session_scope() as s:
            label = annotate_problem(s, 1, 406)
            assert label.brief == "输入两个整数，输出它们的和" and label.source == "llm" and label.version == 1
            assert len(labeler.testcase_labels(s, label)) == 2
            # 参考解用的是同学的通过提交（XDOJ 已经验证过）
            ref = s.scalar(select(m.ReferenceSolution).where(m.ReferenceSolution.problem_id == 406))
            assert ref.origin == "peer" and ref.verified
            assert annotate_problem(s, 1, 406).id == label.id, "题面和测试点没变：不重标"
    finally:
        llm.set_backend(None)


def test_problem_label_prompt_has_complete_evidence(ctx):
    """标注题目时题面、测试点完整给出：长的测试点不截断；没取到的是 null，不当成空输出。"""
    synced(ctx)
    long_output = "1 " * 2000 + "END_OF_OUTPUT"
    with db.session_scope() as s:
        t1, t2 = s.scalars(select(m.Testcase).where(m.Testcase.server_id == 1, m.Testcase.problem_id == 406)
                           .order_by(m.Testcase.testcase_id)).all()
        t1.output_bytes, t1.output_encoding = long_output.encode("utf-8"), "utf-8"
        t2.output_bytes = None
    seen = []
    answer = {"brief": "输入两个整数，输出它们的和", "primary": "C01.03.01", "secondary": [],
              "testcases": [{"id": 1396, "skill": "C01.03.01", "situation": "普通数据", "ordinary": True},
                            {"id": 1397, "skill": "C01.03.01", "situation": "普通数据", "ordinary": True}]}

    def backend(messages, *_args):
        seen.append(messages[1]["content"])
        return json.dumps(answer, ensure_ascii=False), 10, 5
    llm.set_backend(backend)
    try:
        with db.session_scope() as s:
            annotate_problem(s, 1, 406, force=True)
        prompt = next(p for p in seen if "【技能表】" in p)
        cases = json.loads(prompt.split("【测试点】\n", 1)[1])
        assert cases[0]["standardOutput"] == long_output        # 不截断
        assert cases[1]["standardOutput"] is None               # 没取到 ≠ 空输出
    finally:
        llm.set_backend(None)
