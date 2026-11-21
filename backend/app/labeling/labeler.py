"""给没通过的提交标错误类型：1 个主错误 + 最多 2 个次错误，每个错误同时给出对应的技能。

规则先行；规则判不了的交给大模型，只能从错误类型库里选。
编码不在库里：整条作废重标一次；仍不合法就记 OT99。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config, llm
from .. import models as m
from ..taxonomy import SKILLS, course_prompt, errors_for_course, is_error_code, is_skill_code, kp_of_skill
from ..taxonomy.errors import error_kp
from ..taxonomy.skills import skills_for_error
from ..xdoj.assemble import assemble_submission
from .rules import LabelDraft, label_by_rules

log = logging.getLogger("labeling")


def current_problem_label(s: Session, server_id: int, problem_id: int) -> m.ProblemLabel | None:
    return s.scalar(select(m.ProblemLabel).where(
        m.ProblemLabel.server_id == server_id, m.ProblemLabel.problem_id == problem_id,
        m.ProblemLabel.is_current.is_(True)))


def problem_label_at(s: Session, server_id: int, problem_id: int, at: dt.datetime) -> m.ProblemLabel | None:
    return s.scalar(select(m.ProblemLabel).where(
        m.ProblemLabel.server_id == server_id, m.ProblemLabel.problem_id == problem_id, m.ProblemLabel.created_at <= at)
        .order_by(m.ProblemLabel.created_at.desc(), m.ProblemLabel.version.desc()).limit(1))


def testcase_labels(s: Session, label: m.ProblemLabel | None) -> dict[int, m.TestcaseLabel]:
    if label is None:
        return {}
    rows = s.scalars(select(m.TestcaseLabel).where(m.TestcaseLabel.problem_label_id == label.id)).all()
    return {r.testcase_id: r for r in rows}


def current_labels(s: Session, server_id: int, submission_id: int) -> list[m.SubmissionLabel]:
    rows = s.scalars(select(m.SubmissionLabel).where(
        m.SubmissionLabel.server_id == server_id, m.SubmissionLabel.submission_id == submission_id,
        m.SubmissionLabel.is_current.is_(True))).all()
    return sorted(rows, key=lambda r: (r.role != "primary", r.id))


def pick_skill(error_code: str, testcase_ids: list[int], plabel: m.ProblemLabel | None,
               tlabels: dict[int, m.TestcaseLabel]) -> str:
    """错误对应哪个技能：先看失败测试点考的技能，再看这道题的技能里哪个和这种错误有关。"""
    problem_skills = ([plabel.primary_skill] + list(plabel.secondary_skills or [])) if plabel else []
    related = skills_for_error(error_code)
    for tid in testcase_ids:
        t = tlabels.get(tid)
        if t is not None and not t.is_ordinary and (t.skill_code in related or not related):
            return t.skill_code
    for code in problem_skills:
        if code in related:
            return code
    for tid in testcase_ids:
        t = tlabels.get(tid)
        if t is not None and not t.is_ordinary:
            return t.skill_code
    kp = error_kp(error_code)
    if kp:
        for code in related:
            if kp_of_skill(code) == kp:
                return code
    if related:
        return related[0]
    return problem_skills[0] if problem_skills else ""


_SYSTEM = """你是 C 语言课程的助教，任务是给一条没通过的提交标注错误类型。
只能从给出的错误类型库里选编码，不能自己造。输出 JSON：
{"labels":[{"errorCode":"TY09","skillCode":"C06.03.02","role":"primary","confidence":0.9,"line":5,"evidence":"不超过30字的依据"}],
 "candidate":null}
