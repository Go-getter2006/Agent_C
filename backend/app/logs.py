"""开发者按需导出学生的行为日志（以前叫“教师按需申请”；2026-11-21 起只给开发者账号，没有申请记录页）。

做法参考数据库实训平台库道（DBEDU）给授课教师开放的“按需获取自己学生的查询日志”：
哪些 ID、什么时候、用了哪些样例、做了哪些交互。在这里对应成：

- 哪些 ID：学号（只给申请人班里的学生；不在他班里的学号列出来，不给）；
- 什么时候：每条记录的北京时间；
- 用了哪些样例：提交没通过的是第几组测试点（只给组号，不给测试数据）、做了哪道小题（出题方式）、
  智能体给过的示例代码；
- 做了哪些交互：登录、在 XDOJ 提交、提问、点求助按钮、智能体回复（帮助级别）、小题作答、复习、看代码、看差异、复制代码。

处理原则：
1. 只给开发者账号（角色 admin，能要这门课所有的学生）；以前给老师时按他名下的班核对学号。
2. 最少够用：只导出勾选的那几类；原文默认不导出，勾选“包含原文”才有：提问和回复的文字、贴的代码、
   复习小题的题干和给的代码、首稿、每次运行的代码和结果、完整事件、反馈后的问答、做完后的回顾；
   在这之下再勾“包含提交的源码”才有在 XDOJ 提交的源码。
   测试点的输入输出、学生的密码和令牌任何时候都不导出。
3. 留痕：每次导出仍在 log_requests 记一条（谁、什么时候、要了谁的、什么时间段、哪几类），页面上不再列出。
4. 文件不落盘：点导出时按定下的范围现生成，马上下载（下载链接 7 天后失效）。
5. 限量：一次最多 MAX_IDS 个学号、时间段最长 366 天；用途可以不写。
6. 分课程：只导出申请时登录的那门课的记录（C 语言和数据结构分开登录）。
7. 一律带学号和姓名，不做化名：老师要拿学号和期末考试系统的成绩比对（比对由老师自己做）。
   研究用的几类（求助回合、卡点、理解评估）和小题的独立作答字段带指标版本，定义见 docs/研究数据与指标.md。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from . import models as m
from .diagnosis import CHALLENGE_VERSION
from .quiz import challenge_state
from .xdoj.assemble import source_text
from .taxonomy import JUDGEMENTS, KNOWLEDGE_POINTS, course_of, error_name, kp_of_skill, skill_name

KINDS: dict[str, str] = {
    "login": "登录",
    "submission": "在 XDOJ 提交",
    "ask": "提问和求助",
    "reply": "智能体回复",
    "quiz": "小题",
    "review": "复习",
    "view": "看代码、看差异、复制代码",
    "round": "求助回合",
    "stuck": "卡点",
    "help": "帮助之后（每次帮助后的下一次提交）",
    "decision": "系统的决定（复习先出哪个、出正确还是有错的代码、发不发主动消息）",
    "assess": "理解评估",
}
MAX_IDS = 5000   # 开发者全选所有班时也够
# 导出文件的格式版本：列或 data 里的字段改了就改这个（logs-2：加“栏目”“代码”两列、复习小题全过程、可选提交源码）
EXPORT_VERSION = "logs-2"
MAX_DAYS = 366
TTL_DAYS = 7

UI_KIND = {
    "login": ("login", "登录"),
    "open_submission": ("view", "看提交的代码"), "open_diff": ("view", "看两次提交的差异"),
    "copy_code": ("view", "复制回复里的代码"), "open_problem": ("view", "切到一道题"),
    "practice": ("view", "学情页点了去练一题"), "open_review": ("review", "打开复习页"),
    "review_start": ("review", "开始一道复习题"),
}
HELP_TEXT = {"NA": "", "H0": "反问", "H1": "指出位置", "H2": "讲原理", "H3": "给了示例代码"}
BUTTONS = {"我想自己想想": "点了“我想自己想想”", "再给点提示": "点了“再给点提示”", "直接讲给我": "点了“直接讲给我”"}
RESOLUTION_TEXT = {"pending": "还在处理", "recalled": "自己回忆出来", "blank_filled": "挖空补上",
                   "example_given": "给了示例代码", "exercise": "小练习"}
SOURCE_TEXT = {"own_code": "用他自己的代码出的题", "llm": "大模型出的题", "template": "题库的题换成他的变量名",
               "bank": "题库原题"}


class RequestError(ValueError):
    """申请不合格，message 直接给教师看。"""


def parse_ids(text: str) -> list[str]:
    return list(dict.fromkeys(x for x in re.split(r"[\s,，;；、]+", text or "") if x))


@dataclass
class Row:
    user_id: str
    name: str
    time: dt.datetime
    kind: str
    action: str
    course: str = ""
    problem_id: int | None = None
    problem: str = ""
    submission_id: int | None = None
    samples: str = ""
    detail: str = ""
    section: str = ""     # 栏目：知识问答 / 作业引导 / 复习提升 / 主动消息（看得出的才填）
    text: str = ""
    code: str = ""
    data: dict = field(default_factory=dict)


SECTION_ASK, SECTION_HOMEWORK, SECTION_REVIEW, SECTION_NOTICE = "知识问答", "作业引导", "复习提升", "主动消息"


def _section(problem_id: int | None, topic_id: int | None = None) -> str:
    """对话在哪个栏目：绑了题的是作业引导；知识问答的主题、以前不绑题的“综合”对话都算知识问答。"""
    return SECTION_HOMEWORK if problem_id is not None and topic_id is None else SECTION_ASK


def _proactive(msg: m.ChatMessage) -> bool:
    """智能体主动发的：提交没过的提醒（chat/proactive.py）、通过后的总结（chat/summary.py）。"""
    return msg.role == "agent" and (msg.failure_submission_id is not None or "summary" in (msg.content or {}))


def _code_blocks(blocks: list[dict]) -> list[dict]:
    return [{"caption": b.get("caption", ""), "start": b.get("start", 1), "code": "\n".join(b.get("lines", []))}
            for b in blocks if b.get("t") == "code"]


def _join_code(parts: list[tuple[str, str]]) -> str:
    """几段代码放进一格：每段前面一行【标题】。"""
    return "\n\n".join(f"【{title}】\n{code}" if title else code for title, code in parts if code)


def _problem_title(s: Session, cache: dict, server_id: int | None, problem_id: int | None) -> str:
    if problem_id is None:
        return ""
    key = (server_id, problem_id)
    if key not in cache:
        p = s.get(m.Problem, key) if server_id is not None else None
        cache[key] = p.title if p else ""
    return cache[key]


def collect(s: Session, user_ids: list[str], date_from: dt.datetime, date_to: dt.datetime, kinds: list[str],
            include_text: bool, course: str | None = None, include_code: bool = False) -> list[Row]:
    """按申请的范围把日志收齐，按时间排好。course：只要这门课的。

    include_text：带原文（文字、贴的代码、复习小题的全过程）；include_code：再带提交的源码（要先有 include_text）。
    """
    include_code = include_code and include_text
    if not user_ids:
        return []
    names = dict(s.execute(select(m.Student.user_id, m.Student.name).where(m.Student.user_id.in_(user_ids))).all())
    courses = {sv.id: sv.course for sv in s.scalars(select(m.Server)).all()}
    servers = [sid for sid, c in courses.items() if course is None or c == course] or [-1]
    titles: dict = {}
    rows: list[Row] = []

    def add(**kw) -> None:
        rows.append(Row(name=names.get(kw["user_id"], ""), **kw))

    if "submission" in kinds:
        subs = s.scalars(select(m.Submission).where(
            m.Submission.user_id.in_(user_ids), m.Submission.server_id.in_(servers), m.Submission.submit_time >= date_from,
            m.Submission.submit_time <= date_to).order_by(m.Submission.submission_id)).all()
        attempt_no: dict = {}
        for sub in s.scalars(select(m.Submission).where(m.Submission.user_id.in_(user_ids))
                             .order_by(m.Submission.submission_id)).all():
            k = (sub.server_id, sub.user_id, sub.problem_id)
            attempt_no[k] = attempt_no.get(k, 0) + 1
            attempt_no[(sub.server_id, sub.submission_id)] = attempt_no[k]
        prediction_metadata = dict(s.execute(select(m.PredictionLog.source_key, m.PredictionLog.provenance).where(
            m.PredictionLog.user_id.in_(user_ids), m.PredictionLog.ts >= date_from, m.PredictionLog.ts <= date_to,
            m.PredictionLog.skill_code.like((course or "") + "%"))).all())
        for sub in subs:
            judge = s.scalar(select(m.Judge).where(m.Judge.server_id == sub.server_id,
                                                   m.Judge.submission_id == sub.submission_id, m.Judge.is_latest.is_(True)))
            cells = ""
            failed = []
            if judge is not None:
                order = {t: i + 1 for i, t in enumerate(s.scalars(select(m.Testcase.testcase_id).where(
                    m.Testcase.server_id == sub.server_id, m.Testcase.problem_id == sub.problem_id
                ).order_by(m.Testcase.seq, m.Testcase.testcase_id)).all())}
                runs = s.scalars(select(m.JudgeRun).where(m.JudgeRun.server_id == sub.server_id,
                                                          m.JudgeRun.judge_id == judge.judge_id)).all()
                runs = sorted(runs, key=lambda r: order.get(r.testcase_id, 10**6))
                cells = "".join("过" if r.passed else "×" for r in runs)
                failed = [order.get(r.testcase_id) for r in runs if not r.passed and r.testcase_id in order]
            labels = s.scalars(select(m.SubmissionLabel).where(
                m.SubmissionLabel.server_id == sub.server_id, m.SubmissionLabel.submission_id == sub.submission_id,
                m.SubmissionLabel.is_current.is_(True))).all()
            errs = "、".join(f"{lab.error_code} {error_name(lab.error_code)}" for lab in labels)
            n = attempt_no.get((sub.server_id, sub.submission_id), 0)
            source = (source_text(s, sub.server_id, sub.submission_id) or "") if include_code else ""
            add(user_id=sub.user_id, time=sub.submit_time, kind="submission", action="在 XDOJ 提交",
                section=SECTION_HOMEWORK, code=source,
                course=courses.get(sub.server_id, ""), problem_id=sub.problem_id,
                problem=_problem_title(s, titles, sub.server_id, sub.problem_id), submission_id=sub.submission_id,
                samples=("没通过第 " + "、".join(str(x) for x in failed) + " 组") if failed else "",
                detail=f"第 {n} 次，{JUDGEMENTS.get(sub.judgement, sub.judgement_text)}" + (f"，{cells}" if cells else "")
                + (f"，标注：{errs}" if errs else ""),
                data={"attempt": n, **({"predictionRecord": prediction_metadata.get(f"first:{sub.server_id}:{sub.submission_id}")} if n == 1 else {}), "result": sub.judgement, "cells": cells, "failedGroups": failed,
                      "errors": [lab.error_code for lab in labels],
                      **({"source": source, "sourceFetched": bool(source)} if include_code else {})})

    if {"ask", "reply"} & set(kinds):
        q = select(m.ChatMessage)
        if course is not None:
            q = q.where(m.ChatMessage.course == course)
        msgs = s.scalars(q.where(
            m.ChatMessage.user_id.in_(user_ids), m.ChatMessage.created_at >= date_from,
            m.ChatMessage.created_at <= date_to, m.ChatMessage.kind == "text").order_by(m.ChatMessage.id)).all()
        steps = {st.message_id: st for st in s.scalars(select(m.ChatStep).where(
            m.ChatStep.message_id.in_([x.id for x in msgs if x.role == "student"]))).all()} if msgs else {}
        last_step: dict[str, m.ChatStep] = {}
        for msg in msgs:
            blocks = (msg.content or {}).get("blocks", [])
            text = "\n".join(b.get("text", "") for b in blocks if b.get("t") == "p")
            codes = [b for b in blocks if b.get("t") == "code"]
            code_parts = _code_blocks(blocks) if include_text else []
            code = _join_code([(c["caption"], c["code"]) for c in code_parts])
            title = _problem_title(s, titles, msg.server_id, msg.problem_id)
            section = SECTION_NOTICE if _proactive(msg) else _section(msg.problem_id, msg.topic_id)
            if msg.role == "student":
                st = steps.get(msg.id)
                if st is not None:
                    last_step[msg.user_id] = st
                if "ask" not in kinds:
                    continue
                action = BUTTONS.get(text.strip(), "发了一条消息" + ("（贴了代码）" if codes else ""))
                detail = ""
                if st is not None:
                    detail = f"意图 {st.intent}，规则 {st.rule_id}，帮助级别 {st.help_level}" + (
                        f"，诊断 {st.root_error_code}" if st.root_error_code else "")
                add(user_id=msg.user_id, time=msg.created_at, kind="ask", action=action,
                    course=msg.course, problem_id=msg.problem_id, section=section,
                    problem=title, submission_id=st.submission_id if st else None, detail=detail,
                    text=text if include_text else "", code=code,
                    data={"message": msg.id, "round": msg.round_id, "topic": msg.topic_id,
                          "intent": st.intent if st else None,
                          "rule": st.rule_id if st else None, "help": st.help_level if st else None,
                          "pastedCode": bool(codes), **({"codeBlocks": code_parts} if include_text else {})})
            elif "reply" in kinds:
                st = last_step.get(msg.user_id)
                example = [b for b in codes if b.get("caption") in ("示例", "上次给你的那段")]
                proactive = section == SECTION_NOTICE
                add(user_id=msg.user_id, time=msg.created_at, kind="reply",
                    action=("提交没过的提醒" if msg.failure_submission_id is not None else "通过后的总结")
                    if proactive else "智能体回复",
                    course=msg.course, problem_id=msg.problem_id, section=section,
                    problem=title, samples="给了示例代码" if example else "",
                    submission_id=msg.failure_submission_id if proactive else None,
                    detail="" if proactive else (HELP_TEXT.get(st.help_level, "") if st else "") + (
                        f"，回复来源 {st.reply_source}" if st and st.reply_source else ""),
                    text=text if include_text else "", code=code,
                    data={"message": msg.id, "round": msg.round_id, "topic": msg.topic_id, "proactive": proactive,
                          "help": None if proactive else st.help_level if st else None, "exampleCode": bool(example),
                          **({"codeBlocks": code_parts} if include_text else {})})

    if "quiz" in kinds or "review" in kinds:
        attempts = s.scalars(select(m.QuizAttempt).where(
            m.QuizAttempt.user_id.in_(user_ids), m.QuizAttempt.created_at >= date_from,
            m.QuizAttempt.created_at <= date_to).order_by(m.QuizAttempt.id)).all()
        # 以前插在对话里的小题：按它所在的对话定栏目
        in_chat = {x.quiz_attempt_id: _section(x.problem_id, x.topic_id) for x in s.scalars(select(m.ChatMessage).where(
            m.ChatMessage.quiz_attempt_id.in_([a.id for a in attempts] or [-1]))).all()}
        for a in attempts:
            kind = "review" if a.reason == "review" else "quiz"
            if kind not in kinds or (course is not None and course_of(a.skill_code or "C") != course):
                continue
            item = s.get(m.QuizItem, a.item_id)
            result = {"right": "改对了", "right_judge": "判断对", "right_second": "第二次改对",
                      "right_bypass": "绕开原错误改对", "wrong_fix": "两次没改对", "wrong_judge": "判断错",
                      "skipped": "跳过"}.get(a.outcome, "没做完")
            full = _quiz_full(a, item) if include_text else None
            add(user_id=a.user_id, time=a.created_at, kind=kind, course=course_of(a.skill_code or "C"),
                section=SECTION_REVIEW if kind == "review" else in_chat.get(a.id, ""),
                text=full["text"] if full else "", code=full["code"] if full else "",
                action="复习题" if kind == "review" else "小题",
                problem_id=item.source_problem_id if item else None,
                problem=_problem_title(s, titles, item.source_server_id if item else None,
                                       item.source_problem_id if item else None),
                samples=(f"小题 {item.code}，{SOURCE_TEXT.get(item.source, item.source)}" if item else ""),
                detail=f"{skill_name(a.skill_code)}（{a.skill_code}），{a.quiz_type}，把握“{a.confidence or '没选'}”，"
                       f"{result}" + (f"，运行 {a.tries} 次" if a.tries else "") +
                       (f"，用时 {round((a.duration_ms or 0) / 1000)} 秒" if a.duration_ms else ""),
                data={"skill": a.skill_code, "kp": kp_of_skill(a.skill_code or ""), "type": a.quiz_type,
                      "reason": a.reason, "confidence": a.confidence, "outcome": a.outcome, "state": a.state,
                      "tries": a.tries, "changedLines": a.changed_lines, "source": item.source if item else None,
                      "item": item.code if item else None, "hasBug": item.has_bug if item else None,
                      "protocol": a.protocol, "itemVersion": a.item_version, "judgedHasBug": a.judged_has_bug,
                      "judgeCorrect": a.judge_correct, "firstLines": a.first_lines, "locateHit": a.locate_hit,
                      "firstFixPass": a.first_fix_pass, "firstFixClean": a.first_fix_clean, "hints": a.hints,
                      "sampleRuns": a.sample_runs, "skipped": a.skipped, "durationMs": a.duration_ms,
                      "challenge": challenge_state(a) if a.protocol >= 3 else None,
                      # 随机分组：这次分到追问（True）还是不追问（False）；没到这一步的是 None
                      "challengeAssigned": True if any(e.get("kind") == "challenge" for e in a.events or [])
                      else False if any(e.get("kind") == "no_challenge" for e in a.events or []) else None,
                      "challengeVersion": CHALLENGE_VERSION if a.protocol >= 3 else None,
                      "events": list(a.events or []) if include_text else
                      [{k: v for k, v in e.items() if k in ("t", "kind", "misjudge", "pass", "triggerAt", "triggerReason", "phase", "secondsSinceTrigger", "keep")}
                       for e in (a.events or [])],
                      **(full["data"] if full else {})})

    if "round" in kinds:
        q = select(m.ChatRound).where(m.ChatRound.user_id.in_(user_ids), m.ChatRound.started_at >= date_from,
                                      m.ChatRound.started_at <= date_to)
        if course is not None:
            q = q.where(m.ChatRound.course == course)
        for r in s.scalars(q.order_by(m.ChatRound.id)).all():
            n_student = sum(1 for _ in s.scalars(select(m.ChatMessage.id).where(
                m.ChatMessage.round_id == r.id, m.ChatMessage.role == "student", m.ChatMessage.kind == "text")).all())
            minutes = round(((r.ended_at or r.last_message_at) - r.started_at).total_seconds() / 60, 1)
            add(user_id=r.user_id, time=r.started_at, kind="round", section=_section(r.problem_id),
                action="通过后的回顾" if r.end_reason == m.REFLECT_END else "求助回合", course=r.course,
                problem_id=r.problem_id, problem=_problem_title(s, titles, r.server_id, r.problem_id),
                submission_id=r.submission_id,
                detail=f"{skill_name(r.skill_code) if r.skill_code else '没定技能'}，学生发了 {n_student} 条，"
                       f"最多帮助 {HELP_TEXT.get(r.max_help_level) or '没有'}，" + (r.end_reason or "还开着"),
                data={"round": r.id, "skill": r.skill_code, "kp": kp_of_skill(r.skill_code) if r.skill_code else "",
                      "error": r.error_code, "maxHelp": r.max_help_level, "helpCount": r.help_count,
                      "askedBack": r.asked_back, "failedReplies": r.failed_replies, "located": r.located,
                      "studentMessages": n_student, "endReason": r.end_reason, "status": r.status,
                      "minutes": minutes, "bound": r.problem_id is not None,
                      "reflection": r.end_reason == m.REFLECT_END})

    if "stuck" in kinds and course is not None:
        from . import stuckstats

        for st in stuckstats.compute(s, user_ids, course).values():
            for e in st.episodes:
                sp = e.sp
                if not date_from <= sp.created_at <= date_to:
                    continue
                add(user_id=sp.user_id, time=sp.created_at, kind="stuck", action="卡点", course=course,
                    problem_id=sp.problem_id, problem=_problem_title(s, titles, sp.server_id, sp.problem_id),
                    detail=f"{KNOWLEDGE_POINTS.get(e.kp, '')} · {skill_name(sp.skill_code)}，第 {sp.nth} 次，"
                           f"{RESOLUTION_TEXT.get(sp.resolution, sp.resolution)}，"
                           + ("没绑定题" if e.passed is None else ("之后通过" if e.passed else "还没通过")),
                    data={"version": stuckstats.VERSION, "round": sp.round_id, "skill": sp.skill_code, "kp": e.kp,
                          "error": sp.error_code, "nth": sp.nth, "stubborn": sp.stubborn, "resolution": sp.resolution,
                          "passed": e.passed, "triesAfter": e.tries_after,
                          "passedAt": e.passed_at.isoformat() if e.passed_at else None})

    if "help" in kinds:
        from . import helpeffect

        for e in helpeffect.episodes(s, user_ids, course):
            at = helpeffect.happened_at(e)
            if not date_from <= at <= date_to:
                continue
            outcome = ("还没再交" if e.next_sub is None else "下一次通过" if e.next_pass
                       else "下一次还没过" if e.next_pass is False else "下一次还在评测")
            add(user_id=e.step.user_id, time=at, kind="help", action="帮助之后", course=e.step.course_id or course,
                problem_id=e.step.problem_id, problem=_problem_title(s, titles, e.step.server_id, e.step.problem_id),
                submission_id=e.next_sub.submission_id if e.next_sub else None,
                detail=f"{helpeffect.LEVEL_NAMES.get(e.level, e.level)}，{outcome}"
                       + ("" if e.error_gone is None else ("，未再识别到原错误" if e.error_gone else "，讨论的错误还在")),
                data=helpeffect.export_row(e))

    if "decision" in kinds:
        q = select(m.DecisionLog).where(m.DecisionLog.user_id.in_(user_ids), m.DecisionLog.ts >= date_from,
                                        m.DecisionLog.ts <= date_to)
        if course is not None:
            q = q.where(m.DecisionLog.course == course)
        names = {"review_pick": "复习先出哪个", "quiz_plan": "出什么样的小题", "notice": "发不发主动消息"}
        for d in s.scalars(q.order_by(m.DecisionLog.id)).all():
            ref = d.ref or {}
            add(user_id=d.user_id, time=d.ts, kind="decision", action=names.get(d.kind, d.kind), course=d.course,
                problem_id=ref.get("problem"), submission_id=ref.get("submission"),
                detail=f"{names.get(d.kind, d.kind)}：{d.chosen}（候选 {len(d.candidates or [])} 个，概率 {d.probability:g}）",
                data={"decision": d.id, "kind": d.kind, "rule": d.rule, "chosen": d.chosen, "probability": d.probability,
                      "candidates": d.candidates, "context": d.context, "ref": ref})

    if "assess" in kinds:
        q = select(m.AgentAssessment).where(m.AgentAssessment.user_id.in_(user_ids),
                                            m.AgentAssessment.created_at >= date_from,
                                            m.AgentAssessment.created_at <= date_to)
        if course is not None:
            q = q.where(m.AgentAssessment.course == course)
        found = list(s.scalars(q.order_by(m.AgentAssessment.id)).all())
        reflect = set(s.scalars(select(m.ChatRound.id).where(
            m.ChatRound.id.in_([a.round_id for a in found if a.round_id is not None] or [-1]),
            m.ChatRound.end_reason == m.REFLECT_END)).all())
        for a in found:
            add(user_id=a.user_id, time=a.created_at, kind="assess", action="理解评估", course=a.course,
                problem_id=a.problem_id, problem=_problem_title(s, titles, a.server_id, a.problem_id),
                submission_id=a.submission_id,
                detail=f"{skill_name(a.skill_code)}，"
                       f"{'通过后的回顾' if a.round_id in reflect else '看对话' if a.basis == 'chat' else '看代码怎么改的'}，"
                       f"等级 {a.level if a.level is not None else '不评'}（{a.source}，原始 {a.raw_level}，规则 {a.rule_level}）"
                       + (f"，下调：{a.adjusted}" if a.adjusted else ""),
                text="\n".join(a.evidence or []) if include_text else "",
                data={"assessment": a.id, "round": a.round_id, "skill": a.skill_code, "kp": kp_of_skill(a.skill_code),
                      "basis": a.basis, "level": a.level, "rawLevel": a.raw_level, "ruleLevel": a.rule_level,
                      "source": a.source, "confidence": round(a.confidence or 0, 2), "adjusted": a.adjusted,
                      "evidenceCount": len(a.evidence or []), "promptVersion": a.prompt_version,
                      "reflection": a.round_id in reflect})

    ui_kinds = {k for k, (kind, _t) in UI_KIND.items() if kind in kinds}
    if ui_kinds:
        q = select(m.UiEvent)
        if course is not None:
            q = q.where(m.UiEvent.course == course)
        events = s.scalars(q.where(
            m.UiEvent.user_id.in_(user_ids), m.UiEvent.ts >= date_from, m.UiEvent.ts <= date_to,
            m.UiEvent.kind.in_(ui_kinds)).order_by(m.UiEvent.id)).all()
        for e in events:
            kind, action = UI_KIND[e.kind]
            add(user_id=e.user_id, time=e.ts, kind=kind, action=action, detail=e.target or "", course=e.course,
                section=SECTION_REVIEW if kind == "review" else "",
                data={"event": e.kind, "target": e.target})

    rows.sort(key=lambda r: (r.time, r.user_id))
    return rows


def _quiz_full(a: m.QuizAttempt, item: m.QuizItem | None) -> dict:
    """一次小题作答的全过程（勾了“包含原文”才有）：题、给的代码、他的首稿、每次运行、反馈后的问答、做完后的回顾。

    这些都是我们自己出的题和他在这里写的东西，不含 XDOJ 的测试点。
    """
    runs = [{"kind": r.get("kind"), "message": r.get("message"), "counted": r.get("counted"),
             "choice": r.get("choice"), "code": r.get("code") or ""} for r in (a.runs or [])]
    turns = [{k: v for k, v in turn.items() if k in ("t", "question", "answer")} for turn in (a.discussion or [])]
    recap = a.recap or None
    quiz = None
    if item is not None:
        quiz = {"stem": item.stem, "caption": item.caption, "shownCode": item.shown_code, "startLine": item.start_line,
                "judgedCode": item.judged_code or None, "hasBug": item.has_bug, "bugLines": list(item.bug_lines or []),
                "standardFix": item.standard_fix or None, "explanation": item.explanation or None}
    text = []
    if item is not None:
        text.append(f"题干：{item.stem}")
    for turn in turns:
        text.append(f"他问：{turn.get('question', '')}\n智能体答：{turn.get('answer', '')}")
    if recap and recap.get("analysis"):
        text.append(f"做完后的回顾：{recap['analysis']}")
    code = [("给的代码", item.shown_code if item else ""), ("首稿", a.first_fix or "")]
    code += [(f"第 {i} 次运行（{r['kind'] or ''}）", r["code"]) for i, r in enumerate(runs, 1)]
    return {"text": "\n\n".join(text), "code": _join_code(code),
            "data": {"quiz": quiz, "firstFix": a.first_fix or None, "runs": runs, "discussion": turns,
                     "recap": recap}}


def allowed_students(s: Session, user: m.User) -> set[str] | None:
    """申请人能要哪些学生的日志：他名下这门课的班里的。None 表示不限（管理员）。现在不在班上、以前在过的也算。"""
    if user.role == "admin":
        return None
    from .accounts import teacher_source_ids

    ids = teacher_source_ids(s, user)
    if not ids:
        return set()
    servers = list(s.scalars(select(m.Server.id).where(m.Server.course == user.course)).all()) or [-1]
    classes = s.execute(select(m.ClassSource.server_id, m.ClassSource.class_id).where(
        m.ClassSource.data_source_id.in_(ids), m.ClassSource.server_id.in_(servers))).all()
    out: set[str] = set()
    for server_id, class_id in classes:
        out |= set(s.scalars(select(m.Enrollment.user_id).where(
            m.Enrollment.server_id == server_id, m.Enrollment.class_id == class_id)).all())
    return out


def create(s: Session, user: m.User, ids_text: str, class_ids: list[str], date_from: dt.datetime,
           date_to: dt.datetime, kinds: list[str], include_text: bool, fmt: str, purpose: str,
           course: str = "C", include_code: bool = False) -> m.LogRequest:
    now = db.now()
    kinds = [k for k in dict.fromkeys(kinds) if k in KINDS]
    if not kinds:
        raise RequestError("至少选一类记录。")
    if fmt not in ("csv", "json"):
        raise RequestError("格式只能是 CSV 或 JSON。")
    if date_to < date_from:
        raise RequestError("结束时间早于开始时间。")
    if (date_to - date_from).days > MAX_DAYS:
        raise RequestError(f"时间段最长 {MAX_DAYS} 天。")
    requested = parse_ids(ids_text)
    allowed = allowed_students(s, user)
    from_classes: list[str] = []
    for key in class_ids:
        mt = re.fullmatch(r"c(\d+)-(\d+)", key or "")
        if not mt:
            continue
        server_id, class_id = int(mt.group(1)), int(mt.group(2))
        members = s.scalars(select(m.Enrollment.user_id).where(
            m.Enrollment.server_id == server_id, m.Enrollment.class_id == class_id)).all()
        from_classes += [u for u in members if allowed is None or u in allowed]
    wanted = list(dict.fromkeys(requested + from_classes))
    if not wanted:
        raise RequestError("没有填学号，也没有选班级。")
    if len(wanted) > MAX_IDS:
        raise RequestError(f"一次最多 {MAX_IDS} 个学号，这次有 {len(wanted)} 个。")
    known = set(s.scalars(select(m.Student.user_id).where(m.Student.user_id.in_(wanted))).all())
    granted = [u for u in wanted if u in known and (allowed is None or u in allowed)]
    denied = [u for u in wanted if u not in granted]
    req = m.LogRequest(
        requester_id=user.id, requester_name=user.display_name or user.login_name, requested_ids=requested, course=course,
        granted_ids=granted, denied_ids=denied, class_keys=list(class_ids), date_from=date_from,
        date_to=min(date_to, now), kinds=kinds, include_text=bool(include_text),
        include_code=bool(include_code and include_text), fmt=fmt,
        purpose=purpose.strip()[:200], token=secrets.token_urlsafe(24), status="ready", created_at=now,
        expires_at=now + dt.timedelta(days=TTL_DAYS))
    req.rows = len(collect(s, granted, req.date_from, req.date_to, kinds, include_text, course, req.include_code))
    s.add(req)
    s.flush()
    return req


def state_of(req: m.LogRequest, at: dt.datetime | None = None) -> str:
    if req.status == "ready" and (at or db.now()) > req.expires_at:
        return "expired"
    return req.status


COLUMNS = [("user_id", "学号"), ("name", "姓名"), ("time", "时间"), ("kind", "类型"), ("action", "交互"),
           ("section", "栏目"), ("course", "课程"), ("problem_id", "题号"), ("problem", "题目"),
           ("submission_id", "提交编号"), ("samples", "样例"), ("detail", "详情"), ("text", "原文"), ("code", "代码")]


def versions() -> dict:
    """导出里各指标的版本（定义改了版本号就变，docs/研究数据与指标.md）。"""
    from . import config, decisions, diagnosis, helpeffect, stuckstats

    return {"export": EXPORT_VERSION, "stuck": stuckstats.VERSION, "helpNext": helpeffect.VERSION, "decisions": decisions.VERSION,
            "prompt": config.PROMPT_VERSION,
            "diagnosis": diagnosis.VERSION, "quizProtocol": 2,
            "graph": config.GRAPH_VERSION,
            "reviewSchedule": "evidence-priority-1", "mastery": config.MASTERY_VERSION}


def render(s: Session, req: m.LogRequest) -> tuple[bytes, str, str]:
    """按申请时定下的范围现生成文件。返回 (内容, MIME 类型, 文件名)。"""
    rows = collect(s, list(req.granted_ids), req.date_from, req.date_to, list(req.kinds), req.include_text, req.course,
                   req.include_code)
    stamp = req.created_at.astimezone(db.BEIJING).strftime("%Y%m%d-%H%M")
    cols = [c for c in COLUMNS if c[0] not in ("text", "code") or req.include_text]

    def value(r: Row, key: str):
        v = getattr(r, key)
        if key == "time":
            return v.astimezone(db.BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        if key == "kind":
            return KINDS.get(v, v)
        return "" if v is None else v

    if req.fmt == "json":
        payload = {
            "request": {"id": req.id, "requester": req.requester_name, "createdAt": req.created_at.isoformat(),
                        "from": req.date_from.isoformat(), "to": req.date_to.isoformat(), "kinds": req.kinds,
                        "includeText": req.include_text, "includeCode": req.include_code,
                        "students": req.granted_ids, "purpose": req.purpose, "versions": versions()},
            "rows": [{**{k: value(r, k) for k, _ in cols}, "data": r.data} for r in rows],
        }
        return (json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"), "application/json",
                f"学生日志-{stamp}.json")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _k, label in cols])
    for r in rows:
        w.writerow([value(r, k) for k, _ in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", f"学生日志-{stamp}.csv"


def view(req: m.LogRequest, at: dt.datetime | None = None) -> dict:
    return {
        "id": req.id, "createdAt": req.created_at.isoformat(), "requester": req.requester_name,
        "from": req.date_from.isoformat(), "to": req.date_to.isoformat(), "kinds": req.kinds,
        "kindNames": [KINDS.get(k, k) for k in req.kinds], "includeText": req.include_text,
        "includeCode": req.include_code, "format": req.fmt,
        "purpose": req.purpose, "granted": len(req.granted_ids), "denied": req.denied_ids, "rows": req.rows,
        "status": state_of(req, at), "expiresAt": req.expires_at.isoformat(), "downloads": req.downloads,
        "token": req.token if state_of(req, at) == "ready" else None,
    }
