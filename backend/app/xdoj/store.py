"""同步的公共部分：上下文、进度、任务队列。"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import config, db
from .. import models as m
from .client import RequestRecord, XdojClient


@dataclass
class SyncContext:
    server_id: int
    client: XdojClient
    source_id: int | None = None
    term: str = ""
    course: str = "C"
    clock: Callable[[], dt.datetime] = db.now
    stats: dict = field(default_factory=dict)

    def now(self) -> dt.datetime:
        return self.clock()

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n


def request_logger(server_id: int) -> Callable[[RequestRecord], None]:
    """把每次请求记进 sync_request_log：只有接口、状态码、耗时。"""

    def write(rec: RequestRecord) -> None:
        with db.session_scope() as s:
            s.add(m.RequestLog(
                server_id=server_id, ts=dt.datetime.fromtimestamp(rec.started_at, db.UTC),
                endpoint=rec.endpoint, status_code=rec.status_code, duration_ms=rec.duration_ms, ok=rec.ok))
        from .daemon import touch  # 一步做很久时也能证明主循环还活着

        touch()

    return write


# ------------------------------------------------------------ 进度
def get_state(s: Session, server_id: int, key: str, default: str = "") -> str:
    row = s.get(m.SyncState, (server_id, key))
    return row.value if row else default


def set_state(s: Session, server_id: int, key: str, value) -> None:
    row = s.get(m.SyncState, (server_id, key))
    if row is None:
        s.add(m.SyncState(server_id=server_id, key=key, value=str(value), updated_at=db.now()))
    else:
        row.value = str(value)
        row.updated_at = db.now()


def get_time(s: Session, server_id: int, key: str) -> dt.datetime | None:
    raw = get_state(s, server_id, key)
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw)
    except ValueError:
        return None


def set_time(s: Session, server_id: int, key: str, value: dt.datetime) -> None:
    set_state(s, server_id, key, value.isoformat())


# ------------------------------------------------------------ 队列
XDOJ_KINDS = ("detail", "pending_judge", "recheck")
LOCAL_KINDS = ("post_process", "label_problem")


def enqueue(s: Session, server_id: int, kind: str, target_id: int, priority: int,
            delay_seconds: float = 0, when: dt.datetime | None = None) -> m.FetchTask:
    """入队。同一个目标已经在排队时不重复加，只在新优先级更高时往前提。"""
    base = when or db.now()
    due = base + dt.timedelta(seconds=delay_seconds)
    task = s.scalar(
        select(m.FetchTask).where(
            m.FetchTask.server_id == server_id, m.FetchTask.kind == kind,
            m.FetchTask.target_id == target_id, m.FetchTask.status == "queued")
    )
    if task is not None:
        if priority < task.priority:
            task.priority = priority
            task.next_try_at = min(task.next_try_at, due)
        return task
    task = m.FetchTask(server_id=server_id, kind=kind, target_id=target_id, priority=priority,
                       next_try_at=due, created_at=base)
    s.add(task)
    return task


def pick_task(s: Session, server_id: int, kinds: tuple[str, ...], priority: int | None,
              at: dt.datetime) -> m.FetchTask | None:
    q = select(m.FetchTask).where(
        m.FetchTask.server_id == server_id, m.FetchTask.kind.in_(kinds),
        m.FetchTask.status == "queued", m.FetchTask.next_try_at <= at)
    if priority is not None:
        q = q.where(m.FetchTask.priority == priority)
    return s.scalar(q.order_by(m.FetchTask.priority, m.FetchTask.id).limit(1))


def finish_task(s: Session, task_id: int) -> None:
    s.execute(update(m.FetchTask).where(m.FetchTask.id == task_id).values(status="done", last_error=""))


def fail_task(s: Session, task_id: int, error: str, retry: bool, at: dt.datetime) -> None:
    """失败：能重试的按 2、4、8…秒退避；重试用尽或不该重试的标为失败。"""
    task = s.get(m.FetchTask, task_id)
    if task is None:
        return
    task.attempts += 1
    task.last_error = error[:500]
    if retry and task.attempts < config.SYNC["max_retries"]:
        task.next_try_at = at + dt.timedelta(seconds=min(2 ** task.attempts, 300))
    else:
        task.status = "failed"


def requeue_failed(s: Session, server_id: int) -> int:
    """启动时把上次失败、但提交详情仍没取齐的任务重新入队。"""
    rows = s.scalars(select(m.FetchTask).where(
        m.FetchTask.server_id == server_id, m.FetchTask.status == "failed",
        m.FetchTask.kind.in_(("detail", "pending_judge")))).all()
    n = 0
    for task in rows:
        sub = s.get(m.Submission, (server_id, task.target_id))
        if sub is not None and sub.detail_status != "done":
            task.status = "done"
            enqueue(s, server_id, "detail", task.target_id, max(task.priority, 1))
            n += 1
    return n


def queue_backlog(s: Session, server_id: int) -> dict[str, int]:
    from sqlalchemy import func

    rows = s.execute(
        select(m.FetchTask.kind, m.FetchTask.priority, func.count())
        .where(m.FetchTask.server_id == server_id, m.FetchTask.status == "queued")
        .group_by(m.FetchTask.kind, m.FetchTask.priority)).all()
    return {f"{kind}/P{prio}": n for kind, prio, n in rows}
