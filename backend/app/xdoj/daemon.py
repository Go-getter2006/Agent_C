"""常驻主循环。

每一轮按下面的顺序找第一件该做的事，做完回到开头；所有 XDOJ 请求都过同一个限速器：
1. 未处理的触发；2. P0 详情；3. 到期的新提交轮询；4. 到期的“评测未完成”跟进；
5. P1 详情；6. 本地的标注任务；7. P2 详情；8. 到期的回读和每日元数据同步；9. P3（回读）。
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
                       nightly=graphmodel.nightly, idle_work=automation.idle_work).run_forever()
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
                 idle_work: Callable[[], int] | None = None):
        self.contexts = contexts
        self._sleep = sleep
        self._reload = reload      # 重新读一遍数据源：教师新登记的账号要能被发现
        self._nightly = nightly    # 每天夜里做一次的事（图模型全图更新）
        self._idle_work = idle_work  # 空闲时做的本地的事（智能体评估刚结束的回合）：不请求 XDOJ
        # 本地任务（标注、学情证据）的处理函数：(server_id, kind, target_id)
        self._post_process = post_process
        self.poll_failures: dict[int, int] = {c.server_id: 0 for c in contexts}

    # ------------------------------------------------------------ 状态
    def tier(self, ctx: SyncContext) -> tuple[str, int]:
        with db.session_scope() as s:
            last_new = store.get_time(s, ctx.server_id, "last_new_submission_at")
            last_trigger = store.get_time(s, 0, "last_trigger_at")
        return poll_tier(ctx.now(), last_new, last_trigger, self.poll_failures.get(ctx.server_id, 0))

    def _poll_due(self, ctx: SyncContext) -> bool:
        with db.session_scope() as s:
            last = store.get_time(s, ctx.server_id, "last_poll_at")
        if last is None:
            return True
        return ctx.now() - last >= dt.timedelta(seconds=self.tier(ctx)[1])

    # ------------------------------------------------------------ 各类工作
    def _poll(self, ctx: SyncContext) -> None:
        try:
            sync_submissions.poll_new_submissions(ctx)
            self.poll_failures[ctx.server_id] = 0
            with db.session_scope() as s:
                store.set_time(s, ctx.server_id, "last_ok_poll_at", ctx.now())
                store.set_state(s, ctx.server_id, "poll_failures", 0)
        except SessionError:
            raise
        except XdojError as exc:
            self.poll_failures[ctx.server_id] = self.poll_failures.get(ctx.server_id, 0) + 1
            log.warning("查新提交失败（连续第 %d 次）：%s", self.poll_failures[ctx.server_id], exc)
            with db.session_scope() as s:
                store.set_time(s, ctx.server_id, "last_poll_at", ctx.now())
                store.set_state(s, ctx.server_id, "poll_failures", self.poll_failures[ctx.server_id])

    def _handle_triggers(self) -> bool:
        with db.session_scope() as s:
            rows = s.scalars(select(m.Trigger).where(m.Trigger.handled_at.is_(None)).order_by(m.Trigger.id)).all()
            if not rows:
                return False
            # 同一个目标短时间内的重复触发合并成一次
            todo = {(t.kind, t.target_id, t.server_id) for t in rows}
            for t in rows:
                t.handled_at = db.now()
            store.set_time(s, 0, "last_trigger_at", db.now())
        polled: set[int] = set()

        def poll_once(ctx: SyncContext) -> None:
            if ctx.server_id not in polled:
                polled.add(ctx.server_id)
                self._poll(ctx)

        for kind, target, server_id in todo:
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
                        if sub is not None and sub.detail_status != "done":
                            self._raise_to_p0(s, ctx, sid)
            elif kind == "sync_meta":
                for ctx in self.contexts:
                    if server_id is None or ctx.server_id == server_id:
                        self._run_meta(ctx)
        return True

    @staticmethod
    def _raise_to_p0(s, ctx: SyncContext, submission_id: int) -> None:
        pending = s.scalar(select(m.FetchTask).where(
            m.FetchTask.server_id == ctx.server_id, m.FetchTask.kind == "pending_judge",
            m.FetchTask.target_id == submission_id, m.FetchTask.status == "queued"))
        if pending is not None:
            pending.priority = 0
            return
        task = store.enqueue(s, ctx.server_id, "detail", submission_id, 0, when=ctx.now())
        task.next_try_at = min(task.next_try_at, ctx.now())

    def _run_task(self, ctx: SyncContext, kinds: tuple[str, ...], priority: int | None) -> bool:
        with db.session_scope() as s:
            task = store.pick_task(s, ctx.server_id, kinds, priority, ctx.now())
            if task is None:
                return False
            task_id, kind, target, prio, attempts = task.id, task.kind, task.target_id, task.priority, task.attempts
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

    def _run_meta(self, ctx: SyncContext) -> None:
        _state["activity"] = "同步名单和作业"
        with db.session_scope() as s:
            store.set_time(s, ctx.server_id, "last_meta_try_at", ctx.now())
        try:
            sync_meta.sync_metadata(ctx)
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
            done = store.get_time(s, ctx.server_id, "last_meta_at")
            tried = store.get_time(s, ctx.server_id, "last_meta_try_at")
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
        if self._handle_triggers():
            return "trigger"
        for ctx in self.contexts:  # 先同步名单：学生要在名单里才能登录
            if self._meta_due(ctx):
                self._run_meta(ctx)
                return "meta"
        stages: list[tuple[str, Callable[[SyncContext], bool]]] = [
            ("p0", lambda c: self._run_task(c, ("detail", "pending_judge"), 0)),
            ("poll", lambda c: self._poll_due(c) and (self._poll(c) or True)),
            ("pending", lambda c: self._run_task(c, ("pending_judge",), None)),
            ("p1", lambda c: self._run_task(c, ("detail", "problem_assets"), 1)),
            ("local", lambda c: self._run_task(c, store.LOCAL_KINDS, None)),
            ("p2", lambda c: self._run_task(c, ("detail", "problem_assets"), 2)),
            ("periodic", self._periodic),
            ("p3", lambda c: self._run_task(c, ("recheck", "detail", "problem_assets", "problem_check"), 3)),
        ]
        for name, fn in stages:
            for ctx in self.contexts:
                if fn(ctx):
                    return name
        return None

    def startup(self) -> None:
        for ctx in self.contexts:
            with db.session_scope() as s:
                n = store.requeue_failed(s, ctx.server_id, at=ctx.now())
            if n:
                log.info("重新入队上次失败、还没取齐的 %d 个任务（提交详情、题面和测试点）", n)

    def run_forever(self) -> None:
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
            if self._reload is not None and time.monotonic() - last_reload >= 60:
                last_reload = time.monotonic()
                known = {c.source_id for c in self.contexts}
                for ctx in self._reload():
                    if ctx.source_id not in known:
                        self.contexts.append(ctx)
                        self.poll_failures[ctx.server_id] = 0
                        log.info("发现新的数据源 %s", ctx.source_id)
                    else:
                        ctx.client.close()
            did = self.step() if self.contexts else None
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
