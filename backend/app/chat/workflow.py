"""对话主流程：把 8 步串起来，每一步的结果按固定字段存进 chat_steps。"""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, config, db, llm, mastery, quiz
from .. import models as m
from ..taxonomy import HELP_ORDER, SKILLS
from ..xdoj import api as sync_api
from . import context, hints, reply, validate
from .analyze import Analysis, analyze
from .policy import Decision, PolicyInput, decide, willingness_of
from .text import split_message

log = logging.getLogger("chat")

BUTTONS = {"think": "我想自己想想", "hint": "再给点提示", "tell": "直接讲给我"}
ASK_WHY_TEXT = "这次提交为什么错了？"
# 这些步骤不调用大模型，一律用固定模板
TEMPLATE_ONLY = {"ack", "ask_which", "passed", "pending", "blank", "reveal_line", "stubborn"}


# ------------------------------------------------------------ 线程和回合
def thread_filter(user_id: str, thread: tuple[int, int] | None):
    cond = [m.ChatMessage.user_id == user_id]
    if thread is None:
        cond += [m.ChatMessage.server_id.is_(None), m.ChatMessage.problem_id.is_(None)]
    else:
        cond += [m.ChatMessage.server_id == thread[0], m.ChatMessage.problem_id == thread[1]]
    return cond


def thread_messages(s: Session, user_id: str, thread: tuple[int, int] | None) -> list[m.ChatMessage]:
    return list(s.scalars(select(m.ChatMessage).where(*thread_filter(user_id, thread)).order_by(m.ChatMessage.id)).all())


def message_text(msg: m.ChatMessage) -> str:
    return "\n".join(b.get("text", "") for b in (msg.content or {}).get("blocks", []) if b.get("t") == "p")


def open_round(s: Session, user_id: str) -> m.ChatRound | None:
    return s.scalars(select(m.ChatRound).where(m.ChatRound.user_id == user_id, m.ChatRound.status == "open")
                     .order_by(m.ChatRound.id.desc())).first()


def close_round(s: Session, rnd: m.ChatRound, reason: str, at: dt.datetime) -> None:
    rnd.status, rnd.end_reason, rnd.ended_at = "closed", reason, at
    if reason == "通过":
        for sp in s.scalars(select(m.StuckPoint).where(m.StuckPoint.round_id == rnd.id)).all():
            sp.passed_after = True


def after_pass(s: Session, sub: m.Submission, now: dt.datetime | None = None) -> m.ChatMessage | None:
    """一道题通过之后：结束还开着的回合；该出小题就出一道。可以重复调用。"""
    now = now or db.now()
    rnd = s.scalars(select(m.ChatRound).where(
        m.ChatRound.user_id == sub.user_id, m.ChatRound.server_id == sub.server_id,
        m.ChatRound.problem_id == sub.problem_id).order_by(m.ChatRound.id.desc())).first()
    if rnd is not None and rnd.status == "open":
        close_round(s, rnd, "通过", sub.submit_time or now)
    # 已经为这次通过出过题了
    since = sub.submit_time or now
    asked = s.scalar(select(m.ChatMessage.id).where(
        *thread_filter(sub.user_id, (sub.server_id, sub.problem_id)), m.ChatMessage.kind == "quiz",
        m.ChatMessage.created_at >= since))
    if asked is not None:
        return None
    subs = catalog.user_submissions(s, sub.user_id, sub.server_id, sub.problem_id)
    first_ac = next((x for x in subs if x.judgement == "AC"), None)
    if first_ac is None or first_ac.submission_id != sub.submission_id:
        return None
    attempts = [x.submission_id for x in subs].index(sub.submission_id) + 1
    helped = rnd is not None and rnd.end_reason == "通过" and HELP_ORDER.get(rnd.max_help_level, -1) >= 2
    if not helped and attempts < config.QUIZ["pass_after_submissions"]:
        return None
    from ..labeling.labeler import current_problem_label

    label = current_problem_label(s, sub.server_id, sub.problem_id)
    skill = (rnd.skill_code if rnd is not None and rnd.skill_code else "") or (label.primary_skill if label else "")
    if not skill or not quiz.can_start(s, sub.user_id, skill, now):
        return None
    item = quiz.choose_item(s, sub.user_id, skill)
    if item is None:
        return None
    if rnd is not None:
        rnd.quiz_triggered = True
    return quiz.start(s, sub.user_id, item, "after_pass", "过了。顺手问一道：", sub.server_id, sub.problem_id,
                      rnd.id if rnd is not None else None, now)


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
                after_pass(s, ac, now)
                continue
        if now - rnd.last_message_at > dt.timedelta(minutes=config.CHAT["round_timeout_minutes"]):
            close_round(s, rnd, "超时", rnd.last_message_at)


