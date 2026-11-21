"""已经通过的题，学生回头问（“为什么改了这么多次”“当时错在哪”）：按这道题每一次提交的结果和改动来回答。

以前一律回“这道题你已经通过了，还有哪里想弄清楚可以直接问”，等于没回答。

- 事实全用程序取（catalog、changes）：第几次、过没过、几组通过、程序标出的错误（学生端的说法）、
  和上一次比改了哪几行、修好了什么、还在什么、新出了什么。不出现测试数据。
- 有大模型时让它按这些事实回答他问的那句话；没有或不合格时用模板把事实说清楚。
"""
from __future__ import annotations

import json
import logging
from collections import Counter

from sqlalchemy.orm import Session

from .. import catalog, changes, llm, pdftext
from ..taxonomy import course_prompt, error_name

log = logging.getLogger("chat.retro")


def history(s: Session, user_id: str, server_id: int, problem_id: int) -> list[dict]:
    """这道题每一次提交：第几次、结果、几组通过、错误名称、和上一次比的改动。"""
    subs = catalog.user_submissions(s, user_id, server_id, problem_id)
    views = {v["key"]: v for v in catalog.submissions_view(s, user_id, server_id, problem_id)}
    diffs = changes.problem_changes(s, user_id, server_id, problem_id)
    out = []
    for i, sub in enumerate(subs, start=1):
        key = catalog.submission_key(sub.server_id, sub.submission_id)
        v = views.get(key, {})
        ch = diffs.get(key) or {}
        out.append({
            "no": i, "passed": sub.judgement == "AC", "result": v.get("resultText", ""),
            "cases": (v.get("passed"), v.get("total")),
            "errors": [n for n in (error_name(c, student_facing=True) for c in changes.error_codes(s, sub)) if n],
            "changed": ch.get("changed") if ch.get("available") else None,
            "lines": [(x["from"], x["to"]) for x in ch.get("lines", [])],
            "fixed": ch.get("fixed", []), "still": ch.get("still", []), "new": ch.get("new", []),
        })
    return out


def last_failure(s: Session, user_id: str, server_id: int, problem_id: int, name: str = "") -> str:
    """最后一次没过的那次：他的代码（带行号）和没通过的测试点。只给大模型分析“当时错在哪”，回复里不能出现测试数据。"""
    from ..xdoj.assemble import assemble_submission

    subs = catalog.user_submissions(s, user_id, server_id, problem_id)
    failed = [(i, x) for i, x in enumerate(subs, start=1) if x.judgement not in ("AC", "PENDING", "UNKNOWN", "")]
    if not failed:
        return ""
    no, sub = failed[-1]
    ev = assemble_submission(s, server_id, sub.submission_id, include_problem_text=False)
    code = llm.numbered(ev.get("sourceCode") or "", user_id, name)
    cases = llm.failed_cases(ev, user_id, name)
    return (f"第 {no} 次（最后一次没过）的代码：\n{code or '（没有取到）'}\n"
            f"没通过的测试点：{json.dumps(cases, ensure_ascii=False) if cases else '（没有取到）'}")


def _where(lines: list[tuple[int, int]]) -> str:
    if not lines:
        return ""
    a, b = lines[0]
    return f"第 {a} 行" if a == b else f"第 {a}～{b} 行"


def facts_text(hist: list[dict]) -> str:
    """把每一次提交写成一行（给大模型和模板用；只有学生端本来就能看到的东西）。"""
    rows = []
    for h in hist:
        done, total = h["cases"]
        part = [f"第 {h['no']} 次：{h['result']}" + (f"（{done}/{total} 组通过）" if total else "")]
        if h["changed"] == 0:
            part.append("代码没改")
        elif h["changed"]:
            part.append(f"改了 {h['changed']} 行" + (f"（{_where(h['lines'])}）" if h["lines"] else ""))
        if h["fixed"]:
            part.append("修好了：" + "、".join(h["fixed"]))
        if h["new"]:
            part.append("新出现：" + "、".join(h["new"]))
        if h["still"]:
            part.append("还在：" + "、".join(h["still"]))
        elif h["errors"] and not h["passed"] and h["no"] == 1:
            part.append("问题：" + "、".join(h["errors"]))
        rows.append("；".join(part))
    return "\n".join(rows)


