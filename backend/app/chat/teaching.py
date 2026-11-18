"""可编辑的教学提示与本轮教学需求；不替代证据和隐私校验。"""
from __future__ import annotations

import re
from pathlib import Path

from .. import config

_DEFAULT = Path(__file__).with_name("prompts") / "tutoring.md"
_EXAMPLE = re.compile(r"完全(?:不懂|不会|没思路)|一点(?:也|都)?不会|从零(?:开始|讲)|"
                      r"(?:给|看|写|来).{0,8}(?:一段|一点|部分|示例).{0,5}代码|"
                      r"代码(?:示例|例子)|没(?:有)?(?:任何|一点)思路")


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
