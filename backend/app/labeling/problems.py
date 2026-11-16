"""题目标注：1 个主技能 + 最多 4 个次技能、每个测试点考什么、一两句话的题目简述。

需要一份正确代码作参照：先用同学的通过提交（XDOJ 已经用全部测试点验证过）；
没有时让大模型写一份，用全部测试点在沙箱里跑通了才算数。
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config, llm
from .. import models as m
from ..sandbox import run_program
from ..sandbox.precheck import check_reference_solution
from ..taxonomy import KNOWLEDGE_POINTS, SKILLS, is_skill_code
from ..xdoj.assemble import source_text
from ..xdoj.util import decode_bytes, json_hash

log = logging.getLogger("labeling.problems")


def problem_content_hash(s: Session, server_id: int, problem_id: int) -> str:
    text = s.get(m.ProblemText, (server_id, problem_id))
    cases = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id).order_by(m.Testcase.testcase_id)).all()
    return json_hash([text.file_hash if text else "", [(c.testcase_id, c.io_hash) for c in cases]])


def _same_output(a: str, b: str) -> bool:
    norm = lambda t: [line.rstrip() for line in (t or "").replace("\r\n", "\n").rstrip().split("\n")]  # noqa: E731
    return norm(a) == norm(b)


def verify_solution(s: Session, server_id: int, problem_id: int, source: str) -> bool:
    """用全部测试点跑一遍，全对才算数。"""
    if check_reference_solution(source) is not None:
        return False
    cases = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id)).all()
    cases = [c for c in cases if c.input_bytes is not None and c.output_bytes is not None]
    if not cases:
        return False
    inputs = [decode_bytes(c.input_bytes, c.input_encoding)[0] or "" for c in cases]
    result = run_program(source, inputs, cpu_seconds=2, memory_mb=128)
    if result.status != "ok" or len(result.runs) != len(cases):
        return False
    for run, case in zip(result.runs, cases):
        want = decode_bytes(case.output_bytes, case.output_encoding)[0] or ""
        if run.status != "ok" or not _same_output(run.stdout, want):
            return False
    return True


def reference_solution(s: Session, server_id: int, problem_id: int) -> str | None:
    """取一份正确代码：库里验证过的 → 同学的通过提交 → 大模型写并跑通。"""
    saved = s.scalar(select(m.ReferenceSolution).where(
        m.ReferenceSolution.server_id == server_id, m.ReferenceSolution.problem_id == problem_id,
        m.ReferenceSolution.verified.is_(True)).order_by(m.ReferenceSolution.id.desc()))
    if saved is not None:
        return saved.source_code
    peers = s.scalars(select(m.Submission.submission_id).where(
        m.Submission.server_id == server_id, m.Submission.problem_id == problem_id,
        m.Submission.judgement == "AC", m.Submission.detail_status == "done"
    ).order_by(m.Submission.submission_id.desc()).limit(5)).all()
    best = None
    for sid in peers:
        text = source_text(s, server_id, sid)
        if text and (best is None or len(text) < len(best)):
            best = text
    if best is not None:
        s.add(m.ReferenceSolution(server_id=server_id, problem_id=problem_id, origin="peer",
                                  source_code=best, verified=True))
        return best
    if not llm.available():
        return None
    text = s.get(m.ProblemText, (server_id, problem_id))
    if text is None or not text.extracted_text:
        return None
    try:
        code = llm.call_json(
            "reference_solution",
            "你是 C 语言课程的助教。根据题面写一份正确、朴素的 C 程序，只用标准库。"
            '输出 JSON：{"code":"完整的 C 源码"}',
            text.extracted_text[:4000],
            lambda d: str(d["code"]) if isinstance(d.get("code"), str) and "main" in d["code"] else (_ for _ in ()).throw(ValueError("没有给出完整程序")),
            retries=1, max_tokens=1500)
    except llm.LLMUnavailable:
        return None
    ok = verify_solution(s, server_id, problem_id, code)
    s.add(m.ReferenceSolution(server_id=server_id, problem_id=problem_id, origin="llm", source_code=code, verified=ok))
    return code if ok else None


_SYSTEM = """你是 C 语言课程的助教，任务是标注一道编程题考什么。只能从给出的技能表里选编码。输出 JSON：
{"brief":"一两句话说这题要干什么，不超过30字，不出现题号",
 "primary":"C06.03.02","secondary":["C06.02.01"],
 "testcases":[{"id":1397,"skill":"C06.03.02","situation":"全是负数","ordinary":false}]}
