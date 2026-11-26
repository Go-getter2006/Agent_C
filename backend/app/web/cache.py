"""教师端几个汇总页的结果缓存。

这些页面每分钟自动刷新一次，大多数时候数据没变，重算一遍全班的掌握程度是白算。
做法：记下上次的结果和当时的“数据版本”（几张会变的表的最大编号、最近更新时间），版本没变、
也没超过 TTL 秒，就直接用上次的。TTL 兜底：掌握程度随时间衰减、“本周”这类按时间算的数，最多晚 TTL 秒。
只缓存只读的汇总；权限检查在查缓存之前做（键里带班级，进到这里的已经核对过是他的班）。

数据变了不让老师等着重算（班级总览全班一算要几秒）：有上次的结果就先给上次的、标上 refreshing，
同时在后台重算（每个页面同一时间只算一份）；页面看到 refreshing 过两秒再取一次，拿到新的。
最近半小时看过的页面，“数据变没变”的定时询问一发现变了就在后台先算（refresh_recent），
老师的页面来取时多半已经算好了；登录后只预计算默认班的总览（prewarm），后台同时最多计算两页。
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import db, mastery
from .. import models as m

log = logging.getLogger("web.cache")

TTL = 120
MAX_ENTRIES = 128
_lock = threading.Lock()
_store: OrderedDict[tuple, tuple[float, tuple, Any]] = OrderedDict()

_VERSION_COLUMNS = (
    m.SkillEvidence.id, m.Submission.submission_id, m.Submission.fetched_at, m.Judge.judge_id,
    m.StuckPoint.id, m.QuizAttempt.id, m.QuizAttempt.finished_at, m.ChatMessage.id, m.ChatRound.last_message_at,
    m.AgentAssessment.id, m.GraphPosterior.updated_at, m.ModelWeights.updated_at,
    m.PredictionLog.id,
    m.SubmissionLabel.id,
    m.CrossLink.reviewed_at, m.CrossLink.checked_at, m.Enrollment.id, m.ProblemLabel.id, m.ReviewItem.id,
)


def data_version(s: Session) -> tuple:
    """一条查询取齐：每张表各取一个最大值（各自的子查询，不做连接）。"""
    row = s.execute(select(*[select(func.max(c)).scalar_subquery() for c in _VERSION_COLUMNS])).one()
    return (id(s.get_bind()), *row)


_CLASS_COLUMNS = (m.XClass.class_id, m.XClass.fetched_at, m.XClass.removed_at, m.Enrollment.id, m.ClassSource.class_id,
                  m.TeacherSource.created_at, m.DataSource.removed_at)
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


BACKGROUND = True   # 测试里关掉：数据变了就当场重算（测后台重算的测试自己打开）
RECENT = 1800        # 秒：最近这么久看过的页面，数据一变就在后台先算
WAIT = 30            # 秒：没有旧结果、后台正在算同一页时，最多等这么久
_inflight: dict[tuple, threading.Event] = {}
_rebuilders: dict[tuple, tuple[float, Callable[[Session], Any]]] = {}   # 页面 → (最近一次看的时间, 怎么重算)
_workers = threading.BoundedSemaphore(2)  # 后台最多同时算两页，给前台请求和同步保留资源。


def _put(key: tuple, at: float, version: tuple, value: Any) -> None:
    with _lock:
        _store[key] = (at, version, value)
        _store.move_to_end(key)
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)


def _marked(value: Any) -> Any:
    """旧结果：告诉页面后台正在算新的，过一会儿再取。"""
    return {**value, "refreshing": True} if isinstance(value, dict) else value


def _refresh(key: tuple, rebuild: Callable[[Session], Any]) -> bool:
    """后台重算一页（同一页同一时间只算一份）。用自己的数据库会话，只读。"""
    with _lock:
        if key in _inflight:
            return False
        if not _workers.acquire(blocking=False):
            return False  # 旧结果仍可用；后续刷新请求再尝试，不积压全班计算。
        done = _inflight[key] = threading.Event()

    def run() -> None:
        try:
            with db.session_scope() as s:
                mastery.memoize(s)
                at, version = time.monotonic(), data_version(s)   # 先记版本：算的时候数据又变了，下次会再算
                value = rebuild(s)
            _put(key, at, version, value)
        except Exception:   # noqa: BLE001  后台重算失败不影响页面：下次来取时再算
            log.exception("后台重算 %s 失败", key[0])
        finally:
            with _lock:
                _inflight.pop(key, None)
            _workers.release()
            done.set()

    threading.Thread(target=run, name=f"page-cache-{key[0]}", daemon=True).start()
    return True


def cached(s: Session, key: tuple, build: Callable[[], Any], ttl: int = TTL,
           rebuild: Callable[[Session], Any] | None = None) -> Any:
    """rebuild：用另一个数据库会话重算这一页（给后台用）；没有就只能当场算。"""
    version = data_version(s)
    now = time.monotonic()
    with _lock:
        if rebuild is not None:
            _rebuilders[key] = (now, rebuild)
        hit = _store.get(key)
        if hit is not None and hit[1] == version and now - hit[0] < ttl:
            _store.move_to_end(key)
            return hit[2]
        waiting = _inflight.get(key)
        cold_done = None
        if BACKGROUND and rebuild is not None and hit is None and waiting is None:
            # 第一次取也共用同一份计算；两个页面同时打开时不各算一次全班。
            cold_done = _inflight[key] = threading.Event()
    if BACKGROUND and rebuild is not None:
        if hit is not None:            # 有上次的：先给上次的，后台算新的
            _refresh(key, rebuild)
            return _marked(hit[2])
        if waiting is not None and waiting.wait(WAIT):   # 后台正在算（比如登录时先算上的）：等它
            with _lock:
                hit = _store.get(key)
            if hit is not None:
                return hit[2]
    try:
        value = build()
        _put(key, now, version, value)
        return value
    finally:
        if cold_done is not None:
            with _lock:
                _inflight.pop(key, None)
            cold_done.set()


def prewarm(key: tuple, rebuild: Callable[[Session], Any]) -> None:
    """还没算过的页面先在后台算上（登录后取班级列表时，把总览先算好）。"""
    if not BACKGROUND:
        return
    with _lock:
        _rebuilders.setdefault(key, (time.monotonic(), rebuild))
        known = key in _store or key in _inflight
    if not known:
        _refresh(key, rebuild)


def refresh_recent(s: Session) -> int:
    """最近看过、数据已经变了的页面在后台先算（“数据变没变”的定时询问里调用）。返回开始算了几页。"""
    if not BACKGROUND:
        return 0
    version = data_version(s)
    now = time.monotonic()
    with _lock:
        for k in [k for k, (seen, _r) in _rebuilders.items() if now - seen > RECENT]:
            _rebuilders.pop(k, None)
        todo = [(seen, k, r) for k, (seen, r) in _rebuilders.items()
                if k not in _inflight and ((hit := _store.get(k)) is None or hit[1] != version)]
    # 优先最近正在看的班；旧页面不排成一条长队，切班请求无需等它们算完。
    return sum(_refresh(k, r) for _seen, k, r in sorted(todo, key=lambda row: row[0], reverse=True))


def wait_idle(timeout: float = WAIT) -> None:
    """等后台的重算都做完（测试用）。"""
    end = time.monotonic() + timeout
    while True:
        with _lock:
            pending = list(_inflight.values())
        if not pending or time.monotonic() > end:
            return
        pending[0].wait(max(0.0, end - time.monotonic()))


def clear() -> None:
    wait_idle()
    with _lock:
        _store.clear()
        _rebuilders.clear()
