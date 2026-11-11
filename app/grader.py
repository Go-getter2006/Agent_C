"""诊断题五步的判分。第 1、2 步自动判；第 3~5 步由大模型按参考答案判，模拟模式下用关键词规则。
第 5 步（变式题）开启 ENABLE_CODE_RUNNER 时用 gcc 实际运行测试用例。"""
import re

from . import config, llm, runner

STEP_PROMPTS = {
    1: "这段代码能正确完成题目要求吗？",
    2: "错误在哪一行？请点击行号。",
    3: "请解释：为什么会出错？在什么输入或情况下会触发这个错误？",
    4: "请直接修改下面的代码，并说明为什么这样改就对了。",
    5: "变式题：请独立写出完整的 C 程序。",
}
STEP_INPUT = {1: "choice", 2: "line", 3: "text", 4: "code_text", 5: "code"}

EXPLAIN_SYSTEM = """你是C语言课程的阅卷助教。判断学生对代码错误的解释是否同时说清了【错误原因】和【触发条件】。
意思正确即可，不要求和参考答案措辞一致；只说清其中一项时 pass 为 false。
输出 json：{"cause_ok": true/false, "trigger_ok": true/false, "pass": true/false, "feedback": "一句话指出还缺什么，不要直接给出答案"}"""

FIX_SYSTEM = """你是C语言课程的阅卷助教。判断学生修改后的代码是否消除了指定错误、且没有引入新的错误，并判断学生的说明是否讲清了“为什么这样改就有效”。
输出 json：{"code_ok": true/false, "reason_ok": true/false, "pass": true/false, "feedback": "一句话反馈，不要直接给出完整答案"}"""

VARIANT_SYSTEM = """你是C语言课程的阅卷助教。判断学生的程序能否正确完成题目要求：请逐个测试用例在脑中执行程序，比较输出（忽略多余空格和换行）。
输出 json：{"pass": true/false, "failed_case": "第一个不通过的输入（全部通过则为空字符串）", "feedback": "一句话反馈"}"""


def _norm(code: str) -> str:
    return re.sub(r"\s+", "", code or "")


def numbered(code: str) -> str:
    return "\n".join(f"{i + 1:>2}| {line}" for i, line in enumerate(code.split("\n")))


def grade(item: dict, step: int, answer) -> dict:
    """返回 {"passed": bool, "feedback": str, "grader": str}。"""
    if step == 1:
        said_bug = str(answer).strip() in ("有错", "有错误", "bug", "yes", "true", "1")
        passed = said_bug == bool(item["has_bug"])
        fb = "判断正确。" if passed else ("这段代码其实有问题。" if item["has_bug"] else "这段代码其实是对的。")
        return {"passed": passed, "feedback": fb, "grader": "auto"}
    if step == 2:
        try:
            line = int(answer)
        except (TypeError, ValueError):
            line = -1
        passed = line in item.get("bug_lines", [])
        return {"passed": passed, "feedback": "定位正确。" if passed else "错误不在这一行。", "grader": "auto"}
    if step == 3:
        return _grade_explain(item, str(answer or ""))
    if step == 4:
        ans = answer if isinstance(answer, dict) else {"code": str(answer or ""), "reason": ""}
        return _grade_fix(item, ans.get("code", ""), ans.get("reason", ""))
    if step == 5:
        return _grade_variant(item, str(answer or ""))
    raise ValueError(f"unknown step {step}")


# ---------------------------------------------------------------- 第 3 步：解释原因与触发条件
def _mock_explain(item, text):
    t = _norm(text).lower()
    kw = item.get("explain_keywords", {})
    cause_ok = any(_norm(k).lower() in t for k in kw.get("cause", []))
    trigger_ok = any(_norm(k).lower() in t for k in kw.get("trigger", []))
    passed = cause_ok and trigger_ok and len(t) >= 6
    if passed:
        fb = "原因和触发条件都说清楚了。"
    elif cause_ok:
        fb = "原因说到了，但还没说清楚在什么情况下会触发。"
    elif trigger_ok:
        fb = "说到了什么时候出错，但原因还没讲清楚。"
    else:
        fb = "还没有说到出错的原因和触发条件。"
    return {"cause_ok": cause_ok, "trigger_ok": trigger_ok, "pass": passed, "feedback": fb}


