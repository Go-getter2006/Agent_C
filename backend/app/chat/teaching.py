"""可编辑的教学提示与本轮教学需求；不替代证据和隐私校验。"""
from __future__ import annotations

import re
from pathlib import Path

from .. import config

_DEFAULT = Path(__file__).with_name("prompts") / "tutoring.md"
_EXAMPLE = re.compile(r"完全(?:不懂|不会|没思路)|一点(?:也|都)?不会|从零(?:开始|讲)|"
                      r"(?:给|看|写|来).{0,8}(?:一段|一点|部分|示例).{0,5}代码|"
                      r"代码(?:示例|例子)|没(?:有)?(?:任何|一点)思路")

DIAGNOSIS_NAMES = {
    "logic": "处理逻辑", "boundary": "边界条件", "io": "输入输出", "compile": "编译",
    "runtime": "运行异常", "performance": "时间或空间开销", "numeric": "数值类型或精度",
    "task": "题意理解",
}
_RESULT_KIND = {"CE": "compile", "RE": "runtime", "TLE": "performance", "MLE": "performance",
                "OLE": "io", "PE": "io"}
_BOUNDARY_ERRORS = {"BR04", "LP01", "AR01", "AR02", "AR04", "AL02", "AL03", "ST01", "ST02"}


def diagnosis_types(ctx) -> list[str]:
    """给兜底提示和回复模型用的候选。运行结果和已有错误标注都保留。"""
    result = ctx.submission.judgement if ctx.submission else ""
    if result == "CE":
        return ["compile"]
    code = ctx.root_error if ctx.diag_confidence >= config.CHAT["low_confidence"] else ""
    kinds = []
    if code in _BOUNDARY_ERRORS or (code == "TY09" and ctx.root_skill == "C06.03.02" and ctx.situation):
        kinds.append("boundary")
    elif code == "AL01":
        kinds.append("task")
    elif code == "AL05":
        kinds.append("performance")
    elif code.startswith("IO"):
        kinds.append("io")
    elif code.startswith("TY"):
        kinds.append("numeric")
    elif code.startswith(("BR", "LP", "AL", "AR", "ST", "FN")):
        kinds.append("logic")
    rows = (ctx.evidence or {}).get("testCases", [])
    for judgement in [result, *(row.get("resultCode") for row in rows if row.get("passed") is False)]:
        if kind := _RESULT_KIND.get(judgement):
            kinds.append(kind)
    for row in rows:
        expected, actual = row.get("standardOutput"), row.get("programOutput")
        if row.get("passed") is False and row.get("resultCode") == "WA" and not row.get("base64") \
                and not row.get("outputTruncated") and expected is not None and actual is not None \
                and expected != actual and "".join(expected.split()) == "".join(actual.split()):
            kinds.append("io")
            break
    return list(dict.fromkeys(kinds))


def first_hint(ctx):
    """证据已经支持的第一层提示，可用于回复兜底。"""
    checks = {
        "logic": "沿着流程看，在哪一步代码的处理和题意开始不一致？",
        "boundary": (f"先用“{ctx.situation}”这类输入走一遍，检查程序有没有覆盖这种情况。"
                     if ctx.situation else "先按题面核对等于端点、最少输入量或访问首尾元素时的处理。"),
        "io": "逐项对照题面要求的读入顺序、输出内容和分隔符。",
        "compile": "编译没有通过。先读编译器报出的第一条消息，核对它提示的语法或声明。",
        "runtime": "程序运行时中断了。先检查访问和输入读取是否有效。",
        "performance": "按题目的最大数据规模估算循环次数和需要的存储空间。",
        "numeric": "检查中间值的取值范围、整数与小数的运算，以及舍入发生在哪一步。",
        "task": "先用自己的话说清楚题面要求输出什么，再核对代码计算的是不是同一个量。",
    }
    kinds = diagnosis_types(ctx)
    chart = []
    if "logic" in kinds:
        step = "条件与分支" if ctx.root_error.startswith("BR") else "处理与更新"
        chart = ["读入并初始化", step, "生成结果"]
    hints = [checks[k] for k in kinds if not (k == "runtime" and "boundary" in kinds)]
    body = " ".join(hints) or "目前的评测明细还不足以确认原因。挑一个公开样例，先核对读入、处理和输出各步的结果。"
    return kinds, body, chart

JUDGE_EVIDENCE_GUIDANCE = """评测诊断流程（学生询问提交结果时必须按这个顺序分析）：
1. 确认所问的提交版本。先看总评、各组评测结果及编译信息，区分答案错误、编译错误和运行失败。
2. 按同一测试点对照标准输入、标准输出与程序实际输出。区分只差空白、内容也有差异、输出缺失等情况，
   看哪些失败组呈现相同差异。若问为什么全错，要覆盖各组；如有几类差异，分别解释有证据的部分。
3. 再根据每类差异回溯对应的输入处理、计算、分支或输出代码。已有错误标签和高亮行只作为线索。
   一处代码错误只能解释证据支持的那部分失败。根据给出的标准测试数据和实际评测记录说明原因。
4. 据此引导学生检查相关的代码或输出格式，并给一个可执行的检查步骤。隐藏测试点的输入、标准输出和实际输出只用于分析。
   缺失、截断或无法解码的明细要说明边界，不能把推算当成实际观测，也不能用通过判定冒充取到了程序输出。
   programOutput 的空字符串表示程序确实未输出，null 表示未取到。firstDiffLine 是输出文本首次不同的行。
   公开题面样例和隐藏测试点是两种材料。图文以附上的题面为准，缺图或看不清时说明情况。"""


def needs_example(text: str) -> bool:
    return bool(_EXAMPLE.search(text or ""))


def guidance() -> str:
    """每轮读取，管理员修改提示文件后不必改 Python；环境变量可指定挂载文件。"""
    selected = Path(config.CHAT_TEACHING_PROMPT_FILE) if config.CHAT_TEACHING_PROMPT_FILE else _DEFAULT
    try:
        value = selected.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        value = ""
    return (value or _DEFAULT.read_text(encoding="utf-8").strip())[:8000]
