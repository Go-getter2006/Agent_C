"""每个接口一个函数。返回解析好的对象，只保留需要的字段。

字段名以《XDOJ 探查报告》为准；报告里没有逐一列出的字段，这里按候选名依次尝试，
第一次连真实服务器时要用 `cli xdoj-probe` 核对（见 docs/问题清单.md）。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Iterator

from .client import XdojClient
from .util import parse_time, pick, scrub, to_float, to_int


@dataclass
class Page:
    total: int
    total_pages: int
    page: int
    rows: list[dict]


def _page(body) -> Page:
    if isinstance(body, list):
        return Page(len(body), 1, 1, [r for r in body if isinstance(r, dict)])
    if not isinstance(body, dict):
        return Page(0, 0, 1, [])
    rows = pick(body, "data", "rows", "list", default=[]) or []
    return Page(
        total=to_int(pick(body, "totalCount", "records", "total"), len(rows)) or 0,
        total_pages=to_int(pick(body, "totalpages", "totalPages", "total_pages"), 1) or 1,
        page=to_int(body.get("page"), 1) or 1,
        rows=[r for r in rows if isinstance(r, dict)],
    )


def _paged(client: XdojClient, method: str, path: str, extra: dict | None = None, rows: int = 100,
           sidx: str = "id", sord: str = "asc") -> Iterator[dict]:
    """按页取完一个列表。只用于班级、名单、题目这类总量不大的元数据。"""
    page_no = 1
    while True:
        params = {"page": page_no, "rows": rows, "sidx": sidx, "sord": sord, **(extra or {})}
        body = client.post_json(path, params) if method == "POST" else client.get_json(path, params)
        page = _page(body)
        yield from page.rows
        if page_no >= page.total_pages or not page.rows:
            return
        page_no += 1


# ------------------------------------------------------------ 班级、名单
@dataclass
class ClassRow:
    class_id: int
    class_name: str
    owner_id: str


def list_classes(client: XdojClient) -> list[ClassRow]:
    out = []
    for r in _paged(client, "POST", "/class/list.do"):
        cid = to_int(pick(r, "classId", "id"))
        if cid is None:
            continue
        out.append(ClassRow(cid, str(pick(r, "className", "name", default="")), str(pick(r, "ownerId", default=""))))
    return out


@dataclass
class StudentRow:
    user_id: str
    name: str
    class_id: int


def list_students(client: XdojClient, class_id: int) -> list[StudentRow]:
    """只取学号、姓名、班级。接口返回里的密码、令牌、登录 IP 在这里就丢掉，不往外传。"""
    filters = json.dumps({"groupOp": "AND", "rules": [{"field": "class_id", "op": "eq", "data": str(class_id)}]})
    out = []
    for raw in _paged(client, "POST", "/user/listStudents.do", {"search": "true", "filters": filters}):
        uid = pick(raw, "userId")
        if uid is None or str(uid).strip() == "":
            continue
        out.append(StudentRow(str(uid).strip(), str(pick(raw, "userName", default="")).strip(),
                              to_int(pick(raw, "classId"), class_id) or class_id))
        del raw  # 原始行不保留
    return out


# ------------------------------------------------------------ 练习（作业）
@dataclass
class ContestRow:
    contest_id: int
    title: str
    start_time: dt.datetime | None
    end_time: dt.datetime | None
    type: str
    class_ids: list[int]
    problem_ids: list[int] | None  # 列表接口自带时直接用
    raw: dict = field(default_factory=dict)


def _id_list(value) -> list[int] | None:
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        v = to_int(pick(item, "id", "classId", "problemId")) if isinstance(item, dict) else to_int(item)
        if v is not None:
            out.append(v)
    return out


def list_contests(client: XdojClient) -> list[ContestRow]:
    out = []
    for r in _paged(client, "POST", "/contest/list.do"):
        cid = to_int(r.get("id"))
        if cid is None:
            continue
        out.append(ContestRow(
            contest_id=cid,
            title=str(pick(r, "title", "name", default="")),
            start_time=parse_time(r.get("startTime")),
            end_time=parse_time(r.get("endTime")),
            type=str(pick(r, "type", default="")),
            class_ids=_id_list(r.get("classes")) or [],
            problem_ids=_id_list(r.get("problems")),
            raw=scrub(r),
        ))
    return out


def contest_problem_ids(client: XdojClient, contest_id: int) -> list[int]:
    out = []
    for r in _paged(client, "GET", "/contest/problems.do", {"contestId": contest_id}):
        pid = to_int(pick(r, "problemId", "id"))
        if pid is not None:
            out.append(pid)
    return out


# ------------------------------------------------------------ 题目
@dataclass
class ProblemRow:
    problem_id: int
    title: str
    category: str
    difficulty: str
    time_limit: int | None
    memory_limit: int | None
    tags: list[str]
    raw: dict


def list_problems(client: XdojClient) -> list[ProblemRow]:
    out = []
    for r in _paged(client, "POST", "/problem/list.do"):
        pid = to_int(r.get("id"))
        if pid is None:
            continue
        tags = []
        for t in r.get("tags") or []:
            name = t.get("name") if isinstance(t, dict) else t
            if name:
                tags.append(str(name))
        out.append(ProblemRow(
            problem_id=pid, title=str(pick(r, "title", default="")).strip(),
            category=str(pick(r, "categoryName", "category", default="")),
            difficulty=str(pick(r, "difficulty", default="")),
            time_limit=to_int(r.get("timeLimit")), memory_limit=to_int(r.get("memoryLimit")),
            tags=tags, raw=scrub(r),
        ))
    return out


def problem_text_file(client: XdojClient, problem_id: int) -> tuple[bytes, str]:
    return client.get_bytes("/problem/viewTextFile.do", {"problemId": problem_id})


@dataclass
class TestcaseRow:
    __test__ = False
    testcase_id: int
    description: str
    points: float | None


def list_testcases(client: XdojClient, problem_id: int) -> list[TestcaseRow]:
    """返回里的 problemId 有时是 0，所以不读它，一律以请求里的题号为准。"""
    out = []
    for r in _paged(client, "GET", "/testcase/list.do", {"problemId": problem_id}):
        tid = to_int(r.get("id"))
        if tid is not None:
            out.append(TestcaseRow(tid, str(pick(r, "description", default="") or ""), to_float(r.get("points"))))
    return out


def testcase_input(client: XdojClient, testcase_id: int) -> bytes:
    return client.get_bytes("/testcase/downloadInputFile.do", {"id": testcase_id})[0]


def testcase_output(client: XdojClient, testcase_id: int) -> bytes:
    return client.get_bytes("/testcase/downloadOutputFile.do", {"id": testcase_id})[0]


# ------------------------------------------------------------ 提交
@dataclass
class SubmissionRow:
    submission_id: int
    user_id: str
    user_name: str
    problem_id: int
    contest_id: int | None
    class_id: int | None
    submit_time: dt.datetime | None
    language: str
    judgement_text: str
    points: float | None


def list_submissions_after(client: XdojClient, after_id: int, rows: int = 100) -> tuple[list[SubmissionRow], int]:
    """取编号大于 after_id 的一批提交，按编号升序。不用页码翻页，也不按时间筛选。"""
    filters = json.dumps({"groupOp": "AND", "rules": [{"field": "s.id", "op": "gt", "data": str(after_id)}]})
    body = client.post_json("/submission/list.do", {
        "page": 1, "rows": rows, "sidx": "s.id", "sord": "asc", "search": "true", "filters": filters})
    page = _page(body)
    out = []
    for r in page.rows:
        sid = to_int(pick(r, "id", "submissionId"))
        if sid is None:
            continue
        out.append(SubmissionRow(
            submission_id=sid,
            user_id=str(pick(r, "userId", default="")).strip(),
            user_name=str(pick(r, "userName", default="")).strip(),
            problem_id=to_int(pick(r, "problemId"), 0) or 0,
            contest_id=to_int(pick(r, "contestId")),
            class_id=to_int(pick(r, "classId")),
            submit_time=parse_time(pick(r, "submitTime", "createTime", "time")),
            language=str(pick(r, "language", "languageName", default="")),
            judgement_text=str(pick(r, "judgementStr", "judgementText", "resultStr", "result", "judgement", default="")),
            points=to_float(pick(r, "points", "score")),
        ))
    out.sort(key=lambda s: s.submission_id)
    return out, page.total


@dataclass
class JudgeRow:
    judge_id: int
    result_text: str
    points: float | None
    start: dt.datetime | None
    end: dt.datetime | None
    compile_output: str | None


def list_judges(client: XdojClient, submission_id: int) -> list[JudgeRow]:
    body = client.get_json("/submission/listJudge.do", {"submissionId": submission_id, "page": 1, "rows": 20})
    out = []
    for r in _page(body).rows:
        jid = to_int(r.get("id"))
        if jid is None:
            continue
        compile_output = pick(r, "compileOutputStr", "compileOutput")
        result_text = str(pick(r, "resultStr", "judgementStr", "result", "judgement", default=""))
        if result_text.strip() == "3":  # 已知：judgement=3 是编译错误
            result_text = "编译错误"
        out.append(JudgeRow(
            judge_id=jid, result_text=result_text, points=to_float(pick(r, "points", "score")),
            start=parse_time(pick(r, "startTime", "judgeStart")), end=parse_time(pick(r, "endTime", "judgeEnd")),
            compile_output=str(compile_output) if compile_output not in (None, "") else None,
        ))
    out.sort(key=lambda j: j.judge_id)
    return out


@dataclass
class RunRow:
    run_id: int
    testcase_id: int
    result_text: str
    run_time_ms: int | None
    memory_kb: int | None


def list_judge_runs(client: XdojClient, judge_id: int) -> list[RunRow]:
    body = client.post_json("/submission/listAllJudgeRun.do", {"judgeId": judge_id})
    out = []
    for r in _page(body).rows:
        rid, tid = to_int(r.get("id")), to_int(r.get("testcaseId"))
        if rid is None or tid is None:
            continue
        out.append(RunRow(rid, tid, str(pick(r, "resultStr", "result", default="")),
                          to_int(r.get("runTime")), to_int(r.get("memory"))))
    out.sort(key=lambda x: x.run_id)
    return out


@dataclass
class RunOutputRow:
    standard_input: str | None
    standard_output: str | None
    program_output: str | None


def run_output(client: XdojClient, run_id: int) -> RunOutputRow:
    """参数 id 是测试点运行记录的编号（runId），不是提交编号。"""
    body = client.post_json("/submission/getRunOutput.do", {"id": run_id})
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = body["data"]
    body = body if isinstance(body, dict) else {}
    return RunOutputRow(body.get("standardInput"), body.get("standardOutput"), body.get("programOutput"))


def download_source(client: XdojClient, submission_id: int) -> bytes:
    """参数 id 是提交编号。"""
    return client.get_bytes("/submission/downloadSource.do", {"id": submission_id})[0]
