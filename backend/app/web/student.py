"""学生端接口：作业、题目、提交、对话、小题、差异、学情。

这里返回的就是学生能看到的全部。测试点只有“过没过”，没有输入、输出、编号和说明；
对话步骤（chat_steps）、内部编码、概率数值都不返回。

只看这次登录的课程（user.course）：C 语言里看不到数据结构的任何东西，反过来也一样。
"""
from __future__ import annotations

import difflib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import accounts, catalog, changes, db, llm, pdftext, progress, quiz, review
from .. import models as m
from ..chat import workflow
from ..taxonomy import UI_EVENTS, course_of
from ..xdoj import api as sync_api
from ..xdoj import store
from ..xdoj.assemble import source_text
from .deps import get_session, student

router = APIRouter(prefix="/api", tags=["student"])
NOT_FOUND = "没有这道题，或者它不在你的作业里。"


# ------------------------------------------------------------ 小工具
def _problem(s: Session, user: m.User, key: str) -> catalog.ProblemInfo:
    parsed = catalog.parse_key(key, "p")
    if parsed is None or not catalog.is_visible(s, user.student_id, *parsed, course=user.course):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    info = catalog.problem_info(s, *parsed)
    if info is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return info


def _thread(s: Session, user: m.User, key: str) -> tuple[int, int] | None:
    if key == "general":
        return None
    info = _problem(s, user, key)
    return info.server_id, info.problem_id


def _own_submission(s: Session, user: m.User, key: str) -> m.Submission:
    parsed = catalog.parse_key(key, "s")
    sub = s.get(m.Submission, parsed) if parsed else None
    if sub is None or sub.user_id != user.student_id or \
            not catalog.is_visible(s, user.student_id, sub.server_id, sub.problem_id, course=user.course):
        raise HTTPException(status_code=404, detail="没有这条提交。")
    return sub


def _message_view(s: Session, msg: m.ChatMessage, with_actions: bool) -> dict:
    content = msg.content or {}
    out = {"id": msg.id, "role": msg.role, "kind": msg.kind, "time": msg.created_at.isoformat(),
           "blocks": content.get("blocks", []), "actions": content.get("actions", []) if with_actions else []}
    if msg.kind == "quiz" and msg.quiz_attempt_id:
        attempt = s.get(m.QuizAttempt, msg.quiz_attempt_id)
        if attempt is not None:
            out["quiz"] = quiz.view(s, attempt)
    return out


def _thread_view(s: Session, user: m.User, thread: tuple[int, int] | None) -> list[dict]:
    msgs = workflow.thread_messages(s, user.student_id, thread, user.course)
    rnd = workflow.open_round(s, user.student_id, user.course)
    out = []
    for i, msg in enumerate(msgs):
        # 求助按钮只出现在智能体最新一条回复下面，而且这个回合还没结束
        live = (i == len(msgs) - 1 and msg.role == "agent" and msg.kind == "text"
                and rnd is not None and msg.round_id == rnd.id)
        out.append(_message_view(s, msg, live))
    return out


def _homework_view(s: Session, user: m.User, c: m.Contest) -> dict:
    pids = [pid for pid in catalog.contest_problem_ids(s, c.server_id, c.contest_id)]
    passed, last = 0, None
    for pid in pids:
        subs = catalog.user_submissions(s, user.student_id, c.server_id, pid)
        if any(x.judgement == "AC" for x in subs):
            passed += 1
        if subs and subs[-1].submit_time and (last is None or subs[-1].submit_time > last):
            last = subs[-1].submit_time
    now = db.now()
    return {"key": catalog.homework_key(c.server_id, c.contest_id), "title": c.title, "passed": passed,
            "total": len(pids), "endTime": c.end_time.isoformat() if c.end_time else None,
            "ended": bool(c.end_time and c.end_time < now),
            "lastSubmit": last.isoformat() if last else None}


