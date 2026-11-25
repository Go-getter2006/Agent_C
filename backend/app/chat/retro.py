"""已经通过的题，学生回头问（“为什么改了这么多次”“当时错在哪”）：按这道题每一次提交的结果和改动来回答。

以前一律回“这道题你已经通过了，还有哪里想弄清楚可以直接问”，等于没回答。

- 事实全用程序取（catalog、changes）：第几次、过没过、几组通过、程序标出的错误（学生端的说法）、
  和上一次比改了哪几行、修好了什么、还在什么、新出了什么。不出现测试数据。
- 模型回答学生当前的问题，程序核对评测事实和引用的源码版本。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, changes, llm, pdftext
from .. import models as m
from ..taxonomy import course_prompt, error_name
from ..xdoj.assemble import assemble_submission
from .prompt_context import PromptContext
from .teaching import JUDGE_EVIDENCE_GUIDANCE
from .text import dedent, quote_range
from .validate import Draft, _leaks

_ATTEMPT = re.compile(r"第\s*([0-9０-９]+|[一二三四五六七八九十两]{1,3})\s*(?:次|版)"
                      r"(?!\s*(?:循环|迭代|调用|输入|输出|打印))")
_COMPARE = re.compile(r"前后两次|两次提交|两版代码|两次.{0,16}(?:区别|不同|差别|一样|相同)|"
                      r"上一(?:次|版).{0,30}(?:这次|这一版)|(?:这次|这一版).{0,30}上一(?:次|版)|"
                      r"(?:比|相比)上一(?:次|版).{0,20}(?:多|少|过|错)|"
                      r"(?:对比|比较).{0,12}(?:提交|代码|版本)")
_NAMED_COMPARE = re.compile(r"比较|相比|对比|比\s*第|(?:和|跟|与).{0,24}比|区别|差别|不同")
_CHANGE_QUESTION = re.compile(r"改了|改动|变了|变化|有(?:没有|什么|啥)(?:好处|进步|改善|提升)|好在哪")
_HISTORY_QUESTION = re.compile(r"(?:交|改|叫)了?(?:(?:这么|那么)多|好多|多(?:少)?)次|(?:当时|之前|上次).{0,10}(?:错|没过|修改)")
_PAST_CODE_CHANGE = re.compile(r"第\s*(\d+)\s*行[^。！？\n]{0,28}?(?:改成了|换成了|变成了|替换成了)")
_SPECULATION = re.compile(
    r"碰巧|凑巧|恰巧|侥幸|运气|随机抽|随机选|抽样|"
    r"(?:换|改用)(?:了|成|为)?(?:一批|另一批|另一组|不同的)(?:测试)?(?:输入|数据|用例|测试点)|"
    r"(?:两次|前后|这次|上次).{0,10}(?:输入|数据|测试点).{0,6}(?:不同|不一样|不同批|比较简单|更简单|更容易)"
)
_CHINESE = {**dict(zip("一二三四五六七八九", range(1, 10))), "两": 2}


def _number(text: str) -> int:
    if text.isdecimal():
        return int(text[:9])
    left, sep, right = text.partition("十")
    return (_CHINESE.get(left, 1) * 10 + _CHINESE.get(right, 0)) if sep else _CHINESE.get(text, 0)


def attempts_in(text: str) -> list[int]:
    return sorted({_number(match) for match in _ATTEMPT.findall(text)} - {0})


def asks_history(text: str) -> bool:
    return bool(attempts_in(text) or _COMPARE.search(text) or _HISTORY_QUESTION.search(text))


@dataclass
class Comparison:
    nos: tuple[int, int]
    submissions: tuple[dict, ...] = ()
    historical_io_uncertain: bool = False
    switched_focus: bool = False
    named_baseline: bool = False

    @property
    def tests(self) -> list[dict]:
        return [test for sub in self.submissions for test in sub["testCases"]]

    def aligned(self) -> list[dict]:
        """同一道题的评测记录按测试点 ID 对齐，每组标准输入输出只给一次。"""
        if len(self.submissions) != 2:
            return []
        before, after = ({row["testcaseId"]: row for row in sub["testCases"]} for sub in self.submissions)
        return [
            {"testcaseId": tid, "standardInput": old["standardInput"], "standardOutput": old["standardOutput"],
             "before": _run(old), "after": _run(after[tid])}
            for tid, old in before.items() if tid in after
        ]

    def same_testcases(self, aligned: list[dict]) -> bool:
        return (not self.historical_io_uncertain and bool(aligned)
                and all(len(sub["testCases"]) == len(aligned) for sub in self.submissions))

    def complete(self, aligned: list[dict]) -> bool:
        if not self.same_testcases(aligned) or any(not sub["sourceCode"] for sub in self.submissions):
            return False
        for row in aligned:
            for run in (row["before"], row["after"]):
                if run["resultCode"] in ("", None, "UNKNOWN", "PENDING"):
                    return False
                if not run["passed"] and (row["standardInput"] is None or row["standardOutput"] is None
                                          or run["programOutput"] is None or run["base64"] or run["outputTruncated"]):
                    return False
        return True

    def case_facts(self, aligned: list[dict]) -> str:
        """公开的结果计数和输出差异类型，来自已对齐的评测记录。"""
        first, second = self.nos
        improved = [row for row in aligned if row["before"]["passed"] is False and row["after"]["passed"] is True]
        fixed_format = sum(
            row["before"]["resultCode"] == "WA" and row["before"]["programOutput"] != row["standardOutput"]
            and "".join(row["before"]["programOutput"].split()) == "".join(row["standardOutput"].split())
            for row in improved
        )
        before = sum(row["before"]["passed"] is True for row in aligned)
        after = sum(row["after"]["passed"] is True for row in aligned)
        facts = f"两次使用同一组测试点。第 {first} 次通过 {before}/{len(aligned)} 组，第 {second} 次通过 {after}/{len(aligned)} 组。"
        if improved:
            facts += f"有 {len(improved)} 组由未通过变为通过。"
        if fixed_format:
            facts += f"其中 {fixed_format} 组原来的实际输出与标准输出只差空白字符。"
        return facts

    @cached_property
    def code_diff(self) -> changes.Change:
        if len(self.submissions) != 2:
            return changes.Change()
        first, second = (sub["sourceCode"] for sub in self.submissions)
        return changes.compare(first.splitlines() if first else None, second.splitlines() if second else None)

    def code_facts(self, diff: changes.Change) -> str:
        if not diff.available:
            return ""
        if not diff.changed:
            return "两版的代码行内容一致。"
        if not diff.hunks:
            return f"两版相比删了 {diff.changed} 行代码。"
        where = "、".join(str(a) if a == b else f"{a} 到 {b}" for a, b in diff.hunks[:4])
        more = "等" if len(diff.hunks) > 4 else ""
        return f"改动涉及第 {self.nos[1]} 次提交的第 {where}{more} 行。"

    def public_facts(self, hist: list[dict], aligned: list[dict], complete: bool) -> str:
        start, end = self.nos
        rows = {row["no"]: row for row in hist}
        if not self.submissions or any(no not in rows for no in self.nos):
            return f"目前还没取齐第 {start} 次和第 {end} 次的提交记录。"
        facts = []
        for no in self.nos:
            row = rows[no]
            done, total = row["cases"]
            facts.append(f"第 {no} 次通过 {done}/{total} 组" if total else f"第 {no} 次{row['result']}")
        code_facts = self.code_facts(self.code_diff)
        summary = "，".join(facts) + "。" + code_facts
        if self.historical_io_uncertain:
            return summary + "测试点有更新记录，当前保存的输入和标准输出无法直接用于解释当时的两次结果。"
        if not complete:
            if self.same_testcases(aligned):
                summary += "两次使用同一组测试点。"
            return summary + "现有评测证据不足，无法确定通过数变化的原因。"
        return self.case_facts(aligned) + code_facts


def _run(row: dict) -> dict:
    return {k: row.get(k) for k in ("resultCode", "passed", "programOutput", "firstDiffLine",
                                     "outputTruncated", "base64")}


def previous_focus(s: Session, sub: m.Submission, message_id: int) -> int | None:
    """从上一条学生消息的绑定记录读取选中的版本。"""
    return s.scalar(select(m.ChatStep.submission_id).where(
        m.ChatStep.message_id == message_id, m.ChatStep.user_id == sub.user_id,
        m.ChatStep.server_id == sub.server_id, m.ChatStep.problem_id == sub.problem_id,
        m.ChatStep.bind_by == "ask_why", m.ChatStep.submission_id != sub.submission_id))


def comparison(s: Session, sub: m.Submission, attempt_no: int, text: str, now: datetime,
               switched_from: int | None = None) -> Comparison | None:
    """对比显式提到的两次提交，或接着上一条消息切换的两个版本。"""
    nos = attempts_in(text)
    named_baseline = len(nos) == 1 and nos[0] != attempt_no and bool(_NAMED_COMPARE.search(text) or _COMPARE.search(text))
    switched_focus = not nos and switched_from is not None
    if len(nos) == 2:
        pair = (nos[0], nos[1])
    elif named_baseline:
        pair = (nos[0], attempt_no)
    elif switched_focus:
        subs = catalog.user_submissions(s, sub.user_id, sub.server_id, sub.problem_id, until=now)
        previous_no = next((no for no, row in enumerate(subs, 1) if row.submission_id == switched_from), None)
        if previous_no is None or previous_no == attempt_no:
            return None
        pair = (previous_no, attempt_no)
    elif len(nos) == 1 and _CHANGE_QUESTION.search(text):
        pair = (nos[0] - 1, nos[0])
    elif len(nos) < 2 and _COMPARE.search(text):
        end = nos[0] if nos else attempt_no
        pair = (end - 1, end)
    else:
        return None
    start, end = min(pair), max(pair)
    if start < 1:
        return None
    if not switched_focus:
        subs = catalog.user_submissions(s, sub.user_id, sub.server_id, sub.problem_id, until=now)
    if end > len(subs):
        return Comparison(pair, named_baseline=named_baseline)
    evidence = tuple(assemble_submission(s, sub.server_id, subs[no - 1].submission_id,
                                         include_problem_text=False) for no in pair)
    tids = {row["testcaseId"] for version in evidence for row in version["testCases"]}
    # 旧评测只有 run 记录，标准输入输出来自当前题库。若已发现改动，不能拿现有文件当历史证据。
    changed = s.scalar(select(m.Archive.id).where(
        m.Archive.server_id == sub.server_id, m.Archive.kind == "testcase", m.Archive.target_id.in_(tids),
        m.Archive.archived_at >= subs[start - 1].submit_time).limit(1)) if tids else None
    return Comparison(pair, evidence, changed is not None, switched_focus, named_baseline)


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


_SYSTEM = """你是 C 语言课程的助教。一名学生回头问了一个问题。直接回答他问的这句话。
他问为什么交了很多次，解释反复失败的主因、哪些改动真正有帮助。只提有解释价值的几次提交。
他点名某一次，先讲那一次所问的事。问这次改了什么，就和前一次对照，讲清它实际改的代码、哪些问题仍在。
如果问的是最后怎么修好的，讲后来通过的那次实际改动，并分清版本。短追问结合对话理解，不能把以前的回复当评测证据。
先看评测事实，再根据对应版本的源码解释。错误标注只作为线索。缺评测明细时照样解释能确认的改动，说明哪些原因尚无法确定。
不要逐次抄摘要，已经列明的评测事实无需复述。不泄露隐藏测试数据、学生姓名或学号，不手抄代码或输出 Markdown 代码块。
涉及具体源码时用 quotes 引用原版，attempt 填提交次数，from/to 选连续上下文，mark 只标关键行。一般给一到两处引用。
只输出 JSON：{"reply":"针对这句提问的解释","quotes":[{"attempt":4,"from":6,"to":8,"mark":[7]}]}"""


def _quote(sources: dict[int, str], attempt: int, start: int, end: int, marks: list[int]) -> dict:
    lines = sources[attempt].split("\n")
    a, b = quote_range(lines, start, end)
    where = f"第 {a} 行" if a == b else f"第 {a} 到 {b} 行"
    return {"t": "code", "caption": f"你的第 {attempt} 次提交，{where}", "start": a,
            "lines": dedent(lines[a - 1:b]), "mark": sorted(set(marks))}


def reply(hist: list[dict], text: str, tests: list[dict], course: str, statement: str = "", recent: str = "",
          failure: str = "", who: tuple[str, str] = ("", ""), compare: Comparison | None = None,
          materials: PromptContext | None = None, focus: int = 0, student_notes: str = "",
          check: Callable[[Draft], list[str]] | None = None) -> Draft:
    """让模型答本轮追问，引用由程序从相应提交的原始源码取行。"""
    aligned = compare.aligned() if compare is not None else []
    complete = compare.complete(aligned) if compare is not None else False
    facts = compare.public_facts(hist, aligned, complete) if compare is not None else ""
    diff = compare.code_diff if compare is not None else None
    sources = materials.sources if materials else {}

    def validate(d: dict) -> Draft:
        out = d.get("reply")
        if not isinstance(out, str) or not (out := out.strip()):
            raise ValueError("reply 是空的")
        if "```" in out or _leaks(out, tests):
            raise ValueError("不能贴代码、不能出现测试数据")
        if _SPECULATION.search(out):
            raise ValueError("对照同一组固定测试点，不能用运气或数据变化解释")
        if diff is not None and diff.available and any(
            not any(start <= int(line) <= end for start, end in diff.hunks)
            for line in _PAST_CODE_CHANGE.findall(out)
        ):
            raise ValueError("把未修改的代码行说成改过了")
        quotes = {}
        requested = d.get("quotes")
        for item in requested[:2] if isinstance(requested, list) else []:
            if not isinstance(item, dict):
                continue
            no, a, b = (item.get(key) for key in ("attempt", "from", "to"))
            if not all(type(x) is int for x in (no, a, b)) or no not in sources \
                    or (compare is not None and no not in compare.nos) or (compare is None and focus and no != focus):
                continue
            raw_marks = item.get("mark")
            marks = [n for n in raw_marks if type(n) is int and a <= n <= b] if isinstance(raw_marks, list) else []
            if marks and 1 <= a <= b <= len(sources[no].split("\n")) and b - a < 8:
                quotes.setdefault(no, []).append(_quote(sources, no, a, b, marks))
        if compare is not None and diff.hunks and compare.nos[1] in sources \
                and (not quotes or (focus == compare.nos[1] and focus not in quotes)):
            a, _ = diff.hunks[0]
            no = compare.nos[1]
            quotes[no] = [_quote(sources, no, a, a, [a])]
        blocks = quotes.pop(focus, []) + [block for group in quotes.values() for block in group]
        draft = Draft(before=out, after=facts, submission_quotes=blocks[:2])
        if check is not None and (problems := check(draft)):
            raise ValueError("；".join(problems))
        return draft

    system = course_prompt(_SYSTEM + "\n\n" + JUDGE_EVIDENCE_GUIDANCE, course)
    document = materials.document if materials else (
        f"【题目】\n{pdftext.for_llm(llm.strip_identity(statement, *who))[:1500]}" if statement else "")
    images = materials.images if materials else []
    user = (materials.prefix + f"\n\n【全部提交目录】\n{materials.index}\n\n") if materials else ""
    if compare is not None:
        first, second = compare.nos
        user += f"【本轮相关提交次数】\n[{first},{second}]\n\n"
        if compare.switched_focus:
            current = focus or second
            previous = first if current == second else second
            user += f"【提交引用】\n上一条消息是第 {previous} 次，本轮是第 {current} 次。\n\n"
        user += f"【已确认的评测事实】\n{facts}\n\n"
        user += "【源码改动（del 为原行，add 为新行）】\n" + json.dumps(
            [{**row, "text": llm.strip_identity(row["text"], *who)} for row in diff.excerpt],
            ensure_ascii=False) + "\n\n"
    if focus:
        user += f"【本轮重点】\n第 {focus} 次提交。\n\n"
    related = hist if compare is None else [h for h in hist if h["no"] in compare.nos]
    user += "【提交摘要（改动相对于各自上一次提交）】\n" + facts_text(related)
    if failure and materials is None:
        user += f"\n\n【最后一次没过时】\n{failure}"
    if student_notes:
        user += f"\n\n【学生情况】\n{student_notes}"
    if recent:
        user += f"\n\n【最近的对话】\n{recent}"
    user += "\n\n【他问的】\n" + llm.strip_identity(text, *who)
    return llm.call_json("retro_compare" if compare is not None else "retro_reply", system, user, validate,
                         retries=1, max_tokens=None, images=images, document=document)
