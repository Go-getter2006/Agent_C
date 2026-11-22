"""判定失败后给对应的作业会话留一条消息，已读状态跨设备保存。"""
from __future__ import annotations

import datetime as dt
import difflib
import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import catalog, config, db
from .. import models as m
from ..xdoj.assemble import source_text
from .policy import Decision
from .reply import HELP_ACTIONS, template, to_blocks

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


def _log_notice(s: Session, sub: m.Submission, chosen: str, now: dt.datetime, feats: dict | None = None,
                msg: m.ChatMessage | None = None) -> None:
    """决策记录（decisions.py）：这次没过的提交发没发主动消息、发的哪种、为什么。"""
    from .. import decisions

    feats = feats or {}
    cands = [{"option": "recurrence", "applies": bool(feats.get("recurrence"))},
             {"option": "stuck", "applies": bool(feats.get("stuck"))},
             {"option": "failure", "applies": chosen.startswith(("send", "update"))}]
    decisions.record(s, sub.user_id, _course(s, sub.server_id), "notice", cands, chosen, 1.0,
                     context={**feats, "recentMinutes": cfg()["recent_minutes"], "stuckAttempts": cfg()["stuck_attempts"]},
                     ref={"submission": sub.submission_id, "server": sub.server_id, "problem": sub.problem_id,
                          "message": msg.id if msg is not None else None}, ts=now)


def _failure_blocks(s: Session, sub: m.Submission, attempt: int) -> list[dict]:
    """主动消息给可以从当前评测确认的现象与检查方向。"""
    from .context import for_submission

    ctx = for_submission(s, sub, _course(s, sub.server_id))
    head = f"你的第 {attempt} 次提交" + ("编译没有通过。" if sub.judgement == "CE" else f"没有通过（{_grid_text(s, sub)}）。")
    draft = template(ctx, Decision("R12", "H0", "甲", "diagnose"), "diagnose")
    draft.before = head + draft.before
    if sub.judgement == "CE":
        draft.after = draft.after.removeprefix("编译没有通过。")
    return to_blocks(draft, ctx)


def on_failed_submission(s: Session, sub: m.Submission, now: dt.datetime | None = None) -> m.ChatMessage | None:
    """一次没通过的提交标完错误之后调用。该说就在这道题的对话里发一条，返回这条消息。"""
    now = now or db.now()
    if sub.judgement in ("AC", "PENDING", "UNKNOWN", "") or sub.notice_eligible is False:
        return None
    if sub.notice_eligible is None and (sub.submit_time is None or
            now - sub.submit_time > dt.timedelta(minutes=cfg()["recent_minutes"])):
        return None
    s.scalar(select(m.Submission.submission_id).where(m.Submission.server_id == sub.server_id,
             m.Submission.submission_id == sub.submission_id).with_for_update())
    existing = s.scalar(select(m.ChatMessage).where(m.ChatMessage.server_id == sub.server_id,
                        m.ChatMessage.failure_submission_id == sub.submission_id))
    if existing is not None and (existing.notice_read_at is not None or
            (existing.content or {}).get("proactive", {}).get("kind") != "failure"):
        _log_notice(s, sub, "skip:already_noticed", now)
        return None
    label = _primary(s, sub)
    if existing is not None and label is None:
        _log_notice(s, sub, "skip:waiting_label", now)
        return None
    if not catalog.is_visible(s, sub.user_id, sub.server_id, sub.problem_id):
        _log_notice(s, sub, "skip:not_visible", now)
        return None
    subs = catalog.user_submissions(s, sub.user_id, sub.server_id, sub.problem_id)
    upto = [x for x in subs if x.submission_id <= sub.submission_id]
    attempt = len(upto)
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
            blocks.append({"t": "p", "text": "你觉得问题可能出在哪一部分？说说你已经检查过哪一步。"})
            kind = "stuck"
    if not kind:
        kind = "failure"
        blocks = _failure_blocks(s, sub, attempt)
    feats = {"attemptNo": attempt, "kind": kind, "recurrence": recur is not None, "stuck": kind == "stuck",
             "error": label.error_code if label is not None else "",
             "labelConfidence": round(label.confidence, 3) if label is not None else None,
             "minutesSinceSubmit": round((now - sub.submit_time).total_seconds() / 60, 1) if sub.submit_time else None}
    content = {"blocks": blocks, "actions": list(HELP_ACTIONS),
               "proactive": {"kind": kind, "submission": sub.submission_id}}
    if existing is not None:
        if kind != "failure":
            s.execute(update(m.ChatMessage).where(m.ChatMessage.id == existing.id,
                      m.ChatMessage.notice_read_at.is_(None)).values(content=content))
            _log_notice(s, sub, f"update:{kind}", now, feats, existing)
        else:
            _log_notice(s, sub, "skip:already_sent", now, feats, existing)
        return None
    msg = m.ChatMessage(user_id=sub.user_id, failure_submission_id=sub.submission_id,
                        server_id=sub.server_id, problem_id=sub.problem_id,
                        course=_course(s, sub.server_id), role="agent", kind="text", created_at=now,
                        content=content)
    s.add(msg)
    s.flush()
    _log_notice(s, sub, f"send:{kind}", now, feats, msg)
    log.debug("作业提醒已写入（%s）", kind)
    return msg