def _sorted_contests(s: Session, user: m.User) -> list[m.Contest]:
    now = db.now()
    far = now.replace(year=now.year + 50)
    # 按截止时间排序；已过截止的放在后面
    return sorted(catalog.visible_contests(s, user.student_id, user.course),
                  key=lambda c: (bool(c.end_time and c.end_time < now), c.end_time or far, c.contest_id))


def _problem_row(s: Session, user: m.User, server_id: int, problem_id: int) -> dict | None:
    info = catalog.problem_info(s, server_id, problem_id)
    if info is None:
        return None
    views = catalog.submissions_view(s, user.student_id, server_id, problem_id)
    return {"key": info.key, "title": info.title, "brief": info.brief, "attempts": len(views),
            "passed": any(v["result"] == "AC" for v in views), "last": views[-1] if views else None}


# ------------------------------------------------------------ 作业和题目
@router.get("/homeworks")
def homeworks(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    sync_api.notify_student_active(s, user.student_id)
    return {"homeworks": [_homework_view(s, user, c) for c in _sorted_contests(s, user)]}


@router.get("/homeworks/{key}")
def homework_detail(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    parsed = catalog.parse_key(key, "h")
    contest = next((c for c in catalog.visible_contests(s, user.student_id, user.course)
                    if parsed == (c.server_id, c.contest_id)), None)
    if contest is None:
        raise HTTPException(status_code=404, detail="没有这个作业，或者它没有布置给你的班级。")
    rows = [_problem_row(s, user, contest.server_id, pid)
            for pid in catalog.contest_problem_ids(s, contest.server_id, contest.contest_id)]
    return {"homework": _homework_view(s, user, contest), "problems": [r for r in rows if r]}


@router.get("/homeworks/{key}/submissions")
def homework_submissions(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """提交页：这个作业里每道题的全部提交，新的在前。"""
    detail = homework_detail(key, user, s)
    out = []
    for row in detail["problems"]:
        server_id, problem_id = catalog.parse_key(row["key"], "p")
        views = catalog.submissions_view(s, user.student_id, server_id, problem_id)
        out.append({"key": row["key"], "title": row["title"], "brief": row["brief"], "passed": row["passed"],
                    "submissions": views[::-1],
                    "changes": changes.problem_changes(s, user.student_id, server_id, problem_id) if views else {}})
    return {"homework": detail["homework"], "problems": out}


@router.get("/problems/{key}")
def problem_detail(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    info = _problem(s, user, key)
    views = catalog.submissions_view(s, user.student_id, info.server_id, info.problem_id)
    contest = catalog.visible_problems(s, user.student_id, user.course).get((info.server_id, info.problem_id))
    text = s.get(m.ProblemText, (info.server_id, info.problem_id))
    return {"key": info.key, "title": info.title, "brief": info.brief,
            "homeworkKey": catalog.homework_key(*contest) if contest else None,
            "statement": pdftext.usable_text(text)[:6000],
            "submissions": views[::-1],
            "changes": changes.problem_changes(s, user.student_id, info.server_id, info.problem_id),
            "knowledge": progress.problem_knowledge(s, user.student_id, info.server_id, info.problem_id)}


# ------------------------------------------------------------ 提交、源码、差异
@router.get("/submissions/{key}/source")
def submission_source(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """学生自己这次提交的源码。"""
    sub = _own_submission(s, user, key)
    text = source_text(s, sub.server_id, sub.submission_id)
    if text is None:
        sync_api.request_priority(s, sub.server_id, sub.submission_id)
        raise HTTPException(status_code=409, detail="这次提交的代码还在取，稍等几秒再看。")
    return {"key": key, "lines": text.replace("\r\n", "\n").rstrip("\n").split("\n")}


def diff_rows(a: list[str], b: list[str]) -> list[dict]:
    """并排的差异：same 相同；change 改动；del 删掉的行（只在左边）；add 新加的行（只在右边）。"""
    rows: list[dict] = []
    matcher = difflib.SequenceMatcher(None, [x.rstrip() for x in a], [x.rstrip() for x in b], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"type": "same", "left": {"n": i1 + k + 1, "text": a[i1 + k]},
                             "right": {"n": j1 + k + 1, "text": b[j1 + k]}})
            continue
        left, right = list(range(i1, i2)), list(range(j1, j2))
        for k in range(max(len(left), len(right))):
            li = left[k] if k < len(left) else None
            ri = right[k] if k < len(right) else None
            rows.append({
                "type": "change" if li is not None and ri is not None else ("del" if li is not None else "add"),
                "left": {"n": li + 1, "text": a[li]} if li is not None else None,
                "right": {"n": ri + 1, "text": b[ri]} if ri is not None else None})
    return rows


@router.get("/problems/{key}/diff")
def problem_diff(key: str, a: str, b: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """同一道题的两次提交改了什么。只能比自己的提交。"""
    info = _problem(s, user, key)
    sub_a, sub_b = _own_submission(s, user, a), _own_submission(s, user, b)
    if {(sub_a.server_id, sub_a.problem_id), (sub_b.server_id, sub_b.problem_id)} != {(info.server_id, info.problem_id)}:
        raise HTTPException(status_code=400, detail="这两次提交不是同一道题的。")
    views = {v["key"]: v for v in catalog.submissions_view(s, user.student_id, info.server_id, info.problem_id)}
    text_a = source_text(s, sub_a.server_id, sub_a.submission_id)
    text_b = source_text(s, sub_b.server_id, sub_b.submission_id)
    if text_a is None or text_b is None:
        raise HTTPException(status_code=409, detail="代码还在取，稍等几秒再看。")
    split = lambda t: t.replace("\r\n", "\n").expandtabs(4).rstrip("\n").split("\n")  # noqa: E731
    rows = diff_rows(split(text_a), split(text_b))
    return {"problem": {"key": info.key, "title": info.title, "brief": info.brief},
            "from": views[a], "to": views[b], "rows": rows,
            "changed": sum(1 for r in rows if r["type"] != "same")}


# ------------------------------------------------------------ 对话
class SendBody(BaseModel):
    text: str = Field(default="", max_length=8000)
    action: str | None = None
    submissionKey: str | None = None


@router.get("/threads/{key}")
def thread(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    th = _thread(s, user, key)
    sync_api.notify_student_active(s, user.student_id)
    workflow.refresh_rounds(s, user.student_id)
    return {"messages": _thread_view(s, user, th)}


@router.post("/threads/{key}/messages")
def send(key: str, body: SendBody, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    th = _thread(s, user, key)
    if body.submissionKey:
        _own_submission(s, user, body.submissionKey)
    if not body.action and not body.text.strip():
        raise HTTPException(status_code=400, detail="消息是空的。")
    try:
        workflow.handle(s, user.student_id, th, body.text, body.action, body.submissionKey, course=user.course)
    except llm.LLMUnavailable:
        raise HTTPException(status_code=502, detail="这条没发出去，再发一次试试。")
    if body.action in ("think", "hint", "tell"):
        s.add(m.UiEvent(user_id=user.student_id, kind=f"help_{body.action}", target=key, course=user.course))
    elif body.submissionKey:
        s.add(m.UiEvent(user_id=user.student_id, kind="ask_why", target=body.submissionKey, course=user.course))
    return {"messages": _thread_view(s, user, th)}


# ------------------------------------------------------------ 小题
class ConfidenceBody(BaseModel):
    value: str
    elapsedMs: int | None = None


class JudgeBody(BaseModel):
    hasBug: bool


class RunBody(BaseModel):
    code: str | None = Field(default=None, max_length=4000)
    choice: int | None = None


def _attempt(s: Session, user: m.User, attempt_id: int) -> m.QuizAttempt:
    attempt = s.get(m.QuizAttempt, attempt_id)
    if attempt is None or attempt.user_id != user.student_id or course_of(attempt.skill_code or "C") != user.course:
        raise HTTPException(status_code=404, detail="没有这道小题。")
    return attempt


@router.post("/quiz/{attempt_id}/confidence")
def quiz_confidence(attempt_id: int, body: ConfidenceBody, user: m.User = Depends(student),
                    s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.set_confidence(s, attempt, body.value, body.elapsedMs)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/judge")
def quiz_judge(attempt_id: int, body: JudgeBody, user: m.User = Depends(student),
               s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.judge(s, attempt, body.hasBug)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/run")
def quiz_run(attempt_id: int, body: RunBody, user: m.User = Depends(student),
             s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.run_fix(s, attempt, body.code, body.choice)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/skip")
def quiz_skip(attempt_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.skip(s, attempt)
    return quiz.view(s, attempt)


# ------------------------------------------------------------ 学情
@router.get("/progress")
def progress_page(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    return progress.build(s, user.student_id, user.course)


# ------------------------------------------------------------ 复习
@router.get("/review")
def review_page(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    s.add(m.UiEvent(user_id=user.student_id, kind="open_review", target="", course=user.course))
    out = review.summary(s, user.student_id, course=user.course)
    current = next((d for d in out["due"] if d["pending"]), None)
    if current is not None:
        item = s.get(m.ReviewItem, current["id"])
        attempt = s.get(m.QuizAttempt, item.attempt_id) if item and item.attempt_id else None
        if attempt is not None:
            q = s.get(m.QuizItem, attempt.item_id)
            out["current"] = {"itemId": item.id, "lead": review.lead_for(s, user.student_id, item, q),
                              "quiz": quiz.view(s, attempt)}
    return out


@router.post("/review/{item_id}/start")
def review_start(item_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    item, attempt = review.start(s, user.student_id, item_id, course=user.course)
    if item is None:
        raise HTTPException(status_code=404, detail="没有这个复习项。")
    if attempt is None:
        raise HTTPException(status_code=409, detail="这一项今天不用复习，或者今天的复习已经做够了。")
    s.add(m.UiEvent(user_id=user.student_id, kind="review_start", target=str(item_id), course=user.course))
    q = s.get(m.QuizItem, attempt.item_id)
    return {"itemId": item.id, "lead": review.lead_for(s, user.student_id, item, q), "quiz": quiz.view(s, attempt)}


# ------------------------------------------------------------ 其他
class EventBody(BaseModel):
    kind: str
    target: str = Field(default="", max_length=64)


@router.post("/events")
def ui_event(body: EventBody, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    if body.kind not in UI_EVENTS:
        raise HTTPException(status_code=400, detail="不认识的事件。")
    s.add(m.UiEvent(user_id=user.student_id, kind=body.kind, target=body.target, course=user.course))
    return {"ok": True}


@router.get("/status")
def status(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """XDOJ 连得上吗、数据是什么时候的。连不上时页面顶部显示一条细横幅。"""
    servers = {sid for sid, _cid in accounts.active_class_keys(s, user.student_id)
               if catalog.server_course(s, sid) == user.course}
    worst = {"ok": True, "dataTime": None, "demo": False}
    for sid in servers:
        source = s.scalar(select(m.DataSource).where(m.DataSource.server_id == sid, m.DataSource.enabled.is_(True)))
        if source is None:
            worst["demo"] = True  # 没有启用的数据源：演示数据，不连 XDOJ
            continue
        last_ok = store.get_time(s, sid, "last_ok_poll_at")
        failures = int(store.get_state(s, sid, "poll_failures", "0") or 0)
        stale = last_ok is None or (db.now() - last_ok).total_seconds() > 600
        if failures >= 3 or stale:
            worst["ok"] = False
            worst["dataTime"] = last_ok.isoformat() if last_ok else None
    return {"xdoj": worst, "dueReviews": len(review.due_items(s, user.student_id, course=user.course)),
            "llm": llm.available(), "course": user.course}
