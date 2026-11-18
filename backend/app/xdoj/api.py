"""给智能体后端调用的几个函数。都只往 sync_triggers 写一行，不直接发请求；主循环来取。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
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
    """马上同步名单和作业（教师刚登录时）。"""
    s.add(m.Trigger(kind="sync_meta", target_id="", server_id=server_id, created_at=now()))


def request_meta_sync_if_stale(s: Session, server_id: int, minutes: int | None = None) -> bool:
    """发现了不认识的学生或作业时：最近 minutes 分钟内没同步过名单，就马上同步一次。返回是否请求了。"""
    from .store import get_time

    minutes = minutes if minutes is not None else config.SYNC["meta_retry_minutes"]
    cutoff = now() - dt.timedelta(minutes=minutes)
    for key in ("last_meta_at", "last_meta_try_at"):
        t = get_time(s, server_id, key)
        if t is not None and t > cutoff:
            return False
    waiting = s.scalar(select(m.Trigger.id).where(m.Trigger.kind == "sync_meta", m.Trigger.server_id == server_id,
                                                  m.Trigger.handled_at.is_(None)))
    if waiting is None:
        request_meta_sync(s, server_id)
    return True