def main_trouble(hist: list[dict]) -> tuple[str, int] | None:
    """前面没过的那几次里出现最多的错误。"""
    c = Counter(e for h in hist if not h["passed"] for e in h["errors"])
    return c.most_common(1)[0] if c else None


def template(hist: list[dict]) -> str:
    if not hist:
        return "这道题的提交记录还没取到，稍等几秒再问一次。"
    n = len(hist)
    first_pass = next((h["no"] for h in hist if h["passed"]), None)
    if n == 1 or first_pass == 1:
        return "这道题你第一次提交就通过了。"
    lines = [f"这道题你一共交了 {n} 次，第 {first_pass} 次通过。每一次是这样的：", facts_text(hist)]
    trouble = main_trouble(hist)
    if trouble:
        name, k = trouble
        lines.append(f"主要卡在：{name}" + (f"（没过的那几次里出现了 {k} 次）" if k > 1 else "") + "。")
    unchanged = sum(1 for h in hist if h["changed"] == 0)
    if unchanged:
        lines.append(f"其中有 {unchanged} 次代码没改就又交了一次。下次没过时，先对照没过的那组情况想一想再改。")
    return "\n".join(lines)


_SYSTEM = """你是 C 语言课程的助教。一名学生的一道题已经通过了，他回头问了一个问题（比如为什么交了这么多次、当时错在哪）。
下面是这道题他每一次提交的结果和改动（程序算出来的，可信）。请直接回答他问的那句话：
- 说清楚前面几次分别卡在哪、后来是怎么改对的，点出最主要的那个问题；
- 如果有几次代码没改就重交，温和地提醒他先想清楚再交；
- 他问当时错在哪：对照题目、最后一次没过时的代码和没通过的测试点，说清是哪一行、哪种写法、哪类情况出的错（只说是哪类情况，不说具体数据）；
- 不超过 150 个字，不要贴大段代码，不要提测试数据和具体的输入输出，不要提学号姓名，用“你”称呼他。
只输出 JSON：{"reply":"……"}"""


def reply(hist: list[dict], text: str, tests: list[dict], course: str, statement: str = "", recent: str = "",
          failure: str = "", who: tuple[str, str] = ("", "")) -> tuple[str, str]:
    """(回答, llm / template)。statement：题面；recent：最近的对话；failure：last_failure()；who：(学号, 姓名)。"""
    from .validate import _leaks

    fallback = template(hist)
    if not hist or not llm.available() or len(hist) == 1:
        return fallback, "template"

    def validate(d: dict) -> str:
        out = str(d.get("reply") or "").strip()
        if not out:
            raise ValueError("reply 是空的")
        if len(out) > 240:
            raise ValueError("太长了")
        if "```" in out or _leaks(out, tests):
            raise ValueError("不能贴代码、不能出现测试数据")
        return out

    trouble = main_trouble(hist)
    user = (("【题目】\n" + pdftext.for_llm(llm.strip_identity(statement, *who))[:1500] + "\n" if statement else "")
            + "【每一次提交】\n" + facts_text(hist)
            + (f"\n【没过的那几次里出现最多的问题】{trouble[0]}（{trouble[1]} 次）" if trouble else "")
            + (f"\n【最后一次没过时（只给你分析用，不能把测试数据告诉他）】\n{failure}" if failure else "")
            + (f"\n【最近的对话】\n{recent}" if recent else "")
            + "\n【他问的】\n" + llm.strip_identity(text, *who))
    try:
        return llm.call_json("retro_reply", course_prompt(_SYSTEM, course), user, validate,
                             retries=1, max_tokens=300), "llm"
    except llm.LLMUnavailable:
        return fallback, "template"
