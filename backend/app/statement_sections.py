"""题面分段和公开样例保真检查；不处理隐藏测试数据。"""
from __future__ import annotations

import re

_TITLE = r"(?:题目描述|问题描述|题目内容|输入格式|输出格式|输入说明|输出说明|输入|输出|(?:样例|示例)(?:输入|输出)|(?:输入|输出)(?:样例|示例)|样例说明|示例说明|样例解释|示例解释|样例解析|提示|说明|数据范围|注意)"
_HEADING = re.compile(r"^(?:[（(]?\d+[）).、．]\s*)?(" + _TITLE + r"\s*(?:\d+|[一二三四五六七八九十])?)\s*(?:[:：]\s*(.*)|$)")


def samples(text: str) -> list[tuple[str, tuple[str, ...]]]:
    result: list[tuple[str, tuple[str, ...]]] = []
    direction = ""
    body: list[str] = []
    def flush() -> None:
        if direction:
            result.append((direction, tuple("\n".join(body).split())))
    for line in text.replace("\r\n", "\n").split("\n"):
        match = _HEADING.match(line.strip())
        if match:
            flush()
            title = match[1]
            direction = ("input" if "输入" in title else "output") if re.search("样例|示例", title) and re.search("输入|输出", title) else ""
            body = [match[2]] if match[2] else []
        elif direction:
            body.append(line)
    flush()
    return result
