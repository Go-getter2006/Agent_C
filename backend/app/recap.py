"""小题做完、反馈解释过以后：回顾他以前在同一处出过的错。程序整理事实，大模型做分析。

- 事实（程序从记录里取，学生端列出来、每条能点开）：
  - 没过的提交：失败证据（skill_evidence 里 submit_failed）记在这个技能上的，按题汇总：没过几次、错在哪
    （学生端说法）、后来第几次改对、改了哪几行。只列他看得到、这门课作业里的题；
  - 做错的小题：同一技能以前判断错、没改对，或者靠提示（追问）才改对的小题。
  同一技能的不够时，补同一知识点下别的技能的。最多 3 道题、2 道小题，从近到远。一条也没有就不回顾。
- 分析（大模型）：拿到这道小题、他这次怎么答的，以及那几道题当时出错的代码和后来怎么改的，说清楚这几次错在
  同一件事上的什么地方、和这道小题是什么关系、下次写的时候先检查什么。程序核对：不超过 200 字，不贴代码块，
  不出现测试数据。大模型这次没写出合格的，就只列事实，不拿模板凑一段分析。
每次作答只做一次，存在 quiz_attempts.recap；之后他可以接着问（复习答疑的上下文里带着这些）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, llm
from . import models as m
from .taxonomy import course_of, course_prompt, error_name, kp_of_skill, skill_name, skills_of_kp

log = logging.getLogger("recap")

MAX_PROBLEMS, MAX_QUIZZES = 3, 2
INTRO = "回顾一下你以前在这里出过的错："
OUTRO = "哪一道想再弄清楚，可以接着问。"


def _where(lines: list[tuple[int, int]]) -> str:
    if not lines:
        return ""
    a, b = lines[0]
    return f"第 {a} 行" if a == b else f"第 {a}～{b} 行"


def _problem_keys(s: Session, user_id: str, skills: list[str]) -> list[tuple[int, int]]:
    """这几个技能上记过“提交没过”的题，最近的在前。"""
    rows = s.scalars(select(m.SkillEvidence.source_key).where(
        m.SkillEvidence.user_id == user_id, m.SkillEvidence.event_type == "submit_failed",
        m.SkillEvidence.skill_code.in_(skills)).order_by(m.SkillEvidence.ts.desc())).all()
    out: list[tuple[int, int]] = []
    for key in rows:
        parts = (key or "").split(":")
        if len(parts) == 4 and parts[0] == "fail" and parts[1].isdigit() and parts[2].isdigit():
            k = (int(parts[1]), int(parts[2]))
            if k not in out:
                out.append(k)
    return out


def _skill_errors(s: Session, server_id: int, user_id: str, problem_id: int, skills: list[str]) -> list[str]:
    """这道题没过的提交里，程序标在这几个技能上的错误（学生端说法），出现多的在前。"""
    subs = s.scalars(select(m.Submission.submission_id).where(
        m.Submission.server_id == server_id, m.Submission.user_id == user_id,
        m.Submission.problem_id == problem_id, m.Submission.judgement != "AC")).all()
    if not subs:
        return []
    codes = s.scalars(select(m.SubmissionLabel.error_code).where(
        m.SubmissionLabel.server_id == server_id, m.SubmissionLabel.submission_id.in_(subs),
        m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.skill_code.in_(skills),
        m.SubmissionLabel.confidence >= 0.5)).all()
    counts: dict[str, int] = {}
    for c in codes:
        name = error_name(c, student_facing=True)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [n for n, _k in sorted(counts.items(), key=lambda x: -x[1])]


def _problem_item(s: Session, user_id: str, server_id: int, problem_id: int, skills: list[str]) -> dict | None:
    from .chat import retro

    info = catalog.problem_info(s, server_id, problem_id)
    hist = retro.history(s, user_id, server_id, problem_id)
    fails = [h for h in hist if not h["passed"]]
    if info is None or not fails:
        return None
    errors = _skill_errors(s, server_id, user_id, problem_id, skills)
    if not errors:
        trouble = retro.main_trouble(hist)
        errors = [trouble[0]] if trouble else []
    passed = next((h for h in hist if h["passed"]), None)
    text = f"没过 {len(fails)} 次" + (f"，问题是“{errors[0]}”" if errors else "")
    if passed is not None:
        text += f"；第 {passed['no']} 次改对了" + (f"，改了{_where(passed['lines'])}" if passed["lines"] else "")
    else:
        text += "；还没通过"
    return {"kind": "problem", "key": info.key, "title": info.title, "text": text + "。",
            "_ref": [server_id, problem_id]}


_QUIZ_WHAT = {
    ("wrong_judge", True): "没看出代码里的错",
    ("wrong_judge", False): "把正确的代码判成了有错",
    ("wrong_fix", True): "看出了有错，但没改对",
    ("right_second", True): "看了提示才改对",
}


def _quiz_items(s: Session, attempt: m.QuizAttempt, skills: list[str], limit: int) -> list[dict]:
    rows = s.execute(select(m.QuizAttempt, m.QuizItem).join(m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id).where(
        m.QuizAttempt.user_id == attempt.user_id, m.QuizAttempt.skill_code.in_(skills),
        m.QuizAttempt.state == "done", m.QuizAttempt.id != attempt.id, m.QuizAttempt.skipped.is_(False),
        m.QuizAttempt.outcome.in_(("wrong_judge", "wrong_fix", "right_second")))
        .order_by(m.QuizAttempt.finished_at.desc()).limit(limit)).all()
    out = []
    for a, item in rows:
        what = _QUIZ_WHAT.get((a.outcome, bool(item.has_bug)))
        if not what:
            continue
        when = a.finished_at or a.created_at
        title = (item.scene or item.stem or "小题").rstrip("？?。")
        out.append({"kind": "quiz", "attemptId": a.id, "title": f"{when.month} 月 {when.day} 日的小题「{title}」",
                    "text": what + "。"})
    return out


def build(s: Session, attempt: m.QuizAttempt) -> dict | None:
    """这道小题做完以后的回顾：{intro, items, outro}；一条也没有返回 None。"""
    skill = attempt.skill_code or ""
    if not skill or attempt.state != "done" or attempt.skipped:
        return None
    course = course_of(skill)
    groups = [[skill], [c for c in skills_of_kp(kp_of_skill(skill)) if c != skill]]
    problems: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for skills in groups:
        if not skills:
            continue
        for server_id, problem_id in _problem_keys(s, attempt.user_id, skills):
            if len(problems) >= MAX_PROBLEMS:
                break
            if (server_id, problem_id) in seen or catalog.server_course(s, server_id) != course \
                    or not catalog.is_visible(s, attempt.user_id, server_id, problem_id, course):
                continue
            seen.add((server_id, problem_id))
            row = _problem_item(s, attempt.user_id, server_id, problem_id, skills)
            if row is not None:
                problems.append(row)
    quizzes = _quiz_items(s, attempt, [skill], MAX_QUIZZES)
    if len(quizzes) < MAX_QUIZZES and groups[1]:
        quizzes += _quiz_items(s, attempt, groups[1], MAX_QUIZZES - len(quizzes))
    items = problems + quizzes
    if not items:
        return None
    return {"intro": INTRO, "items": items, "outro": OUTRO}


def _excerpt(lines: list[str], line: int | None, around: int = 2) -> str:
    if not lines:
        return ""
    if not line or not 1 <= line <= len(lines):
        return "\n".join(f"{i:>3}| {x}" for i, x in enumerate(lines[:12], start=1))
    lo, hi = max(1, line - around), min(len(lines), line + around)
    return "\n".join(f"{i:>3}| {lines[i - 1]}" for i in range(lo, hi + 1))


def _problem_material(s: Session, user_id: str, server_id: int, problem_id: int, skills: list[str]) -> tuple[str, list[dict]]:
    """给大模型看的：那道题最后一次没过时出错的那几行（带行号）、后来改对的那几行。还有这道题的测试点（只用来查泄露）。"""
    from .xdoj.assemble import source_text
    from .xdoj.util import decode_bytes

    subs = s.scalars(select(m.Submission).where(
        m.Submission.server_id == server_id, m.Submission.user_id == user_id, m.Submission.problem_id == problem_id)
        .order_by(m.Submission.submission_id)).all()
    failed = [x for x in subs if x.judgement not in ("AC", "PENDING", "UNKNOWN", "")]
    parts = []
    if failed:
        bad = failed[-1]
        lab = s.scalars(select(m.SubmissionLabel).where(
            m.SubmissionLabel.server_id == server_id, m.SubmissionLabel.submission_id == bad.submission_id,
            m.SubmissionLabel.is_current.is_(True)).order_by(m.SubmissionLabel.role.desc())).first()
        code = (source_text(s, server_id, bad.submission_id) or "").replace("\r\n", "\n").split("\n")
        parts.append("没过时出错的地方：\n" + llm.strip_identity(_excerpt(code, lab.line if lab else None), user_id))
        good = next((x for x in subs if x.judgement == "AC" and x.submission_id > bad.submission_id), None)
        if good is not None:
            fixed = (source_text(s, server_id, good.submission_id) or "").replace("\r\n", "\n").split("\n")
            parts.append("后来改对的代码：\n" + llm.strip_identity(_excerpt(fixed, lab.line if lab else None), user_id))
    tests = []
    for tc in s.scalars(select(m.Testcase).where(m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id)).all():
        tests.append({"standardInput": decode_bytes(tc.input_bytes, tc.input_encoding)[0] if tc.input_bytes is not None else None,
                      "standardOutput": decode_bytes(tc.output_bytes, tc.output_encoding)[0] if tc.output_bytes is not None else None})
    return "\n".join(parts), tests


_SYSTEM = """你是 C 语言课程的助教。一名学生刚做完一道复习小题，下面是这道小题、他这次的作答，以及他以前在同一处出过的错
（程序从记录里整理的事实，可信；还有那几道题当时出错的代码和后来改对的代码）。
请写一段回顾，直接对他说（用“你”）：
- 这几次错的是不是同一件事、同一件事里的什么地方（说具体：哪个变量、哪种写法、哪类输入）；
- 和刚才这道小题是什么关系；
- 下次写这类代码时先检查什么（一句话）。
不超过 200 字，不要贴代码块（可以提一个很短的写法，用反引号），不要提测试数据和具体的输入输出，不要编造没给出的事实。
只输出 JSON：{"analysis":"……"}"""


def analyze(s: Session, attempt: m.QuizAttempt, facts: dict) -> str:
    """大模型对这几次出错的分析；没写出合格的返回空串（只列事实）。"""
    from .chat.validate import _leaks

    if not llm.available():
        return ""
    item = s.get(m.QuizItem, attempt.item_id)
    tests: list[dict] = []
    blocks = []
    for it in facts["items"]:
        line = f"{it['title']}：{it['text']}"
        if it["kind"] == "problem" and it.get("_ref"):
            material, t = _problem_material(s, attempt.user_id, *it["_ref"], [attempt.skill_code])
            tests += t
            if material:
                line += "\n" + material
        blocks.append(line)
    outcome = {"right": "答对了", "right_judge": "判断对了", "right_second": "看了提示（或被追问后）才改对",
               "right_bypass": "改对了", "wrong_fix": "看出有错但没改对", "wrong_judge": "判断错了"}.get(attempt.outcome or "", "")
    did = []
    if attempt.judged_has_bug is not None:
        did.append(f"先判断这段代码{'有问题' if attempt.judged_has_bug else '满足要求'}")
    reply_ev = next((e for e in (attempt.events or []) if e.get("kind") == "challenge_reply"), None)
    if reply_ev is not None:
        did.append("被追问以后" + ("坚持原来的判断" if reply_ev.get("keep") else "改判为有问题"))
    if attempt.first_lines:
        did.append("选的位置：第 " + "、".join(str(x) for x in attempt.first_lines) + " 行")
    if attempt.hints:
        did.append(f"看了 {attempt.hints} 次提示")
    start = item.start_line if item and item.start_line else 1
    shown = "\n".join(f"{start + i:>3}| {x}" for i, x in enumerate((item.shown_code if item else "").split("\n")))
    user = (f"【这道小题】{item.stem if item else ''}\n考的是：{skill_name(attempt.skill_code)}\n"
            f"【小题的代码】\n{shown}\n"
            + (f"【错在哪几行】{'、'.join(str(start + x - 1) for x in (item.bug_lines or []))}\n" if item and item.has_bug else
               "【这段代码本来是对的】\n")
            + f"【他这次】{outcome}" + ("；" + "；".join(did) if did else "") + "\n"
            + (f"【他的首稿】\n{attempt.first_fix}\n" if attempt.first_fix else "")
            + (f"【小题的解释】{item.explanation}\n" if item and item.explanation else "")
            + "【他以前在这里出过的错】\n" + "\n\n".join(blocks))

    def validate(d: dict) -> str:
        text = str(d.get("analysis") or "").strip()
        if not text:
            raise ValueError("analysis 是空的")
        if len(text) > 260:   # 要求 200 字以内，稍微超一点不算错
            raise ValueError(f"太长了（{len(text)} 字），要 200 字以内")
        if "```" in text or _leaks(text, tests):
            raise ValueError("不能贴代码块、不能出现测试数据")
        return text

    try:
        return llm.call_json("quiz_recap", course_prompt(_SYSTEM, course_of(attempt.skill_code or "C")),
                             llm.strip_identity(user, attempt.user_id), validate, retries=1, max_tokens=400)
    except llm.LLMUnavailable:
        return ""


def for_attempt(s: Session, attempt: m.QuizAttempt) -> dict | None:
    """这次作答的回顾：第一次要时整理事实、请大模型分析，存下来；以后直接用存的。还没做完返回 None。"""
    if attempt.state != "done" or attempt.skipped:
        return None
    if attempt.recap is not None:
        return attempt.recap or None
    facts = build(s, attempt)
    if facts is None:
        attempt.recap = {}
        return None
    analysis = analyze(s, attempt, facts)
    attempt.recap = {**facts, "analysis": analysis, "source": "llm" if analysis else "facts",
                     "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in facts["items"]]}
    return attempt.recap