要求：正好 1 个 primary，最多 2 个 secondary；skillCode 从给出的技能里选；line 是源码行号，定不了填 null；
confidence 取 0 到 1。库里确实没有合适的，才用 OT99，并在 candidate 里写
{"name":"候选名称","description":"一句话描述","nearest":["最接近的两个现有编码"]}。"""


def _llm_prompt(evidence: dict, plabel: m.ProblemLabel | None, tlabels: dict[int, m.TestcaseLabel],
                user_id: str, name: str, course: str = "C") -> str:
    lib = "\n".join(f"{code} {item[0]}（信号：{item[2]}）" for code, item in errors_for_course(course).items())
    skills = ([plabel.primary_skill] + list(plabel.secondary_skills or [])) if plabel else []
    skills += [t.skill_code for t in tlabels.values()]
    skill_lines = "\n".join(f"{c} {SKILLS[c][0]}" for c in dict.fromkeys(skills) if c in SKILLS) or "（这道题还没标技能）"
    source = llm.strip_identity(evidence.get("sourceCode") or "", user_id, name)
    numbered = "\n".join(f"{i + 1:>3}| {line}" for i, line in enumerate(source.splitlines()))
    data = llm.evidence_for_model(evidence, user_id, name)
    for case in data["testCases"]:
        label = tlabels.get(case.get("testcaseId"))
        if label is not None:
            case["situation"] = label.situation
    return (
        f"【错误类型库】\n{lib}\n\n【这道题涉及的技能】\n{skill_lines}\n\n"
        f"【源码】\n{numbered}\n\n【题面与后台评测证据】\n{json.dumps(data, ensure_ascii=False)}")


def _validate(n_lines: int):
    def check(data: dict) -> tuple[list[LabelDraft], dict | None]:
        labels = data.get("labels")
        if not isinstance(labels, list) or not labels:
            raise ValueError("labels 为空")
        drafts: list[LabelDraft] = []
        for item in labels[:3]:
            code = str(item.get("errorCode", ""))
            if not is_error_code(code):
                raise ValueError(f"错误编码 {code} 不在库里")
            skill = str(item.get("skillCode") or "")
            if skill and not is_skill_code(skill):
                raise ValueError(f"技能编码 {skill} 不在技能表里")
            role = "primary" if item.get("role") == "primary" else "secondary"
            try:
                conf = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            line = item.get("line")
            line = int(line) if isinstance(line, int) and 1 <= line <= max(n_lines, 1) else None
            drafts.append(LabelDraft(code, skill, role, conf, line, str(item.get("evidence", ""))[:200], [], "llm"))
        if sum(1 for d in drafts if d.role == "primary") != 1:
            raise ValueError("必须正好有 1 个 primary")
        candidate = data.get("candidate") if isinstance(data.get("candidate"), dict) else None
        if any(d.error_code == "OT99" for d in drafts) and not candidate:
            raise ValueError("用了 OT99 就必须给 candidate")
        return drafts, candidate

    return check


def label_submission(s: Session, server_id: int, submission_id: int, use_llm: bool = True,
                     force: bool = False) -> list[m.SubmissionLabel]:
    """标注一条提交。已经标过就直接返回（force 时重标，旧标注保留、标为非当前）。"""
    existing = current_labels(s, server_id, submission_id)
    if existing and not force:
        return existing
    sub = s.get(m.Submission, (server_id, submission_id))
    if sub is None or sub.judgement in ("AC", "PENDING") or sub.detail_status != "done":
        return []
    evidence = assemble_submission(s, server_id, submission_id)
    plabel = current_problem_label(s, server_id, sub.problem_id)
    tlabels = testcase_labels(s, plabel)
    failed_ids = [t["testcaseId"] for t in evidence["testCases"] if t.get("passed") is False]

    drafts: list[LabelDraft] = []
    candidate = None
    rule = label_by_rules(evidence)
    if rule is not None:
        drafts = [rule]
    elif use_llm and llm.available():
        student = s.get(m.Student, sub.user_id)
        server = s.get(m.Server, server_id)
        n_lines = len((evidence.get("sourceCode") or "").splitlines())
        try:
            drafts, candidate = llm.call_json(
                "label_submission", course_prompt(_SYSTEM, server.course if server else "C"),
                _llm_prompt(evidence, plabel, tlabels, sub.user_id, student.name if student else "",
                            server.course if server else "C"),
                _validate(n_lines), retries=1)
        except llm.LLMUnavailable:
            # 重标一次仍不合法：记 OT99
            drafts = [LabelDraft("OT99", confidence=0.0, evidence="自动标注失败", source="llm")]
    llm.flush_records(s)
    if not drafts:
        return []

    if existing:
        s.execute(update(m.SubmissionLabel).where(
            m.SubmissionLabel.server_id == server_id, m.SubmissionLabel.submission_id == submission_id
        ).values(is_current=False))
    rows = []
    for d in drafts:
        if not d.testcase_ids:
            d.testcase_ids = failed_ids
        if not d.skill_code:
            d.skill_code = pick_skill(d.error_code, d.testcase_ids, plabel, tlabels)
        row = m.SubmissionLabel(
            server_id=server_id, submission_id=submission_id, error_code=d.error_code, skill_code=d.skill_code,
            role=d.role, confidence=d.confidence, line=d.line, evidence=d.evidence, testcase_ids=d.testcase_ids,
            source=d.source, library_version=config.ERROR_LIBRARY_VERSION, labeler_version=config.LABELER_VERSION,
            problem_label_version=plabel.version if plabel else None)
        s.add(row)
        rows.append(row)
        if d.error_code == "OT99" and candidate:
            s.add(m.LabelCandidate(
                kind="error", name=str(candidate.get("name", ""))[:64],
                description=str(candidate.get("description", ""))[:255],
                nearest=[c for c in candidate.get("nearest", []) if is_error_code(str(c))][:2],
                server_id=server_id, submission_id=submission_id, problem_id=sub.problem_id, user_id=sub.user_id))
    s.flush()
    return rows
