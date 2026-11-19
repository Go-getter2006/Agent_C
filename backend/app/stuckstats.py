"""卡点按知识点汇总，带“卡住以后怎么样了”。每个数都能从记录复算，写清分子分母。

一次卡点（episode）：学生在求助回合里卡在某个技能上，智能体记下的一条 stuck_points。
同一个知识点下有几个技能，教师端按知识点汇总、展开再看技能。原始记录仍是技能一层，不丢。

指标（版本 stuck-kp-1，研究导出里同名）：
- 卡住人数、卡住次数：这个知识点上有卡点记录的学生数、卡点条数。
- 反复卡住：在这个知识点上卡住过两个及以上回合的学生数。
- 顽固：第三次及以上卡在同一个技能上（stuck_points.stubborn）的学生数。
- 第二次自己想起来：第二次卡在同一个技能上、智能体先请他回忆时，自己说出来的次数 / 第二次卡住且已经有结果的次数。
- 卡后通过：卡住时绑定了题、之后这道题在 XDOJ 上通过的次数 / 卡住时绑定了题的次数。
- 之后新题一次通过：第一次卡在这个知识点以后，才开始做的、主知识点是它的题里，第一次提交就通过的题数 / 这样的题数。
- 没来问：这个班布置的、主知识点是它的题上，通过前（或到现在）没通过的提交不少于 SILENT_FAILS 次、
  却没就这道题开过求助回合的学生数。智能体只看得到来问的学生，这一项补上只在 OJ 上反复试的。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .labeling.labeler import current_problem_label
from .taxonomy import KNOWLEDGE_POINTS, kp_of_skill, skill_name

VERSION = "stuck-kp-1"
SILENT_FAILS = 3
DONE = {"recalled", "blank_filled", "example_given", "exercise"}
_SKIP = {"PENDING", "UNKNOWN"}


@dataclass
class Ratio:
    n: int = 0
    d: int = 0

    def add(self, ok: bool) -> None:
        self.d += 1
        self.n += 1 if ok else 0

    def view(self) -> dict | None:
        return {"n": self.n, "d": self.d} if self.d else None


@dataclass
class Episode:
    """一次卡点和它之后的结果（研究导出也用这个）。"""
    sp: m.StuckPoint
    kp: str
    passed: bool | None = None        # None：卡住时没绑定题
    tries_after: int | None = None    # 卡住以后到通过（或到现在）交了几次
    passed_at: dt.datetime | None = None


@dataclass
class KpStat:
    kp: str
    episodes: list[Episode] = field(default_factory=list)
    recall: Ratio = field(default_factory=Ratio)
    passed: Ratio = field(default_factory=Ratio)
    transfer: Ratio = field(default_factory=Ratio)
    silent: dict[str, list[tuple[int, int, int]]] = field(default_factory=dict)   # 学生 → [(服务器, 题号, 没通过的次数)]

    @property
    def students(self) -> set[str]:
        return {e.sp.user_id for e in self.episodes}


class _Labels:
    def __init__(self, s: Session):
        self.s, self.cache = s, {}

    def kp(self, server_id: int | None, problem_id: int | None) -> str | None:
        if server_id is None or problem_id is None:
            return None
        key = (server_id, problem_id)
        if key not in self.cache:
            label = current_problem_label(self.s, server_id, problem_id)
            self.cache[key] = kp_of_skill(label.primary_skill) if label is not None and label.primary_skill else None
        return self.cache[key]


def compute(s: Session, user_ids: list[str], course: str, problems: list[tuple[int, int]] | None = None,
            since: dt.datetime | None = None) -> dict[str, KpStat]:
    """user_ids 这些学生在这门课上的卡点，按知识点汇总。problems：这个班布置的题（算“没来问”用；None 不算）。"""
    out: dict[str, KpStat] = {}
    if not user_ids:
        return out
    q = select(m.StuckPoint).where(m.StuckPoint.user_id.in_(user_ids), m.StuckPoint.skill_code.like(course + "%"))
    if since is not None:
        q = q.where(m.StuckPoint.created_at >= since)
    points = list(s.scalars(q.order_by(m.StuckPoint.id)).all())
    servers = {sp.server_id for sp in points if sp.server_id is not None} | {sv for sv, _p in problems or []}
    subs: dict[tuple[str, int, int], list[m.Submission]] = defaultdict(list)
    if servers:
        for sub in s.scalars(select(m.Submission).where(
                m.Submission.user_id.in_(user_ids), m.Submission.server_id.in_(servers))
                .order_by(m.Submission.submission_id)).all():
            if sub.judgement not in _SKIP:
                subs[(sub.user_id, sub.server_id, sub.problem_id)].append(sub)
    labels = _Labels(s)

    first_at: dict[tuple[str, str], dt.datetime] = {}
    for sp in points:
        kp = kp_of_skill(sp.skill_code)
        st = out.setdefault(kp, KpStat(kp=kp))
        ep = Episode(sp=sp, kp=kp)
        if sp.problem_id is not None and sp.server_id is not None:
            after = [x for x in subs.get((sp.user_id, sp.server_id, sp.problem_id), [])
                     if x.submit_time is not None and x.submit_time >= sp.created_at]
            ac = next((i for i, x in enumerate(after) if x.judgement == "AC"), None)
            ep.passed = bool(sp.passed_after) or ac is not None
            ep.tries_after = (ac + 1) if ac is not None else len(after)
            ep.passed_at = after[ac].submit_time if ac is not None else None
            st.passed.add(ep.passed)
        if sp.nth == 2 and sp.resolution in DONE:
            st.recall.add(sp.resolution == "recalled")
        st.episodes.append(ep)
        first_at.setdefault((sp.user_id, kp), sp.created_at)

    # 之后新题一次通过：第一次卡在这个知识点以后才开始做的、主知识点是它的题
    stuck_problems = {(sp.user_id, sp.server_id, sp.problem_id) for sp in points}
    for (uid, server_id, problem_id), rows in subs.items():
        kp = labels.kp(server_id, problem_id)
        t0 = first_at.get((uid, kp)) if kp else None
        if t0 is None or (uid, server_id, problem_id) in stuck_problems:
            continue
        first = next((x for x in rows if x.submit_time is not None), None)
        if first is not None and first.submit_time > t0:
            out[kp].transfer.add(first.judgement == "AC")

    if problems:
        asked = set(s.execute(select(m.ChatRound.user_id, m.ChatRound.server_id, m.ChatRound.problem_id).where(
            m.ChatRound.user_id.in_(user_ids), m.ChatRound.problem_id.is_not(None),
            m.ChatRound.end_reason != m.REFLECT_END)).all())   # 通过后的回顾不算“来问过”
        for uid in user_ids:
            for server_id, problem_id in problems:
                rows = subs.get((uid, server_id, problem_id), [])
                ac = next((i for i, x in enumerate(rows) if x.judgement == "AC"), None)
                fails = ac if ac is not None else len(rows)
                if fails < SILENT_FAILS or (uid, server_id, problem_id) in asked:
                    continue
                kp = labels.kp(server_id, problem_id)
                if kp is None or (since is not None and (rows[-1].submit_time or since) < since):
                    continue
                st = out.setdefault(kp, KpStat(kp=kp))
                st.silent.setdefault(uid, []).append((server_id, problem_id, fails))
    return out


def episodes_by_round(stats: dict[str, KpStat]) -> dict[str, dict[str, int]]:
    """每个知识点、每个学生卡住过几个回合。"""
    out: dict[str, dict[str, int]] = {}
    for kp, st in stats.items():
        rounds: dict[str, set] = defaultdict(set)
        for e in st.episodes:
            rounds[e.sp.user_id].add(e.sp.round_id or -e.sp.id)
        out[kp] = {u: len(r) for u, r in rounds.items()}
    return out


def kp_rows(s: Session, stats: dict[str, KpStat], names: dict[str, str]) -> list[dict]:
    """教师端：每个知识点一行，展开看技能和学生。按卡住人数（加没来问的）排。"""
    titles: dict = {}

    def title(server_id, problem_id) -> str:
        if problem_id is None or server_id is None:
            return ""
        if (server_id, problem_id) not in titles:
            p = s.get(m.Problem, (server_id, problem_id))
            titles[(server_id, problem_id)] = p.title if p else ""
        return titles[(server_id, problem_id)]

    rounds = episodes_by_round(stats)
    rows = []
    for kp, st in stats.items():
        by_skill: dict[str, list[Episode]] = defaultdict(list)
        by_student: dict[str, list[Episode]] = defaultdict(list)
        for e in st.episodes:
            by_skill[e.sp.skill_code].append(e)
            by_student[e.sp.user_id].append(e)
        students = []
        for uid, eps in by_student.items():
            last = eps[-1]
            students.append({
                "userId": uid, "name": names.get(uid, ""), "times": len(eps), "rounds": rounds[kp].get(uid, 0),
                "stubborn": any(e.sp.stubborn for e in eps),
                "skills": sorted({skill_name(e.sp.skill_code) for e in eps}),
                "lastTime": last.sp.created_at.isoformat(), "lastProblem": title(last.sp.server_id, last.sp.problem_id),
                "resolution": last.sp.resolution, "passed": last.passed, "triesAfter": last.tries_after,
                "silent": len(st.silent.get(uid, []))})
        students.sort(key=lambda r: (not r["stubborn"], -r["rounds"], -r["times"], r["lastTime"]))
        silent = [{"userId": uid, "name": names.get(uid, ""),
                   "problems": [{"title": title(sv, pid), "problemId": pid, "fails": n} for sv, pid, n in items]}
                  for uid, items in st.silent.items() if uid not in by_student]
        silent.sort(key=lambda r: -max(p["fails"] for p in r["problems"]))
        rows.append({
            "code": kp, "name": KNOWLEDGE_POINTS.get(kp, ""),
            "students": len(by_student), "episodes": len(st.episodes),
            "repeat": sum(1 for n in rounds[kp].values() if n >= 2),
            "stubborn": len({e.sp.user_id for e in st.episodes if e.sp.stubborn}),
            "recall": st.recall.view(), "passed": st.passed.view(), "transfer": st.transfer.view(),
            "silent": len(silent),
            "skills": sorted(({"code": c, "name": skill_name(c), "students": len({e.sp.user_id for e in eps}),
                               "episodes": len(eps)} for c, eps in by_skill.items()), key=lambda x: -x["episodes"]),
            "studentRows": students, "silentRows": silent,
            "lastTime": max((e.sp.created_at for e in st.episodes), default=None),
        })
    for r in rows:
        r["lastTime"] = r["lastTime"].isoformat() if r["lastTime"] else None
    rows.sort(key=lambda r: (-r["stubborn"], -(r["students"] + r["silent"]), -r["episodes"], r["code"]))
    return rows