要求：primary 正好 1 个；secondary 最多 4 个；每个测试点都要给：skill 是它主要考的技能，
situation 是不超过 8 个字的“哪类情况”（如“只有一个数”“全是负数”“数据很多”），不能照抄测试数据；
普通数据的 ordinary 填 true，situation 写“普通数据”。"""


def save_problem_label(s: Session, server_id: int, problem_id: int, brief: str, primary: str,
                       secondary: list[str], testcases: list[dict], source: str) -> m.ProblemLabel:
    """保存一版标注：旧的不删，标为非当前；版本号加一。"""
    prev = s.scalars(select(m.ProblemLabel).where(
        m.ProblemLabel.server_id == server_id, m.ProblemLabel.problem_id == problem_id
    ).order_by(m.ProblemLabel.version.desc())).first()
    s.execute(update(m.ProblemLabel).where(
        m.ProblemLabel.server_id == server_id, m.ProblemLabel.problem_id == problem_id).values(is_current=False))
    label = m.ProblemLabel(
        server_id=server_id, problem_id=problem_id, version=(prev.version + 1) if prev else 1, is_current=True,
        brief=brief[:255], primary_skill=primary, secondary_skills=[c for c in secondary if c != primary][:4],
        source=source, taxonomy_version=config.TAXONOMY_VERSION, labeler_version=config.LABELER_VERSION,
        content_hash=problem_content_hash(s, server_id, problem_id))
    s.add(label)
    s.flush()
    for t in testcases:
        s.add(m.TestcaseLabel(problem_label_id=label.id, server_id=server_id, testcase_id=int(t["id"]),
                              skill_code=t["skill"], situation=str(t.get("situation", ""))[:64],
                              is_ordinary=bool(t.get("ordinary"))))
    return label


def annotate_problem(s: Session, server_id: int, problem_id: int, force: bool = False) -> m.ProblemLabel | None:
    """标注一道题。题面和测试点没变、已经标过时直接返回。没有大模型时返回 None（留到以后标）。"""
    from .labeler import current_problem_label

    current = current_problem_label(s, server_id, problem_id)
    if current is not None and not force and current.content_hash == problem_content_hash(s, server_id, problem_id):
        return current
    if not llm.available():
        return current
    problem = s.get(m.Problem, (server_id, problem_id))
    text = s.get(m.ProblemText, (server_id, problem_id))
    if problem is None or text is None or text.extract_status != "ok":
        return current
    cases = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id
    ).order_by(m.Testcase.seq, m.Testcase.testcase_id)).all()
    solution = reference_solution(s, server_id, problem_id)
    tags = s.scalars(select(m.ProblemTag.tag).where(
        m.ProblemTag.server_id == server_id, m.ProblemTag.problem_id == problem_id)).all()
    case_ids = {c.testcase_id for c in cases}

    def validate(d: dict):
        primary = str(d.get("primary", ""))
        if not is_skill_code(primary):
            raise ValueError(f"主技能 {primary} 不在技能表里")
        secondary = [str(c) for c in (d.get("secondary") or [])][:4]
        for code in secondary:
            if not is_skill_code(code):
                raise ValueError(f"次技能 {code} 不在技能表里")
        brief = str(d.get("brief", "")).strip()
        if not brief or len(brief) > 60:
            raise ValueError("简述为空或太长")
        out = []
        for t in d.get("testcases") or []:
            tid = t.get("id")
            if tid not in case_ids:
                continue
            if not is_skill_code(str(t.get("skill", ""))):
                raise ValueError(f"测试点 {tid} 的技能不在技能表里")
            out.append({"id": tid, "skill": t["skill"], "situation": str(t.get("situation", ""))[:16],
                        "ordinary": bool(t.get("ordinary"))})
        if len(out) != len(case_ids):
            raise ValueError("每个测试点都要标")
        return brief, primary, secondary, out

    skill_table = "\n".join(f"{code} {item[0]}（{KNOWLEDGE_POINTS[code[:6]]}）" for code, item in SKILLS.items())
    case_text = "\n\n".join(
        f"测试点 {c.testcase_id}\n输入：\n{(decode_bytes(c.input_bytes, c.input_encoding)[0] or '')[:300]}\n"
        f"输出：\n{(decode_bytes(c.output_bytes, c.output_encoding)[0] or '')[:300]}" for c in cases)
    user = (f"【技能表】\n{skill_table}\n\n【题目】{problem.title}\n【XDOJ 的类别和标签】{problem.category} {' '.join(tags)}\n"
            f"【题面】\n{text.extracted_text[:3000]}\n\n【正确代码】\n{(solution or '（暂无）')[:3000]}\n\n【测试点】\n{case_text}")
    try:
        brief, primary, secondary, tcs = llm.call_json("label_problem", _SYSTEM, user, validate,
                                                       retries=1, max_tokens=1200)
    except llm.LLMUnavailable:
        return current
    return save_problem_label(s, server_id, problem_id, brief, primary, secondary, tcs, "llm")
