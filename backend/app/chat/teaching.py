"""可编辑的教学提示与本轮教学需求；不替代证据和隐私校验。"""
from __future__ import annotations

import re
from pathlib import Path

from .. import config

_DEFAULT = Path(__file__).with_name("prompts") / "tutoring.md"
_EXAMPLE = re.compile(r"完全(?:不懂|不会|没思路)|一点(?:也|都)?不会|从零(?:开始|讲)|"
                      r"(?:给|看|写|来).{0,8}(?:一段|一点|部分|示例).{0,5}代码|"
                      r"代码(?:示例|例子)|没(?:有)?(?:任何|一点)思路")

JUDGE_EVIDENCE_GUIDANCE = """评测诊断流程（学生询问提交结果时必须按这个顺序分析）：
1. 确认所问的提交版本。先看总评、各组评测结果及编译信息，区分答案错误、编译错误和运行失败。
2. 按同一测试点对照标准输入、标准输出与程序实际输出。区分只差空白、内容也有差异、输出缺失等情况，
   看哪些失败组呈现相同差异。若问为什么全错，要覆盖各组；如有几类差异，分别解释有证据的部分。
3. 再根据每类差异回溯对应的输入处理、计算、分支或输出代码。已有错误标签和高亮行只作为线索。
   一处代码错误只能解释证据支持的那部分失败。对比两版时，先看相同测试点的判定或实际输出怎么变，再找对应的改动。
   同一个测试点使用相同的标准输入。某个错误可能只影响指定条件下的结果；不要把两次通过数的变化归因为运气或猜测换了输入。
4. 据此引导学生检查相关的代码或输出格式，并给一个可执行的检查步骤。隐藏测试点的输入、标准输出和实际输出只用于分析。
   缺失、截断或无法解码的明细要说明边界，不能把推算当成实际观测，也不能用通过判定冒充取到了程序输出。"""


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
