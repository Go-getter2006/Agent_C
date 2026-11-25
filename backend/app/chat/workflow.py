"""对话主流程：把 8 步串起来，每一步的结果按固定字段存进 chat_steps。"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import catalog, config, db, llm, mastery, review, textbook
from .. import models as m
from ..taxonomy import HELP_ORDER, SKILLS
from ..xdoj import api as sync_api
from . import context, hints, reply, teaching, validate
from . import prompt_context, retro, talk
from .analyze import Analysis, analyze, dialogue_act
from .policy import Decision, PolicyInput, decide, willingness_of
from .text import split_message
from .text import previous_question_for_followup

log = logging.getLogger("chat")

BUTTONS = {"think": "我想自己想想", "hint": "再给点提示", "tell": "直接讲给我"}
ASK_WHY_TEXT = "这次提交为什么错了？"
# 这些步骤不调用大模型，一律用固定模板
TEMPLATE_ONLY = {"ack", "ask_which", "passed", "pending", "blank", "reveal_line", "stubborn"}
# 智能体刚指出问题，学生不同意（“没关系吧”“反正后面会比较”）：当成在回答，按没答对往上帮一级
_PUSHBACK = re.compile(r"(没关系|不影响|没问题吧|不对吧|不是吧|反正|应该可以|不会错|没错啊|没错吧|有什么问题)")


# ------------------------------------------------------------ 线程和回合
def thread_filter(user_id: str, thread: tuple[int, int] | None, course: str | None = None,
                  topic: int | None = None):
    """一个对话线程里的消息。没绑定题的对话按课程分开：知识问答的每个主题一个线程（topic），
    以前的“综合”对话（没有主题）两门课各一个。"""
    cond = [m.ChatMessage.user_id == user_id]
    if thread is None:
        cond += [m.ChatMessage.server_id.is_(None), m.ChatMessage.problem_id.is_(None),
                 m.ChatMessage.course == (course or "C"),
                 m.ChatMessage.topic_id.is_(None) if topic is None else m.ChatMessage.topic_id == topic]
    else:
        cond += [m.ChatMessage.server_id == thread[0], m.ChatMessage.problem_id == thread[1]]
    return cond


def thread_messages(s: Session, user_id: str, thread: tuple[int, int] | None,
                    course: str | None = None, topic: int | None = None) -> list[m.ChatMessage]:
    return list(s.scalars(select(m.ChatMessage).where(*thread_filter(user_id, thread, course, topic))
                          .order_by(m.ChatMessage.id)).all())


def delete_knowledge_thread(s: Session, user_id: str, course: str, topic: int | None) -> None:
    message_filter = thread_filter(user_id, None, course, topic)
    round_ids = list(s.scalars(select(m.ChatMessage.round_id).where(
        *message_filter, m.ChatMessage.round_id.is_not(None)).distinct()))
    s.execute(delete(m.ChatStep).where(m.ChatStep.message_id.in_(
        select(m.ChatMessage.id).where(*message_filter))))
    s.execute(delete(m.ChatMessage).where(*message_filter))
    if not round_ids:
        return

    # 旧回合可能混有别的问答或作业消息。保留这些消息，但移除引用过被删原话的评估。
    s.execute(delete(m.AgentAssessment).where(
        m.AgentAssessment.user_id == user_id, m.AgentAssessment.course == course,
        m.AgentAssessment.basis == "chat", m.AgentAssessment.round_id.in_(round_ids)))
    s.execute(delete(m.SkillEvidence).where(
        m.SkillEvidence.user_id == user_id,
        m.SkillEvidence.source_key.in_([f"assess:{rid}" for rid in round_ids])))

    remaining = set(s.scalars(select(m.ChatMessage.round_id).where(m.ChatMessage.round_id.in_(round_ids))))
    empty = set(round_ids) - remaining
    if empty:
        stuck_ids = list(s.scalars(select(m.StuckPoint.id).where(
            m.StuckPoint.user_id == user_id, m.StuckPoint.round_id.in_(empty))))
        if stuck_ids:
            s.execute(delete(m.SkillEvidence).where(
                m.SkillEvidence.user_id == user_id,
                m.SkillEvidence.source_key.in_([f"stuck:{sid}" for sid in stuck_ids])))
            s.execute(delete(m.StuckPoint).where(m.StuckPoint.id.in_(stuck_ids)))
        s.execute(delete(m.ChatRound).where(
            m.ChatRound.user_id == user_id, m.ChatRound.course == course, m.ChatRound.id.in_(empty)))


def message_text(msg: m.ChatMessage) -> str:
    return "\n".join(b.get("text", "") for b in (msg.content or {}).get("blocks", []) if b.get("t") == "p")


def conversation_key(thread: tuple[int, int] | None, topic: int | None = None) -> str:
    if topic is not None:
        return f"t{topic}"
    if thread is not None:
        return catalog.problem_key(*thread)
    return "general"


def open_round(s: Session, user_id: str, course: str | None = None,
               conversation: str | None = None) -> m.ChatRound | None:
    q = select(m.ChatRound).where(m.ChatRound.user_id == user_id, m.ChatRound.status == "open")
    if course is not None:
        q = q.where(m.ChatRound.course == course)  # 两门课的回合各算各的
    if conversation is not None:
        q = q.where(m.ChatRound.conversation_key == conversation)
    return s.scalars(q.order_by(m.ChatRound.id.desc())).first()


def close_round(s: Session, rnd: m.ChatRound, reason: str, at: dt.datetime) -> None:
    rnd.status, rnd.end_reason, rnd.ended_at = "closed", reason, at
    if reason == "通过":
        for sp in s.scalars(select(m.StuckPoint).where(m.StuckPoint.round_id == rnd.id)).all():
            sp.passed_after = True


def after_pass(s: Session, sub: m.Submission, now: dt.datetime | None = None) -> None:
    at = sub.submit_time or now or db.now()
    rounds = s.scalars(select(m.ChatRound).where(
        m.ChatRound.user_id == sub.user_id, m.ChatRound.server_id == sub.server_id,
        m.ChatRound.problem_id == sub.problem_id, m.ChatRound.status == "open",
        m.ChatRound.started_at <= at))
    for rnd in rounds:
        close_round(s, rnd, "通过", at)


def refresh_rounds(s: Session, user_id: str, now: dt.datetime | None = None) -> None:
    """超过 30 分钟没有消息的回合按超时结束；绑定的题已经通过的回合按通过结束。"""
    now = now or db.now()
    for rnd in s.scalars(select(m.ChatRound).where(m.ChatRound.user_id == user_id, m.ChatRound.status == "open")).all():
        if rnd.problem_id is not None:
            ac = s.scalars(select(m.Submission).where(
                m.Submission.server_id == rnd.server_id, m.Submission.user_id == user_id,
                m.Submission.problem_id == rnd.problem_id, m.Submission.judgement == "AC",
                m.Submission.submit_time >= rnd.started_at, m.Submission.submit_time <= now,
            ).order_by(m.Submission.submission_id)).first()
            if ac is not None:
                close_round(s, rnd, "通过", ac.submit_time)
                continue
        if now - rnd.last_message_at > dt.timedelta(minutes=config.CHAT["round_timeout_minutes"]):
            close_round(s, rnd, "超时", rnd.last_message_at)


def close_stale_rounds(s: Session, now: dt.datetime | None = None) -> int:
    """后台自动做：超时没说话的回合按超时结束、绑定的题已经通过的按通过结束，不用等学生再打开页面。

    结束以后智能体才会评估这个回合（assess.py）。返回结束了几个回合。
    """
    now = now or db.now()
    users = s.scalars(select(m.ChatRound.user_id).where(m.ChatRound.status == "open").distinct()).all()
    before = s.scalar(select(func.count()).select_from(m.ChatRound).where(m.ChatRound.status == "open")) or 0
    for uid in users:
        refresh_rounds(s, uid, now)
    s.flush()
    after = s.scalar(select(func.count()).select_from(m.ChatRound).where(m.ChatRound.status == "open")) or 0
    return before - after


def _round_for(s: Session, user_id: str, key: tuple[int, int] | None, now: dt.datetime,
               course: str, conversation: str) -> m.ChatRound:
    rnd = open_round(s, user_id, course, conversation)
    current = (rnd.server_id, rnd.problem_id) if rnd is not None and rnd.problem_id is not None else None
    if rnd is not None and current != key:
        if current is None and key is not None and rnd.help_count == 0:
            rnd.server_id, rnd.problem_id = key  # 刚弄清楚问的是哪道题：沿用这个回合
            return rnd
        close_round(s, rnd, "换题", now)
        rnd = None
    if rnd is None:
        rnd = m.ChatRound(user_id=user_id, server_id=key[0] if key else None, problem_id=key[1] if key else None,
                          course=course, conversation_key=conversation, started_at=now, last_message_at=now)
        s.add(rnd)
        s.flush()
    return rnd


def _reflect_target(s: Session, user_id: str, thread: tuple[int, int] | None, last_agent: m.ChatMessage | None,
                    action: str | None, pasted: str, prose: str) -> m.ChatMessage | None:
    """这条消息是不是在回答通过后的回顾问题：上一条是这道题的总结，之后没再交过，他说的不是新问题。"""
    if thread is None or action or pasted or last_agent is None or not prose.strip():
        return None
    if not ((last_agent.content or {}).get("summary") or {}).get("submission"):
        return None
    if prose.rstrip().endswith(("？", "?")) or retro.asks_history(prose):
        return None   # 他问了个新问题：照常回答
    newer = s.scalar(select(m.Submission.submission_id).where(
        m.Submission.server_id == thread[0], m.Submission.problem_id == thread[1], m.Submission.user_id == user_id,
        m.Submission.submit_time > last_agent.created_at))
    return last_agent if newer is None else None


def _reflect_round(s: Session, user_id: str, thread: tuple[int, int], now: dt.datetime, course: str,
                   summary_msg: m.ChatMessage, facts) -> m.ChatRound:
    """回顾单独一个回合：挂到这道题前面没过时的技能和错误上；总结那条消息也放进来，评估能看到问的是什么。"""
    conversation = conversation_key(thread)
    rnd = open_round(s, user_id, course, conversation)
    if rnd is not None:
        close_round(s, rnd, "通过", now)
    rnd = m.ChatRound(user_id=user_id, server_id=thread[0], problem_id=thread[1], course=course,
                      conversation_key=conversation,
                      started_at=now, last_message_at=now,
                      skill_code=facts.skill if facts else "", error_code=facts.error if facts else "",
                      submission_id=facts.ac.submission_id if facts else None)
    s.add(rnd)
    s.flush()
    if summary_msg.round_id is None:
        summary_msg.round_id = rnd.id
    return rnd


def _plan_next(s: Session, ctx: context.Ctx, state: dict, pick: list[int] | None, analysis: Analysis,
               said: str, course: str, history: list[tuple[str, str]] | None = None) -> tuple[dict, str, str]:
    """流程图往下走一步：(新的状态, 话, 帮助级别)。
    选了卡在哪几步：从第一步开始讲；回答了这一步：答上了换下一步，没答上讲具体一点，两次没答上讲清楚再往下走。"""
    from . import plan as plan_mod

    steps = state["steps"]
    tests = (ctx.evidence or {}).get("testCases", []) + list(ctx.all_tests)
    st = {**state, "picking": False, "done": list(state.get("done", []))}
    situation = [x for x in [
        f"他在这道题考的知识点上：{ctx.profile_note}" if ctx.profile_note else "",
        ("学情里薄弱的地方：" + "；".join(ctx.learning)) if ctx.learning else "",
        ("在别的题上卡过：" + "；".join(ctx.struggles)) if ctx.struggles else ""] if x]
    # 引导时大模型要看的最近对话和他的情况（plan.guiding 包住这几次 guide 调用）
    with plan_mod.guiding(recent=talk.dialogue(history or [], ctx.user_id, ctx.user_name), situation=situation,
                          who=(ctx.user_id, ctx.user_name)):
        if pick is not None:
            st.update(queue=pick, current=pick[0], tries=0)
            said_text, _src = plan_mod.guide(steps, pick[0], 0, said, ctx.statement, course, tests, ctx.document)
            return st, said_text, "H1"
        cur, tries = st["current"], int(st.get("tries", 0))
        ok = analysis.answer_correct is True or analysis.understood
        if not ok and tries < 1:
            st["tries"] = tries + 1
            said_text, _src = plan_mod.guide(steps, cur, tries + 1, said, ctx.statement, course, tests, ctx.document)
            return st, said_text, "H1"
        parts = []
        level = "H1"
        if not ok:   # 第二次还没答上：把这一步讲清楚，再往下走
            explained, _src = plan_mod.guide(steps, cur, 2, said, ctx.statement, course, tests, ctx.document)
            parts.append(explained)
            level = "H2"
        else:
            parts.append("对，这一步就是这样。")
        st["done"] = sorted(set(st["done"]) | {cur})
        queue = [i for i in st.get("queue", []) if i != cur and i not in st["done"]]
        st["queue"] = queue
        if queue:
            st.update(current=queue[0], tries=0)
            nxt, _src = plan_mod.guide(steps, queue[0], 0, "", ctx.statement, course, tests, ctx.document)
            parts.append(nxt)
        else:
            st.update(current=None, tries=0, ended=True)
            parts.append("这几步你都想清楚了。按流程图的顺序把代码写出来，在 XDOJ 上交一次；没过的话我们再一起看。")
        return st, "\n".join(parts), level


# ------------------------------------------------------------ 卡住时怎么办（5.6）
def _stuck_step(s: Session, ctx: context.Ctx, rnd: m.ChatRound, answer_ok: bool | None,
                now: dt.datetime) -> tuple[str, str, m.StuckPoint | None, m.StuckPoint | None]:
    """按第几次卡在这个技能上决定怎么做。返回 (做法, 帮助级别, 本回合的卡点, 以前的卡点)。"""
    prior = ctx.prior_stuck[-1] if ctx.prior_stuck else None
    with_code = next((p for p in reversed(ctx.prior_stuck) if p.given_code), prior)
    rs = ctx.round_stuck
    if rs is None:
        nth = len(ctx.prior_stuck) + 1
        rs = m.StuckPoint(user_id=ctx.user_id, course=ctx.course, server_id=ctx.problem.server_id if ctx.problem else None,
                          problem_id=ctx.problem.problem_id if ctx.problem else None, skill_code=ctx.root_skill,
                          error_code=ctx.root_error, nth=nth, round_id=rnd.id, created_at=now)
        s.add(rs)
        s.flush()
        mastery.record_stuck(s, ctx.user_id, ctx.root_skill, nth, f"stuck:{rs.id}", now)
        # 有卡点就进复习：按记忆半衰期安排什么时候回忆（app/review.py）
        review.enqueue(s, ctx.user_id, ctx.root_skill, "stuck",
                       ctx.problem.server_id if ctx.problem else None,
                       ctx.problem.problem_id if ctx.problem else None, now)
        if nth == 1:
            rs.resolution = "example_given"
            return "example", "H3", rs, None
        if nth == 2:
            rnd.recall_stage = "asked"
            return "recall", "H0", rs, with_code
        rs.stubborn, rs.resolution = True, "exercise"
        return "stubborn", "H3", rs, with_code
    if rnd.recall_stage == "asked":
        if answer_ok:
            rnd.recall_stage, rs.resolution = "done", "recalled"
            return "recalled", "H1", rs, with_code
        rnd.recall_stage, rs.resolution = "blanked", "blank_filled"
        return "blank", "H2", rs, with_code
    if rnd.recall_stage == "blanked":
        rnd.recall_stage = "done"
        if answer_ok:
            return "recalled", "H2", rs, with_code
        rs.resolution = "example_given"
        return "reveal_line", "H3", rs, with_code
    return "explain", "H3", rs, with_code


# ------------------------------------------------------------ 生成与校验
def _produce(s: Session, ctx: context.Ctx, decision: Decision, flow: str, text: str, history: list[tuple[str, str]],
             prior: m.StuckPoint | None, prior_ref: str, kind: str,
             visible_titles: dict[str, str], reflect=None, retro_hist=None,
             plan_text: str | None = None,
             compare: retro.Comparison | None = None, retro_focus: int = 0) -> tuple[validate.Draft, str, dict, int]:
    testcases = (ctx.evidence or {}).get("testCases", []) + list(ctx.all_tests)   # 全部测试点都不能出现在回复里
    if compare is not None:
        testcases += compare.tests

    def run_check(d: validate.Draft) -> list[str]:
        return validate.check(
            d, help_level=decision.help_level, reveal_level=decision.reveal_level, kind=kind,
            source=None if flow == "retro" else ctx.source,
            testcases=testcases, reference=ctx.reference, current_title=ctx.problem.title if ctx.problem else "",
            visible_titles=visible_titles, knowledge=ctx.knowledge)

    if flow in ("plan", "plan_step") and plan_text is not None:   # 解题流程图（chat/plan.py），话已经生成并核对过
        draft = validate.Draft(before=plan_text)
        problems = run_check(draft)
        return draft, ("llm" if llm.available() else "template"), {"ok": not problems, "problems": problems}, 0

    if flow == "retro":   # 回看或对比提交：按对应版本的评测记录回答
        # 对比只用之前的学生问题解指代，避免把旧回复谈到的其他版本当作本轮证据。
        conversation = history if compare is None else [turn for turn in history if turn[0] == "student"]
        materials = prompt_context.build(s, ctx, compare.submissions if compare is not None else ())
        testcases.extend(materials.tests)
        draft = retro.reply(retro_hist or [], text, testcases, ctx.course,
                            recent=talk.dialogue(conversation, ctx.user_id, ctx.user_name),
                            who=(ctx.user_id, ctx.user_name), compare=compare, materials=materials,
                            focus=retro_focus, student_notes=prompt_context.student_notes(ctx), check=run_check)
        return draft, "llm", {"ok": True, "problems": []}, 0

    if flow == "reflect":   # 通过后的回顾：接着他说的回一两句（chat/summary.py）
        from . import summary

        said, source = summary.reflect_reply(reflect, text, testcases, ctx.course, statement=ctx.statement,
                                             recent=talk.dialogue(history, ctx.user_id, ctx.user_name),
                                             who=(ctx.user_id, ctx.user_name))
        draft = validate.Draft(before=said)
        problems = run_check(draft)
        if problems and source == "llm":
            draft, source = validate.Draft(before=summary.reflect_template(reflect)), "template"
            problems = run_check(draft)
        return draft, source, {"ok": not problems, "problems": problems}, 0

    retries = 0
    if llm.available() and flow not in TEMPLATE_ONLY:
        materials = prompt_context.build(s, ctx)
        testcases.extend(materials.tests)
        book_directory = textbook.directory(s, ctx.course) if ctx.knowledge and flow == "concept" else None
        book_available = book_directory is not None
        book_searched = False
        book_evidence = {}
        feedback = ""
        for attempt in range(2):
            try:
                draft = reply.generate_llm(ctx, decision, flow, text, history, prior_ref, feedback, materials,
                                           book_evidence, book_directory)
                if isinstance(draft, textbook.Lookup):
                    book_searched = True
                    book_evidence = textbook.retrieve(s, ctx.course, text, history, book_directory, draft)
                    book_directory = None
                    draft = reply.generate_llm(ctx, decision, flow, text, history, prior_ref, feedback, materials,
                                               book_evidence)
            except llm.LLMUnavailable:
                if ctx.knowledge:
                    raise
                break
            if draft.quote is None and flow == "help" and decision.help_level in ("H0", "H1") \
                    and not reply.ctx_located(ctx):
                draft.quote = reply.default_quote(ctx)  # 指出问题时，把他自己的代码贴出来
            problems = run_check(draft)
            if not problems:
                checks = {"ok": True, "problems": []}
                if ctx.knowledge:
                    checks["textbookLookup"] = {"available": book_available, "searched": book_searched,
                                               "candidates": len(book_evidence), "cited": len(draft.textbook_refs)}
                    if draft.textbook_skip_reason or (book_searched and not book_evidence):
                        checks["textbookLookup"]["reason"] = draft.textbook_skip_reason or "所选章节没有可定位的段落"
                return draft, "llm", checks, retries
            retries = attempt + 1
            feedback = "；".join(problems)
        if ctx.knowledge:
            err = llm.LLMUnavailable("知识问答回复未通过校验")
            err.invalid = True
            raise err
        log.info("回复两次没通过校验，改用固定模板")
    if ctx.knowledge:
        raise llm.LLMUnavailable("知识问答需要大模型")
    draft = reply.template(ctx, decision, flow, text, prior, prior_ref)
    if flow in ("help", "locate", "diagnose", "example", "reveal_line", "explain"):
        overview = reply.all_failed_overview(ctx, text)
        if overview:
            draft.before = "\n".join(part for part in (overview, draft.before) if part)
    problems = run_check(draft)
    return draft, "template", {"ok": not problems, "problems": problems}, retries


# ------------------------------------------------------------ 入口
def handle(s: Session, user_id: str, thread: tuple[int, int] | None, text: str = "", action: str | None = None,
           submission_key: str | None = None, now: dt.datetime | None = None,
           course: str | None = None, topic: int | None = None, knowledge: bool = False,
           pin: tuple[int, int] | None = None) -> list[m.ChatMessage]:
    """处理学生的一条消息（或一次按钮点击）。返回新增的消息：学生这一条和智能体的回复。

    course：这次登录的课程。绑定了题的线程按题所在的服务器定；综合对话按登录的课程。
    knowledge：知识问答。不按题名、贴的代码、最近的提交自动绑到作业题上；学生选了“结合当前题”（pin）才带上那道题。
    知识问答里不出小题（小题在“复习提升”里做）。topic：知识问答的主题。
    """
    now = now or db.now()
    course = catalog.server_course(s, thread[0]) if thread is not None else (course or "C")
    t0 = time.monotonic()
    if action not in (None, *BUTTONS):
        action = None
    text = BUTTONS[action] if action else (text or "").strip()
    if not text:
        return []
    refresh_rounds(s, user_id, now)
    sync_api.notify_student_active(s, user_id)  # 让同步程序立刻查一次他的新提交
    picked = catalog.parse_key(submission_key or "", "s")
    if picked is not None:
        sync_api.request_priority(s, picked[0], picked[1])

    prose, pasted = (text, "") if action else split_message(text)
    earlier_messages = thread_messages(s, user_id, thread, course, topic)
    last_agent = next((x for x in reversed(earlier_messages) if x.role == "agent"), None)
    last_agent_text = message_text(last_agent) if last_agent is not None and last_agent.kind == "text" else ""
    awaiting = last_agent_text.rstrip().endswith(("？", "?"))
    if awaiting and thread is not None and last_agent is not None:
        newer = s.scalar(select(m.Submission.submission_id).where(
            m.Submission.server_id == thread[0], m.Submission.problem_id == thread[1], m.Submission.user_id == user_id,
            m.Submission.submit_time > last_agent.created_at))
        if newer is not None:
            awaiting = False  # 智能体问完以后他又交过一次：这条消息是在说新的提交，不是在回答那个问题
    if last_agent is not None and last_agent.failure_submission_id and (prose or text).rstrip().endswith(("？", "?")):
        awaiting = False  # 对提醒提出新的调试问题，不把它记成答错一次。
    since_reply = (now - last_agent.created_at).total_seconds() if last_agent is not None else None

    # 第 2 步 绑定 → 回合
    key, selected, bind_by, _cands = context.bind(s, user_id, thread, submission_key, text, pasted, now, course,
                                                  knowledge=knowledge, pin=pin)
    switched_from = None
    if bind_by == "ask_why" and selected is not None and not action and not pasted:
        previous_question = next((msg for msg in reversed(earlier_messages)
                                  if msg.role == "student" and msg.kind == "text"), None)
        if previous_question is not None:
            switched_from = retro.previous_focus(s, selected, previous_question.id)
    reflect_on = None if knowledge or switched_from is not None \
        else _reflect_target(s, user_id, thread, last_agent, action, pasted, prose)
    reflect = None
    if reflect_on is not None:
        from . import summary

        reflect = summary.reflect_facts(s, reflect_on)
        rnd = _reflect_round(s, user_id, thread, now, course, reflect_on, reflect)
        awaiting = True   # 他在回答“当时卡住的关键是什么”
    else:
        rnd = _round_for(s, user_id, key, now, course, conversation_key(thread, topic))
    awaiting = awaiting or rnd.recall_stage in ("asked", "blanked")  # 让他回忆时也在等他回答
    # 解题流程图（chat/plan.py）：上一条带着的状态；他贴了代码、之后又交过，就不接着走流程图了
    plan_state = None
    if thread is not None and not knowledge and not pasted and reflect_on is None and not action and switched_from is None:
        from . import plan as plan_mod

        plan_state = plan_mod.active(last_agent)
        if plan_state is not None and s.scalar(select(m.Submission.submission_id).where(
                m.Submission.server_id == thread[0], m.Submission.problem_id == thread[1],
                m.Submission.user_id == user_id, m.Submission.submit_time > last_agent.created_at)) is not None:
            plan_state = None
    plan_guiding = plan_state is not None and plan_state.get("current") is not None
    if plan_guiding:
        awaiting = True   # 他在回答“这一步你打算怎么做”
    if not awaiting and not action and rnd.help_count > 0 and _PUSHBACK.search(prose or text):
        awaiting = True
    if switched_from is not None:
        awaiting = False  # 重新选了一版，接着问的是当前提交
    blocks = [{"t": "p", "text": p} for p in prose.split("\n") if p.strip()]
    if pasted:
        blocks.append({"t": "code", "caption": "我贴的代码", "start": 1, "lines": pasted.split("\n"), "mark": []})
    student_msg = m.ChatMessage(user_id=user_id, round_id=rnd.id, server_id=thread[0] if thread else None,
                                problem_id=thread[1] if thread else None, topic_id=topic, course=course,
                                role="student", kind="text", content={"blocks": blocks}, created_at=now)
    s.add(student_msg)
    s.flush()
    # 给大模型看的最近对话（含消息里的代码块）；他刚发的这条单独给
    history = talk.history(earlier_messages[-talk.TURNS:])

    # 第 3~5 步 取证、诊断、状态
    previous_question = previous_question_for_followup(text, history) if knowledge else ""
    ctx = context.build(s, user_id, thread, submission_key, text, pasted, rnd, now, course, knowledge=knowledge, pin=pin,
                        previous_question=previous_question)
    if ctx.submission is not None and ctx.submission.detail_status != "done" and picked is None:
        sync_api.request_priority(s, ctx.submission.server_id, ctx.submission.submission_id)
    ctx._located = bool(rnd.located)
    if last_agent is not None and last_agent.failure_submission_id and ctx.submission and ctx.root_error:
        from .proactive import _primary

        previous = s.scalars(select(m.Submission).where(
            m.Submission.server_id == ctx.submission.server_id, m.Submission.user_id == user_id,
            m.Submission.problem_id == ctx.submission.problem_id,
            m.Submission.submission_id < ctx.submission.submission_id).order_by(m.Submission.submission_id.desc()).limit(1)).first()
        label = _primary(s, previous) if previous and previous.judgement not in ("AC", "PENDING", "UNKNOWN") else None
        if label and label.confidence >= config.CHAT["low_confidence"] and label.error_code == ctx.root_error:
            ctx.same_error_resubmit = True
    hint = ctx.hint()

    # 第 1 步 消息分析（点按钮不用分析）
    if action:
        analysis = Analysis(intent="ANSWER" if action == "tell" else "REPLY", has_attempt=False,
                            icap="passive" if action != "think" else "active", source="button")
    elif knowledge:
        analysis = Analysis(intent="", has_code=bool(pasted), icap="", source="direct")
    else:
        options = []
        if ctx.plabel is not None:
            for code in [ctx.plabel.primary_skill, *(ctx.plabel.secondary_skills or [])]:
                if code in SKILLS:
                    options.append((code, SKILLS[code][0]))
        direction = ctx.fill(hint.why) if awaiting and reflect_on is None else ""
        if plan_guiding:
            step = plan_state["steps"][plan_state["current"]]
            direction = (step.get("how") or step["title"]) + (f"（关键知识点：{step['kp']}）" if step.get("kp") else "")
        shown = talk.message_for_model(last_agent) if last_agent is not None and last_agent.kind == "text" else ""
        analysis = analyze(llm.strip_identity(prose or text, user_id, ctx.user_name), bool(pasted), awaiting,
                           llm.strip_identity(shown, user_id, ctx.user_name),
                           direction, options,
                           bound=ctx.problem is not None, course=course,
                           recent=talk.dialogue(history[:-1] if last_agent is not None and history
                                                and history[-1][0] == "agent" else history,
                                                ctx.user_id, ctx.user_name, limit=6, per=400))
        if analysis.intent == "REPLY" and awaiting and analysis.answer_correct is None and not analysis.understood \
                and not plan_guiding:
            analysis.answer_correct = bool(hints.answered(text, hint))  # 判不了按没答对处理：多帮一点比错夸要稳妥
        if reflect_on is not None:
            # 回顾没有“标准答案”，不记答对答错；说了有内容的话就是在解释自己的想法（进理解评估）
            analysis.answer_correct = None
            if analysis.intent not in ("STUCK", "ANSWER") and len(re.sub(r"\s", "", prose)) >= 4:
                analysis.intent, analysis.has_attempt = "REPLY", True
    in_recall = rnd.recall_stage in ("asked", "blanked") and action is None \
        and analysis.intent in ("REPLY", "STUCK", "ANSWER", "DEBUG")
    willingness = willingness_of(analysis.intent, analysis.has_attempt, action, since_reply,
                                 config.CHAT["quick_hint_seconds"])

    # 第 6 步 策略
    llm.last_model.set("")  # 这条回复单独记，不能把消息分析用的模型当成读题模型
    direct = analysis.source == "direct"
    decision = Decision("R02", "NA", "乙", "concept") if direct else decide(PolicyInput(
        intent=analysis.intent, action=action, bound=ctx.problem is not None,
        has_code=bool(ctx.source), has_failure=ctx.has_failure, diag_confidence=ctx.diag_confidence,
        answer_correct=analysis.answer_correct, understood=analysis.understood, willingness=willingness,
        base_level=ctx.level, last_help=rnd.last_help_level, help_count=rnd.help_count,
        failed_replies=rnd.failed_replies, located=bool(rnd.located),
        same_error_resubmit=ctx.same_error_resubmit, no_progress=ctx.no_progress,
        needs_example=analysis.intent != "CONCEPT" and action != "think" and teaching.needs_example(prose or text)))
    flow = decision.kind
    if reflect_on is not None:
        decision, flow = Decision("R00", "NA", "甲", "reflect"), "reflect"
    plan_step = None   # (新的流程图状态, 话)
    asking_for_approach = False
    if reflect_on is None and thread is not None and not knowledge and not pasted and not action:
        from . import plan as plan_mod

        asking_for_approach = plan_mod.wants_plan(prose, analysis.intent)
        n_steps = len(plan_state["steps"]) if plan_state else 0
        pick = plan_mod.picked(prose, n_steps) if plan_state is not None and plan_state.get("picking") else None
        if pick is not None or plan_guiding:
            plan_step = _plan_next(s, ctx, plan_state, pick, analysis, prose, course, history)
            decision = Decision("R00", plan_step[2], "甲", "plan_step")
            flow = "plan_step"
        elif ctx.problem is not None and ctx.submission is None and asking_for_approach:
            # 有缓存直接取，首次问到时生成；没有合格步骤就按平常的引导走
            steps = plan_mod.steps_for(s, ctx.problem.server_id, ctx.problem.problem_id, ctx.statement, ctx.plabel,
                                       course, list(ctx.all_tests))
            if steps:
                state = {"version": plan_mod.VERSION, "steps": steps, "picking": True, "current": None,
                         "queue": [], "done": [], "tries": 0}
                plan_step = (state, "这道题可以分成下面几步。你卡在哪一步？可以选一步或几步，都不会也没关系，我们从头一步一步来。",
                             "H0")
                decision, flow = Decision("R00", "H0", "甲", "plan"), "plan"
    prior: m.StuckPoint | None = None
    stuck_point: m.StuckPoint | None = None
    if in_recall and ctx.root_skill and reflect_on is None and plan_step is None:
        decision = Decision("R07", "H3", "乙", "stuck")
        flow = "stuck"
    if ctx.problem is not None and flow not in ("other", "concept", "task", "ack", "reflect", "plan", "plan_step"):
        if ctx.submission is not None and ctx.submission.detail_status != "done" and not ctx.source:
            decision, flow = Decision("R00", "NA", "甲", "pending"), "pending"
        elif ctx.submission is not None and ctx.submission.judgement == "AC" and not pasted \
                and not asking_for_approach and flow != "affirm":
            # 已经通过的题回头问：按这道题每一次提交的结果和改动回答（chat/retro.py），不再只说“已经通过了”
            decision, flow = Decision("R00", "NA", "甲", "retro"), "retro"
    compare = None
    retro_focus = 0
    if thread is not None and ctx.submission is not None and not action and not pasted \
            and reflect_on is None and plan_step is None:
        named = retro.attempts_in(prose)
        if not named and not submission_key:
            previous = previous_question_for_followup(prose, history)
            if re.fullmatch(r"[?？]+", prose.strip()):
                previous = next((said for role, said in reversed(history) if role == "student"
                                 and not re.fullmatch(r"[?？]+", said.strip())), "")
            named = retro.attempts_in(previous)
        compare = retro.comparison(s, ctx.submission, ctx.attempt_no, prose, now,
                                   switched_from=switched_from if flow not in ("concept", "task") else None)
        if len(named) == 1 and not (compare and compare.named_baseline):
            retro_focus = named[0]
        elif submission_key or (compare and compare.named_baseline):
            retro_focus = ctx.attempt_no
        if compare is not None or retro.asks_history(prose) or (named and not submission_key):
            decision, flow = Decision("R00", "NA", "甲", "retro"), "retro"
    staged_fix = False
    if thread is not None and not knowledge and not pasted and ctx.has_failure and ctx.source \
            and ctx.submission.judgement not in ("UNKNOWN", "") \
            and flow in ("help", "locate", "stuck") and not in_recall:
        same_submission = rnd.submission_id == ctx.submission.submission_id
        last_stage = s.scalar(select(m.ChatStep.help_level).where(
            m.ChatStep.round_id == rnd.id, m.ChatStep.submission_id == ctx.submission.submission_id,
            m.ChatStep.rule_id == "R12").order_by(m.ChatStep.id.desc()).limit(1)) if same_submission else None
        if last_stage == "H0":
            decision, flow = Decision("R12", "H1", "甲", "diagnose_where"), "diagnose_where"
        elif last_stage == "H1":
            staged_fix = True
            decision, flow = Decision("R12", "H3", "乙", "stuck"), "stuck"
        elif last_stage is None and not ctx.same_error_resubmit and (not same_submission or not rnd.help_count):
            decision, flow = Decision("R12", "H0", "甲", "diagnose"), "diagnose"
    if flow == "stuck":
        if not ctx.root_skill:
            decision, flow = Decision(decision.rule_id, "H3", "乙", "help"), "example"
        else:
            ok = analysis.answer_correct if analysis.intent == "REPLY" else False
            if rnd.recall_stage == "asked" and analysis.intent == "REPLY" and analysis.answer_correct is None:
                ok = analysis.has_attempt
            flow, level, stuck_point, prior = _stuck_step(s, ctx, rnd, ok, now)
            if (teaching.needs_example(prose or text) or staged_fix) and flow in ("recall", "blank", "stubborn"):
                flow, level = "example", "H3"
                rnd.recall_stage = "done"
                if stuck_point is not None:
                    stuck_point.resolution = "example_given"
            decision = Decision(decision.rule_id, level, "乙", "stuck")
    prior_ref = ""
    if prior is not None and prior.problem_id is not None and catalog.is_visible(s, user_id, prior.server_id, prior.problem_id):
        info = catalog.problem_info(s, prior.server_id, prior.problem_id)
        if info is not None and (ctx.problem is None or info.key != ctx.problem.key):
            prior_ref = info.ref

    # 第 7 步 生成与校验
    visible_titles = {}
    for (sid, pid) in catalog.visible_problems(s, user_id, course):
        info = catalog.problem_info(s, sid, pid)
        if info is not None:
            visible_titles[info.title] = info.brief
    retro_hist = None
    if flow == "retro" and ctx.problem is not None:
        retro_hist = retro.history(s, user_id, ctx.problem.server_id, ctx.problem.problem_id)
    draft, source, check_result, retries = _produce(s, ctx, decision, flow, text, history, prior, prior_ref,
                                                    decision.kind, visible_titles, reflect, retro_hist,
                                                    plan_step[1] if plan_step else None,
                                                    compare, retro_focus)
    reply_model = llm.last_model.get() if source == "llm" else ""
    if stuck_point is not None and flow in ("example", "stubborn") and draft.code and not draft.code_is_old:
        stuck_point.given_code, stuck_point.key_line = draft.code, draft.key_line or 1
    actions = list(draft.actions)
    if flow in ("help", "locate", "diagnose", "diagnose_where") and not actions:
        actions = list(reply.HELP_ACTIONS)
    content = {"blocks": reply.to_blocks(draft, ctx), "actions": actions}
    if draft.textbook_refs:
        content["textbook"] = draft.textbook_refs
    if plan_step is not None:   # 流程图跟着消息走：学生看到的块，和接着引导要用的状态（学生端看不到）
        from . import plan as plan_mod

        content["blocks"].append(plan_mod.block(plan_step[0]))
        content["plan"] = plan_step[0]
    agent_msg = m.ChatMessage(user_id=user_id, round_id=rnd.id, server_id=thread[0] if thread else None,
                              problem_id=thread[1] if thread else None, topic_id=topic, course=course, role="agent",
                              kind="text", content=content, created_at=now + dt.timedelta(milliseconds=1))
    s.add(agent_msg)
    s.flush()
    out = [student_msg, agent_msg]

    # 第 8 步 收尾
    rnd.last_message_at = now
    if ctx.submission is not None and reflect_on is None:
        rnd.submission_id = ctx.submission.submission_id
    if ctx.root_skill and reflect_on is None:
        rnd.skill_code, rnd.error_code = ctx.root_skill, ctx.root_error
    if analysis.intent == "REPLY" and analysis.answer_correct is False and action is None:
        rnd.failed_replies += 1
    if decision.help_level != "NA":
        rnd.help_count += 1
        rnd.last_help_level = decision.help_level
        if HELP_ORDER[decision.help_level] > HELP_ORDER.get(rnd.max_help_level, -1):
            rnd.max_help_level = decision.help_level
        if content["blocks"] and content["blocks"][-1].get("text", "").rstrip().endswith(("？", "?")):
            rnd.asked_back += 1
    if draft.quote is not None:
        rnd.located = True
    if reflect_on is not None:
        close_round(s, rnd, m.REFLECT_END, now)   # 一问一答就结束；后台照常评“理解”
    elif analysis.understood and action is None:
        close_round(s, rnd, "学生表示懂了", now)

    s.add(m.ChatStep(
        message_id=student_msg.id, round_id=rnd.id, user_id=user_id,
        intent=analysis.intent, skill_code=analysis.skill_code or ctx.root_skill, has_attempt=analysis.has_attempt,
        act=("" if direct else "self_explain" if reflect_on is not None and analysis.has_attempt
             else dialogue_act(analysis.intent, analysis.has_attempt, action)), answer_correct=analysis.answer_correct,
        has_code=bool(pasted), icap=analysis.icap, analysis_source=analysis.source,
        course_id=course, server_id=ctx.problem.server_id if ctx.problem else None,
        problem_id=ctx.problem.problem_id if ctx.problem else None,
        submission_id=ctx.submission.submission_id if ctx.submission else None, bind_by=ctx.bind_by,
        evidence_card=context.evidence_card(ctx),
        root_error_code=ctx.root_error, root_skill_code=ctx.root_skill, diag_confidence=ctx.diag_confidence,
        willingness=willingness, base_level=ctx.level, stuck_count=len(ctx.prior_stuck) + (1 if (ctx.round_stuck or stuck_point) else 0),
        rule_id=decision.rule_id, help_level=decision.help_level, reveal_level=decision.reveal_level,
        reply={**content, "flow": flow}, reply_source=source, check_result=check_result, retries=retries,
        round_status=rnd.status, end_reason=rnd.end_reason, quiz_triggered=False,
        stuck_point_id=stuck_point.id if stuck_point is not None else None,
        prompt_version=config.PROMPT_VERSION, model_version=reply_model,
        workflow_version=config.WORKFLOW_VERSION, duration_ms=int((time.monotonic() - t0) * 1000), created_at=now))
    llm.flush_records(s)
    s.flush()
    return out
