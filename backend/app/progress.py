"""学生端的学情页：概况、接下来做什么、知识点地图、常见错误、做题情况。只统计这个学生可见的题。

只看这次登录的课程。不返回：概率数值、技能（细）、内部编码、知识点之间的连线、另一门课的答题记录、班级排名。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, db, mastery, profile, review
from . import models as m
from .labeling.labeler import current_problem_label
from .taxonomy import (KNOWLEDGE_POINTS, LEVEL_BASIC, LEVEL_MASTERED, LEVEL_NONE, LEVEL_SEGMENTS, LEVEL_WEAK, TOPICS,
                       course_of, error_name, kp_of_skill)


def level_view(level: str) -> dict:
    return {"level": level, "segments": LEVEL_SEGMENTS.get(level, 0)}


class Snapshot:
    """把算学情页要用的东西一次取齐。"""

    def __init__(self, s: Session, user_id: str, now: dt.datetime | None = None, course: str = "C"):
        self.s, self.user_id, self.now, self.course = s, user_id, now or db.now(), course
        self.visible = catalog.visible_problems(s, user_id, course)
        self.info: dict[tuple[int, int], catalog.ProblemInfo] = {}
        self.skills: dict[tuple[int, int], list[str]] = {}
        self.subs: dict[tuple[int, int], list[m.Submission]] = {}
        for key in self.visible:
            info = catalog.problem_info(s, *key)
            if info is None:
                continue
            self.info[key] = info
            label = current_problem_label(s, *key)
            self.skills[key] = ([label.primary_skill] + list(label.secondary_skills or [])) if label else []
            self.subs[key] = catalog.user_submissions(s, user_id, *key)
        self.kp = {k: v for k, v in mastery.kp_states(s, user_id).items() if course_of(k) == course}
        self.profile = profile.kp_profile(s, user_id, course, self.now)
        self.stuck = [p for p in s.scalars(select(m.StuckPoint).where(m.StuckPoint.user_id == user_id)
                                           .order_by(m.StuckPoint.id)).all() if course_of(p.skill_code) == course]

    def passed(self, key) -> bool:
        return any(x.judgement == "AC" for x in self.subs.get(key, []))

    def first_try(self, key) -> bool:
        subs = self.subs.get(key, [])
        return bool(subs) and subs[0].judgement == "AC"

    def kps_of(self, key) -> list[str]:
        return list(dict.fromkeys(kp_of_skill(c) for c in self.skills.get(key, [])))

    def practice_for(self, kp: str, exclude: set | None = None):
        """这个知识点“去练一题”：先找主技能就是它、还没通过的题；没做过的优先。"""
        best, best_rank = None, None
        for key, skills in self.skills.items():
            if self.passed(key) or (exclude and key in exclude) or not skills:
                continue
            if kp not in [kp_of_skill(c) for c in skills]:
                continue
            rank = (0 if kp_of_skill(skills[0]) == kp else 1, 0 if not self.subs.get(key) else 1, key)
            if best_rank is None or rank < best_rank:
                best, best_rank = key, rank
        return best

    def problem_view(self, key) -> dict:
        info = self.info[key]
        return {"key": info.key, "title": info.title, "brief": info.brief}


def knowledge_block(snap: Snapshot) -> list[dict]:
    """知识点地图：本课程全部知识点按主题排开，每个一档，加一句依据和“去练一题”。

    作业还没涉及的知识点也列出来（covered=False），让学生看到这门课的全貌；不显示知识点之间的连线和概率。
    """
    kps: dict[str, list] = {}
    for key in snap.info:
        for kp in snap.kps_of(key):
            kps.setdefault(kp, []).append(key)
    course = snap.course
    groups: dict[str, list[dict]] = {}
    for kp in sorted(k for k in KNOWLEDGE_POINTS if course_of(k) == course):
        keys = kps.get(kp, [])
        tried = [k for k in keys if snap.subs.get(k)]
        passed = sum(1 for k in tried if snap.passed(k))
        stuck = sum(1 for p in snap.stuck if kp_of_skill(p.skill_code) == kp)
        state = snap.kp.get(kp)
        level = state.level if state else LEVEL_NONE
        if not keys:
            basis = "作业里还没有这类题"
        elif not tried:
            basis = f"作业里有 {len(keys)} 道，还没有做"
        elif level == LEVEL_NONE:
            basis = f"做过 {len(tried)} 题，还不够判断"
        else:
            basis = f"做过 {len(tried)} 题，通过 {passed} 题"
            if stuck:
                basis += f"，卡住过 {stuck} 次"
        practice = snap.practice_for(kp) if keys else None
        groups.setdefault(TOPICS.get(kp[:3], ""), []).append({
            "name": KNOWLEDGE_POINTS.get(kp, ""), **level_view(level), "basis": basis, "covered": bool(keys),
            "practice": snap.problem_view(practice) if practice else None,
            **profile.student_dims(snap.profile.get(kp))})
    return [{"topic": topic, "items": items} for topic, items in groups.items()]


def error_block(snap: Snapshot, days: int = 30, top: int = 5) -> list[dict]:
    """我的常见错误：最近 30 天最多的几种，名称、次数、最近一次在哪道题、状态。"""
    since = snap.now - dt.timedelta(days=days)
    occurrences: dict[str, list[tuple[dt.datetime, tuple[int, int], bool]]] = {}
    for key, subs in snap.subs.items():
        coded: list[tuple[m.Submission, set[str] | None]] = []
        for sub in subs:
            if sub.judgement == "AC":
                coded.append((sub, set()))
                continue
            labels = snap.s.scalars(select(m.SubmissionLabel).where(
                m.SubmissionLabel.server_id == sub.server_id, m.SubmissionLabel.submission_id == sub.submission_id,
                m.SubmissionLabel.is_current.is_(True))).all()
            # 置信度低于 0.5 的标注照样保存，但不用于学生端显示
            codes = {lab.error_code for lab in labels if lab.confidence >= 0.5 and lab.error_code != "OT99"}
            coded.append((sub, codes if labels else None))  # None：还没标，看不出改没改
        for i, (sub, codes) in enumerate(coded):
            if not codes or not sub.submit_time:
                continue
            for code in codes:
                # 已改正：同一道题后面的提交通过了，或者后面的提交里不再有这种错误
                fixed = any(later is not None and code not in later for _s, later in coded[i + 1:])
                occurrences.setdefault(code, []).append((sub.submit_time, key, fixed))
    rows = []
    for code, occ in occurrences.items():
        occ.sort()
        recent = [o for o in occ if o[0] >= since]
        if not recent:
            continue
        last = occ[-1]
        if last[2]:
            status = "已改正"
        elif any(o[2] for o in occ[:-1]):
            status = "改正后又出现"
        else:
            status = "还没解决"
        rows.append({"name": error_name(code, student_facing=True), "count": len(recent),
                     "last": snap.problem_view(last[1]), "lastTime": last[0].isoformat(), "status": status,
                     "_code": code, "_key": last[1]})
    rows.sort(key=lambda r: (-r["count"], -dt.datetime.fromisoformat(r["lastTime"]).timestamp()))
    return rows[:top]


def activity_block(snap: Snapshot) -> dict:
    """做题情况：每个作业的完成进度；首次提交通过率；平均每题提交几次；按周的变化。"""
    homeworks = []
    for c in sorted(catalog.visible_contests(snap.s, snap.user_id, snap.course),
                    key=lambda c: (c.end_time is not None and c.end_time < snap.now, c.end_time or snap.now)):
        pids = catalog.contest_problem_ids(snap.s, c.server_id, c.contest_id)
        keys = [(c.server_id, pid) for pid in pids if (c.server_id, pid) in snap.info]
        homeworks.append({
            "key": catalog.homework_key(c.server_id, c.contest_id), "title": c.title,
            "passed": sum(1 for k in keys if snap.passed(k)), "total": len(keys),
            "endTime": c.end_time.isoformat() if c.end_time else None,
            "ended": bool(c.end_time and c.end_time < snap.now), "lastSubmit": None})
    tried = [k for k in snap.info if snap.subs.get(k)]
    # 通过的题平均交几次才通过（不看“第一次就通过”：OJ 上先交上去看看很正常，改对的过程更说明问题）
    tries = [next(i for i, x in enumerate(snap.subs[k], start=1) if x.judgement == "AC") for k in tried if snap.passed(k)]
    total_subs = sum(len(snap.subs[k]) for k in tried)
    # 按周：本周一 0 点起算，往前共 8 周（旧的在前）
    local = snap.now.astimezone(db.BEIJING)
    monday = (local - dt.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = []
    for i in range(7, -1, -1):
        start, end = monday - dt.timedelta(weeks=i), monday - dt.timedelta(weeks=i - 1)
        n_subs = n_pass = 0
        for k in tried:
            subs = snap.subs[k]
            n_subs += sum(1 for x in subs if x.submit_time and start <= x.submit_time < end)
            first_ac = next((x for x in subs if x.judgement == "AC"), None)
            if first_ac is not None and first_ac.submit_time and start <= first_ac.submit_time < end:
                n_pass += 1
        label = "本周" if i == 0 else "上周" if i == 1 else f"{start.month}/{start.day}"
        weeks.append({"label": label, "start": start.date().isoformat(), "submissions": n_subs, "passed": n_pass})
    return {
        "homeworks": homeworks,
        "tried": len(tried),
        "passed": sum(1 for k in tried if snap.passed(k)),
        "avgToPass": round(sum(tries) / len(tries), 1) if tries else None,
        "avgSubmissions": round(total_subs / len(tried), 1) if tried else None,
        "weeks": weeks,
    }


def next_block(snap: Snapshot, errors: list[dict]) -> list[dict]:
    """接下来做什么：2~3 条，每条指向一道具体的题或回访题；顽固卡点排第一。"""
    out: list[dict] = []
    used: set = set()

    def add_problem(key, reason: str) -> None:
        if key is None or key in used or len(out) >= 3:
            return
        used.add(key)
        out.append({"kind": "problem", "problem": snap.problem_view(key), "reason": reason})

    for sp in [p for p in snap.stuck if p.stubborn]:
        kp = kp_of_skill(sp.skill_code)
        add_problem(snap.practice_for(kp, used), f"“{KNOWLEDGE_POINTS.get(kp, '')}”已经卡住好几次了，建议再练一题，也可以找老师当面讲一遍。")
    for rv in review.due_items(snap.s, snap.user_id, snap.now, snap.course):
        if len(out) >= 3:
            break
        kp = KNOWLEDGE_POINTS.get(kp_of_skill(rv.skill_code), "")
        ref = review._origin_ref(snap.s, snap.user_id, rv)
        where = f"你在「{ref['title']}」（{ref['brief']}）里{review.ORIGIN_TEXT.get(rv.origin, '卡过')}。" if ref else ""
        out.append({"kind": "review", "reviewId": rv.id, "title": f"复习：{kp}",
                    "reason": f"{where}现在{review.memory(rv, snap.now)['text']}，做一道小题巩固一下。"})
    for row in errors:
        if row["status"] == "已改正":
            continue
        key = row["_key"]
        if not snap.passed(key):
            add_problem(key, f"“{row['name']}”这个问题{'改正后又出现了' if row['status'] == '改正后又出现' else '还没解决'}，这道题还没通过。")
    for kp, state in sorted(snap.kp.items(), key=lambda kv: kv[1].p):
        if state.level == LEVEL_WEAK:
            add_problem(snap.practice_for(kp, used), f"“{KNOWLEDGE_POINTS.get(kp, '')}”还需要加强，这道题正好练它。")
    return out[:3]


def foundation_block(snap: Snapshot) -> list[dict]:
    """数据结构这边：作业里要学的知识点用到的 C 语言基础里，还薄弱的。只有档位，不出现 C 语言的答题记录。"""
    if snap.course != "D":
        return []
    from . import cross

    covered = {kp for key in snap.info for kp in snap.kps_of(key)}
    out = []
    for r in cross.weak_foundation(cross.c_foundation(snap.s, snap.user_id), covered or None):
        supports = [x["name"] for x in r["supports"] if not covered or x["code"] in covered]
        out.append({"name": r["name"], **level_view(r["level"]), "supports": supports,
                    "text": f"学「{'」「'.join(supports[:2])}」要用到，C 语言里这块还要再练练"})
    return out[:6]


def build(s: Session, user_id: str, course: str = "C") -> dict:
    snap = Snapshot(s, user_id, course=course)
    errors = error_block(snap)
    nxt = next_block(snap, errors)
    for row in errors:
        row.pop("_code", None)
        row.pop("_key", None)
    activity = activity_block(snap)
    weeks = activity["weeks"]
    rv = review.summary(s, user_id, snap.now, course)
    levels = [state.level for state in snap.kp.values()]
    return {
        "summary": {
            "passed": sum(1 for k in snap.info if snap.passed(k)), "total": len(snap.info),
            "avgToPass": activity["avgToPass"], "avgSubmissions": activity["avgSubmissions"],
            "thisWeek": weeks[-1]["submissions"], "lastWeek": weeks[-2]["submissions"],
            "reviewDue": len(rv["due"]), "reviewActive": rv["active"],
            "levels": {lv: levels.count(lv) for lv in (LEVEL_MASTERED, LEVEL_BASIC, LEVEL_WEAK)},
        },
        "knowledge": knowledge_block(snap), "errors": errors, "activity": activity, "next": nxt,
        "foundation": foundation_block(snap)}


def problem_knowledge(s: Session, user_id: str, server_id: int, problem_id: int, limit: int = 4) -> list[dict]:
    """右栏“这道题用到的知识点”：粗知识点、档位。"""
    label = current_problem_label(s, server_id, problem_id)
    if label is None:
        return []
    states = mastery.kp_states(s, user_id)
    out = []
    for kp in dict.fromkeys(kp_of_skill(c) for c in [label.primary_skill, *(label.secondary_skills or [])]):
        state = states.get(kp)
        out.append({"name": KNOWLEDGE_POINTS.get(kp, ""), **level_view(state.level if state else LEVEL_NONE)})
    return out[:limit]
