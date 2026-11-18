"""智能体主动发消息：同步到一次没通过的提交时，不等学生开口，先在这道题的对话里说一句。

两种情况才发（第一次没过不打扰，让他自己先想）：
- 又犯了以前改正过的同类错误：把以前那道题、他当时改对后的那几行代码摘出来，让他对照；
- 卡住了：同一道题连着交了好几次还没过，而且错的还是同一处。

只对刚交的提交发（回填的旧提交不发）；他正在这道题上和智能体说话时不插嘴；每人每天有上限。
消息里只有他自己的代码和题目，不出现测试点的输入输出。
"""
from __future__ import annotations

import datetime as dt
import difflib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, config, db
from .. import models as m
from ..xdoj.assemble import source_text
from .reply import HELP_ACTIONS

log = logging.getLogger("chat.proactive")


def cfg() -> dict:
    return config.PROACTIVE


def _lines(s: Session, sub: m.Submission) -> list[str] | None:
    text = source_text(s, sub.server_id, sub.submission_id)
    return text.replace("\r\n", "\n").rstrip("\n").split("\n") if text else None


def _primary(s: Session, sub: m.Submission) -> m.SubmissionLabel | None:
    return s.scalars(select(m.SubmissionLabel).where(
        m.SubmissionLabel.server_id == sub.server_id, m.SubmissionLabel.submission_id == sub.submission_id,
        m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.role == "primary")).first()


def _course(s: Session, server_id: int) -> str:
    server = s.get(m.Server, server_id)
    return server.course if server is not None else "C"


def find_recurrence(s: Session, sub: m.Submission, label: m.SubmissionLabel) -> tuple[m.Submission, m.Submission] | None:
    """以前在别的题上犯过同一种错误、后来改对了：返回 (当时犯错的那次提交, 后来改对的那次提交)。最近的一个。"""
    course = _course(s, sub.server_id)
    rows = s.execute(
        select(m.Submission, m.SubmissionLabel)
        .join(m.SubmissionLabel, (m.SubmissionLabel.server_id == m.Submission.server_id)
              & (m.SubmissionLabel.submission_id == m.Submission.submission_id))
        .where(m.Submission.user_id == sub.user_id, m.SubmissionLabel.error_code == label.error_code,
               m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.role == "primary",
               m.Submission.submission_id < sub.submission_id)
        .order_by(m.Submission.submission_id.desc())).all()
    for bad, _lab in rows:
        if (bad.server_id, bad.problem_id) == (sub.server_id, sub.problem_id):
            continue
        if _course(s, bad.server_id) != course or not catalog.is_visible(s, sub.user_id, bad.server_id, bad.problem_id):
            continue  # 只在同一门课里找
        fixed = s.scalars(select(m.Submission).where(
            m.Submission.server_id == bad.server_id, m.Submission.user_id == sub.user_id,
            m.Submission.problem_id == bad.problem_id, m.Submission.judgement == "AC",
            m.Submission.submission_id > bad.submission_id, m.Submission.submission_id < sub.submission_id)
            .order_by(m.Submission.submission_id)).first()
        if fixed is not None:
            return bad, fixed
    return None


def fix_excerpt(before: list[str], after: list[str], hint_line: int | None = None,
                max_lines: int = 8) -> tuple[int, list[str], list[int]] | None:
    """从犯错时的代码到改对后的代码，改了哪几行：返回 (起始行号, 改对后的这几行, 改动的行号)。"""
    matcher = difflib.SequenceMatcher(a=[x.strip() for x in before], b=[x.strip() for x in after], autojunk=False)
    changed = [op for op in matcher.get_opcodes() if op[0] in ("replace", "insert") and op[4] > op[3]]
    if changed:
        if hint_line is not None:  # 优先取离标注出问题的那一行最近的改动
            changed.sort(key=lambda op: abs(op[1] + 1 - hint_line))
        _tag, _i1, _i2, j1, j2 = changed[0]
        marks = list(range(j1 + 1, j2 + 1))[:max_lines]
        a = max(1, j1 + 1 - 1)
        b = min(len(after), max(marks) + 1)
        if b - a + 1 > max_lines:
            b = a + max_lines - 1
        return a, after[a - 1:b], [x for x in marks if a <= x <= b]
    if hint_line and 1 <= hint_line <= len(after):
        a, b = max(1, hint_line - 2), min(len(after), hint_line + 2)
        return a, after[a - 1:b], [hint_line]
    return None


def _grid_text(s: Session, sub: m.Submission) -> str:
    cells = catalog.grid(s, sub)
    if not cells:
        return sub.judgement_text or ""
    ok = sum(1 for c in cells if c)
    return f"{len(cells)} 组过 {ok} 组"


