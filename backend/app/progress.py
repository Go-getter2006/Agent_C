"""学生端的学情页：一页四块。只统计和展示这个学生可见的题。

不返回：概率数值、技能（细）、内部编码、知识点图、班级排名。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, db, mastery, quiz
from . import models as m
from .labeling.labeler import current_problem_label
from .taxonomy import (KNOWLEDGE_POINTS, LEVEL_NONE, LEVEL_SEGMENTS, LEVEL_WEAK, TOPICS, error_name, kp_of_skill)


def level_view(level: str) -> dict:
    return {"level": level, "segments": LEVEL_SEGMENTS.get(level, 0)}


class Snapshot:
    """把算学情页要用的东西一次取齐。"""

    def __init__(self, s: Session, user_id: str, now: dt.datetime | None = None):
        self.s, self.user_id, self.now = s, user_id, now or db.now()
        self.visible = catalog.visible_problems(s, user_id)
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
        self.kp = mastery.kp_states(s, user_id)
        self.stuck = s.scalars(select(m.StuckPoint).where(m.StuckPoint.user_id == user_id)
                               .order_by(m.StuckPoint.id)).all()

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
    """知识点掌握：粗知识点，每个一档，加一句依据和“去练一题”。按主题分组。"""
    kps: dict[str, list] = {}
    for key in snap.info:
        for kp in snap.kps_of(key):
            kps.setdefault(kp, []).append(key)
    groups: dict[str, list[dict]] = {}
    for kp in sorted(kps):
        keys = kps[kp]
        tried = [k for k in keys if snap.subs.get(k)]
        first = sum(1 for k in tried if snap.first_try(k))
        stuck = sum(1 for p in snap.stuck if kp_of_skill(p.skill_code) == kp)
        state = snap.kp.get(kp)
        level = state.level if state else LEVEL_NONE
        if not tried:
            basis = "还没有做过这类题"
        elif level == LEVEL_NONE:
            basis = f"做过 {len(tried)} 题，还不够判断"
        else:
            basis = f"做过 {len(tried)} 题，{first} 题一次通过"
            if stuck:
                basis += f"，卡住过 {stuck} 次"
        practice = snap.practice_for(kp)
        groups.setdefault(TOPICS.get(kp[:3], ""), []).append({
            "name": KNOWLEDGE_POINTS.get(kp, ""), **level_view(level), "basis": basis,
            "practice": snap.problem_view(practice) if practice else None})
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
    for c in sorted(catalog.visible_contests(snap.s, snap.user_id),
                    key=lambda c: (c.end_time is not None and c.end_time < snap.now, c.end_time or snap.now)):
        pids = catalog.contest_problem_ids(snap.s, c.server_id, c.contest_id)
        keys = [(c.server_id, pid) for pid in pids if (c.server_id, pid) in snap.info]
        homeworks.append({
            "key": catalog.homework_key(c.server_id, c.contest_id), "title": c.title,
            "passed": sum(1 for k in keys if snap.passed(k)), "total": len(keys),
            "endTime": c.end_time.isoformat() if c.end_time else None,
            "ended": bool(c.end_time and c.end_time < snap.now), "lastSubmit": None})
    tried = [k for k in snap.info if snap.subs.get(k)]
    first = sum(1 for k in tried if snap.first_try(k))
    total_subs = sum(len(snap.subs[k]) for k in tried)
    # 按周：本周一 0 点起算，往前共 4 周
    local = snap.now.astimezone(db.BEIJING)
    monday = (local - dt.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = []
    for i in range(4):
        start, end = monday - dt.timedelta(weeks=i), monday - dt.timedelta(weeks=i - 1)
        n_subs = n_pass = 0
        for k in tried:
            subs = snap.subs[k]
            n_subs += sum(1 for x in subs if x.submit_time and start <= x.submit_time < end)
            first_ac = next((x for x in subs if x.judgement == "AC"), None)
            if first_ac is not None and first_ac.submit_time and start <= first_ac.submit_time < end:
                n_pass += 1
        label = ["本周", "上周", "两周前", "三周前"][i]
        weeks.append({"label": label, "submissions": n_subs, "passed": n_pass})
    return {
        "homeworks": homeworks,
        "tried": len(tried),
        "passed": sum(1 for k in tried if snap.passed(k)),
        "firstTryPassed": first,
        "firstTryRate": round(100 * first / len(tried)) if tried else None,
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
    due = snap.s.scalars(select(m.RevisitItem).where(
        m.RevisitItem.user_id == snap.user_id, m.RevisitItem.status == "due", m.RevisitItem.due_at <= snap.now
    ).order_by(m.RevisitItem.due_at)).all()
    seen_skill: set[str] = set()
    for rv in due:
        if len(out) >= 3 or rv.skill_code in seen_skill:
            continue
        seen_skill.add(rv.skill_code)
        if not quiz.can_start(snap.s, snap.user_id, rv.skill_code, snap.now):
            continue
        kp = KNOWLEDGE_POINTS.get(kp_of_skill(rv.skill_code), "")
        origin = next((p for p in reversed(snap.stuck) if p.skill_code == rv.skill_code and p.problem_id is not None
                       and (p.server_id, p.problem_id) in snap.info), None)
        where = f"你在{snap.info[(origin.server_id, origin.problem_id)].ref}卡过。" if origin else ""
        out.append({"kind": "quiz", "revisitId": rv.id, "title": f"回访小题：{kp}",
                    "reason": f"{where}做一道小题，看看现在是不是真的会了。"})
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


def build(s: Session, user_id: str) -> dict:
    snap = Snapshot(s, user_id)
    errors = error_block(snap)
    nxt = next_block(snap, errors)
    for row in errors:
        row.pop("_code", None)
        row.pop("_key", None)
    return {"knowledge": knowledge_block(snap), "errors": errors, "activity": activity_block(snap), "next": nxt}


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


def start_revisit(s: Session, user_id: str, revisit_id: int) -> m.ChatMessage | None:
    """做一道到期的回访题：出在不绑定题目的线程里。"""
    rv = s.get(m.RevisitItem, revisit_id)
    now = db.now()
    if rv is None or rv.user_id != user_id or rv.status != "due" or rv.due_at > now:
        return None
    if rv.attempt_id is not None:
        return s.scalar(select(m.ChatMessage).where(m.ChatMessage.quiz_attempt_id == rv.attempt_id))
    if not quiz.can_start(s, user_id, rv.skill_code, now):
        return None
    used = set(s.scalars(select(m.QuizItem.scene).join(m.QuizAttempt, m.QuizAttempt.item_id == m.QuizItem.id).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.skill_code == rv.skill_code)).all())
    item = quiz.choose_item(s, user_id, rv.skill_code, exclude_scenes=used if rv.stage == "variant" else None)
    if item is None:
        rv.status = "dropped"  # 题库里还没有这个技能的小题
        return None
    origin = s.scalars(select(m.StuckPoint).where(
        m.StuckPoint.user_id == user_id, m.StuckPoint.skill_code == rv.skill_code).order_by(m.StuckPoint.id.desc())).first()
    lead = "来一道回访小题："
    if rv.stage == "variant":
        lead = "再来一道，换了个场景："
    elif origin is not None and origin.problem_id is not None and catalog.is_visible(s, user_id, origin.server_id, origin.problem_id):
        info = catalog.problem_info(s, origin.server_id, origin.problem_id)
        lead = f"前几天你在{info.ref}卡过。回忆一下，做这道小题："
    msg = quiz.start(s, user_id, item, "recall" if rv.stage == "recall" else "revisit", lead, None, None, None, now)
    rv.attempt_id = msg.quiz_attempt_id
    return msg
