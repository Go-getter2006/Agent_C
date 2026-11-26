"""常驻主循环。

每一轮按下面的顺序找第一件该做的事，做完回到开头；所有 XDOJ 请求都过同一个限速器：
1. 未处理的触发；2. P0 详情；3. 到期的新提交轮询；4. 到期的“评测未完成”跟进；
5. 已取齐提交的分析；6. P1 详情；7. 题目标注；8. P2 详情；
9. 到期的回读和每日元数据同步；10. P3（回读）。
都没有就睡 0.5 秒。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import socket
import threading
import time
from typing import Callable

from sqlalchemy import select

from .. import config, db
from .. import models as m
from . import store, sync_meta, sync_submissions
from .client import ClientError, ResponseError, RetryExhausted, SessionError, XdojError
from .store import SyncContext

log = logging.getLogger("xdoj.daemon")


def poll_tier(now: dt.datetime, last_new: dt.datetime | None, last_trigger: dt.datetime | None,
              fail_streak: int = 0) -> tuple[str, int]:
    """三档轮询：活跃 10 秒、平时 60 秒、夜间 5 分钟。连续失败 10 次降到 5 分钟。"""
    cfg = config.SYNC
    if fail_streak >= cfg["fail_streak_slowdown"]:
        return "degraded", cfg["poll_night_seconds"]

    def within(t: dt.datetime | None, minutes: int) -> bool:
        return t is not None and now - t <= dt.timedelta(minutes=minutes)

    if within(last_new, cfg["active_window_minutes"]) or within(last_trigger, cfg["active_window_minutes"]):
        return "active", cfg["poll_active_seconds"]
    hour = now.astimezone(db.BEIJING).hour
    start, end = cfg["night_hours"]
    quiet = not within(last_new, cfg["night_quiet_minutes"]) and not within(last_trigger, cfg["night_quiet_minutes"])
    if start <= hour < end and quiet:
        return "night", cfg["poll_night_seconds"]
    return "normal", cfg["poll_normal_seconds"]


HEARTBEAT_KEY = "daemon_heartbeat"

# 这个进程里主循环的状态：谁在跑、正在做什么。心跳把它写进库，“数据源”页显示出来。
_state: dict = {"owner": None, "activity": "启动中", "last": 0.0}


def set_activity(text: str) -> None:
    _state["activity"] = text
    touch()


def touch() -> None:
    """长步骤里每发一个 XDOJ 请求都会调到这里：隔 15 秒写一次心跳，免得一步做很久时被当成没在运行。"""
    owner = _state["owner"]
    if owner is None or time.monotonic() - _state["last"] < 15:
        return
    _state["last"] = time.monotonic()
    try:
        claim(owner)
    except Exception:  # 心跳写不进去不影响同步
        log.exception("写心跳失败")


def _owner_of(value: str) -> str:
    try:
        return str(json.loads(value).get("owner", ""))
    except (ValueError, AttributeError):
        return value


def claim(owner: str, stale_seconds: int = 60) -> bool:
    """占住“正在同步”的位置并写心跳。别的主循环一分钟内跳过心跳，就让给它，免得两个进程同时向 XDOJ 发请求。"""
    with db.session_scope() as s:
        row = s.get(m.SyncState, (0, HEARTBEAT_KEY))
        now = db.now()
        value = json.dumps({"owner": owner, "activity": _state["activity"]}, ensure_ascii=False)
        if row is None:
            s.add(m.SyncState(server_id=0, key=HEARTBEAT_KEY, value=value, updated_at=now))
            return True
        if _owner_of(row.value) != owner and row.updated_at and (now - row.updated_at).total_seconds() < stale_seconds:
            return False
        row.value, row.updated_at = value, now
        return True


def last_beat(s) -> tuple[dt.datetime | None, str]:
    """上次心跳的时间，和那时正在做什么。"""
    row = s.get(m.SyncState, (0, HEARTBEAT_KEY))
    if row is None:
        return None, ""
    try:
        activity = str(json.loads(row.value).get("activity", ""))
    except (ValueError, AttributeError):
        activity = ""
    return row.updated_at, activity


def start_in_thread() -> threading.Thread:
    """在网页进程里起一个后台线程跑主循环。"""
    from .. import automation, graphmodel, pipeline, sources

    def run() -> None:
        while True:
            try:
                Daemon(sources.build_contexts(), post_process=pipeline.local_task, reload=sources.build_contexts,
                       nightly=graphmodel.nightly, idle_work=automation.idle_work,
                       maintenance=automation.maintain_mastery).run_forever()
            except Exception:  # 出错了等一会儿重来，不能把网页带崩
                log.exception("后台同步出错，30 秒后重启")
                time.sleep(30)

    t = threading.Thread(target=run, name="xdoj-sync", daemon=True)
    t.start()
    return t


ACTIVITY = {
    "detail": "取提交的评测详情", "pending_judge": "等刚交的提交评测完", "recheck": "回读最近的提交，看有没有重判",
    "problem_assets": "下载题目的测试点和题面（题库所有老师共用，每道题只下载一次）",
    "problem_check": "核对作业里的题有没有改动", "label_problem": "标注题目",
    "post_process": "分析提交、更新学情",
}


class Daemon:
    def __init__(self, contexts: list[SyncContext], *, sleep: Callable[[float], None] = time.sleep,
                 post_process: Callable[[int, str, int], None] | None = None,
                 reload: Callable[[], list[SyncContext]] | None = None,
                 nightly: Callable[[], object] | None = None,
                 idle_work: Callable[[], int] | None = None,
                 maintenance: Callable[[], int] | None = None):
        self.contexts = contexts
        self._sleep = sleep
        self._reload = reload      # 重新读一遍数据源：教师新登记的账号要能被发现
        self._nightly = nightly    # 每天夜里做一次的事（图模型全图更新）
        self._idle_work = idle_work  # 空闲时做的本地的事（智能体评估刚结束的回合）：不请求 XDOJ
        self._maintenance = maintenance
        self._last_maintenance: dt.datetime | None = None
        # 本地任务（标注、学情证据）的处理函数：(server_id, kind, target_id)
        self._post_process = post_process
        self.poll_failures: dict[tuple[int, int | None], int] = {(c.server_id, c.source_id): 0 for c in contexts}
        self._poll_next = 0
        self._last_trigger_reload: float | None = None
        self._waiting_trigger_ids: set[int] = set()

    def _reload_contexts(self) -> None:
        if self._reload is None:
            return
        known = {(c.server_id, c.source_id) for c in self.contexts}
        live_clients = {id(c.client) for c in self.contexts}
        for ctx in self._reload():
            key = (ctx.server_id, ctx.source_id)
            if key not in known:
                known.add(key)
                self.contexts.append(ctx)
                self.poll_failures[(ctx.server_id, ctx.source_id)] = 0
                log.info("发现新的数据源 %s", ctx.source_id)
            elif id(ctx.client) not in live_clients:
                ctx.client.close()

    def _maintain(self, ctx: SyncContext) -> bool:
        if self._maintenance is None:
            return False
        now = ctx.now()
        if self._last_maintenance is None or now - self._last_maintenance >= dt.timedelta(seconds=60):
            self._last_maintenance = now
            try:
                self._maintenance()
            except Exception:
                log.exception("自动补记掌握证据失败")
        return False  # 维护不能让本轮的同步或分析任务失去执行机会。

    # ------------------------------------------------------------ 状态
    def tier(self, ctx: SyncContext) -> tuple[str, int]:
        with db.session_scope() as s:
            last_new = store.get_time(s, ctx.server_id, "last_new_submission_at")
            last_trigger = store.get_time(s, 0, "last_trigger_at")
        return poll_tier(ctx.now(), last_new, last_trigger, self.poll_failures.get((ctx.server_id, ctx.source_id), 0))

    def _poll_due(self, ctx: SyncContext) -> bool:
        if ctx.stats.get("poll_has_more"):
            return True
        with db.session_scope() as s:
            last = store.get_time(s, ctx.server_id, ctx.metadata_key("last_poll_at"))
        if last is None:
            return True
        return ctx.now() - last >= dt.timedelta(seconds=self.tier(ctx)[1])

    # ------------------------------------------------------------ 各类工作
    def _poll(self, ctx: SyncContext) -> None:
        source = (ctx.server_id, ctx.source_id)
        try:
            sync_submissions.poll_new_submissions(ctx, max_requests=1)
            self.poll_failures[source] = 0
            with db.session_scope() as s:
                store.set_time(s, ctx.server_id, "last_ok_poll_at", ctx.now())
                store.set_state(s, ctx.server_id, "poll_failures", 0)
                if ctx.source_id is not None:
                    store.set_time(s, ctx.server_id, ctx.metadata_key("last_ok_poll_at"), ctx.now())
                    store.set_state(s, ctx.server_id, ctx.metadata_key("poll_failures"), 0)
        except SessionError:
            raise
        except XdojError as exc:
            ctx.stats["poll_has_more"] = False
            self.poll_failures[source] = self.poll_failures.get(source, 0) + 1
            log.warning("查新提交失败（数据源 %s，连续第 %d 次）：%s", ctx.source_id, self.poll_failures[source], exc)
            with db.session_scope() as s:
                store.set_time(s, ctx.server_id, "last_poll_at", ctx.now())
                store.set_state(s, ctx.server_id, "poll_failures", self.poll_failures[source])
                if ctx.source_id is not None:
                    store.set_time(s, ctx.server_id, ctx.metadata_key("last_poll_at"), ctx.now())
                    store.set_state(s, ctx.server_id, ctx.metadata_key("poll_failures"), self.poll_failures[source])

    def _handle_triggers(self) -> str | None:
        with db.session_scope() as s:
            rows = s.scalars(select(m.Trigger).where(m.Trigger.handled_at.is_(None)).order_by(m.Trigger.id)).all()
            if not rows:
                return None
            pending = [(t.id, t.kind, t.target_id, t.server_id) for t in rows]
        def matches(target, server_id):
            return any((server_id is None or c.server_id == server_id)
                       and (not target or str(c.source_id) == target) for c in self.contexts)

        # 登录已写入数据源和触发，新教师不应再等一分钟才被主循环发现。
        if any(kind == "sync_meta" and not matches(target, server) for _, kind, target, server in pending):
            now = time.monotonic()
            if self._last_trigger_reload is None or now - self._last_trigger_reload >= 5:
                self._last_trigger_reload = now
                self._reload_contexts()
        ready = [(tid, kind, target, server) for tid, kind, target, server in pending
                 if kind == "sync_meta" and matches(target, server)]
        ready += [r for r in pending if r[1] != "sync_meta" and self.contexts]
        if not ready:
            # 首次发现先让出这一轮；之后仍未加载成功时，其他同步任务可继续。
            # 始终保留触发，不提前标成已处理，也不能一直空转挡住其他教师。
            waiting = {r[0] for r in pending}
            first_wait = bool(waiting - self._waiting_trigger_ids)
            self._waiting_trigger_ids = waiting
            return "trigger" if first_wait else None
        self._waiting_trigger_ids.difference_update(r[0] for r in ready)
        todo = {(kind, target, server) for _, kind, target, server in ready}
        with db.session_scope() as s:
            s.execute(m.Trigger.__table__.update().where(m.Trigger.id.in_([r[0] for r in ready])).values(handled_at=db.now()))
            store.set_time(s, 0, "last_trigger_at", db.now())
        polled: set[int] = set()

        def poll_once(ctx: SyncContext) -> None:
            if ctx.server_id not in polled:
                polled.add(ctx.server_id)
                self._poll(ctx)

        # 先取学生明确点开的这一条，再处理“活跃”触发的批量轮询和其他历史提交。
        for kind, target, server_id in sorted(todo, key=lambda t: t[0] != "submission_priority"):
            if kind == "student_active":
                for ctx in self.contexts:
                    poll_once(ctx)
                    with db.session_scope() as s:
                        ids = s.scalars(select(m.Submission.submission_id).where(
                            m.Submission.server_id == ctx.server_id, m.Submission.user_id == target,
                            m.Submission.detail_status != "done")).all()
                        for sid in ids:
                            self._raise_to_p0(s, ctx, sid)
            elif kind == "submission_priority":
                for ctx in self.contexts:
                    if server_id is not None and ctx.server_id != server_id:
                        continue
                    sid = int(target)
                    with db.session_scope() as s:
                        known = s.get(m.Submission, (ctx.server_id, sid)) is not None
                    if not known:
                        poll_once(ctx)
                    with db.session_scope() as s:
                        sub = s.get(m.Submission, (ctx.server_id, sid))
                        if sub is not None:
                            self._raise_to_p0(s, ctx, sid)
                    self._run_task(ctx, ("detail", "pending_judge"), 0, target_id=sid)
            elif kind == "sync_meta":
                for ctx in self.contexts:
                    if (server_id is None or ctx.server_id == server_id) and (not target or target == str(ctx.source_id)):
                        self._run_meta(ctx)
        return "meta" if all(kind == "sync_meta" for kind, _target, _server in todo) else "trigger"

    @staticmethod
    def _raise_to_p0(s, ctx: SyncContext, submission_id: int) -> None:
        pending = s.scalar(select(m.FetchTask).where(
            m.FetchTask.server_id == ctx.server_id, m.FetchTask.kind == "pending_judge",
            m.FetchTask.target_id == submission_id, m.FetchTask.status == "queued"))
        if pending is not None:
            pending.priority = 0
            pending.next_try_at = min(pending.next_try_at, ctx.now())
            return
        task = store.enqueue(s, ctx.server_id, "detail", submission_id, 0, when=ctx.now())
        task.next_try_at = min(task.next_try_at, ctx.now())

    def _run_task(self, ctx: SyncContext, kinds: tuple[str, ...], priority: int | None,
                  target_id: int | None = None) -> bool:
        with db.session_scope() as s:
            task = store.pick_task(s, ctx.server_id, kinds, priority, ctx.now(), target_id=target_id)
            if task is None:
                return False
            task_id, kind, target, prio, attempts = task.id, task.kind, task.target_id, task.priority, task.attempts
            if kind in ("detail", "pending_judge", "recheck"):
                ctx = self._submission_context(s, ctx, target)
        _state["activity"] = ACTIVITY.get(kind, kind)
        try:
            if kind in ("problem_assets", "problem_check"):
                # problem_check 只比对测试点列表和题面，变了才下载测试点文件
                sync_meta.sync_problem_assets(ctx, target, force_files=(kind == "problem_assets"), priority=max(prio, 2))
            elif kind == "detail":
                try:
                    sync_submissions.fetch_detail(ctx, target, source_first=(prio == 0))
                except sync_submissions.NotJudgedYet:
                    with db.session_scope() as s:
                        store.enqueue(s, ctx.server_id, "pending_judge", target, prio,
                                      delay_seconds=config.SYNC["pending_judge_delays"][0], when=ctx.now())
            elif kind == "pending_judge":
                try:
                    sync_submissions.fetch_detail(ctx, target, source_first=(prio == 0))
                except sync_submissions.NotJudgedYet:
                    delays = config.SYNC["pending_judge_delays"]
                    with db.session_scope() as s:
                        row = s.get(m.FetchTask, task_id)
                        row.attempts = attempts + 1
                        if row.attempts >= len(delays):
                            row.status = "done"  # 跟进 5 次仍没有结果：交给回读处理
                            row.last_error = "评测一直没完成，留给回读"
                        else:
                            row.next_try_at = ctx.now() + dt.timedelta(seconds=delays[row.attempts])
                    return True
            elif kind == "recheck":
                sync_submissions.recheck_submission(ctx, target)
            elif kind in store.LOCAL_KINDS:
                if self._post_process is not None:
                    self._post_process(ctx.server_id, kind, target)
            with db.session_scope() as s:
                store.finish_task(s, task_id)
        except SessionError:
            raise  # 登录本身失败（账号密码不对）：停下报错
        except (ClientError, ResponseError, RetryExhausted, XdojError) as exc:
            # XDOJ 拒绝了这一个请求、或者一时出错：只有这个任务失败，慢慢退避重试，不让整个同步停下来。
            # 以前 400 错误页被当成“掉线”，重新登录后抛会话错误，后台同步每 30 秒重启一次又卡在同一个任务上
            log.warning("任务 %s/%s 没做完：%s", kind, target, exc)
            with db.session_scope() as s:
                store.fail_task(s, task_id, str(exc), retry=True, at=ctx.now(), remote=True)
        except LookupError as exc:
            with db.session_scope() as s:
                store.fail_task(s, task_id, str(exc), retry=False, at=ctx.now())
        except Exception as exc:  # 本地任务出错不能把主循环带崩
            log.exception("任务 %s/%s 出错", kind, target)
            with db.session_scope() as s:
                store.fail_task(s, task_id, f"{type(exc).__name__}: {exc}", retry=True, at=ctx.now())
        return True

    def _submission_context(self, s, ctx: SyncContext, submission_id: int) -> SyncContext:
        """队列按服务器共用，详情要用这次提交所属班级的教师会话获取。"""
        sub = s.get(m.Submission, (ctx.server_id, submission_id))
        if sub is None:
            return ctx
        sources = set(s.scalars(select(m.ClassSource.data_source_id).where(
            m.ClassSource.server_id == ctx.server_id, m.ClassSource.class_id == sub.class_id)).all()) \
            if sub.class_id is not None else set()
        available = [c for c in self.contexts if c.server_id == ctx.server_id]
        if not any(c.source_id in sources for c in available):
            # 部分提交不带班号或带的是另一种编号，按已同步的学生名单找教师。
            sources = set(s.scalars(select(m.ClassSource.data_source_id).join(
                m.Enrollment, (m.Enrollment.server_id == m.ClassSource.server_id)
                & (m.Enrollment.class_id == m.ClassSource.class_id)).where(
                m.Enrollment.server_id == ctx.server_id, m.Enrollment.user_id == sub.user_id,
                m.Enrollment.status == "active")).all())
        return next((c for c in available if c.source_id in sources), ctx)

    def _run_meta(self, ctx: SyncContext) -> None:
        _state["activity"] = "同步名单和作业"
        with db.session_scope() as s:
            first = (store.get_time(s, ctx.server_id, ctx.metadata_key("last_meta_at")) is None
                     and store.get_time(s, ctx.server_id, ctx.metadata_key("last_poll_at")) is None)
            store.set_time(s, ctx.server_id, "last_meta_try_at", ctx.now())
            if ctx.source_id is not None:
                store.set_time(s, ctx.server_id, ctx.metadata_key("last_meta_try_at"), ctx.now())
        try:
            sync_meta.sync_metadata(ctx, before_bank=(lambda: self._poll(ctx)) if first else None)
        except SessionError:
            raise
        except XdojError as exc:
            log.warning("元数据同步失败：%s", exc)
        with db.session_scope() as s:
            store.set_state(s, ctx.server_id, "last_meta_date", ctx.now().astimezone(db.BEIJING).date().isoformat())

    def _meta_due(self, ctx: SyncContext) -> bool:
        """名单和作业到时间该同步了没有：每两小时一次，自动的，不用谁去点。

        名单关系到学生能不能登录，所以排在一切之前（启动后第一件事）。上一次没成功的，10 分钟后再试。
        """
        now = ctx.now()
        with db.session_scope() as s:
            done = store.get_time(s, ctx.server_id, ctx.metadata_key("last_meta_at"))
            tried = store.get_time(s, ctx.server_id, ctx.metadata_key("last_meta_try_at"))
        if tried is not None and now - tried < dt.timedelta(minutes=config.SYNC["meta_retry_minutes"]):
            return False
        return done is None or now - done >= dt.timedelta(minutes=config.SYNC["meta_interval_minutes"])

    def _periodic(self, ctx: SyncContext) -> bool:
        now = ctx.now()
        with db.session_scope() as s:
            last_recheck = store.get_time(s, ctx.server_id, "last_recheck_at")
        if last_recheck is None or now - last_recheck >= dt.timedelta(seconds=config.SYNC["recheck_interval_seconds"]):
            sync_submissions.enqueue_recheck(ctx)
            return True
        local = now.astimezone(db.BEIJING)
        if self._nightly is not None and ctx is self.contexts[0]:
            with db.session_scope() as s:
                done = store.get_state(s, 0, "last_nightly_date")
            if done != local.date().isoformat() and local.hour >= config.SYNC["meta_hour"]:
                try:
                    for c in self.contexts:  # 失败了的详情、题面和测试点，每天再试一次
                        with db.session_scope() as s:
                            store.requeue_failed(s, c.server_id, at=c.now())
                    self._nightly()
                except Exception:
                    log.exception("夜间任务出错")
                with db.session_scope() as s:
                    store.set_state(s, 0, "last_nightly_date", local.date().isoformat())
                return True
        return False

    # ------------------------------------------------------------ 主循环
    def step(self) -> str | None:
        """做一件事，返回做了什么；没事可做返回 None。"""
        source_ids = {ctx.source_id for ctx in self.contexts if ctx.source_id is not None}
        if source_ids:
            with db.session_scope() as s:
                removed = set(s.scalars(select(m.DataSource.id).where(
                    m.DataSource.id.in_(source_ids), m.DataSource.removed_at.is_not(None))).all())
            if removed:
                for ctx in self.contexts:
                    if ctx.source_id in removed:
                        ctx.client.close()
                self.contexts[:] = [ctx for ctx in self.contexts if ctx.source_id not in removed]
        triggered = self._handle_triggers()
        if triggered:
            return triggered
        for ctx in self.contexts:  # 先同步名单：学生要在名单里才能登录
            if self._meta_due(ctx):
                self._run_meta(ctx)
                return "meta"
        stages: list[tuple[str, Callable[[SyncContext], bool]]] = [
            ("p0", lambda c: self._run_task(c, ("detail", "pending_judge", "problem_assets"), 0)),
            ("poll", lambda c: self._poll_due(c) and (self._poll(c) or True)),
            ("pending", lambda c: self._run_task(c, ("pending_judge",), None)),
            ("maintenance", self._maintain),
            ("local", lambda c: self._run_task(c, ("post_process",), None)),
            ("p1", lambda c: self._run_task(c, ("detail", "problem_assets"), 1)),
            ("local", lambda c: self._run_task(c, ("label_problem",), None)),
            ("p2", lambda c: self._run_task(c, ("detail", "problem_assets"), 2)),
            ("periodic", self._periodic),
            ("p3", lambda c: self._run_task(c, ("recheck", "detail", "problem_assets", "problem_check"), 3)),
        ]
        for name, fn in stages:
            # 每页让给下一位老师，避免一个老师补历史时其他老师一直等。
            offset = self._poll_next % len(self.contexts) if name == "poll" and self.contexts else 0
            ordered = self.contexts[offset:] + self.contexts[:offset]
            for index, ctx in enumerate(ordered):
                if fn(ctx):
                    if name == "poll":
                        self._poll_next = (offset + index + 1) % len(self.contexts)
                    return name
        return None

    def startup(self) -> None:
        for ctx in self.contexts:
            with db.session_scope() as s:
                n = store.requeue_failed(s, ctx.server_id, at=ctx.now())
            if n:
                log.info("重新入队上次失败、还没取齐的 %d 个任务（提交详情、题面和测试点）", n)

    def run_forever(self) -> None:
        from ..automation import schedule_reviews

        self.startup()
        log.info("主循环启动，数据源 %d 个", len(self.contexts))
        last_reload = time.monotonic()
        last_beat = 0.0
        owner = f"{socket.gethostname()}:{os.getpid()}:{id(self)}"
        _state["owner"] = owner
        while True:
            if time.monotonic() - last_beat >= 20:
                last_beat = time.monotonic()
                try:
                    if not claim(owner):
                        log.warning("另一个后台同步进程正在运行，这个先等着")
                        self._sleep(30)
                        last_beat = 0.0
                        continue
                except Exception:  # 心跳写不进去不影响同步
                    log.exception("写心跳失败")
            schedule_reviews()
            if self._reload is not None and time.monotonic() - last_reload >= 60:
                last_reload = time.monotonic()
                self._reload_contexts()
            did = self.step()
            if did is None and self._idle_work is not None:
                try:
                    if self._idle_work():
                        _state["activity"] = "智能体评估、自动写评语"
                        continue
                except Exception:
                    log.exception("空闲时的本地任务出错")
            if did is None:
                _state["activity"] = "空闲，等新的提交" if self.contexts else "还没有数据源：等老师在网页上用 XDOJ 账号登录"
                self._sleep(config.SYNC["idle_sleep_seconds"])

    def drain(self, max_steps: int = 100000) -> int:
        """把眼下能做的都做完（测试和回填用）。到期时间还没到的任务不等。"""
        n = 0
        while n < max_steps and self.step() is not None:
            n += 1
        return n
