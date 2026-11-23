"""账号：学生账号在同步名单时自动建，不能自己注册。"""
from __future__ import annotations

from sqlalchemy import or_, select, update
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


def active_class_keys(s: Session, user_id: str, course: str | None = None) -> set[tuple[int, int]]:
    """这个学生当前所在的班级：(服务器, 班级编号)。"""
    enrollment, cls = m.Enrollment, m.XClass
    query = select(enrollment.server_id, enrollment.class_id).outerjoin(
        cls, (cls.server_id == enrollment.server_id) & (cls.class_id == enrollment.class_id)
    ).where(enrollment.user_id == user_id, enrollment.status == "active", cls.removed_at.is_(None))
    if course is not None:
        query = query.join(m.Server, m.Server.id == enrollment.server_id).where(m.Server.course == course)
    rows = s.execute(query).all()
    return {(r[0], r[1]) for r in rows}


def student_course_removed(s: Session, user_id: str, course: str) -> bool:
    enrollment, cls = m.Enrollment, m.XClass
    removed = s.scalar(select(enrollment.id).join(
        cls, (cls.server_id == enrollment.server_id) & (cls.class_id == enrollment.class_id)
    ).join(m.Server, m.Server.id == enrollment.server_id)
        .where(enrollment.user_id == user_id, m.Server.course == course, cls.removed_at.is_not(None)).limit(1))
    return removed is not None and not active_class_keys(s, user_id, course)


def teacher_source_ids(s: Session, user: m.User) -> list[int]:
    """这位老师关联的全部数据源（每门课的 XDOJ 账号各一个）。"""
    ids = set(s.scalars(select(m.TeacherSource.data_source_id).where(m.TeacherSource.user_id == user.id)).all())
    if user.data_source_id is not None:
        ids.add(user.data_source_id)
    return sorted(ids)


def teacher_course_removed(s: Session, user: m.User, course: str) -> bool:
    ids = teacher_source_ids(s, user)
    if not ids:
        return False
    sources = s.scalars(select(m.DataSource).join(m.Server, m.Server.id == m.DataSource.server_id).where(
        m.DataSource.id.in_(ids), m.Server.course == course)).all()
    return bool(sources and all(source.removed_at is not None for source in sources))


def class_removed(s: Session, server_id: int, class_id: int, *, lock: bool = False) -> bool:
    query = select(m.XClass.removed_at).where(m.XClass.server_id == server_id, m.XClass.class_id == class_id)
    return s.scalar(query.with_for_update() if lock else query) is not None


def source_removed(s: Session, source_id: int | None, *, lock: bool = False) -> bool:
    if source_id is None:
        return False
    query = select(m.DataSource.removed_at).where(m.DataSource.id == source_id)
    return s.scalar(query.with_for_update() if lock else query) is not None


def submission_removed(s: Session, server_id: int, user_id: str, class_id: int | None,
                       contest_id: int | None = None) -> bool:
    """已移除班级的提交不用同步或产生新消息。无班号时沿作业和已有名单确认。"""
    if class_id is not None and class_removed(s, server_id, class_id):
        return True
    cls, enrollment = m.XClass, m.Enrollment
    removed = set(s.scalars(select(enrollment.class_id).join(
        cls, (cls.server_id == enrollment.server_id) & (cls.class_id == enrollment.class_id)
    ).where(enrollment.server_id == server_id, enrollment.user_id == user_id, cls.removed_at.is_not(None))).all())
    if not removed:
        return False
    active = {cid for sid, cid in active_class_keys(s, user_id) if sid == server_id}
    if not active:
        return True
    if contest_id is None:
        return False
    assigned = set(s.scalars(select(m.ContestClass.class_id).where(
        m.ContestClass.server_id == server_id, m.ContestClass.contest_id == contest_id)).all())
    return bool(assigned & removed and not assigned & active)


def remove_class(s: Session, row: m.XClass, at) -> None:
    row.removed_at = row.removed_at or at
    s.execute(update(m.Enrollment).where(
        m.Enrollment.server_id == row.server_id, m.Enrollment.class_id == row.class_id,
        m.Enrollment.status == "active"
    ).values(status="removed", until=row.removed_at))


def source_classes(s: Session, source: m.DataSource) -> list[m.XClass]:
    linked = select(m.ClassSource.class_id).where(m.ClassSource.server_id == source.server_id,
                                                 m.ClassSource.data_source_id == source.id)
    return list(s.scalars(select(m.XClass).where(m.XClass.server_id == source.server_id,
        or_(m.XClass.data_source_id == source.id, m.XClass.class_id.in_(linked)))
        .order_by(m.XClass.class_id)).all())


def remove_teacher_source(s: Session, source: m.DataSource, at) -> None:
    source.removed_at = source.removed_at or at
    source.enabled = False
    source.password_enc = None
    s.flush()
    for cls in source_classes(s, source):
        s.refresh(cls, with_for_update=True)
        other = s.scalar(select(m.ClassSource.data_source_id).join(
            m.DataSource, m.DataSource.id == m.ClassSource.data_source_id
        ).where(m.ClassSource.server_id == cls.server_id, m.ClassSource.class_id == cls.class_id,
                m.ClassSource.data_source_id != source.id, m.DataSource.removed_at.is_(None),
                m.DataSource.enabled.is_(True)).limit(1))
        if other is None:
            remove_class(s, cls, at)


def owner_of_source(s: Session, source_id: int) -> m.User | None:
    """这个 XDOJ 账号已经关联给了哪位老师。"""
    uid = s.scalar(select(m.TeacherSource.user_id).where(m.TeacherSource.data_source_id == source_id))
    if uid is None:
        uid = s.scalar(select(m.User.id).where(m.User.data_source_id == source_id, m.User.role == "teacher"))
    return s.get(m.User, uid) if uid is not None else None


def touch_login(s: Session, user: m.User) -> None:
    user.last_login_at = now()
