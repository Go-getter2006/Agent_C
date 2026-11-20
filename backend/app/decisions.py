"""系统替学生做的决定（研究用）：当时有哪些候选、各自的依据和分数、按什么规则选了哪个、选中的概率、结果在哪。

为什么要记：学期末能回放“掌握程度”，但回放不出“系统当时是怎么决定的”（模型和标注之后还会更新）。
要研究出题、复习、主动消息有没有用，要知道当时为什么给他这个、没给那个。

三处决定（kind）：
- review_pick：复习先出哪个技能。到期的复习项按 review.priority 排，从第一名开始试，出得了题的那个；固定排序，概率 1。
- quiz_plan：出正确还是有错的代码、出判断题还是找错并改。最近正确代码占比在中间时随机（各一半），否则按规则定，概率 1。
- notice：提交没过时发不发主动消息、发哪种（普通没过 / 卡住了 / 改对后又犯）。

学生端任何接口都不返回这张表；研究导出（logs.py 的 decision）带学号姓名，和别的导出一样。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from . import config, db
from . import models as m

VERSION = "decision-1"
RULES = {"review_pick": "evidence-priority-1", "quiz_plan": "quiz-plan-1", "notice": "notice-1"}
# 学生自己在复习安排里点了“去复习”：不是系统选的，rule 记成 student-pick


def record(s: Session, user_id: str, course: str, kind: str, candidates: list[dict], chosen: str,
           probability: float = 1.0, context: dict | None = None, ref: dict | None = None,
           ts: dt.datetime | None = None, rule: str | None = None) -> m.DecisionLog:
    row = m.DecisionLog(
        user_id=user_id, course=course or "C", kind=kind, ts=ts or db.now(), rule=rule or RULES.get(kind, ""),
        candidates=candidates, chosen=str(chosen)[:64], probability=round(float(probability), 4),
        context={**(context or {}), "versions": {"decisions": VERSION, "mastery": config.MASTERY_VERSION,
                                                 "prompt": config.PROMPT_VERSION}},
        ref={k: v for k, v in (ref or {}).items() if v is not None})
    s.add(row)
    s.flush()
    return row