def _round_for(s: Session, user_id: str, key: tuple[int, int] | None, now: dt.datetime) -> m.ChatRound:
    rnd = open_round(s, user_id)
    current = (rnd.server_id, rnd.problem_id) if rnd is not None and rnd.problem_id is not None else None
    if rnd is not None and current != key:
        if current is None and key is not None and rnd.help_count == 0:
            rnd.server_id, rnd.problem_id = key  # 刚弄清楚问的是哪道题：沿用这个回合
            return rnd
        close_round(s, rnd, "换题", now)
        rnd = None
    if rnd is None:
        rnd = m.ChatRound(user_id=user_id, server_id=key[0] if key else None, problem_id=key[1] if key else None,
                          started_at=now, last_message_at=now)
        s.add(rnd)
        s.flush()
    return rnd


# ------------------------------------------------------------ 卡住时怎么办（5.6）
def _stuck_step(s: Session, ctx: context.Ctx, rnd: m.ChatRound, answer_ok: bool | None,
                now: dt.datetime) -> tuple[str, str, m.StuckPoint | None, m.StuckPoint | None]:
    """按第几次卡在这个技能上决定怎么做。返回 (做法, 帮助级别, 本回合的卡点, 以前的卡点)。"""
    prior = ctx.prior_stuck[-1] if ctx.prior_stuck else None
    with_code = next((p for p in reversed(ctx.prior_stuck) if p.given_code), prior)
    rs = ctx.round_stuck
    if rs is None:
        nth = len(ctx.prior_stuck) + 1
        rs = m.StuckPoint(user_id=ctx.user_id, course="C", server_id=ctx.problem.server_id if ctx.problem else None,
                          problem_id=ctx.problem.problem_id if ctx.problem else None, skill_code=ctx.root_skill,
                          error_code=ctx.root_error, nth=nth, round_id=rnd.id, created_at=now)
        s.add(rs)
        s.flush()
        mastery.record_stuck(s, ctx.user_id, ctx.root_skill, nth, f"stuck:{rs.id}", now)
        # 有卡点：几天后出回忆题；卡住两次及以上的技能由回访队列继续跟（答错了会再安排）
        if True:
            due = s.scalar(select(m.RevisitItem.id).where(
                m.RevisitItem.user_id == ctx.user_id, m.RevisitItem.skill_code == ctx.root_skill,
                m.RevisitItem.status == "due"))
            if due is None:
                s.add(m.RevisitItem(user_id=ctx.user_id, skill_code=ctx.root_skill, stage="recall",
                                    due_at=now + dt.timedelta(days=3)))
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
def _produce(ctx: context.Ctx, decision: Decision, flow: str, text: str, history: list[tuple[str, str]],
             prior: m.StuckPoint | None, prior_ref: str, kind: str,
             visible_titles: dict[str, str]) -> tuple[validate.Draft, str, dict, int]:
    testcases = (ctx.evidence or {}).get("testCases", [])

    def run_check(d: validate.Draft) -> list[str]:
        return validate.check(
            d, help_level=decision.help_level, reveal_level=decision.reveal_level, kind=kind, source=ctx.source,
            testcases=testcases, reference=ctx.reference, current_title=ctx.problem.title if ctx.problem else "",
            visible_titles=visible_titles)

    retries = 0
    if llm.available() and flow not in TEMPLATE_ONLY:
        feedback = ""
        for attempt in range(2):
            try:
                draft = reply.generate_llm(ctx, decision, flow, text, history, prior_ref, feedback)
            except llm.LLMUnavailable:
                break
            if draft.quote is None and flow == "help" and decision.help_level in ("H0", "H1") \
                    and not reply.ctx_located(ctx):
                draft.quote = reply.default_quote(ctx)  # 指出问题时，把他自己的代码贴出来
            problems = run_check(draft)
            if not problems:
                return draft, "llm", {"ok": True, "problems": []}, retries
            retries = attempt + 1
            feedback = "；".join(problems)
        log.info("回复两次没通过校验，改用固定模板")
    draft = reply.template(ctx, decision, flow, text, prior, prior_ref)
    problems = run_check(draft)
    return draft, "template", {"ok": not problems, "problems": problems}, retries


