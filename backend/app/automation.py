"""后台自动做的本地工作（不请求 XDOJ）。全部自动，不需要老师或学生去点任何按钮。

主循环没有同步任务要做时（空闲）调用 idle_work；每天夜里调用 nightly。

空闲时：
- 结束超时的对话回合（学生走开了也会结束），这样才能评估；
- 智能体评估刚结束的回合的“理解”（assess.py）；
- 给有了新数据的学生自动写（或重写）学情评语（profile.py）；
- 作业里还没标注的题按 XDOJ 的标签兜底标上，以前没记上的“第一次通过”补记（掌握程度不再只剩失败）；
- 以前的程序处理的 PDF 题面重新抽一遍，抽好了还没交给大模型整理的整理一遍（pdftext.py）；
- 给最近来过、有到期复习的学生提前备好一道复习题（review.prepare_active），进“复习提升”就能做。

每天夜里（graphmodel.nightly）：把没评完的回合评完、图模型全图更新、补记预测、重新学融合权重、
所有该写评语的学生写评语、跨课程联系自动补充、检验、采用或不用（cross.auto_links）。
"""
from __future__ import annotations

import logging
import time

from . import assess, db, pdftext, pipeline, profile, review
from .labeling import problems
from .chat import workflow

log = logging.getLogger("automation")

_last: dict[str, float] = {}
EVERY = {"rounds": 60.0, "reviews": 300.0, "assess": 10.0, "pdftext": 60.0, "labels": 60.0, "quizprep": 120.0}   # 秒：多久看一次（空闲时主循环转得很快，不用每次都查）


def _due(name: str) -> bool:
    now = time.monotonic()
    if now - _last.get(name, -1e9) < EVERY[name]:
        return False
    _last[name] = now
    return True


def idle_work() -> int:
    """返回做了几件事；0 表示没事可做（主循环就去睡一会儿）。"""
    done = 0
    if _due("rounds"):
        with db.session_scope() as s:
            closed = workflow.close_stale_rounds(s)
        if closed:
            log.info("自动结束了 %d 个超时的对话回合", closed)
        done += closed
    if _due("assess"):
        assessed = assess.run_pending()
        if assessed:
            _last["assess"] = -1e9   # 还有没评完的：下一轮接着评
        done += assessed
    if _due("reviews"):
        done += profile.refresh_reviews(limit=profile.config.PROFILE["reviews_per_run"])
    if _due("labels"):
        # 作业里还没有标注的题，按 XDOJ 的标签兜底标上（不需要大模型）；以前因为没有标注没记上的通过补记
        with db.session_scope() as s:
            labeled = problems.label_assigned(s, limit=50)
            filled = pipeline.backfill_passes(s, limit=200)
        if labeled or filled:
            _last["labels"] = -1e9
        done += labeled + filled
    if _due("quizprep"):
        try:
            done += review.prepare_active(limit=3)
        except Exception:   # 出题失败不能拖累别的自动工作；下次再试
            log.exception("提前备复习题失败")
    if _due("pdftext"):
        # 以前的程序处理的 PDF 题面重新抽；还没交给大模型整理的整理（每次两道）
        with db.session_scope() as s:
            redone = pdftext.redo_stored(s)
        if redone:
            _last["pdftext"] = -1e9
        done += redone
    return done