def _quiet(s: Session, sub: m.Submission, now: dt.datetime) -> bool:
    """他正在这道题上和智能体说话，或者这次提交之后已经说过了：不插嘴。"""
    from .workflow import thread_filter

    last = s.scalars(select(m.ChatMessage).where(
        *thread_filter(sub.user_id, (sub.server_id, sub.problem_id)), m.ChatMessage.role == "agent")
        .order_by(m.ChatMessage.id.desc())).first()
    if last is None:
        return False
    if sub.submit_time is not None and last.created_at >= sub.submit_time:
        return True
    return now - last.created_at < dt.timedelta(minutes=cfg()["quiet_minutes"])


def _sent_today(s: Session, user_id: str, now: dt.datetime) -> int:
    local = now.astimezone(db.BEIJING)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(db.UTC)
    rows = s.scalars(select(m.ChatMessage.content).where(
        m.ChatMessage.user_id == user_id, m.ChatMessage.role == "agent", m.ChatMessage.created_at >= start)).all()
    return sum(1 for c in rows if isinstance(c, dict) and c.get("proactive"))


def on_failed_submission(s: Session, sub: m.Submission, now: dt.datetime | None = None) -> m.ChatMessage | None:
    """一次没通过的提交标完错误之后调用。该说就在这道题的对话里发一条，返回这条消息。"""
    now = now or db.now()
    at = sub.submit_time or now
    if sub.judgement in ("AC", "PENDING", "UNKNOWN") or now - at > dt.timedelta(minutes=cfg()["recent_minutes"]):
        return None
    if not catalog.is_visible(s, sub.user_id, sub.server_id, sub.problem_id):
        return None
    subs = catalog.user_submissions(s, sub.user_id, sub.server_id, sub.problem_id)
    upto = [x for x in subs if x.submission_id <= sub.submission_id]
    if any(x.judgement == "AC" for x in upto):
        return None  # 已经通过过，这是通过以后的改进
    if _quiet(s, sub, now) or _sent_today(s, sub.user_id, now) >= cfg()["daily_limit"]:
        return None

    label = _primary(s, sub)
    attempt = len(upto)
    info = catalog.problem_info(s, sub.server_id, sub.problem_id)
    blocks: list[dict] = []
    kind = ""
    recur = find_recurrence(s, sub, label) if label is not None and label.confidence >= config.CHAT["low_confidence"] else None
    if recur is not None:
        bad, fixed = recur
        old = catalog.problem_info(s, bad.server_id, bad.problem_id)
        bad_label = _primary(s, bad)
        before, after = _lines(s, bad), _lines(s, fixed)
        excerpt = fix_excerpt(before, after, bad_label.line if bad_label else None) if before and after else None
        where = f"「{old.title}」（{old.brief}）" if old and old.brief else f"「{old.title if old else '以前那道题'}」"
        blocks.append({"t": "p", "text": f"这次没过的地方，和你以前在{where}里遇到过的问题很像。"})
        if excerpt is not None:
            start, lines, marks = excerpt
            end = start + len(lines) - 1
            rng = f"第 {start} 行" if start == end else f"第 {start} 到 {end} 行"
            blocks.append({"t": "p", "text": "当时你是这样改对的："})
            blocks.append({"t": "code", "caption": f"你在「{old.title if old else ''}」里改对后的代码，{rng}",
                           "start": start, "lines": lines, "mark": marks})
        blocks.append({"t": "p", "text": "对照一下，你这次的代码里，哪一处是同样的问题？"})
        kind = "recurrence"
    else:
        prev = upto[-2] if len(upto) >= 2 else None
        prev_label = _primary(s, prev) if prev is not None else None
        same = label is not None and prev_label is not None and label.error_code == prev_label.error_code
        if attempt >= cfg()["stuck_attempts"] and (same or attempt >= cfg()["stuck_attempts"] + 1):
            blocks.append({"t": "p", "text": f"第 {attempt} 次提交还是没过（{_grid_text(s, sub)}）。"
                                             f"{'问题好像还在同一个地方。' if same else ''}要不要一起看看？"})
            blocks.append({"t": "p", "text": "你觉得问题可能出在哪一部分？说说你的想法，或者点下面的按钮。"})
            kind = "stuck"
    if not kind:
        return None

    from .workflow import _round_for

    thread = (sub.server_id, sub.problem_id)
    rnd = _round_for(s, sub.user_id, thread, now, _course(s, sub.server_id))
    rnd.submission_id, rnd.last_message_at = sub.submission_id, now
    if label is not None and label.skill_code:
        rnd.skill_code, rnd.error_code = label.skill_code, label.error_code
    msg = m.ChatMessage(user_id=sub.user_id, round_id=rnd.id, server_id=sub.server_id, problem_id=sub.problem_id,
                        course=_course(s, sub.server_id), role="agent", kind="text", created_at=now,
                        content={"blocks": blocks, "actions": list(HELP_ACTIONS),
                                 "proactive": {"kind": kind, "submission": sub.submission_id}})
    s.add(msg)
    s.flush()
    log.info("主动消息（%s）：%s", kind, info.title if info else sub.problem_id)
    return msg
