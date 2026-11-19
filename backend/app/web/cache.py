"""教师端几个汇总页的结果缓存。

这些页面每分钟自动刷新一次，大多数时候数据没变，重算一遍全班的掌握程度是白算。
做法：记下上次的结果和当时的“数据版本”（几张会变的表的最大编号、最近更新时间），版本没变、
也没超过 TTL 秒，就直接用上次的。TTL 兜底：掌握程度随时间衰减、“本周”这类按时间算的数，最多晚 TTL 秒。
只缓存只读的汇总；权限检查在查缓存之前做（键里带班级，进到这里的已经核对过是他的班）。
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m

TTL = 120
MAX_ENTRIES = 128
_lock = threading.Lock()
_store: OrderedDict[tuple, tuple[float, tuple, Any]] = OrderedDict()

_VERSION_COLUMNS = (
    m.SkillEvidence.id, m.Submission.submission_id, m.Submission.fetched_at, m.Judge.judge_id,
    m.StuckPoint.id, m.QuizAttempt.id, m.QuizAttempt.finished_at, m.ChatMessage.id, m.ChatRound.last_message_at,
    m.AgentAssessment.id, m.HumanRating.created_at, m.GraphPosterior.updated_at, m.ModelWeights.updated_at,
    m.CrossLink.reviewed_at, m.CrossLink.checked_at, m.Enrollment.id, m.ProblemLabel.id, m.ReviewItem.id,
)


def data_version(s: Session) -> tuple:
    """一条查询取齐：每张表各取一个最大值（各自的子查询，不做连接）。"""
    row = s.execute(select(*[select(func.max(c)).scalar_subquery() for c in _VERSION_COLUMNS])).one()
    return (id(s.get_bind()), *row)


_CLASS_COLUMNS = (m.XClass.class_id, m.XClass.fetched_at, m.Enrollment.id, m.ClassSource.class_id, m.TeacherSource.created_at)
REFRESH = 600   # 秒：数据没变也最多隔这么久让页面重取一次（“本周”、随遗忘变化的掌握程度这类按时间算的数）


def _short(x) -> str:
    return hashlib.sha1(repr(x).encode("utf-8")).hexdigest()[:16]


def version_tag(s: Session) -> dict:
    """教师端页面定时只问这一个“数据变没变”，变了才重取（不再每页各自每分钟重取一遍）。
    data：几张会变的表的最大编号和最近更新时间（和结果缓存同一个），加上十分钟一档的时间；
    classes：班级、名单、老师关联的数据源（班级列表只在它变了时重取）。"""
    row = data_version(s)[1:]
    classes = s.execute(select(*[select(func.count(c)).scalar_subquery() for c in _CLASS_COLUMNS],
                               *[select(func.max(c)).scalar_subquery() for c in _CLASS_COLUMNS])).one()
    return {"data": _short((row, int(time.time() // REFRESH))), "classes": _short(tuple(classes))}


def cached(s: Session, key: tuple, build: Callable[[], Any], ttl: int = TTL) -> Any:
    version = data_version(s)
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit is not None and hit[1] == version and now - hit[0] < ttl:
            _store.move_to_end(key)
            return hit[2]
    value = build()
    with _lock:
        _store[key] = (now, version, value)
        _store.move_to_end(key)
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)
    return value


def clear() -> None:
    with _lock:
        _store.clear()
