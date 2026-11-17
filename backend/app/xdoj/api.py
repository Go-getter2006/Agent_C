"""给智能体后端调用的两个函数。都只往 sync_triggers 写一行，不直接发请求；主循环来取。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models as m
from ..db import now


def notify_student_active(s: Session, user_id: str) -> None:
    """学生打开页面、发消息时调用：主循环会立刻查一次新提交，并优先取这个学生的详情。"""
    s.add(m.Trigger(kind="student_active", target_id=str(user_id), created_at=now()))


def request_priority(s: Session, server_id: int, submission_id: int) -> None:
    """学生点了某条提交的“问问为什么错”：把这条提交的详情提到最前面。"""
    s.add(m.Trigger(kind="submission_priority", target_id=str(submission_id), server_id=server_id,
                    created_at=now()))


def request_meta_sync(s: Session, server_id: int | None = None) -> None:
    """教师手动“立即同步”名单和作业。"""
    s.add(m.Trigger(kind="sync_meta", target_id="", server_id=server_id, created_at=now()))
