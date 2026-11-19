"""每次帮助之后怎么样了：把作业引导里给出的帮助，和这道题后面的提交连起来。全用程序算，不用大模型。

一次帮助：智能体在一道绑定了的题上给出帮助的那一步（chat_steps.help_level 是 H0～H3）。
H0 反问，H1 指出位置，H2 讲原理，H3 给示例代码。

只算“再交之前的最后一次帮助”：两次提交之间给了好几次帮助，算最后那一次，前面的次数记在 helpsBefore 里
（这样每次再交只对应一次帮助，不重复算）。还没再交的，也是最后一次帮助。

指标（版本 help-next-1，研究导出里同名），按帮助级别分开：
- 再交了：帮助之后这道题又交过 / 帮助的次数
- 下一次就通过：下一次提交通过 / 再交了、下一次已经评测完的次数
- 讨论的错误没了：下一次提交通过，或者程序在下一次提交里没再标出帮助时诊断的那个错误
  / 再交了、帮助时诊断出了错误、下一次提交已经评测完并且标注好（或者通过了）的次数
- 之后通过：帮助之后这道题通过了 / 帮助的次数
- 隔多久再交：帮助到下一次提交的分钟数（中位数，只算再交了的）
- 到通过交了几次：从下一次提交到第一次通过（含）交了几次（中位数，只算之后通过了的）

这是描述性的：帮助级别是按学生当时的情况选的（卡得越深给得越多），不同级别之间不能直接比较效果。
要比较效果，要在同样合理的几种帮法之间随机分配（见 docs/研究数据与指标.md）。
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import changes
from . import models as m
from .taxonomy import kp_of_skill

VERSION = "help-next-1"
LEVELS = ("H0", "H1", "H2", "H3")
LEVEL_NAMES = {"H0": "反问", "H1": "指出位置", "H2": "讲原理", "H3": "给示例代码"}
_SKIP = {"PENDING", "UNKNOWN", ""}


@dataclass
class HelpEpisode:
    step: m.ChatStep
    level: str
    skill: str
    kp: str
    error: str                         # 帮助时程序诊断的错误（可能为空）
    helps_before: int                  # 同一段（上一次提交到下一次提交之间）里，这次之前还给过几次帮助
    next_sub: m.Submission | None      # 帮助之后这道题的下一次提交
    minutes: float | None              # 帮助到下一次提交隔了几分钟
    next_pass: bool | None             # 下一次提交通过了没有（还在评测：None）
    error_gone: bool | None            # 讨论的错误在下一次提交里没了（判断不了：None）
    passed: bool                       # 帮助之后这道题通过了没有
    tries_to_pass: int | None          # 从下一次提交到第一次通过（含）交了几次


def _labeled(s: Session, sub: m.Submission) -> bool:
    """程序已经给这次提交标过错误了（标出来的可能是“其他”或者置信度不够，那也算标过）。"""
    return s.scalar(select(m.SubmissionLabel.id).where(m.SubmissionLabel.server_id == sub.server_id,
                           m.SubmissionLabel.submission_id == sub.submission_id,
                           m.SubmissionLabel.is_current.is_(True)).limit(1)) is not None


def episodes(s: Session, user_ids: list[str], course: str | None = None,
             problems: set[tuple[int, int]] | None = None) -> list[HelpEpisode]:
    """这些学生在作业引导里得到的每一次“再交之前的最后一次帮助”和之后的结果。problems：只看这些题（班上布置的）。"""
    if not user_ids:
        return []
    q = select(m.ChatStep).where(m.ChatStep.user_id.in_(user_ids), m.ChatStep.help_level.in_(LEVELS),
                                 m.ChatStep.problem_id.is_not(None))
    if course is not None:
        q = q.where(m.ChatStep.course_id == course)
    groups: dict[tuple[str, int, int], list[m.ChatStep]] = defaultdict(list)
    for st in s.scalars(q.order_by(m.ChatStep.id)).all():
        key = (st.server_id, st.problem_id)
        if problems is not None and key not in problems:
            continue
        groups[(st.user_id, st.server_id, st.problem_id)].append(st)
    out: list[HelpEpisode] = []
    for (uid, sid, pid), steps in groups.items():
        subs = [x for x in s.scalars(select(m.Submission).where(
            m.Submission.user_id == uid, m.Submission.server_id == sid, m.Submission.problem_id == pid)
            .order_by(m.Submission.submit_time, m.Submission.submission_id)).all() if x.submit_time is not None]
        steps.sort(key=lambda x: (x.created_at, x.id))
        before = 0
        for i, st in enumerate(steps):
            later = [x for x in subs if x.submit_time > st.created_at]
            nxt = later[0] if later else None
            following = steps[i + 1] if i + 1 < len(steps) else None
            if following is not None and (nxt is None or following.created_at < nxt.submit_time):
                before += 1   # 再交之前又给了帮助：算后面那一次
                continue
            ac_idx = next((j for j, x in enumerate(later) if x.judgement == "AC"), None)
            next_pass = None if nxt is None or nxt.judgement in _SKIP else nxt.judgement == "AC"
            error = st.root_error_code or ""
            gone = None
            if error and nxt is not None and next_pass is not None:
                if next_pass:
                    gone = True
                elif _labeled(s, nxt):
                    gone = error not in changes.error_codes(s, nxt)
            skill = st.root_skill_code or st.skill_code or ""
            out.append(HelpEpisode(
                step=st, level=st.help_level, skill=skill, kp=kp_of_skill(skill) if skill else "", error=error,
                helps_before=before, next_sub=nxt,
                minutes=round((nxt.submit_time - st.created_at).total_seconds() / 60, 1) if nxt else None,
                next_pass=next_pass, error_gone=gone, passed=ac_idx is not None,
                tries_to_pass=ac_idx + 1 if ac_idx is not None else None))
            before = 0
    return out


def _ratio(n: int, d: int) -> dict | None:
    return {"n": n, "d": d} if d else None


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 1) if xs else None


def summarize(eps: list[HelpEpisode]) -> list[dict]:
    """按帮助级别汇总（最后一行是全部）。每个比例都给分子分母。"""
    rows = []
    for level in (*LEVELS, None):
        es = [e for e in eps if level is None or e.level == level]
        if level is not None and not es:
            continue
        again = [e for e in es if e.next_sub is not None]
        judged = [e for e in again if e.next_pass is not None]
        known = [e for e in again if e.error_gone is not None]
        rows.append({
            "level": level or "all", "name": LEVEL_NAMES.get(level, "全部") if level else "全部", "n": len(es),
            "resubmitted": _ratio(len(again), len(es)),
            "nextPass": _ratio(sum(1 for e in judged if e.next_pass), len(judged)),
            "errorGone": _ratio(sum(1 for e in known if e.error_gone), len(known)),
            "passed": _ratio(sum(1 for e in es if e.passed), len(es)),
            "medianMinutes": _median([e.minutes for e in again if e.minutes is not None]),
            "medianTries": _median([float(e.tries_to_pass) for e in es if e.tries_to_pass is not None]),
        })
    return rows if eps else []


def export_row(e: HelpEpisode) -> dict:
    """研究导出用的一条（logs.py 的“帮助之后”）。"""
    return {"version": VERSION, "round": e.step.round_id, "step": e.step.id, "level": e.level,
            "skill": e.skill, "kp": e.kp, "error": e.error, "rule": e.step.rule_id, "replySource": e.step.reply_source,
            "helpsBefore": e.helps_before,
            "nextSubmission": e.next_sub.submission_id if e.next_sub else None, "minutes": e.minutes,
            "nextPass": e.next_pass, "errorGone": e.error_gone, "passed": e.passed, "triesToPass": e.tries_to_pass}


def happened_at(e: HelpEpisode) -> dt.datetime:
    return e.step.created_at
