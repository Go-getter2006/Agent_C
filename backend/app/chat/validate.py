"""第 7 步的校验（附件一 5.7）。由程序检查；不通过就重写一次，再不通过用固定模板。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import config
from ..taxonomy import HELP_ORDER
from .text import C_WORDS, EMOJI, code_line_count, identifiers, squash, visible_chars


@dataclass
class Draft:
    """一条回复的草稿：前一段话、引用他自己的代码、示例代码、后一段话。"""

    before: str = ""
    quote: tuple[int, int, list[int]] | None = None  # 引用最近一次提交的第几行到第几行，标出哪几行
    code: str | None = None                          # 新写的示例代码
    code_caption: str = "示例"
    code_is_old: bool = False                        # 是以前给过的那段（不算新写的代码）
    code_mark: list[int] = field(default_factory=list)
    after: str = ""
    key_line: int | None = None                      # 示例代码里最关键的一行
    actions: list[str] = field(default_factory=list)
    flow_steps: list[str] = field(default_factory=list)
    flow_gap: int | None = None

    def text(self) -> str:
        return f"{self.before}\n{self.after}".strip()


_INTERNAL = re.compile(
    r"(\b[A-Z]{2}\d{2}\b|\bC\d{2}\.\d{2}|\bH[0-3]\b|\bR\d{2}\b|帮助级别|透露级别|根据你的学情|你的学情|掌握程度|"
    r"置信度|错误类型库|技能编码|工作流|意图|甲级|乙级|标注)")
_PROBLEM_NO = re.compile(r"(第\s*\d+\s*题|题号|\b\d{3,}\s*号题|[Pp]roblem\s*\d+)")
_LINE_REF = re.compile(r"第\s*(\d+)\s*(?:到|至|-|~|、)?\s*(\d+)?\s*行")
_CODE_MENTION = re.compile(r"(第\s*\d+\s*行|这几行|这段代码|看你这)")
_EXAMPLE_DATA = re.compile(r"-?\d+(?:\s+|\s*[,，、]\s*)-?\d+(?:(?:\s+|\s*[,，、]\s*)-?\d+)*")
_BACKTICK = re.compile(r"`([^`\n]+)`")
_TITLE = re.compile(r"「([^」]+)」(（[^）]*）)?")


def _leaks(text: str, testcases: list[dict]) -> bool:
    """回复里有没有出现测试点的原始输入、标准输出或程序在测试点上的输出。"""
    hay = squash(text)
    for t in testcases:
        for key in ("standardInput", "standardOutput", "programOutput"):
            raw = t.get(key)
            if not raw:
                continue
            whole = squash(raw)
            if len(whole.split()) >= 2 and len(whole) >= 3 and whole in hay:
                return True
            if key != "standardInput" and len(whole) >= 4 and whole in hay:
                return True
            for line in raw.splitlines():
                line = squash(line)
                if len(line.split()) >= 2 and len(line) >= 3 and line in hay:
                    return True
    return False


def check(draft: Draft, *, help_level: str, reveal_level: str, kind: str, source: str | None,
          testcases: list[dict], reference: str | None, current_title: str,
          visible_titles: dict[str, str]) -> list[str]:
    """返回不合格的原因；空列表表示通过。"""
    problems: list[str] = []
    text = draft.text()
    everything = "\n".join([text, draft.code or "", *draft.flow_steps])
    n_source = len(source.split("\n")) if source else 0

    # 1 新写的代码行数：H3 不超过 8 行，其余级别不超过 2 行；引用他自己的代码、以前给过的代码不计
    new_lines = 0 if draft.code_is_old else code_line_count(draft.code)
    limit = config.CHAT["h3_max_lines"] if help_level == "H3" else config.CHAT["other_max_lines"]
    if new_lines > limit:
        problems.append(f"新写的代码有 {new_lines} 行，这一级最多 {limit} 行")
    if help_level in ("H0", "H1") and new_lines > 0:
        problems.append("这一级不能给新写的代码")

    # 2 和参考解不能高度相似
    if reference and draft.code and not draft.code_is_old:
        ref_lines = {squash(x) for x in reference.split("\n")}
        core = [x for x in ref_lines if len(x) > 3 and not x.startswith("#") and x not in ("{", "}", "return 0;")]
        mine = [squash(x) for x in draft.code.split("\n") if squash(x)]
        same = sum(1 for x in mine if x in ref_lines)
        if core and mine and same / len(mine) >= 0.9 and len(mine) >= 0.7 * len(core):
            problems.append("和参考解太像，等于给了整题")

    # 3 不能出现测试点的原始输入或标准输出；构造的例子只能在乙级出现
    if _leaks(everything, testcases):
        problems.append("出现了测试点的原始数据")
    if reveal_level == "甲" and kind in ("help", "locate", "diagnose", "diagnose_where") \
            and _EXAMPLE_DATA.search(re.sub(r"第\s*\d+\s*(到|至)\s*\d+\s*行", "", everything)):
        problems.append("这一级只能说是哪类情况，不能给具体的例子")
    if kind == "diagnose" and (draft.quote or _LINE_REF.search(everything) or "```" in everything):
        problems.append("先解释问题类型和要检查的步骤，留到下一轮再指出代码行")

    # 4 提到的行号、变量名必须真实存在
    if source:
        for m in _LINE_REF.finditer(text):
            for g in m.groups():
                if g and not (1 <= int(g) <= n_source):
                    problems.append(f"提到了不存在的第 {g} 行")
        known = identifiers(source) | identifiers(draft.code or "") | C_WORDS
        for span in _BACKTICK.findall(text):
            if re.fullmatch(r"[A-Za-z_]\w*", span) and span not in known:
                problems.append(f"提到了代码里没有的名字 {span}")
    if draft.quote is not None:
        a, b, marks = draft.quote
        if not source or not (1 <= a <= b <= n_source):
            problems.append("引用的行号不在他的代码范围里")
        elif not (1 <= b - a + 1 <= config.CHAT["quote_max_lines"]):
            problems.append("引用的代码太长")

    # 5 不能出现题号；提到以前的题必须带简述，并且必须是这个学生可见的题
    if _PROBLEM_NO.search(text):
        problems.append("出现了题号")
    for title, brief in _TITLE.findall(text):
        if title == current_title:
            continue
        if title not in visible_titles:
            problems.append(f"提到了学生看不到的题「{title}」")
        elif visible_titles[title] and not brief:
            problems.append(f"提到「{title}」时没有带简述")

    # 6 回复里提到代码时必须附代码块
    if _CODE_MENTION.search(text) and draft.quote is None and not draft.code and source:
        if kind in ("help", "locate", "stuck") and HELP_ORDER.get(help_level, -1) <= 0:
            problems.append("提到了代码却没有附代码块")

    # 7 长度：一般不超过 150 字（代码块不计），概念讲解不超过 300 字
    limit_chars = config.CHAT["concept_max_chars"] if kind == "concept" else config.CHAT["reply_max_chars"]
    if visible_chars(text) > limit_chars:
        problems.append(f"太长了，最多 {limit_chars} 字")
    if not text:
        problems.append("回复是空的")

    # 8 不能出现内部用语，不用 emoji
    if _INTERNAL.search("\n".join([text, *draft.flow_steps])):
        problems.append("出现了内部用语")
    if EMOJI.search(everything):
        problems.append("用了 emoji")
    return problems
