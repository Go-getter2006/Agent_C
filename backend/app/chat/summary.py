"""一道题通过以后，智能体在这道题的对话里先说一段总结：你是怎么改对的。不再问“哪里卡住了”。

- 前面没过的时候是什么问题（程序标注的错误，学生端的说法）、最后一次改了哪几行（摘出他自己的代码）；
- 如果是大改（改了一半以上，或者 20 行以上），多半是换了一种做法：有大模型时，让它对比两种做法
  （原来的思路、现在的思路、区别和原来卡在哪），再问他为什么换；没有大模型时直接问他为什么换。
- 最后问一句回顾的问题（当时卡住的关键是什么），让他自己说出来——自己解释比看答案记得牢。

只对两天内的新通过发；一次就过的不发；每道题只发一次。不出现测试点的数据（对比也要过泄露检查）。
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, changes, db, llm
from .. import models as m
from ..taxonomy import course_prompt, error_name
from .proactive import _course, fix_excerpt

log = logging.getLogger("chat.summary")
RECENT = dt.timedelta(days=2)
REFLECT = "回头看，你觉得当时卡住的关键是什么？用一句话说说。"


def _sent(s: Session, sub: m.Submission) -> bool:
    rows = s.scalars(select(m.ChatMessage.content).where(
        m.ChatMessage.user_id == sub.user_id, m.ChatMessage.server_id == sub.server_id,
        m.ChatMessage.problem_id == sub.problem_id, m.ChatMessage.role == "agent")).all()
    return any(isinstance(c, dict) and (c.get("summary") or {}).get("submission") == sub.submission_id for c in rows)


_SYSTEM = """你是 C 语言课程的助教。一名学生一道题前面没通过，后来大幅改了代码、通过了，看起来换了一种做法。
对比他前后两份代码的思路。只输出 JSON：
{"before":"原来的做法，一句话","after":"现在的做法，一句话","compare":"两种做法的区别，原来的写法问题可能出在哪，不超过 100 字","question":"问他为什么换做法的一句话，以问号结尾"}
要求：只说思路和写法，不要贴大段代码；不要提测试数据、具体的输入输出；不要提学号、姓名；用“你”称呼他。"""


def _compare_llm(before: list[str], after: list[str], tests: list[dict], course: str) -> list[str] | None:
    from .validate import _leaks

    def validate(d: dict) -> list[str]:
        parts = [str(d.get(k) or "").strip() for k in ("before", "after", "compare", "question")]
        if not all(parts):
            raise ValueError("四项都要有")
        if len(parts[2]) > 160 or any(len(x) > 80 for x in (parts[0], parts[1], parts[3])):
            raise ValueError("太长了")
        if not parts[3].endswith(("？", "?")):
            raise ValueError("question 要以问号结尾")
        if _leaks(" ".join(parts), tests):
            raise ValueError("不能出现测试数据")
        return parts

    user = ("【原来的代码】\n" + llm.strip_identity("\n".join(before))[:3000] +
            "\n\n【通过的代码】\n" + llm.strip_identity("\n".join(after))[:3000])
    try:
        return llm.call_json("summary_compare", course_prompt(_SYSTEM, course), user, validate, retries=1, max_tokens=400)
    except llm.LLMUnavailable:
        return None


def on_pass(s: Session, sub: m.Submission, now: dt.datetime | None = None) -> m.ChatMessage | None:
    """sub 是这道题第一次通过的提交。之前没过过的不发（一次就过）。"""
    now = now or db.now()
    if sub.judgement != "AC" or (sub.submit_time and now - sub.submit_time > RECENT):
        return None
    if not catalog.is_visible(s, sub.user_id, sub.server_id, sub.problem_id) or _sent(s, sub):
        return None
    subs = [x for x in catalog.user_submissions(s, sub.user_id, sub.server_id, sub.problem_id)
            if x.submission_id <= sub.submission_id]
    if any(x.judgement == "AC" for x in subs[:-1]):
        return None   # 不是第一次通过
    failed = [x for x in subs if x.judgement not in ("AC", "PENDING")]
    if not failed:
        return None   # 一次就过
    first_lines, last_lines, ac_lines = (changes.code_lines(s, failed[0]), changes.code_lines(s, failed[-1]),
                                         changes.code_lines(s, sub))
    names = list(dict.fromkeys(error_name(c, student_facing=True) for x in failed for c in changes.error_codes(s, x)))
    names = [n for n in names if n]
    whole = changes.compare(first_lines, ac_lines)
    last = changes.compare(last_lines, ac_lines)

    blocks: list[dict] = [{"t": "p", "text": f"通过了！这道题一共交了 {len(subs)} 次。来回顾一下你是怎么改对的。"}]
    if names:
        blocks.append({"t": "p", "text": "前面没过的时候，问题出在：" + "、".join(names[:3]) + "。"})
    big = whole.available and whole.big
    if big:
        compared = None
        if llm.available():
            from ..chat.context import Ctx, tutor_context

            ctx = Ctx(user_id=sub.user_id, course=_course(s, sub.server_id))
            ctx.problem = catalog.problem_info(s, sub.server_id, sub.problem_id)
            tutor_context(s, ctx, now)
            compared = _compare_llm(first_lines or [], ac_lines or [], ctx.all_tests, ctx.course)
        if compared:
            before, after, diff, question = compared
            blocks += [{"t": "p", "text": f"看你大幅改了代码（改了 {whole.changed} 行），换了一种做法："},
                       {"t": "p", "text": f"原来：{before}"}, {"t": "p", "text": f"现在：{after}"},
                       {"t": "p", "text": diff}, {"t": "p", "text": question}]
        else:
            blocks.append({"t": "p", "text": f"看你大幅改了代码（改了 {whole.changed} 行），像是换了一种做法。"
                                             "能说说为什么换吗？原来的写法卡在哪里？"})
    else:
        if last.available and last_lines and ac_lines:
            excerpt = fix_excerpt(last_lines, ac_lines)
            if excerpt is not None:
                start, lines, marks = excerpt
                end = start + len(lines) - 1
                blocks.append({"t": "p", "text": "最后一次是这样改对的："})
                blocks.append({"t": "code", "caption": f"你通过的代码，第 {start} 到 {end} 行" if end > start else f"你通过的代码，第 {start} 行",
                               "start": start, "lines": lines, "mark": marks})
        blocks.append({"t": "p", "text": REFLECT})

    msg = m.ChatMessage(user_id=sub.user_id, server_id=sub.server_id, problem_id=sub.problem_id,
                        course=_course(s, sub.server_id), role="agent", kind="text",
                        created_at=now, content={"blocks": blocks, "actions": [],
                                                 "summary": {"submission": sub.submission_id, "big": big}})
    s.add(msg)
    s.flush()
    llm.flush_records(s)
    log.info("通过后的总结（%s）", "大改" if big else "小改")
    return msg


def drop_reflection(msg: m.ChatMessage) -> None:
    """紧接着要出小题时，去掉最后那句回顾的问题，不同时问两件事。"""
    content = dict(msg.content or {})
    blocks = [b for b in content.get("blocks", []) if b.get("text") != REFLECT]
    content["blocks"] = blocks
    msg.content = content