def _grade_explain(item, text):
    if not text.strip():
        return {"passed": False, "feedback": "没有作答。", "grader": "auto"}
    user = (f"【题目要求】{item['task']}\n【代码】\n{numbered(item['code'])}\n【错误所在行】{item['bug_lines']}\n"
            f"【参考：错误原因】{item['cause']}\n【参考：触发条件】{item['trigger']}\n【学生的解释】{text[:2000]}")
    out = llm.chat_json(EXPLAIN_SYSTEM, user, fallback=lambda: _mock_explain(item, text))
    passed = bool(out.get("pass")) if "pass" in out else bool(out.get("cause_ok") and out.get("trigger_ok"))
    return {"passed": passed, "feedback": str(out.get("feedback", ""))[:300], "grader": out.get("_source", "llm")}


# ---------------------------------------------------------------- 第 4 步：修复并说明
def _mock_fix(item, code, reason):
    c = _norm(code)
    checks = [_norm(x) for x in item.get("fix_check", [])]
    forbids = [_norm(x) for x in item.get("fix_forbid", [])]
    code_ok = (not checks or any(x in c for x in checks)) and not any(x and x in c for x in forbids)
    code_ok = code_ok and c != _norm(item["code"])
    reason_ok = len(_norm(reason)) >= 6
    return {"code_ok": code_ok, "reason_ok": reason_ok, "pass": code_ok and reason_ok,
            "feedback": "修改正确，说明也到位。" if code_ok and reason_ok else
            ("代码改对了，但还没说明为什么有效。" if code_ok else "修改后的代码还没有消除这个错误。")}


def _grade_fix(item, code, reason):
    if not code.strip():
        return {"passed": False, "feedback": "没有提交修改后的代码。", "grader": "auto"}
    user = (f"【题目要求】{item['task']}\n【原代码】\n{numbered(item['code'])}\n【错误所在行】{item['bug_lines']}\n"
            f"【错误类型】{item.get('bug_type', '')}\n【参考修复】{item['fix']}\n【参考原理】{item['fix_rationale']}\n"
            f"【学生修改后的代码】\n{code[:4000]}\n【学生的说明】{reason[:1500]}")
    out = llm.chat_json(FIX_SYSTEM, user, fallback=lambda: _mock_fix(item, code, reason))
    passed = bool(out.get("pass")) if "pass" in out else bool(out.get("code_ok") and out.get("reason_ok"))
    return {"passed": passed, "feedback": str(out.get("feedback", ""))[:300], "grader": out.get("_source", "llm")}


# ---------------------------------------------------------------- 第 5 步：独立完成变式题
def _mock_variant(item, code):
    c = _norm(code)
    tokens = [_norm(t) for t in item["variant"].get("check_tokens", [])]
    ok = "main(" in c and "printf" in c and (not tokens or any(t in c for t in tokens))
    return {"pass": ok, "failed_case": "", "feedback": "程序看起来完成了题目要求。" if ok else "程序还没有完成题目要求。"}


def _grade_variant(item, code):
    if not code.strip():
        return {"passed": False, "feedback": "没有提交程序。", "grader": "auto"}
    variant = item["variant"]
    if config.ENABLE_CODE_RUNNER and runner.gcc_available():
        res = runner.compile_and_test(code, variant["tests"])
        if res["stage"] == "compile":
            fb = "编译没有通过：" + res["detail"].strip().split("\n")[0][:200]
        else:
            fb = res["detail"]
        return {"passed": res["ok"], "feedback": fb, "grader": "runner"}
    cases = "\n".join(f"输入：{t['in']!r} 期望输出：{t['out']!r}" for t in variant["tests"])
    user = f"【题目】{variant['prompt']}\n【测试用例】\n{cases}\n【学生程序】\n{code[:5000]}"
    out = llm.chat_json(VARIANT_SYSTEM, user, fallback=lambda: _mock_variant(item, code))
    return {"passed": bool(out.get("pass")), "feedback": str(out.get("feedback", ""))[:300],
            "grader": out.get("_source", "llm")}


def reveal(item: dict) -> dict:
    """一道题做完后给学生看的参考答案。"""
    if not item["has_bug"]:
        return {"has_bug": False, "explanation": item["explanation"]}
    out = {"has_bug": True, "bug_lines": item["bug_lines"], "bug_type": item.get("bug_type", ""),
           "cause": item["cause"], "trigger": item["trigger"], "fix": item["fix"],
           "fix_rationale": item["fix_rationale"]}
    if item.get("variant", {}).get("reference"):
        out["variant_reference"] = item["variant"]["reference"]
    return out
