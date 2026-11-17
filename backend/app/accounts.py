"""账号：学生账号在同步名单时自动建，不能自己注册。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .db import now


def ensure_student_user(s: Session, user_id: str, name: str) -> m.User:
    user = s.scalar(select(m.User).where(m.User.login_name == user_id))
    if user is None:
        user = m.User(role="student", login_name=user_id, student_id=user_id, display_name=name)
        s.add(user)
    else:
        if name and user.display_name != name:
            user.display_name = name
        if user.status != "active":
            user.status = "active"
    return user


def active_class_keys(s: Session, user_id: str) -> set[tuple[int, int]]:
    """这个学生当前所在的班级：(服务器, 班级编号)。"""
    rows = s.execute(
        select(m.Enrollment.server_id, m.Enrollment.class_id)
        .where(m.Enrollment.user_id == user_id, m.Enrollment.status == "active")
    ).all()
    return {(r[0], r[1]) for r in rows}


def touch_login(s: Session, user: m.User) -> None:
    user.last_login_at = now()
