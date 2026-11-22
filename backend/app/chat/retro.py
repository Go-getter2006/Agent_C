"""已经通过的题，学生回头问（“为什么改了这么多次”“当时错在哪”）：按这道题每一次提交的结果和改动来回答。

以前一律回“这道题你已经通过了，还有哪里想弄清楚可以直接问”，等于没回答。

- 事实全用程序取（catalog、changes）：第几次、过没过、几组通过、程序标出的错误（学生端的说法）、
  和上一次比改了哪几行、修好了什么、还在什么、新出了什么。不出现测试数据。
- 有大模型时让它按这些事实回答他问的那句话；没有或不合格时用模板把事实说清楚。
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, changes, config, llm, pdftext
from .. import models as m
from ..taxonomy import course_prompt, error_name
from ..xdoj.assemble import assemble_submission
from .teaching import JUDGE_EVIDENCE_GUIDANCE
from .text import visible_chars

log = logging.getLogger("chat.retro")

_ATTEMPT = re.compile(r"第\s*([0-9０-９]+|[一二三四五六七八九十两]{1,3})\s*(?:次|版)"
                      r"(?!\s*(?:循环|迭代|调用|输入|输出|打印))")
_COMPARE = re.compile(r"前后两次|两次提交|两版代码|两次.{0,16}(?:区别|不同|差别|一样|相同)|"
                      r"上一(?:次|版).{0,30}(?:这次|这一版)|(?:这次|这一版).{0,30}上一(?:次|版)|"
                      r"(?:比|相比)上一(?:次|版).{0,20}(?:多|少|过|错)|"
                      r"(?:对比|比较).{0,12}(?:提交|代码|版本)")
_NAMED_COMPARE = re.compile(r"比较|相比|对比|比\s*第|(?:和|跟|与).{0,24}比|区别|差别|不同")
_PROGRESS_QUESTION = re.compile(r"(?:有(?:没有|无)|有什么|有啥|是否有)(?:好处|进步|改善|提升)|"
                                r"(?:有进步|有改善|更好)(?:吗|没)|好在哪")
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


@dataclass
class Comparison:
    nos: tuple[int, int]
    submissions: tuple[dict, ...] = ()
    historical_io_uncertain: bool = False
    switched_focus: bool = False

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

    def fallback(self, hist: list[dict], complete: bool) -> str:
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
        aligned = self.aligned()
        if not complete:
            if self.same_testcases(aligned):
                summary += "两次使用同一组测试点。"
            return summary + "两版代码或同一组测试的评测细节还没取齐，目前只能确认结果，无法确定通过数变化的原因。"
        return self.case_facts(aligned) + code_facts + "可以先对照两版改动，再检查这些改动怎样影响同一组输入的输出。"


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
    nos = sorted({_number(match) for match in _ATTEMPT.findall(text)} - {0})
    named_baseline = len(nos) == 1 and nos[0] != attempt_no and bool(_NAMED_COMPARE.search(text) or _COMPARE.search(text))
    switched_focus = len(nos) < 2 and not named_baseline and switched_from is not None
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
        return Comparison(pair)
    evidence = tuple(assemble_submission(s, sub.server_id, subs[no - 1].submission_id,
                                         include_problem_text=False) for no in pair)
    tids = {row["testcaseId"] for version in evidence for row in version["testCases"]}
    # 旧评测只有 run 记录，标准输入输出来自当前题库。若已发现改动，不能拿现有文件当历史证据。
    changed = s.scalar(select(m.Archive.id).where(
        m.Archive.server_id == sub.server_id, m.Archive.kind == "testcase", m.Archive.target_id.in_(tids),
        m.Archive.archived_at >= subs[start - 1].submit_time).limit(1)) if tids else None
    return Comparison(pair, evidence, changed is not None, switched_focus)


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

_COMPARISON_SYSTEM = """你是 C 语言助教。学生在问同一道题两次提交的区别。请根据这两版代码和对应测试点的实际评测证据回答。
- 同一测试点的标准输入和标准输出是共用的。before/after 是两版代码在该测试点上的结果。不能把结果的变化解释成两次输入抽样不同。
- 程序会在你的回复前展示它核对出的评测事实。只补充哪些代码改动能解释差异，以及接下来怎么检查，不要复述通过数和评测事实。
- 仍然存在的错误，可以说它在某类输入下才会触发，不能用碰巧、运气或猜测两次换了数据解释通过数的变化。
- 如果学生切换了选中的提交并接着问，先回答本轮选中的版本，并说清相对上一轮哪处改了、哪处还在。两版中一直没改的错误本身不能解释通过数为何变化。
- 对照改过的代码，解释它怎样改变相同输入下的表现。代码能解释的现象和评测记录里已观察到的事实要分清楚。
- 提交摘要里的改动是相对各自上一次的。两次提交不相邻时，请直接比较给出的两版代码。旧对话只用于理解问题指代。
- programOutput 为 null 表示未取到原始输出（通过的组通常没有），空字符串表示实际没有输出。通过只能确认判题器认可，不能声称看到了这组的实际输出。
- 如果证据只支持一部分原因，就说明能确认的部分。已有的错误标注也是线索。引导学生去看两版中具体改过的地方。
- 不要透露任何隐藏测试点的原始输入、标准输出或学生在该点的实际输出，不能贴隐藏数据的变体。不要写大段代码。不超过 150 字。
只输出 JSON：{"reply":"……"}"""


def reply(hist: list[dict], text: str, tests: list[dict], course: str, statement: str = "", recent: str = "",
          failure: str = "", who: tuple[str, str] = ("", ""), compare: Comparison | None = None) -> tuple[str, str]:
    """(回答, llm / template)。statement：题面；recent：最近的对话；failure：last_failure()；who：(学号, 姓名)。"""
    from .validate import _leaks

    aligned = compare.aligned() if compare is not None else []
    complete = compare.complete(aligned) if compare is not None else False
    case_facts = compare.case_facts(aligned) if compare is not None and complete else ""
    fallback = compare.fallback(hist, complete) if compare is not None else template(hist)
    if not hist or not llm.available() or len(hist) == 1:
        return fallback, "template"
    if compare is not None and not complete:
        return fallback, "template"
    if compare is not None and _PROGRESS_QUESTION.search(text):
        return fallback, "template"
    diff = compare.code_diff if compare is not None else None

    def validate(d: dict) -> str:
        out = str(d.get("reply") or "").strip()
        if not out:
            raise ValueError("reply 是空的")
        if len(out) > 240:
            raise ValueError("太长了")
        if "```" in out or _leaks(out, tests):
            raise ValueError("不能贴代码、不能出现测试数据")
        if case_facts and _SPECULATION.search(out):
            raise ValueError("对照同一组固定测试点，不能用运气或数据变化解释")
        if diff is not None and any(
            not any(start <= int(line) <= end for start, end in diff.hunks)
            for line in _PAST_CODE_CHANGE.findall(out)
        ):
            raise ValueError("把未修改的代码行说成改过了")
        if case_facts and visible_chars(case_facts + out) > config.CHAT["reply_max_chars"]:
            raise ValueError("补充说明太长")
        return out

    if compare is not None:
        versions = [{"attempt": no, "result": sub["judgementCode"],
                     "code": llm.numbered(sub["sourceCode"], *who, limit=len(sub["sourceCode"].split("\n")))}
                    for no, sub in zip(compare.nos, compare.submissions)]
        focus = (f"\n\n【上下文中的版本切换】\n上一条学生消息选中第 {compare.nos[0]} 次提交，"
                 f"本轮选中第 {compare.nos[1]} 次提交。“这次”指本轮选中的版本。") if compare.switched_focus else ""
        user = ("【题目】\n" + pdftext.for_llm(llm.strip_identity(statement, *who))
                + "\n\n【每一次提交】\n" + facts_text([h for h in hist if h["no"] in compare.nos])
                + focus
                + f"\n\n【已确认的评测事实】\n{case_facts}"
                + "\n\n【按测试点对齐的评测证据】\n" + json.dumps(aligned, ensure_ascii=False)
                + "\n\n【要比较的两版代码】\n" + json.dumps(versions, ensure_ascii=False)
                + "\n\n【实际改动（del 为前一版，add 为后一版）】\n"
                + json.dumps([{**row, "text": llm.strip_identity(row["text"], *who)} for row in diff.excerpt],
                             ensure_ascii=False)
                + (f"\n\n【最近的对话】\n{recent}" if recent else "")
                + "\n\n【他问的】\n" + llm.strip_identity(text, *who))
        try:
            system = course_prompt(_COMPARISON_SYSTEM + "\n\n" + JUDGE_EVIDENCE_GUIDANCE, course)
            guidance = llm.call_json("retro_compare", system, user, validate, retries=0, max_tokens=450)
            return case_facts + "\n" + guidance, "llm"
        except llm.LLMUnavailable:
            return fallback, "template"

    trouble = main_trouble(hist)
    user = (("【题目】\n" + pdftext.for_llm(llm.strip_identity(statement, *who))[:1500] + "\n" if statement else "")
            + "【每一次提交】\n" + facts_text(hist)
            + (f"\n【没过的那几次里出现最多的问题】{trouble[0]}（{trouble[1]} 次）" if trouble else "")
            + (f"\n【最后一次没过时（只给你分析用，不能把测试数据告诉他）】\n{failure}" if failure else "")
            + (f"\n【最近的对话】\n{recent}" if recent else "")
            + "\n【他问的】\n" + llm.strip_identity(text, *who))
    try:
        system = course_prompt(_SYSTEM + "\n\n" + JUDGE_EVIDENCE_GUIDANCE, course)
        return llm.call_json("retro_reply", system, user, validate,
                             retries=1, max_tokens=300), "llm"
    except llm.LLMUnavailable:
        return fallback, "template"