# ------------------------------------------------------------ 入口
def handle(s: Session, user_id: str, thread: tuple[int, int] | None, text: str = "", action: str | None = None,
           submission_key: str | None = None, now: dt.datetime | None = None) -> list[m.ChatMessage]:
    """处理学生的一条消息（或一次按钮点击）。返回新增的消息：学生这一条和智能体的回复。"""
    now = now or db.now()
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
    last_agent = next((x for x in reversed(thread_messages(s, user_id, thread)) if x.role == "agent"), None)
    last_agent_text = message_text(last_agent) if last_agent is not None and last_agent.kind == "text" else ""
    awaiting = last_agent_text.rstrip().endswith(("？", "?"))
    since_reply = (now - last_agent.created_at).total_seconds() if last_agent is not None else None

    # 第 2 步 绑定 → 回合
    key, _sub, _by, _cands = context.bind(s, user_id, thread, submission_key, text, pasted, now)
    rnd = _round_for(s, user_id, key, now)
    awaiting = awaiting or rnd.recall_stage in ("asked", "blanked")  # 让他回忆时也在等他回答
    blocks = [{"t": "p", "text": p} for p in prose.split("\n") if p.strip()]
    if pasted:
        blocks.append({"t": "code", "caption": "我贴的代码", "start": 1, "lines": pasted.split("\n"), "mark": []})
    student_msg = m.ChatMessage(user_id=user_id, round_id=rnd.id, server_id=thread[0] if thread else None,
                                problem_id=thread[1] if thread else None, role="student", kind="text",
                                content={"blocks": blocks}, created_at=now)
    s.add(student_msg)
    s.flush()

    # 第 3~5 步 取证、诊断、状态
    ctx = context.build(s, user_id, thread, submission_key, text, pasted, rnd, now)
    ctx._located = bool(rnd.located)
    hint = ctx.hint()

    # 第 1 步 消息分析（点按钮不用分析）
    if action:
        analysis = Analysis(intent="ANSWER" if action == "tell" else "REPLY", has_attempt=False,
                            icap="passive" if action != "think" else "active", source="button")
    else:
        options = []
        if ctx.plabel is not None:
            for code in [ctx.plabel.primary_skill, *(ctx.plabel.secondary_skills or [])]:
                if code in SKILLS:
                    options.append((code, SKILLS[code][0]))
        analysis = analyze(prose or text, bool(pasted), awaiting, last_agent_text,
                           ctx.fill(hint.why) if awaiting else "", options)
        if analysis.intent == "REPLY" and awaiting and analysis.answer_correct is None and not analysis.understood:
            analysis.answer_correct = bool(hints.answered(text, hint))  # 判不了按没答对处理：多帮一点比错夸要稳妥
    in_recall = rnd.recall_stage in ("asked", "blanked") and action is None \
        and analysis.intent in ("REPLY", "STUCK", "ANSWER", "DEBUG")
    willingness = willingness_of(analysis.intent, analysis.has_attempt, action, since_reply,
                                 config.CHAT["quick_hint_seconds"])

    # 第 6 步 策略
    decision = decide(PolicyInput(
        intent=analysis.intent, action=action, bound=ctx.problem is not None,
        has_code=bool(ctx.source), has_failure=ctx.has_failure, diag_confidence=ctx.diag_confidence,
        answer_correct=analysis.answer_correct, understood=analysis.understood, willingness=willingness,
        base_level=ctx.level, last_help=rnd.last_help_level, help_count=rnd.help_count,
        failed_replies=rnd.failed_replies, located=bool(rnd.located),
        same_error_resubmit=ctx.same_error_resubmit, no_progress=ctx.no_progress))
    flow = decision.kind
    prior: m.StuckPoint | None = None
    stuck_point: m.StuckPoint | None = None
    if in_recall and ctx.root_skill:
        decision = Decision("R07", "H3", "乙", "stuck")
        flow = "stuck"
    if ctx.problem is not None and flow not in ("other", "concept", "task", "ack"):
        if ctx.submission is not None and ctx.submission.detail_status != "done":
            decision, flow = Decision("R00", "NA", "甲", "pending"), "pending"
        elif ctx.submission is not None and ctx.submission.judgement == "AC" and not pasted \
                and flow not in ("affirm",):
            decision, flow = Decision("R00", "NA", "甲", "passed"), "passed"
    if flow == "stuck":
        if not ctx.root_skill:
            decision, flow = Decision(decision.rule_id, "H2", "乙", "help"), "help"
        else:
            ok = analysis.answer_correct if analysis.intent == "REPLY" else False
            if rnd.recall_stage == "asked" and analysis.intent == "REPLY" and analysis.answer_correct is None:
                ok = analysis.has_attempt
            flow, level, stuck_point, prior = _stuck_step(s, ctx, rnd, ok, now)
            decision = Decision(decision.rule_id, level, "乙", "stuck")
    prior_ref = ""
    if prior is not None and prior.problem_id is not None and catalog.is_visible(s, user_id, prior.server_id, prior.problem_id):
        info = catalog.problem_info(s, prior.server_id, prior.problem_id)
        if info is not None and (ctx.problem is None or info.key != ctx.problem.key):
            prior_ref = info.ref

    # 第 7 步 生成与校验
    visible_titles = {}
    for (sid, pid) in catalog.visible_problems(s, user_id):
        info = catalog.problem_info(s, sid, pid)
        if info is not None:
            visible_titles[info.title] = info.brief
    history = [(x.role, message_text(x)) for x in thread_messages(s, user_id, thread)[-6:] if x.kind == "text"]
    draft, source, check_result, retries = _produce(ctx, decision, flow, text, history, prior, prior_ref,
                                                    decision.kind, visible_titles)
    if stuck_point is not None and flow in ("example", "stubborn") and draft.code and not draft.code_is_old:
        stuck_point.given_code, stuck_point.key_line = draft.code, draft.key_line or 1
    actions = list(draft.actions)
    if flow in ("help", "locate") and not actions:
        actions = list(reply.HELP_ACTIONS)
    content = {"blocks": reply.to_blocks(draft, ctx), "actions": actions}
    agent_msg = m.ChatMessage(user_id=user_id, round_id=rnd.id, server_id=thread[0] if thread else None,
                              problem_id=thread[1] if thread else None, role="agent", kind="text",
                              content=content, created_at=now + dt.timedelta(milliseconds=1))
    s.add(agent_msg)
    s.flush()
    out = [student_msg, agent_msg]

    # 第 8 步 收尾
    rnd.last_message_at = now
    if ctx.submission is not None:
        rnd.submission_id = ctx.submission.submission_id
    if ctx.root_skill:
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
    quiz_msg = None
    if flow == "stubborn" and quiz.can_start(s, user_id, ctx.root_skill, now):
        item = quiz.choose_item(s, user_id, ctx.root_skill)
        if item is not None:
            quiz_msg = quiz.start(s, user_id, item, "revisit", "", thread[0] if thread else None,
                                  thread[1] if thread else None, rnd.id, now + dt.timedelta(milliseconds=2))
            rnd.quiz_triggered = True
            out.append(quiz_msg)
    if analysis.understood and action is None:
        close_round(s, rnd, "学生表示懂了", now)

    s.add(m.ChatStep(
        message_id=student_msg.id, round_id=rnd.id, user_id=user_id,
        intent=analysis.intent, skill_code=analysis.skill_code or ctx.root_skill, has_attempt=analysis.has_attempt,
        has_code=bool(pasted), icap=analysis.icap, analysis_source=analysis.source,
        course_id="C" if ctx.problem is not None else "", server_id=ctx.problem.server_id if ctx.problem else None,
        problem_id=ctx.problem.problem_id if ctx.problem else None,
        submission_id=ctx.submission.submission_id if ctx.submission else None, bind_by=ctx.bind_by,
        evidence_card=context.evidence_card(ctx),
        root_error_code=ctx.root_error, root_skill_code=ctx.root_skill, diag_confidence=ctx.diag_confidence,
        willingness=willingness, base_level=ctx.level, stuck_count=len(ctx.prior_stuck) + (1 if (ctx.round_stuck or stuck_point) else 0),
        rule_id=decision.rule_id, help_level=decision.help_level, reveal_level=decision.reveal_level,
        reply={**content, "flow": flow}, reply_source=source, check_result=check_result, retries=retries,
        round_status=rnd.status, end_reason=rnd.end_reason, quiz_triggered=quiz_msg is not None,
        stuck_point_id=stuck_point.id if stuck_point is not None else None,
        prompt_version=config.PROMPT_VERSION, model_version=config.DEEPSEEK_MODEL if source == "llm" else "",
        workflow_version=config.WORKFLOW_VERSION, duration_ms=int((time.monotonic() - t0) * 1000), created_at=now))
    llm.flush_records(s)
    s.flush()
    return out
