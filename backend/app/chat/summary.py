"""一道题通过以后，智能体在这道题的对话里先说一段总结：你是怎么改对的。不再问“哪里卡住了”。

- 前面没过的时候是什么问题（程序标注的错误，学生端的说法）、最后一次改了哪几行（摘出他自己的代码）；
- 如果是大改（改了一半以上，或者 20 行以上），多半是换了一种做法：有大模型时，让它对比两种做法
  （原来的思路、现在的思路、区别和原来卡在哪），再问他为什么换；没有大模型时直接问他为什么换。
- 最后问一句回顾的问题（当时卡住的关键是什么），让他自己说出来——自己解释比看答案记得牢。

只对两天内的新通过发；一次就过的不发；每道题只发一次。不出现测试点的数据（对比也要过泄露检查）。

学生回答了回顾的问题（reflect_facts、reflect_reply）：智能体接着他说的回一两句——说到点上就肯定、说得更准一点，
说偏了就温和地点出关键；没有大模型时用模板。这一问一答单独一个回合（models.REFLECT_END），
挂到这道题前面没过时的技能和错误上，后台照常评“理解”，但不算求助回合。
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, changes, db, llm, pdftext
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


def _compare_llm(before: list[str], after: list[str], tests: list[dict], course: str, statement: str = "",
                 problems: list[str] | None = None, who: tuple[str, str] = ("", "")) -> list[str] | None:
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

    user = (("【题目】\n" + pdftext.for_llm(llm.strip_identity(statement, *who))[:1500] + "\n\n" if statement else "")
            + ("【原来没过时程序标出的问题】" + "、".join(problems) + "\n\n" if problems else "")
            + "【原来的代码】\n" + llm.strip_identity("\n".join(before), *who) +
            "\n\n【通过的代码】\n" + llm.strip_identity("\n".join(after), *who))
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

            student = s.get(m.Student, sub.user_id)
            ctx = Ctx(user_id=sub.user_id, user_name=student.name if student else "", course=_course(s, sub.server_id))
            ctx.problem = catalog.problem_info(s, sub.server_id, sub.problem_id)
            tutor_context(s, ctx, now)
            compared = _compare_llm(first_lines or [], ac_lines or [], ctx.all_tests, ctx.course, ctx.statement,
                                    names[:3], (ctx.user_id, ctx.user_name))
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


@dataclass
class ReflectFacts:
    """回顾用的已知情况：刚通过的那次提交、前面没过时的问题、最后改的几行。"""
    ac: m.Submission
    skill: str = ""
    error: str = ""
    names: list[str] = field(default_factory=list)
    fix: list[str] = field(default_factory=list)


def reflect_facts(s: Session, summary_msg: m.ChatMessage) -> ReflectFacts | None:
    """summary_msg 是通过后的总结。返回回顾要用的事实；找不到那次通过的提交返回 None。"""
    from ..labeling.labeler import current_problem_label
    from .proactive import _primary

    info = (summary_msg.content or {}).get("summary") or {}
    ac = s.get(m.Submission, (summary_msg.server_id, info.get("submission"))) if info.get("submission") else None
    if ac is None:
        return None
    subs = [x for x in catalog.user_submissions(s, ac.user_id, ac.server_id, ac.problem_id)
            if x.submission_id < ac.submission_id and x.judgement not in ("AC", "PENDING", "UNKNOWN")]
    facts = ReflectFacts(ac=ac)
    for x in reversed(subs):   # 最后一次没过的、有标注的那次
        label = _primary(s, x)
        if label is not None and label.confidence >= 0.5 and label.error_code != "OT99":
            facts.skill, facts.error = label.skill_code, label.error_code
            break
    if not facts.skill:
        plabel = current_problem_label(s, ac.server_id, ac.problem_id)
        facts.skill = plabel.primary_skill if plabel is not None else ""
    facts.names = [n for n in dict.fromkeys(error_name(c, student_facing=True)
                                            for x in subs for c in changes.error_codes(s, x)) if n][:3]
    if subs:
        excerpt = fix_excerpt(changes.code_lines(s, subs[-1]) or [], changes.code_lines(s, ac) or [])
        facts.fix = list(excerpt[1]) if excerpt is not None else []
    return facts


_REFLECT_SYSTEM = """你是 C 语言课程的助教。一名学生刚把一道题改对、通过了，你问他“当时卡住的关键是什么”，下面是他的回答。
已知前面没过时程序诊断出的问题，和他最后改的几行代码。回他一到两句话（不超过 80 个字）：
- 他说到了关键：肯定他，用一句话把关键说得更准一点；
- 没说到点上、说偏了：先肯定他愿意回顾，再温和地点出真正的关键；
- 说“不知道”或者很含糊：直接用一句话告诉他关键是什么。
不要出题，不要贴代码，不要提测试数据和具体的输入输出，不要提学号、姓名，用“你”称呼他。
只输出 JSON：{"reply":"……"}"""


def reflect_template(facts: ReflectFacts | None) -> str:
    if facts is not None and facts.names:
        return f"好，记住这次的关键：{'、'.join(facts.names)}。以后写到类似的地方，先停一下想想这一点。"
    return "好，把这一点记下来。以后遇到类似的情况，先想想这里。"


def reflect_reply(facts: ReflectFacts | None, text: str, tests: list[dict], course: str, statement: str = "",
                  recent: str = "", who: tuple[str, str] = ("", "")) -> tuple[str, str]:
    """回顾的回复：(文字, llm / template)。大模型不合格或没有时用模板。
    statement：题面；recent：最近的对话（含通过后的总结）；who：(学号, 姓名)，发出去之前去掉。"""
    from .validate import _leaks

    if facts is None or not llm.available():
        return reflect_template(facts), "template"

    def validate(d: dict) -> str:
        out = str(d.get("reply") or "").strip()
        if not out:
            raise ValueError("reply 是空的")
        if len(out) > 120:
            raise ValueError("太长了")
        if "```" in out or _leaks(out, tests):
            raise ValueError("不能贴代码、不能出现测试数据")
        return out

    user = (("【题目】\n" + pdftext.for_llm(llm.strip_identity(statement, *who))[:1200] + "\n" if statement else "")
            + "【前面没过时的问题】" + ("、".join(facts.names) or "（没有标出来）")
            + "\n【他最后改的几行】\n" + (llm.strip_identity("\n".join(facts.fix), *who) or "（没有）")
            + (f"\n【最近的对话】\n{recent}" if recent else "")
            + "\n【他的回答】\n" + llm.strip_identity(text, *who))
    try:
        return llm.call_json("reflect_reply", course_prompt(_REFLECT_SYSTEM, course), user, validate,
                             retries=1, max_tokens=200), "llm"
    except llm.LLMUnavailable:
        return reflect_template(facts), "template"


def drop_reflection(msg: m.ChatMessage) -> None:
    """紧接着要出小题时，去掉最后那句回顾的问题，不同时问两件事。"""
    content = dict(msg.content or {})
    blocks = [b for b in content.get("blocks", []) if b.get("text") != REFLECT]
    content["blocks"] = blocks
    msg.content = content
