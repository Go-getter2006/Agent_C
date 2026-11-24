"""后台自动做的本地工作（不请求 XDOJ）。全部自动，不需要老师或学生去点任何按钮。

主循环没有同步任务要做时（空闲）调用 idle_work；每天夜里调用 nightly。

空闲时：
- 结束超时的对话回合（学生走开了也会结束），这样才能评估；
- 智能体评估刚结束的回合的“理解”（assess.py）；
- 作业里还没标注的题按 XDOJ 的标签兜底标上，以前没记上的“第一次通过”补记（掌握程度不再只剩失败）；
- 以前的程序处理的 PDF 题面重新抽一遍，抽好了还没交给大模型整理的整理一遍（pdftext.py）；
- 给最近来过、有到期复习的学生提前备好一道复习题（review.prepare_active），进“复习提升”就能做。

每天夜里（graphmodel.nightly）：把没评完的回合评完、图模型全图更新、补记预测、重新学融合权重、
跨课程联系自动补充、检验、采用或不用（cross.auto_links）。
"""
from __future__ import annotations

import logging
import threading
import time

from . import assess, db, graphmodel, mastery, pdftext, pipeline, review
from .labeling import problems
from .chat import workflow

log = logging.getLogger("automation")

_last: dict[str, float] = {}
EVERY = {"rounds": 60.0, "assess": 10.0, "pdftext": 60.0, "labels": 60.0, "quizprep": 15.0, "graph_refresh": 30.0}


def _due(name: str) -> bool:
    now = time.monotonic()
    if now - _last.get(name, -1e9) < EVERY[name]:
        return False
    _last[name] = now
    return True


_review_worker: threading.Thread | None = None


def schedule_reviews() -> None:
    """同步拥有者定期唤醒独立的备题线程，抓取、会话读取和页面渲染都无需等它。"""
    global _review_worker
    if (_review_worker is not None and _review_worker.is_alive()) or not _due("quizprep"):
        return

    def run():
        try:
            review.prepare_active(limit=3)
        except Exception:
            log.exception("提前备复习题失败")

    _review_worker = threading.Thread(target=run, name="review-preparation", daemon=True)
    _review_worker.start()


def idle_work() -> int:
    """返回做了几件事；0 表示没事可做（主循环就去睡一会儿）。"""
    done = 0
    if _due("rounds"):
        with db.session_scope() as s:
            closed = workflow.close_stale_rounds(s)
        if closed:
            log.info("自动结束了 %d 个超时的对话回合", closed)
        done += closed
    if _due("graph_refresh"):
        done += graphmodel.refresh_old_posteriors()
    if _due("assess"):
        assessed = assess.run_pending()
        if assessed:
            _last["assess"] = -1e9   # 还有没评完的：下一轮接着评
        done += assessed
    if _due("labels"):
        # 作业里还没有标注的题，按 XDOJ 的标签兜底标上（不需要大模型）；以前因为没有标注没记上的通过补记
        with db.session_scope() as s, db.releasing(s):   # 重标要调大模型：调之前先提交（db.releasing）
            labeled = problems.label_assigned(s, limit=50)
            labeled += problems.relabel_changed(s, limit=5)   # 技能表改过：用到改了的技能的标注重标（大模型）
            filled = pipeline.backfill_passes(s, limit=200)
            reweighed = mastery.reweigh_passes(s, limit=500)   # 以前的版本记下的次技能证据按现在的规则重算
        if labeled or filled or reweighed:
            _last["labels"] = -1e9
        done += labeled + filled + reweighed
    if _due("pdftext"):
        # 以前的程序处理的 PDF 题面重新抽；还没交给大模型整理的整理（每次两道）
        with db.session_scope() as s, db.releasing(s):
            redone = pdftext.redo_stored(s)
        if redone:
            _last["pdftext"] = -1e9
        done += redone
    return done
