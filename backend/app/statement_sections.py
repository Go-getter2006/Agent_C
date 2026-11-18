"""题面分段和公开样例保真检查；不处理隐藏测试数据。"""
from __future__ import annotations

import re
import unicodedata

_GAP = r"[\s\u200b-\u200d\u2060\ufeff\u00ad]*"
_TITLES = ("题目描述", "问题描述", "题目内容", "输入格式", "输出格式", "输入说明", "输出说明",
           "样例输入", "样例输出", "示例输入", "示例输出", "输入样例", "输出样例", "输入示例", "输出示例",
           "样例说明", "示例说明", "样例解释", "示例解释", "样例解析", "数据范围", "输入", "输出", "提示", "说明", "注意")
_TITLE = "|".join(_GAP.join(t) for t in _TITLES)
_HEADING = re.compile(r"^" + _GAP + r"(?:#{1,6}\s*)?(?:[（(]?[0-9０-９]+[）).、．]" + _GAP +
                      r")?(?:\*\*|__)?(" + _TITLE + r")" + _GAP + r"([0-9０-９一二三四五六七八九十]*)" +
                      _GAP + r"(?:\*\*|__)?" + _GAP + r"(?:[:：︰﹕]" + _GAP + r"(?:\*\*|__)?[^\S\r\n]*(.*)|$)")


def heading(line: str) -> tuple[str, str] | None:
    """容忍 PDF 的标题空格和排版标记；正文原字符不变。"""
    match = _HEADING.match(line.strip())
    if match is None:
        return None
    title = re.sub(_GAP, "", match[1])
    number = unicodedata.normalize("NFKC", match[2])
    return title + (" " + number if number else ""), match[3] or ""


def samples(text: str) -> list[tuple[str, tuple[str, ...]]]:
    result: list[tuple[str, tuple[str, ...]]] = []
    direction = ""
    body: list[str] = []
    def flush() -> None:
        if direction:
            result.append((direction, tuple("\n".join(body).split())))
    after_heading = False
    for line in re.sub(r"\r\n?|[\u2028\u2029\f]", "\n", text).split("\n"):
        match = heading(line)
        if match:
            flush()
            title, tail = match
            direction = ("input" if "输入" in title else "output") if re.search("样例|示例", title) and re.search("输入|输出", title) else ""
            body = [tail] if tail else []
            after_heading = not bool(tail)
        elif after_heading and re.fullmatch(r"[\s\u200b\ufeff]*[:：︰﹕]\s*", line):
            continue
        elif direction:
            body.append(line)
            after_heading = False
    flush()
    return result
