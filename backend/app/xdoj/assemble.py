"""把一条提交组装成完整的证据 JSON：题面、源码、每个测试点的输入、期望输出、实际输出。

这份 JSON 含测试点原始数据，只在后台使用（标注、诊断）。学生端的任何接口都不返回它。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import pdftext
from ..db import BEIJING
from ..taxonomy import JUDGEMENTS
from .util import decode_bytes, first_diff_line


def attempt_info(s: Session, server_id: int, sub: m.Submission) -> tuple[int, int | None]:
    """这是该学生在这道题上的第几次提交，以及上一次提交的编号。"""
    n = s.scalar(select(func.count()).select_from(m.Submission).where(
        m.Submission.server_id == server_id, m.Submission.user_id == sub.user_id,
        m.Submission.problem_id == sub.problem_id, m.Submission.submission_id <= sub.submission_id)) or 1
    prev = s.scalar(select(func.max(m.Submission.submission_id)).where(
        m.Submission.server_id == server_id, m.Submission.user_id == sub.user_id,
        m.Submission.problem_id == sub.problem_id, m.Submission.submission_id < sub.submission_id))
    return int(n), prev


def _clip(text: str | None, limit: int | None) -> tuple[str | None, bool]:
    if text is None or limit is None or len(text) <= limit:
        return text, False
    return text[:limit], True


def source_text(s: Session, server_id: int, submission_id: int) -> str | None:
    snap = s.get(m.SourceSnapshot, (server_id, submission_id))
    if snap is None:
        return None
    text, _ok = decode_bytes(snap.source_bytes, snap.encoding)
    return text


def assemble_submission(s: Session, server_id: int, submission_id: int, include_problem_text: bool = True,
                        include_all_testcases: bool = False, max_output_chars: int | None = None) -> dict:
    sub = s.get(m.Submission, (server_id, submission_id))
    if sub is None:
        raise LookupError(f"没有提交 {submission_id}")
    student = s.get(m.Student, sub.user_id)
    attempt_no, prev_id = attempt_info(s, server_id, sub)
    judge = s.scalar(select(m.Judge).where(
        m.Judge.server_id == server_id, m.Judge.submission_id == submission_id, m.Judge.is_latest.is_(True)))
    out: dict = {
        "serverId": server_id,
        "submissionId": submission_id,
        "userId": sub.user_id,
        "userName": student.name if student else "",
        "problemId": sub.problem_id,
        "contestId": sub.contest_id,
        "submitTime": sub.submit_time.astimezone(BEIJING).isoformat() if sub.submit_time else None,
        "attemptNo": attempt_no,
        "prevSubmissionId": prev_id,
        "sourceCode": source_text(s, server_id, submission_id),
        "judgeId": judge.judge_id if judge else None,
        "judgement": (judge.result_text if judge and judge.result_text else sub.judgement_text)
        or JUDGEMENTS.get(sub.judgement, ""),
        "judgementCode": sub.judgement,
        "points": judge.points if judge and judge.points is not None else sub.points,
        "compileOutput": judge.compile_output if judge else None,
        "testCases": [],
    }
    if include_problem_text:
        text = s.get(m.ProblemText, (server_id, sub.problem_id))
        out["problemText"] = pdftext.usable_text(text) or None

    testcases = {t.testcase_id: t for t in s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == sub.problem_id)).all()}

    def io_of(tid: int) -> tuple[str | None, str | None, bool]:
        t = testcases.get(tid)
        if t is None:
            return None, None, False
        text_in, ok_in = decode_bytes(t.input_bytes, t.input_encoding)
        text_out, ok_out = decode_bytes(t.output_bytes, t.output_encoding)
        return text_in, text_out, not (ok_in and ok_out)

    runs = []
    if judge is not None:
        runs = s.scalars(select(m.JudgeRun).where(
            m.JudgeRun.server_id == server_id, m.JudgeRun.judge_id == judge.judge_id)).all()
        order = {tid: (t.seq or 10**6, tid) for tid, t in testcases.items()}
        runs = sorted(runs, key=lambda r: (order.get(r.testcase_id, (10**6, r.testcase_id)), r.run_id))
    for r in runs:
        std_in, std_out, as_b64 = io_of(r.testcase_id)
        ro = s.get(m.RunOutput, (server_id, r.run_id))
        program_output = None
        if ro is not None:  # None 表示没取（通过了或没运行）；"" 表示程序确实什么都没输出
            program_output, ok = decode_bytes(ro.program_output_bytes, ro.encoding)
            as_b64 = as_b64 or not ok
        item = {
            "testcaseId": r.testcase_id, "runId": r.run_id, "result": r.result_text, "resultCode": r.result,
            "passed": bool(r.passed), "runTimeMs": r.run_time_ms, "memoryKb": r.memory_kb,
            "standardInput": std_in, "standardOutput": std_out, "programOutput": program_output,
            "outputTruncated": bool(ro.truncated) if ro is not None else False,
        }
        if as_b64:
            item["base64"] = True
        if max_output_chars is not None:
            if program_output is not None and std_out is not None:
                item["firstDiffLine"] = first_diff_line(std_out, program_output)
            for key in ("standardInput", "standardOutput", "programOutput"):
                item[key], clipped = _clip(item[key], max_output_chars)
                if clipped:
                    item["outputTruncated"] = True
        out["testCases"].append(item)

    if not runs and include_all_testcases:
        for tid, t in sorted(testcases.items(), key=lambda kv: (kv[1].seq or 10**6, kv[0])):
            std_in, std_out, _ = io_of(tid)
            out["testCases"].append({
                "testcaseId": tid, "runId": None, "result": None, "resultCode": None, "passed": None,
                "runTimeMs": None, "memoryKb": None, "standardInput": std_in, "standardOutput": std_out,
                "programOutput": None, "outputTruncated": False})
    return out
