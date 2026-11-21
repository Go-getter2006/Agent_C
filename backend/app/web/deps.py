"""公用依赖：数据库会话、当前用户、角色检查。"""
from __future__ import annotations

from typing import Iterator

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crypto, db
from .. import models as m

COOKIE = "pda_session"


def get_session() -> Iterator[Session]:
    s = db.new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def current_user(s: Session = Depends(get_session), token: str | None = Cookie(default=None, alias=COOKIE)) -> m.User:
    """当前登录的人。user.course 是这次登录的课程：C 语言和数据结构分开登录，各页面只看这门课。"""
    found = crypto.verify_session(token or "")
    user = s.get(m.User, found[0]) if found is not None else None
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="请先登录。")
    user.course = found[1]
    return user


def student(user: m.User = Depends(current_user)) -> m.User:
    if user.role != "student" or not user.student_id:
        raise HTTPException(status_code=403, detail="这个页面只对学生开放。")
    return user


def teacher(user: m.User = Depends(current_user)) -> m.User:
    if user.role not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="这个页面只对教师开放。")
    return user


def admin(user: m.User = Depends(current_user)) -> m.User:
    """开发者账号（角色 admin）：教师端的研究、数据两块只给它。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="这个页面只对开发者开放。")
    return user
