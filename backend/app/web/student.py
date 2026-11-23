"""学生端接口：作业、题目、提交、对话、小题、差异、学情。

这里返回的就是学生能看到的全部。测试点只有“过没过”，没有输入、输出、编号和说明；
对话步骤（chat_steps）、内部编码、概率数值都不返回。

只看这次登录的课程（user.course）：C 语言里看不到数据结构的任何东西，反过来也一样。
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import accounts, catalog, changes, config, db, llm, pdftext, progress, quiz, review
from .. import models as m
from ..chat import reply, workflow
from ..chat.text import dedent, quote_range
from ..taxonomy import KNOWLEDGE_POINTS, UI_EVENTS, course_of, kp_of_skill, skill_name
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
    if key == "general" or key == "new" or key.startswith("t"):
        return None
    info = _problem(s, user, key)
    return info.server_id, info.problem_id


def _topic(s: Session, user: m.User, key: str) -> int | None:
    """知识问答的主题：t<编号>。只认自己的、这门课的。general 是以前的综合对话（没有主题）。"""
    if not key.startswith("t"):
        return None
    try:
        tid = int(key[1:])
    except ValueError:
        raise HTTPException(status_code=404, detail="没有这个问答。")
    row = s.get(m.ChatTopic, tid)
    if row is None or row.user_id != user.student_id or row.course != user.course:
        raise HTTPException(status_code=404, detail="没有这个问答。")
    return tid


def _knowledge(key: str) -> bool:
    """知识问答的线程：以前的综合对话、各个主题、还没发第一条的新问答。"""
    return key == "general" or key == "new" or key.startswith("t")


def topic_title(text: str) -> str:
    """主题名：第一个问题的第一句，去掉结尾的标点，最长 16 个汉字宽（字母、数字、空格算半个）。"""
    first = re.split(r"[\n。？?！!]", (text or "").strip(), maxsplit=1)[0].strip()
    first = re.sub(r"\s+", " ", first).strip("，,：: ")
    width, out = 0, ""
    for ch in first:
        width += 1 if ord(ch) < 0x2E80 else 2
        if width > 32:
            return out.rstrip() + "…"
        out += ch
    return out or "新的问答"


def _own_submission(s: Session, user: m.User, key: str) -> m.Submission:
    parsed = catalog.parse_key(key, "s")
    sub = s.get(m.Submission, parsed) if parsed else None
    if sub is None or sub.user_id != user.student_id or \
            not catalog.is_visible(s, user.student_id, sub.server_id, sub.problem_id, course=user.course):
        raise HTTPException(status_code=404, detail="没有这条提交。")
    return sub


def _contextual_blocks(s: Session, msg: m.ChatMessage) -> list[dict]:
    """兼容旧对话中的孤立引用：只用该回复当时的确切源码，不拿最新提交补旧代码。"""
    blocks = (msg.content or {}).get("blocks", [])
    small = [b for b in blocks if b.get("t") == "code" and 0 < len(b.get("lines", [])) < 3
             and re.fullmatch(r"(?:你的第 \d+ 次提交|你贴的代码)，第 \d+(?: 到 \d+)? 行", b.get("caption", ""))]
    if msg.role != "agent" or not msg.round_id or not small:
        return blocks
    step = s.scalars(select(m.ChatStep).where(
        m.ChatStep.round_id == msg.round_id, m.ChatStep.user_id == msg.user_id,
        m.ChatStep.course_id == msg.course, m.ChatStep.created_at < msg.created_at)
        .order_by(m.ChatStep.created_at.desc(), m.ChatStep.id.desc()).limit(1)).first()
    if step is None or (step.reply or {}).get("blocks") != blocks:
        return blocks  # 没有确切来源，不猜测、不改写历史记录
    source = ""
    kind = (step.evidence_card or {}).get("sourceKind")
    if kind == "submission" and step.server_id and step.submission_id:
        sub = s.get(m.Submission, (step.server_id, step.submission_id))
        server = s.get(m.Server, step.server_id)
        if sub is not None and sub.user_id == msg.user_id and sub.problem_id == step.problem_id \
                and server is not None and server.course == msg.course:
            source = source_text(s, sub.server_id, sub.submission_id) or ""
    elif kind == "pasted":
        original = s.get(m.ChatMessage, step.message_id)
        if original is not None and original.role == "student" and original.user_id == msg.user_id and original.course == msg.course:
            source = "\n".join(line for b in (original.content or {}).get("blocks", [])
                               if b.get("t") == "code" and b.get("caption") == "我贴的代码"
                               for line in b.get("lines", []))
    if not source:
        return blocks
    lines = source.replace("\r\n", "\n").split("\n")
    out = []
    for block in blocks:
        if block not in small:
            out.append(block)
            continue
        a = block.get("start", 0)
        if not isinstance(a, int):
            out.append(block)
            continue
        b = a + len(block["lines"]) - 1
        # 再核对原引用的文字，避免关联记录损坏后展示另一版源码。
        if not 1 <= a <= b <= len(lines) or dedent(lines[a - 1:b]) != dedent(block["lines"]):
            out.append(block)
            continue
        start, end = quote_range(lines, a, b)
        where = f"第 {start} 行" if start == end else f"第 {start} 到 {end} 行"
        caption = re.sub(r"第 \d+(?: 到 \d+)? 行$", where, block["caption"])
        out.append({**block, "caption": caption, "start": start, "lines": dedent(lines[start - 1:end])})
    return out


def _message_view(s: Session, msg: m.ChatMessage, with_actions: bool) -> dict:
    content = msg.content or {}
    blocks = _contextual_blocks(s, msg)
    if msg.role == "agent" and msg.problem_id is None:
        blocks = reply.restore_knowledge_code(blocks)
    out = {"id": msg.id, "role": msg.role, "kind": msg.kind, "time": msg.created_at.isoformat(),
           "blocks": blocks, "actions": content.get("actions", []) if with_actions else []}
    if msg.failure_submission_id is not None and msg.notice_read_at is None:
        out["unreadNotice"] = True
    if msg.kind == "quiz" and msg.quiz_attempt_id:
        attempt = s.get(m.QuizAttempt, msg.quiz_attempt_id)
        if attempt is not None:
            out["quiz"] = quiz.view(s, attempt)
    return out


def _thread_view(s: Session, user: m.User, thread: tuple[int, int] | None, topic: int | None = None) -> list[dict]:
    msgs = workflow.thread_messages(s, user.student_id, thread, user.course, topic)
    quoted_attempts = {}
    if msgs and thread is not None:
        # 用提问当时记录的次数。默认绑定到最新提交的消息没有引用标记。
        quoted_attempts = dict(s.execute(
            select(m.ChatStep.message_id, m.ChatStep.evidence_card["attemptNo"].as_integer())
            .join(m.ChatMessage, m.ChatMessage.id == m.ChatStep.message_id)
            .where(*workflow.thread_filter(user.student_id, thread, user.course, topic),
                   m.ChatMessage.role == "student", m.ChatStep.user_id == user.student_id,
                   m.ChatStep.bind_by == "ask_why", m.ChatStep.submission_id.is_not(None))
        ).all())
    rnd = workflow.open_round(s, user.student_id, user.course) if msgs else None
    now = db.now()
    if rnd and now - rnd.last_message_at > dt.timedelta(minutes=config.CHAT["round_timeout_minutes"]):
        rnd = None
    if rnd and thread and msgs[-1].round_id == rnd.id:
        passed = s.scalar(select(m.Submission.submission_id).where(
            m.Submission.server_id == thread[0], m.Submission.problem_id == thread[1],
            m.Submission.user_id == user.student_id, m.Submission.judgement == "AC",
            m.Submission.submit_time.between(rnd.started_at, now)).limit(1))
        if passed is not None:
            rnd = None
    out = []
    for i, msg in enumerate(msgs):
        # 求助按钮只出现在智能体最新一条回复下面，而且这个回合还没结束
        live = (i == len(msgs) - 1 and msg.role == "agent" and msg.kind == "text"
                and rnd is not None and msg.round_id == rnd.id)
        view = _message_view(s, msg, live)
        attempt_no = quoted_attempts.get(msg.id)
        if isinstance(attempt_no, int) and attempt_no > 0:
            view["quotedAttemptNo"] = attempt_no
        out.append(view)
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
    subs = catalog.user_submissions(s, user.student_id, server_id, problem_id)
    return {"key": info.key, "title": info.title, "brief": info.brief, "attempts": len(subs),
            "passed": any(sub.judgement == "AC" for sub in subs),
            "last": catalog.submission_view(s, subs[-1], len(subs)) if subs else None}


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
    parsed = catalog.parse_key(key, "p")
    contest = catalog.problem_contest(s, user.student_id, *parsed, user.course) if parsed else None
    info = catalog.problem_info(s, *parsed) if contest is not None else None
    if info is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    views = catalog.submissions_view(s, user.student_id, info.server_id, info.problem_id)
    text = s.get(m.ProblemText, (info.server_id, info.problem_id))
    figures = s.execute(select(m.ProblemFigure.seq, m.ProblemFigure.width, m.ProblemFigure.height).where(
        m.ProblemFigure.server_id == info.server_id, m.ProblemFigure.problem_id == info.problem_id)
        .order_by(m.ProblemFigure.seq)).all()
    statement = pdftext.usable_text(text)
    # 文字整理不出来（乱码、还没整理）时，直接把 PDF 的页面显示出来（渲染成图），不用学生点开 PDF
    pages = pdftext.sidebar_page_count(text, statement, {n for n, _, _ in figures})
    return {"key": info.key, "title": info.title, "brief": info.brief,
            "homeworkKey": catalog.homework_key(info.server_id, contest),
            "statement": statement,
            # 示意图（题面文字里 [图N] 的位置）；文字整理不出来时是题面 PDF 的各页。学生在 XDOJ 上本来就能看到题面
            "figures": [{"n": n, "url": f"/api/problems/{info.key}/figures/{n}", "width": w, "height": h}
                        for n, w, h in figures] if statement else [],
            "pages": [f"/api/problems/{info.key}/pages/{n}" for n in range(1, pages + 1)],
            "submissions": views[::-1],
            "changes": changes.problem_changes(s, user.student_id, info.server_id, info.problem_id),
            "knowledge": progress.problem_knowledge(s, user.student_id, info.server_id, info.problem_id)}


@router.get("/problems/{key}/figures/{n}")
def problem_figure(key: str, n: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> Response:
    """题面里的第 n 张示意图（从 PDF 页面上裁下来的 PNG）。"""
    info = _problem(s, user, key)
    fig = s.get(m.ProblemFigure, (info.server_id, info.problem_id, n))
    if fig is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(fig.png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/problems/{key}/pages/{n}")
def problem_page(key: str, n: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> Response:
    """题面 PDF 的第 n 页（渲染成图、裁掉页边空白）。文字整理不出来时直接显示这个。"""
    info = _problem(s, user, key)
    png = pdftext.page_png(s.get(m.ProblemText, (info.server_id, info.problem_id)), n)
    if png is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


# ------------------------------------------------------------ 提交、源码、差异
@router.get("/problems/{key}/document")
def problem_document(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    info = _problem(s, user, key)
    doc = s.get(m.ProblemText, (info.server_id, info.problem_id))
    ready = bool(doc is not None and doc.file_bytes)
    if not ready:
        store.enqueue(s, info.server_id, "problem_assets", info.problem_id, 0)
    return {"ready": ready, "pdf": bool(ready and doc.file_bytes.startswith(b"%PDF-")),
            "url": f"/api/problems/{key}/document/file" if ready else None,
            "text": pdftext.usable_text(doc) if ready else None}


@router.get("/problems/{key}/document/file")
def problem_document_file(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)):
    info = _problem(s, user, key)
    doc = s.get(m.ProblemText, (info.server_id, info.problem_id))
    if doc is None or not doc.file_bytes:
        store.enqueue(s, info.server_id, "problem_assets", info.problem_id, 0)
        return JSONResponse(status_code=409, content={"error": "题面正在下载，请稍候。"})
    is_pdf = doc.file_bytes.startswith(b"%PDF-")
    return Response(content=doc.file_bytes, media_type="application/pdf" if is_pdf else "text/plain",
                    headers={"Content-Disposition": 'inline; filename="problem.pdf"' if is_pdf else 'inline; filename="problem.txt"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/submissions/{key}/source")
def submission_source(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """学生自己这次提交的源码。"""
    sub = _own_submission(s, user, key)
    text = source_text(s, sub.server_id, sub.submission_id)
    if text is None:
        sync_api.request_priority(s, sub.server_id, sub.submission_id)
        return JSONResponse(status_code=409, content={"error": "这次提交的代码正在下载，会自动更新。"})
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
        for sub, text in ((sub_a, text_a), (sub_b, text_b)):
            if text is None:
                sync_api.request_priority(s, sub.server_id, sub.submission_id)
        return JSONResponse(status_code=409, content={"error": "代码正在下载，请稍候再比较。"})
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
    problemKey: str | None = None   # 知识问答里选了“结合当前题”：带上这道题


@router.get("/topics")
def topics(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """知识问答的问答记录：最近的在上。以前的综合对话有内容的话，列在里面，叫“以前的问答”。"""
    rows = s.scalars(select(m.ChatTopic).where(m.ChatTopic.user_id == user.student_id,
                                               m.ChatTopic.course == user.course)
                     .order_by(m.ChatTopic.last_message_at.desc(), m.ChatTopic.id.desc())).all()
    out = [{"key": f"t{r.id}", "title": r.title, "time": r.last_message_at.isoformat()} for r in rows]
    legacy = s.scalar(select(func.max(m.ChatMessage.created_at)).where(
        *workflow.thread_filter(user.student_id, None, user.course)))
    if legacy is not None:
        out.append({"key": "general", "title": "以前的问答", "time": legacy.isoformat()})
        out.sort(key=lambda x: x["time"], reverse=True)
    return {"topics": out}


@router.get("/threads/{key}")
def thread(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    th = _thread(s, user, key)
    topic = _topic(s, user, key)
    # 活跃同步由作业列表和发消息触发。读聊天记录只查库，避免跟后台同步争用写锁。
    if key == "new":
        return {"messages": []}
    return {"messages": _thread_view(s, user, th, topic)}


@router.post("/threads/{key}/messages")
def send(key: str, body: SendBody, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    th = _thread(s, user, key)
    topic = _topic(s, user, key)
    knowledge = _knowledge(key)
    if body.submissionKey:
        _own_submission(s, user, body.submissionKey)
    pin = None
    if knowledge and body.problemKey:
        info = _problem(s, user, body.problemKey)
        pin = (info.server_id, info.problem_id)
    if not body.action and not body.text.strip():
        raise HTTPException(status_code=400, detail="消息是空的。")
    if key == "new":   # 新建问答：第一条消息发出时才建主题，名字取这一问
        row = m.ChatTopic(user_id=user.student_id, course=user.course, title=topic_title(body.text))
        s.add(row)
        s.flush()
        topic, key = row.id, f"t{row.id}"
    try:
        workflow.handle(s, user.student_id, th, body.text, body.action, body.submissionKey, course=user.course,
                        topic=topic, knowledge=knowledge, pin=pin)
    except llm.LLMUnavailable:
        raise HTTPException(status_code=502, detail="这条没发出去，再发一次试试。")
    if topic is not None:
        row = s.get(m.ChatTopic, topic)
        if row is not None:
            row.last_message_at = db.now()
    if body.action in ("think", "hint", "tell"):
        s.add(m.UiEvent(user_id=user.student_id, kind=f"help_{body.action}", target=key, course=user.course))
    elif body.submissionKey:
        s.add(m.UiEvent(user_id=user.student_id, kind="ask_why", target=body.submissionKey, course=user.course))
    if pin is not None:
        s.add(m.UiEvent(user_id=user.student_id, kind="ask_with_problem", target=body.problemKey or "", course=user.course))
    return {"messages": _thread_view(s, user, th, topic), "thread": key}


@router.post("/threads/{key}/welcome")
def welcome(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    th = _thread(s, user, key)
    topic = _topic(s, user, key)
    if key == "new":
        return {"messages": []}
    return {"messages": _thread_view(s, user, th, topic)}


@router.post("/threads/{key}/review")
def offer_review(key: str, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    _thread(s, user, key)
    if key != "general":
        return {"status": "busy"}
    return {"status": "ready" if review.ready_count(s, user.student_id, user.course) else "idle"}


class ReadNotices(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


@router.post("/threads/{key}/read")
def read_notices(key: str, body: ReadNotices, user: m.User = Depends(student), s: Session = Depends(get_session)):
    thread = _thread(s, user, key)
    if thread is None:
        raise HTTPException(status_code=404, detail="只支持作业提醒。")
    s.execute(update(m.ChatMessage).where(*workflow.thread_filter(user.student_id, thread, user.course),
              m.ChatMessage.id.in_(body.ids), m.ChatMessage.failure_submission_id.is_not(None),
              m.ChatMessage.notice_read_at.is_(None)).values(notice_read_at=db.now()))
    newest = s.scalar(select(func.max(m.ChatMessage.id)).where(*workflow.thread_filter(user.student_id, thread, user.course),
                      m.ChatMessage.id.in_(body.ids), m.ChatMessage.failure_submission_id.is_not(None)))
    if newest is not None:   # 看到了这道题最新的提醒：更早的提醒也算看过（没往上翻到的旧提醒不再一直挂着红点）
        s.execute(update(m.ChatMessage).where(*workflow.thread_filter(user.student_id, thread, user.course),
                  m.ChatMessage.id < newest, m.ChatMessage.failure_submission_id.is_not(None),
                  m.ChatMessage.notice_read_at.is_(None)).values(notice_read_at=db.now()))
    return {"ok": True}


def _unread(s: Session, user: m.User) -> dict:
    msg, cp, cc, en = m.ChatMessage, m.ContestProblem, m.ContestClass, m.Enrollment
    rows = s.execute(select(msg.problem_id, msg.server_id, cp.contest_id, func.max(msg.id)).select_from(msg)
        .join(cp, (cp.server_id == msg.server_id) & (cp.problem_id == msg.problem_id))
        .join(cc, (cc.server_id == cp.server_id) & (cc.contest_id == cp.contest_id))
        .join(en, (en.server_id == cc.server_id) & (en.class_id == cc.class_id))
        .where(msg.user_id == user.student_id, msg.course == user.course, en.user_id == user.student_id,
               en.status == "active", msg.failure_submission_id.is_not(None), msg.notice_read_at.is_(None))
        .group_by(msg.problem_id, msg.server_id, cp.contest_id))
    problems, homeworks = {}, set()
    for pid, sid, cid, latest in rows:
        problems[catalog.problem_key(sid, pid)] = latest
        homeworks.add(catalog.homework_key(sid, cid))
    return {"problems": problems, "homeworks": sorted(homeworks)}


# ------------------------------------------------------------ 小题
class ConfidenceBody(BaseModel):
    value: str
    elapsedMs: int | None = None


class ChallengeBody(BaseModel):
    keep: bool


class JudgeBody(BaseModel):
    hasBug: bool
    confidence: str | None = None   # 独立作答：把握可选，放在确认判断旁边
    elapsedMs: int | None = None


class LocateBody(BaseModel):
    lines: list[int] = Field(default_factory=list, max_length=40)


class CodeBody(BaseModel):
    code: str = Field(default="", max_length=4000)


class RunBody(BaseModel):
    code: str | None = Field(default=None, max_length=4000)
    choice: int | None = None


def _attempt(s: Session, user: m.User, attempt_id: int) -> m.QuizAttempt:
    attempt = s.scalar(select(m.QuizAttempt).where(m.QuizAttempt.id == attempt_id,
                       m.QuizAttempt.user_id == user.student_id).with_for_update())
    if attempt is None or attempt.user_id != user.student_id or course_of(attempt.skill_code or "C") != user.course:
        raise HTTPException(status_code=404, detail="没有这道小题。")
    return attempt


class ReviewQuestion(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    requestId: str = Field(min_length=8, max_length=80)


def _finished_review(s: Session, user: m.User, attempt_id: int) -> m.QuizAttempt:
    a = _attempt(s, user, attempt_id)
    if a.reason != "review" or a.state != quiz.DONE or a.skipped:
        raise HTTPException(status_code=409, detail="完成这道复习题后可以提问。")
    return a


@router.get("/review/{attempt_id}/discussion")
def review_discussion(attempt_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    return {"turns": _finished_review(s, user, attempt_id).discussion or []}


@router.post("/review/{attempt_id}/discussion")
def ask_review(attempt_id: int, body: ReviewQuestion, user: m.User = Depends(student),
               s: Session = Depends(get_session)) -> dict:
    a = _finished_review(s, user, attempt_id)
    turns = a.discussion or []
    if any(turn["id"] == body.requestId for turn in turns):
        return {"turns": turns}
    question = body.text.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请先写下问题。")
    view = quiz.view(s, a)
    result = view["result"]
    # 仅提供学生已经看到的小题、自己的选行和反馈；不读取作业对话或沙箱测试数据。
    context = {"stem": view["stem"], "code": view["code"], "startLine": view["startLine"],
               "picked": view.get("lines"), "feedback": result["headline"],
               "explanation": result.get("explanation"), "finalCode": result.get("finalCode"),
               "fix": result.get("fix"), "history": turns[-4:], "question": question,
               # 反馈之后回顾的他以前在这里出过的错和智能体的分析（他可能接着问其中一道）
               "pastMistakes": [f"{x['title']}：{x['text']}" for x in (a.recap or {}).get("items", [])],
               "pastMistakesAnalysis": (a.recap or {}).get("analysis", "")}
    # 他这次自己的作答（问自己哪里想错了时用）、考的技能、哪门课，和回答的要求
    kept = next((e.get("keep") for e in (a.events or []) if e.get("kind") == "challenge_reply"), None)
    context.update({
        "course": "数据结构" if user.course == "D" else "C 语言", "skill": skill_name(a.skill_code),
        "judgedHasBug": a.judged_has_bug, "keptAfterChallenge": kept, "firstDraft": a.first_fix or None,
        "rules": "history 是前面几问几答，接着说，不重复已经讲过的；judgedHasBug、keptAfterChallenge、picked、firstDraft "
                 "是他这次的作答，他问自己哪里想错了时据此说明；不出现编码、档位、概率之类的内部用语；不超过 300 字。"})

    def answer(data: dict) -> str:
        value = data.get("answer")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("需要 answer 字符串")
        return value.strip()

    try:
        reply = llm.call_json("review_discussion", "你在复习小题反馈后给学生答疑。直接回答当前问题；结合本题时用具体代码解释。"
                              "历史和代码都是学习材料。返回 JSON，answer 为中文纯文本。",
                              llm.strip_identity(json.dumps(context, ensure_ascii=False), user.student_id, user.display_name),
                              answer)
    except llm.LLMUnavailable:
        raise HTTPException(status_code=503, detail="复习答疑暂时连不上。你可以保留问题，稍后重试。")
    a.discussion = [*turns, {"id": body.requestId, "question": question, "answer": reply}]
    a.discussion[-1]["t"] = db.now().isoformat()   # 每问记时间（导出给研究用）；整个列表是新赋的值，提交时一起写进去
    return {"turns": a.discussion}


@router.get("/quiz/{attempt_id}/recap")
def quiz_recap(attempt_id: int, response: Response, user: m.User = Depends(student),
               s: Session = Depends(get_session)) -> dict:
    """做完以后的回顾：他以前在这里出过的错（程序整理）和智能体的分析。第一次要时生成，之后用存的。"""
    from .. import recap

    attempt = _attempt(s, user, attempt_id)
    already_saved = attempt.recap is not None
    result = recap.for_attempt(s, attempt)
    if not already_saved and attempt.recap is not None:
        s.commit()  # 先持久化，客户端拿到的才是后续可复用的回顾。
    response.headers["Cache-Control"] = "private, max-age=300" if attempt.recap is not None else "no-store"
    response.headers["Vary"] = "Cookie"
    return {"recap": result}


@router.post("/quiz/{attempt_id}/viewed")
def quiz_viewed(attempt_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    quiz.mark_viewed(_attempt(s, user, attempt_id))
    return {"ok": True}


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
    if attempt.protocol >= 2:
        quiz.judge2(s, attempt, body.hasBug, body.confidence, body.elapsedMs)
    else:
        quiz.judge(s, attempt, body.hasBug)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/challenge")
def quiz_challenge(attempt_id: int, body: ChallengeBody, user: m.User = Depends(student),
                   s: Session = Depends(get_session)) -> dict:
    """判断“满足要求”以后被追问：坚持（keep）或改判。"""
    attempt = _attempt(s, user, attempt_id)
    quiz.challenge_reply(s, attempt, body.keep)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/locate")
def quiz_locate(attempt_id: int, body: LocateBody, user: m.User = Depends(student),
                s: Session = Depends(get_session)) -> dict:
    """独立作答第二步：选位置（判断有问题）或依据（判断满足要求）。确认以后锁定。"""
    attempt = _attempt(s, user, attempt_id)
    problem = quiz.locate(s, attempt, body.lines)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/sample")
def quiz_sample(attempt_id: int, body: CodeBody, user: m.User = Depends(student),
                s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.run_sample(s, attempt, body.code)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/hint")
def quiz_hint(attempt_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.hint(s, attempt)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/submit")
def quiz_submit(attempt_id: int, body: CodeBody, user: m.User = Depends(student),
                s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.submit_fix(s, attempt, body.code)
    return quiz.view(s, attempt)


@router.post("/quiz/{attempt_id}/run")
def quiz_run(attempt_id: int, body: RunBody, user: m.User = Depends(student),
             s: Session = Depends(get_session)) -> dict:
    attempt = _attempt(s, user, attempt_id)
    quiz.run_fix(s, attempt, body.code, body.choice)
    return quiz.view(s, attempt)


# ------------------------------------------------------------ 学情
@router.get("/progress")
def progress_page(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    return progress.build(s, user.student_id, user.course)


# ------------------------------------------------------------ 复习
@router.get("/review")
def review_page(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """复习提升：进来就是一道备好的题（后台提前备好，未就绪时页面可以继续使用）。
    中途离开的，回来接着做同一道。今天没有可做的题时，给最近一次的反馈和下一次的安排。"""
    s.add(m.UiEvent(user_id=user.student_id, kind="open_review", target="", course=user.course))
    out = review.summary(s, user.student_id, course=user.course)
    out["last"] = review.last_feedback(s, user.student_id, user.course)
    current = next((d for d in out["due"] if d["pending"]), None)
    if current is not None:
        item = s.get(m.ReviewItem, current["id"])
        attempt = s.get(m.QuizAttempt, item.attempt_id) if item and item.attempt_id else None
        if attempt is not None:
            q = s.get(m.QuizItem, attempt.item_id)
            out["current"] = {"itemId": item.id, "lead": review.lead_for(s, user.student_id, item, q),
                              "quiz": quiz.view(s, attempt)}
    return out


@router.get("/review/attempts")
def review_attempts(user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """复习提升左边的列表：做完的复习题（最近的在上），每道是一段对话（题、反馈、之后的提问）。"""
    rows = s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user.student_id, m.QuizAttempt.reason == "review", m.QuizAttempt.state == quiz.DONE,
        m.QuizAttempt.skipped.is_(False), m.QuizAttempt.skill_code.like(user.course + "%"))
        .order_by(m.QuizAttempt.finished_at.desc(), m.QuizAttempt.id.desc()).limit(60)).all()
    return {"attempts": [{"attemptId": a.id, "title": KNOWLEDGE_POINTS.get(kp_of_skill(a.skill_code), "复习题"),
                          "time": (a.finished_at or a.created_at).isoformat(), "right": bool(a.correct),
                          "asked": len(a.discussion or [])} for a in rows]}


@router.get("/review/attempts/{attempt_id}")
def review_attempt(attempt_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """一道做完的复习题：题和反馈（和当时看到的一样），之后可以接着就这道题提问。"""
    a = _finished_review(s, user, attempt_id)
    view = quiz.view(s, a)
    if a.recap is not None:
        view["recap"] = a.recap or None
    view["turns"] = a.discussion or []   # 之后的一问一答也一起给：右边一次画全，不先画上面再跳到底
    return {"attemptId": a.id, "title": KNOWLEDGE_POINTS.get(kp_of_skill(a.skill_code), "复习题"),
            "time": (a.finished_at or a.created_at).isoformat(), "quiz": view}


@router.post("/review/{item_id}/request")
def review_request(item_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    """复习安排里点了“去复习”：马上给这一项出一道题（不等到期、不受每天两道的限制，一天有上限）。"""
    try:
        item, attempt = review.request_now(s, user.student_id, item_id, user.course)
    except review.RequestError as e:
        raise HTTPException(status_code=409, detail=str(e))
    s.add(m.UiEvent(user_id=user.student_id, kind="review_start", target=str(item_id), course=user.course))
    q = s.get(m.QuizItem, attempt.item_id)
    return {"itemId": item.id, "lead": review.lead_for(s, user.student_id, item, q), "quiz": quiz.view(s, attempt)}


@router.post("/review/{item_id}/start")
def review_start(item_id: int, user: m.User = Depends(student), s: Session = Depends(get_session)) -> dict:
    item, attempt = review.prepared_attempt(s, user.student_id, item_id, course=user.course)
    if item is None:
        raise HTTPException(status_code=404, detail="没有这个复习项。")
    if attempt is None:
        if item.id in {d.id for d in review.due_items(s, user.student_id, course=user.course)}:
            raise HTTPException(status_code=503, detail="这一项仍需复习，小题暂时没有准备好，稍后会自动重试。")
        raise HTTPException(status_code=409, detail="复习计划已经更新，请查看最新复习安排。")
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
    servers = set(s.scalars(accounts.student_class_scope(user.student_id, user.course)))
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
    return {"xdoj": worst, "unread": _unread(s, user), "dueReviews": review.ready_count(s, user.student_id, user.course),
            "llm": llm.available(), "course": user.course}
