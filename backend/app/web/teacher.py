"""教师端接口：班级概况、学生列表和明细、顽固卡点、图模型结果（试验中）、数据源。

教师只看自己的班（按 class_sources 控制）；管理员看全部。教师端可以看到题号、内部编码和概率数值。

C 语言和数据结构分开登录（user.course 来自登录凭证）：
- C 语言：只有 C 语言的班、提交、知识点、卡点、对话，任何地方都不出现数据结构；
- 数据结构：只有数据结构的班和记录；可以看学生在 C 语言里相关知识点的掌握程度（只有档位和估计），
  不出现 C 语言的答题记录（提交、对话、卡点、小题）。
"""
from __future__ import annotations

import datetime as dt
import functools
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import (accounts, assess, catalog, config, cross, crypto, db, diagnosis, graphmodel, kpdesc, logs, mastery, profile,
               rating, review, stuckstats)
from .. import models as m
from ..labeling.labeler import current_problem_label
from ..taxonomy import (COURSE_NAMES, ERRORS, JUDGEMENTS, KNOWLEDGE_POINTS, LEVELS, SKILLS, TOPICS, course_of,
                        error_name, kp_of_skill, skill_name, skills_of_kp)
from ..taxonomy.prereq import PREREQ_STATUS
from ..xdoj import api as sync_api
from ..xdoj import store
from ..xdoj.client import SessionError, XdojClient, XdojError, limiter_for
from .auth import record_failure, set_login_cookie, throttle
from . import cache
from .deps import get_session, teacher


def _memo_states(s: Session = Depends(get_session)) -> None:
    """教师端的页面只读学生的状态，同一个请求里每个学生的状态只算一次（mastery.memoize）。"""
    mastery.memoize(s)


router = APIRouter(prefix="/api/teacher", tags=["teacher"], dependencies=[Depends(_memo_states)])
_test_transport = None  # 测试时注入假的 XDOJ


# ------------------------------------------------------------ 登录
class TeacherLogin(BaseModel):
    server: str
    userId: str
    password: str


@router.get("/servers")
def servers(s: Session = Depends(get_session)) -> dict:
    """教师登录时可以选的 XDOJ 服务器。地址由管理员事先配好，登录页不接受随手填的地址。"""
    rows = s.scalars(select(m.Server).where(m.Server.code != "demo", m.Server.base_url != "").order_by(m.Server.id)).all()
    return {"servers": [{"code": r.code, "name": r.name} for r in rows]}


class NotTeacher(Exception):
    """XDOJ 登录成功，但这不是教师账号（userType 不是 1）。"""


def verify_on(server: m.Server, user_id: str, password: str, key: str) -> str | None:
    """去这台 XDOJ 试登教师账号，返回姓名（可能读不到）。不是教师抛 NotTeacher；密码错、连不上抛 HTTPException。"""
    client = XdojClient(server.base_url, user_id, password, limiter=limiter_for(server.base_url),
                        transport=_test_transport, max_retries=1)
    try:
        client.login()
        if client.user_type is not None and str(client.user_type) != "1":
            raise NotTeacher()
        return client.teacher_name()
    except SessionError:
        record_failure(key)
        raise HTTPException(status_code=401, detail="账号或密码不对。用的是 XDOJ 的账号和密码。")
    except XdojError:
        raise HTTPException(status_code=502, detail="XDOJ 暂时连不上，过一会儿再试。")
    finally:
        client.close()


def _verify_teacher(s: Session, body: TeacherLogin, key: str) -> tuple[m.Server, str, str | None]:
    """去 XDOJ 试登这个教师账号。返回 (服务器, XDOJ 账号, 姓名)。不是教师账号、密码错、连不上都抛出。"""
    user_id = body.userId.strip()
    server = s.scalar(select(m.Server).where(m.Server.code == body.server, m.Server.code != "demo"))
    if server is None or not server.base_url:
        raise HTTPException(status_code=400, detail="没有这台 XDOJ 服务器，请联系管理员先登记。")
    try:
        return server, user_id, verify_on(server, user_id, body.password, key)
    except NotTeacher:
        record_failure(key)
        raise HTTPException(status_code=403, detail="这不是 XDOJ 的教师账号。学生直接用学号登录。")


def _save_source(s: Session, server: m.Server, user_id: str, password: str) -> m.DataSource:
    source = s.scalar(select(m.DataSource).where(
        m.DataSource.server_id == server.id, m.DataSource.teacher_user_id == user_id))
    if source is None:
        source = m.DataSource(server_id=server.id, teacher_user_id=user_id)
        s.add(source)
    if not source.from_env:
        source.password_enc = crypto.encrypt_secret(password)  # 后台同步要长期使用，加密保存
    source.enabled, source.last_login_at = True, db.now()
    s.flush()
    return source


def _link(s: Session, user: m.User, source: m.DataSource) -> None:
    if s.get(m.TeacherSource, (user.id, source.id)) is None:
        s.add(m.TeacherSource(user_id=user.id, data_source_id=source.id))
    if user.data_source_id is None:
        user.data_source_id = source.id
    s.flush()


@router.post("/login")
def login(body: TeacherLogin, request: Request, response: Response, s: Session = Depends(get_session)) -> dict:
    """教师用某一门课的 XDOJ 账号登录（登录页的统一入口在 auth.login，这个接口保留给脚本和旧页面）。"""
    key = f"t:{request.client.host if request.client else ''}:{body.userId.strip()}"
    throttle(key)
    server, user_id, name = _verify_teacher(s, body, key)
    return finish_login(s, server, user_id, body.password, name, response)


def finish_login(s: Session, server: m.Server, user_id: str, password: str, name: str | None,
                 response: Response) -> dict:
    """XDOJ 已经确认是这台服务器的教师：登记数据源、建账号、马上同步名下的班级，进入这门课。

    两门课是两台 XDOJ，账号密码可能不同，分开登录：登录 C 语言就只看 C 语言，登录数据结构就只看数据结构。
    """
    source = _save_source(s, server, user_id, password)
    user = accounts.owner_of_source(s, source.id)
    if user is None:
        login_name = f"{server.code}:{user_id}"
        user = s.scalar(select(m.User).where(m.User.login_name == login_name))
        if user is None:
            user = m.User(role="teacher", login_name=login_name, display_name=name or user_id, data_source_id=source.id)
            s.add(user)
            s.flush()
    if name:
        user.display_name = name
    user.status, user.last_login_at = "active", db.now()
    _link(s, user, source)
    sync_api.request_meta_sync(s, server.id)  # 让主循环马上同步他名下的班级和名单
    set_login_cookie(response, user, server.course)
    return {"role": user.role, "name": user.display_name, "course": server.course}


# ------------------------------------------------------------ 权限
def class_key(server_id: int, class_id: int) -> str:
    return f"c{server_id}-{class_id}"


def course_server_ids(s: Session, course: str) -> list[int]:
    return list(s.scalars(select(m.Server.id).where(m.Server.course == course)).all())


def my_classes(s: Session, user: m.User) -> list[m.XClass]:
    """他名下、这次登录的这门课的班。"""
    servers = course_server_ids(s, user.course)
    if user.role == "admin":
        return list(s.scalars(select(m.XClass).where(m.XClass.server_id.in_(servers or [-1]))
                              .order_by(m.XClass.server_id, m.XClass.class_id)).all())
    ids = accounts.teacher_source_ids(s, user)
    if not ids:
        return []
    rows = s.scalars(
        select(m.XClass).join(m.ClassSource, (m.ClassSource.server_id == m.XClass.server_id)
                              & (m.ClassSource.class_id == m.XClass.class_id))
        .where(m.ClassSource.data_source_id.in_(ids), m.XClass.server_id.in_(servers or [-1]))
        .order_by(m.XClass.server_id, m.XClass.class_id)).all()
    return list(dict.fromkeys(rows))


def in_course(code: str | None, course: str) -> bool:
    """技能、知识点编码属于这门课。"""
    return bool(code) and course_of(code) == course


def _class(s: Session, user: m.User, key: str) -> m.XClass:
    parsed = catalog.parse_key(key, "c")
    found = next((c for c in my_classes(s, user) if parsed == (c.server_id, c.class_id)), None)
    if found is None:
        raise HTTPException(status_code=404, detail="没有这个班，或者它不在你名下。")
    return found


def page_cache(name: str):
    """班级汇总页：先核对是不是他的班，再按（页面, 课程, 参数）查结果缓存（app/web/cache.py）。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(**kwargs):
            s, user = kwargs["s"], kwargs["user"]
            _class(s, user, kwargs["key"])
            extra = tuple(sorted((k, v) for k, v in kwargs.items() if k not in ("s", "user")))
            return cache.cached(s, (name, user.course, *extra), lambda: fn(**kwargs))
        return wrapper
    return deco


def class_students(s: Session, cls: m.XClass) -> list[m.Student]:
    return list(s.scalars(
        select(m.Student).join(m.Enrollment, m.Enrollment.user_id == m.Student.user_id)
        .where(m.Enrollment.server_id == cls.server_id, m.Enrollment.class_id == cls.class_id,
               m.Enrollment.status == "active").order_by(m.Student.user_id)).all())


def class_problems(s: Session, cls: m.XClass) -> list[tuple[int, int]]:
    rows = s.execute(
        select(m.ContestProblem.server_id, m.ContestProblem.problem_id)
        .join(m.ContestClass, (m.ContestClass.server_id == m.ContestProblem.server_id)
              & (m.ContestClass.contest_id == m.ContestProblem.contest_id))
        .where(m.ContestClass.server_id == cls.server_id, m.ContestClass.class_id == cls.class_id)).all()
    return list(dict.fromkeys((r[0], r[1]) for r in rows))


def assigned_topics(s: Session, problems, course: str) -> dict:
    by_problem = {}
    for key in problems:
        label = current_problem_label(s, *key)
        skills = [label.primary_skill, *(label.secondary_skills or [])] if label else []
        by_problem[key] = {kp_of_skill(c) for c in skills if c and in_course(c, course)}
    return by_problem


def class_kps(s: Session, cls: m.XClass) -> set[str]:
    topics = assigned_topics(s, class_problems(s, cls), _course_of_class(s, cls))
    return set().union(*topics.values())


def assignment_mastery(topics, submissions, states) -> dict:
    """班级作业综合分，练习广度与已练部分的掌握质量分别展示。"""
    practiced = {(x.server_id, x.problem_id) for x in submissions
                 if x.judgement in JUDGEMENTS and x.judgement not in ("UNKNOWN", "PENDING")}
    practiced &= topics.keys()
    assigned = set().union(*topics.values())
    reached = set().union(*(topics[key] for key in practiced))
    values = [states[c].score for c in reached if c in states and not states[c].predicted
              and states[c].score is not None and states[c].level != "还没练到"]
    quality = sum(values) / len(values) if values else None
    combined = None
    if topics and assigned and quality is not None:
        coverage = (len(practiced) / len(topics) + len(reached) / len(assigned)) / 2
        combined = score(quality * coverage)
    return {"score": combined, "quality": score(quality), "scoredTopics": len(values),
            "coveredTopics": len(reached), "totalTopics": len(assigned),
            "coveredProblems": len(practiced), "totalProblems": len(topics)}


def _student_in_my_classes(s: Session, user: m.User, user_id: str, class_key_filter: str | None = None) -> tuple[m.Student, m.XClass]:
    for cls in my_classes(s, user):
        if class_key_filter and class_key(cls.server_id, cls.class_id) != class_key_filter:
            continue
        e = s.scalar(select(m.Enrollment.id).where(
            m.Enrollment.user_id == user_id, m.Enrollment.server_id == cls.server_id,
            m.Enrollment.class_id == cls.class_id))
        if e is not None:
            student = s.get(m.Student, user_id)
            if student is not None:
                return student, cls
    raise HTTPException(status_code=404, detail="没有这个学生，或者他不在你的班里。")


def _skill_view(code: str) -> dict:
    return {"code": code, "name": skill_name(code), "kp": KNOWLEDGE_POINTS.get(kp_of_skill(code), ""),
            "course": course_of(code)}


# ------------------------------------------------------------ 班级
@router.get("/version")
def version(user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    """数据变没变（app/web/cache.py 的 version_tag）。教师端页面定时只问这个，变了才重取。"""
    return cache.version_tag(s)


@router.get("/classes")
def classes(user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    out = []
    for c in my_classes(s, user):
        server = s.get(m.Server, c.server_id)
        out.append({"key": class_key(c.server_id, c.class_id), "name": c.class_name,
                    "server": server.name if server else "", "students": len(class_students(s, c))})
    return {"classes": out, "name": user.display_name, "role": user.role, "course": user.course,
            "courseName": COURSE_NAMES.get(user.course, "")}


class _ClassData:
    """一个班的学生和他们的提交、学情，一次取齐，几个接口共用。"""

    def __init__(self, s: Session, cls: m.XClass):
        self.s, self.cls = s, cls
        self.course = _course_of_class(s, cls)
        self.students = class_students(s, cls)
        self.ids = [st.user_id for st in self.students]
        self.problems = class_problems(s, cls)
        self.problem_set = set(self.problems)
        self.subs: dict[str, dict[tuple[int, int], list[m.Submission]]] = {uid: defaultdict(list) for uid in self.ids}
        if self.ids:
            rows = s.scalars(select(m.Submission).where(
                m.Submission.server_id == cls.server_id, m.Submission.user_id.in_(self.ids)
            ).order_by(m.Submission.submission_id)).all()
            for sub in rows:
                if (sub.server_id, sub.problem_id) in self.problem_set:
                    self.subs[sub.user_id][(sub.server_id, sub.problem_id)].append(sub)
        course = self.course
        self.skills = {uid: {c: v for c, v in mastery.skill_states(s, uid).items() if in_course(c, course)}
                       for uid in self.ids}
        self.kps = {uid: {c: v for c, v in mastery.kp_states(s, uid).items() if in_course(c, course)}
                    for uid in self.ids}
        self.topics = assigned_topics(s, self.problems, course)
        self.focus = set().union(*self.topics.values())

    def stuck_points(self, since: dt.datetime | None = None) -> list[m.StuckPoint]:
        """这个班的学生在这门课上的卡点。"""
        if not self.ids:
            return []
        q = select(m.StuckPoint).where(m.StuckPoint.user_id.in_(self.ids),
                                       m.StuckPoint.skill_code.like(self.course + "%"))
        if since is not None:
            q = q.where(m.StuckPoint.created_at >= since)
        return list(self.s.scalars(q.order_by(m.StuckPoint.id)).all())


@router.get("/classes/{key}/overview")
@page_cache("overview")
def overview(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    cls = _class(s, user, key)
    data = _ClassData(s, cls)
    now = db.now()
    stats = stuckstats.compute(s, data.ids, data.course, data.problems)
    # 全班最常见的错误（最近 30 天）；也按知识点记下，给“要讲的知识点”配上最常见的错
    since = now - dt.timedelta(days=30)
    err_students: dict[str, set[str]] = defaultdict(set)
    err_count: Counter = Counter()
    kp_errors: dict[str, Counter] = defaultdict(Counter)
    if data.ids:
        rows = s.execute(
            select(m.SubmissionLabel.error_code, m.SubmissionLabel.skill_code, m.Submission.user_id)
            .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
                  & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
            .where(m.SubmissionLabel.is_current.is_(True), m.Submission.server_id == cls.server_id,
                   m.Submission.user_id.in_(data.ids), m.Submission.submit_time >= since)).all()
        for code, skill, uid in rows:
            err_students[code].add(uid)
            err_count[code] += 1
            if skill:
                kp_errors[kp_of_skill(skill)][code] += 1
    errors = [{"code": code, "name": error_name(code), "students": len(err_students[code]), "count": n}
              for code, n in err_count.most_common(10)]
    # 作业完成情况
    homeworks = []
    contests = s.scalars(
        select(m.Contest).join(m.ContestClass, (m.ContestClass.server_id == m.Contest.server_id)
                               & (m.ContestClass.contest_id == m.Contest.contest_id))
        .where(m.ContestClass.server_id == cls.server_id, m.ContestClass.class_id == cls.class_id)
        .order_by(m.Contest.end_time.desc())).all()
    for c in contests:
        pids = catalog.contest_problem_ids(s, c.server_id, c.contest_id)
        done = started = 0
        for uid in data.ids:
            passed = sum(1 for pid in pids if any(x.judgement == "AC" for x in data.subs[uid].get((c.server_id, pid), [])))
            done += passed == len(pids) and bool(pids)
            started += any(data.subs[uid].get((c.server_id, pid)) for pid in pids)
        homeworks.append({"title": c.title, "contestId": c.contest_id, "problems": len(pids), "allPassed": int(done),
                          "started": int(started), "endTime": c.end_time.isoformat() if c.end_time else None,
                          "ended": bool(c.end_time and c.end_time < now)})
    week = now - dt.timedelta(days=7)
    active = sum(1 for uid in data.ids if any(sub.submit_time and sub.submit_time >= week
                                              for subs in data.subs[uid].values() for sub in subs))
    trend = _weekly(s, data, now)
    this, last = trend[-1], trend[-2]
    fps = s.scalars(select(m.CodeFingerprint).where(
        m.CodeFingerprint.server_id == cls.server_id, m.CodeFingerprint.user_id.in_(data.ids),
        m.CodeFingerprint.suspicious.is_(True))).all() if data.ids else []
    stuck_week = len(data.stuck_points(week))
    kpis = {
        "students": len(data.ids), "active": this["active"], "activeDelta": this["active"] - last["active"],
        "passed": this["passed"], "passedDelta": this["passed"] - last["passed"],
        "avgToPass": this["avgToPass"], "avgToPassDelta": (None if this["avgToPass"] is None or last["avgToPass"] is None
                                                           else round(this["avgToPass"] - last["avgToPass"], 1)),
        "submissions": this["submissions"], "submissionsDelta": this["submissions"] - last["submissions"],
        "stuckThisWeek": stuck_week or 0, "suspicious": len(fps),
        "review": review.teacher_stats(s, data.ids, now, data.course),
    }
    return {"class": {"key": key, "name": cls.class_name, "students": len(data.ids), "activeThisWeek": active},
            "kpis": kpis, "trend": trend, "attention": _attention(s, data, now, stats),
            "focus": _focus(data, stats, kp_errors), "errors": errors, "homeworks": homeworks}


def _focus(data: "_ClassData", stats: dict, kp_errors: dict[str, Counter], limit: int = 6) -> list[dict]:
    """要讲的知识点：这个班作业涉及的（和对话里卡住过的）知识点里，待加强的、卡住过的、没来问的人多的排前面，
    带上最近 30 天这个知识点上最常见的错误。细看在知识图谱的知识点详情。"""
    rows = []
    for kp in sorted(set(data.focus) | set(stats)):
        if not in_course(kp, data.course):
            continue
        weak = sum(1 for uid in data.ids if (st := data.kps[uid].get(kp)) is not None and st.level == "待加强")
        info = stats.get(kp)
        stuck = info.students if info is not None else set()
        silent = [u for u in (info.silent if info is not None else {}) if u not in stuck]
        if not (weak or stuck or silent):
            continue
        top = kp_errors.get(kp, Counter()).most_common(1)
        rows.append({"code": kp, "name": KNOWLEDGE_POINTS.get(kp, ""), "weak": weak, "stuck": len(stuck),
                     "silent": len(silent),
                     "error": {"code": top[0][0], "name": error_name(top[0][0]), "count": top[0][1]} if top else None})
    rows.sort(key=lambda r: (-(r["weak"] + r["stuck"] + r["silent"]), -r["weak"], r["code"]))
    return rows[:limit]


def score(p: float | None) -> int | None:
    """掌握程度的估计换成 0~100 的分数（教师端看；学生端不显示数字）。"""
    return None if p is None else int(round(100 * p))


def tries_to_pass(subs: list[m.Submission]) -> int | None:
    """交到第几次第一次通过；还没通过返回 None。"""
    return next((i for i, x in enumerate(subs, start=1) if x.judgement == "AC"), None)


def _weekly(s: Session, data: "_ClassData", now: dt.datetime, weeks: int = 8) -> list[dict]:
    """按周：交了多少次、多少人交过、通过了几道题、这些题平均交几次才通过、求助回合数。旧的在前。

    不用“第一次就通过的比例”：OJ 上交一次没有代价，学生常常先交上去看看；格式小错也算没过。
    改错的过程（交几次、怎么改的）比第一次过没过更能说明会不会。
    """
    local = now.astimezone(db.BEIJING)
    monday = (local - dt.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    rounds = s.scalars(select(m.ChatRound.started_at).where(
        m.ChatRound.user_id.in_(data.ids), m.ChatRound.course == data.course,
        m.ChatRound.end_reason != m.REFLECT_END)).all() if data.ids else []
    out = []
    for i in range(weeks - 1, -1, -1):
        start, end = monday - dt.timedelta(weeks=i), monday - dt.timedelta(weeks=i - 1)
        n = 0
        people = set()
        tries: list[int] = []
        for uid in data.ids:
            for subs in data.subs[uid].values():
                inside = [x for x in subs if x.submit_time and start <= x.submit_time < end]
                n += len(inside)
                if inside:
                    people.add(uid)
                k = tries_to_pass(subs)
                if k is not None and subs[k - 1].submit_time and start <= subs[k - 1].submit_time < end:
                    tries.append(k)
        out.append({"label": "本周" if i == 0 else f"{start.month}/{start.day}", "start": start.date().isoformat(),
                    "submissions": n, "active": len(people), "passed": len(tries),
                    "avgToPass": round(sum(tries) / len(tries), 1) if tries else None,
                    "rounds": sum(1 for t in rounds if start <= t < end)})
    return out


def _attention(s: Session, data: "_ClassData", now: dt.datetime, stats: dict | None = None) -> list[dict]:
    """需要留意的学生，每人列出理由。规则是透明的，不是训练出来的预测模型。"""
    out = []
    week = now - dt.timedelta(days=7)
    stats = stats if stats is not None else stuckstats.compute(s, data.ids, data.course)
    rounds_by_kp = stuckstats.episodes_by_round(stats)   # 知识点 → 学生 → 卡住过几个回合
    copied = Counter(f.user_id for f in s.scalars(select(m.CodeFingerprint).where(
        m.CodeFingerprint.user_id.in_(data.ids), m.CodeFingerprint.server_id == data.cls.server_id,
        m.CodeFingerprint.suspicious.is_(True))).all()) if data.ids else Counter()
    stuck_week = Counter(sp.user_id for sp in data.stuck_points(week))

    def tries_of(uid: str) -> list[int]:
        return [k for v in data.subs[uid].values() if v and (k := tries_to_pass(v)) is not None]

    all_tries = [k for uid in data.ids for k in tries_of(uid)]
    class_avg = sum(all_tries) / len(all_tries) if all_tries else None
    for st in data.students:
        reasons = []
        per = data.subs[st.user_id]
        tried = [k for k, v in per.items() if v]
        mine = tries_of(st.user_id)
        if class_avg is not None and len(mine) >= 4:
            avg = sum(mine) / len(mine)
            if avg >= class_avg * 1.6 and avg - class_avg >= 1:
                reasons.append(f"平均交 {avg:.1f} 次才通过，全班是 {class_avg:.1f} 次")
        if stuck_week.get(st.user_id, 0) >= 2:
            reasons.append(f"最近 7 天卡住 {stuck_week[st.user_id]} 次")
        many = [(n, kp) for kp, per_uid in rounds_by_kp.items() if (n := per_uid.get(st.user_id, 0)) >= 3]
        if many:
            n, kp = max(many)
            reasons.append(f"在「{KNOWLEDGE_POINTS.get(kp, kp)}」上卡住 {n} 次")
        weak = [kp for kp in data.kps[st.user_id].values() if kp.level == "待加强"]
        if len(weak) >= 3:
            reasons.append(f"{len(weak)} 个知识点待加强")
        last = max((x.submit_time for k in tried for x in per[k] if x.submit_time), default=None)
        if tried and last is not None and last < week:
            reasons.append(f"{(now - last).days} 天没有交题")
        if not tried:
            reasons.append("还没有交过题")
        stuck_on = [k for k in tried if len(per[k]) >= 5 and not any(x.judgement == "AC" for x in per[k])]
        if stuck_on:
            reasons.append(f"{len(stuck_on)} 道题交了 5 次以上还没通过")
        if copied.get(st.user_id):
            reasons.append(f"{copied[st.user_id]} 次通过和同学的代码雷同")
        gaps = [kp for kp, d in profile.kp_profile(s, st.user_id, data.course).items() if "explain_gap" in d.flags]
        if len(gaps) >= 2:
            reasons.append(f"{len(gaps)} 个知识点会做但讲不清为什么（智能体评估）")
        if data.course == "D":
            weak_c = cross.weak_foundation(cross.c_foundation(s, st.user_id), data.focus or None)
            if weak_c:
                reasons.append("C 语言基础薄弱：" + "、".join(r["name"] for r in weak_c[:3]))
        if reasons:
            out.append({"userId": st.user_id, "name": st.name, "reasons": reasons})
    out.sort(key=lambda r: -len(r["reasons"]))
    return out[:12]


@router.get("/classes/{key}/students")
@page_cache("students")
def students(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    cls = _class(s, user, key)
    data = _ClassData(s, cls)
    stuck = Counter()
    stubborn = Counter()
    if data.ids:
        for sp in data.stuck_points():
            stuck[sp.user_id] += 1
            stubborn[sp.user_id] += 1 if sp.stubborn else 0
        rounds = dict(s.execute(select(m.ChatRound.user_id, func.count()).where(
            m.ChatRound.user_id.in_(data.ids), m.ChatRound.course == data.course,
            m.ChatRound.end_reason != m.REFLECT_END)
            .group_by(m.ChatRound.user_id)).all())
    else:
        rounds = {}
    # 提交名次：每道题按第一次提交的先后排，换成百分位（越小越早）。Qu 等（2019）发现它和成绩明显相关
    order_pct: dict = {}
    for prob in data.problems:
        firsts = sorted((data.subs[uid][prob][0].submit_time, uid) for uid in data.ids
                        if data.subs[uid].get(prob) and data.subs[uid][prob][0].submit_time)
        for rank, (_t, uid) in enumerate(firsts):
            order_pct[(uid, prob)] = 100 * rank / max(1, len(firsts) - 1) if len(firsts) > 1 else 0
    copied = Counter(f.user_id for f in s.scalars(select(m.CodeFingerprint).where(
        m.CodeFingerprint.user_id.in_(data.ids), m.CodeFingerprint.server_id == cls.server_id,
        m.CodeFingerprint.suspicious.is_(True))).all()) if data.ids else Counter()
    rows = []
    for st in data.students:
        per = data.subs[st.user_id]
        tried = [k for k, v in per.items() if v]
        passed = sum(1 for k in tried if any(x.judgement == "AC" for x in per[k]))
        tries = [k for v in per.values() if v and (k := tries_to_pass(v)) is not None]
        total = sum(len(per[k]) for k in tried)
        times = [x.submit_time for k in tried for x in per[k] if x.submit_time]
        levels = Counter(kp.level for kp in data.kps[st.user_id].values())
        coverage = assignment_mastery(data.topics, (x for v in per.values() for x in v), data.kps[st.user_id])
        orders = [order_pct[(st.user_id, k)] for k in tried if (st.user_id, k) in order_pct]
        rows.append({
            **coverage,
            "orderPct": round(sum(orders) / len(orders)) if orders else None,
            "copies": copied.get(st.user_id, 0),
            "activeDays": len({x.submit_time.astimezone(db.BEIJING).date() for k in tried for x in per[k] if x.submit_time}),
            "userId": st.user_id, "name": st.name, "tried": len(tried), "passed": passed, "submissions": total,
            "avgToPass": round(sum(tries) / len(tries), 1) if tries else None,
            "weak": levels.get("待加强", 0), "mastered": levels.get("已掌握", 0),
            "stuck": stuck.get(st.user_id, 0), "stubborn": stubborn.get(st.user_id, 0),
            "rounds": rounds.get(st.user_id, 0), "lastActive": max(times).isoformat() if times else None})
    return {"class": {"key": key, "name": cls.class_name}, "total": len(data.problems), "students": rows}


@router.get("/classes/{key}/help")
@page_cache("help")
def help_effect(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    """研究 · 求助效果：按知识点的卡点结局（app/stuckstats.py）和对话行为与做题表现。
    谁卡在哪个知识点（上课用）在知识图谱的知识点详情里看，这里只给比例。"""
    from .. import helpeffect

    cls = _class(s, user, key)
    people = {st.user_id: st.name for st in class_students(s, cls)}
    course = _course_of_class(s, cls)
    problems = class_problems(s, cls)
    stats = stuckstats.compute(s, list(people), course, problems)
    help_next = helpeffect.summarize(helpeffect.episodes(s, list(people), course, set(problems)))
    rows = [{k: v for k, v in r.items() if k not in ("studentRows", "silentRows")}
            for r in stuckstats.kp_rows(s, stats, people)]

    def pooled(name: str) -> dict | None:
        n = sum(getattr(st, name).n for st in stats.values())
        d = sum(getattr(st, name).d for st in stats.values())
        return {"n": n, "d": d} if d else None

    stuck_people = set().union(*(st.students for st in stats.values())) if stats else set()
    silent_people = {u for st in stats.values() for u in st.silent} - stuck_people
    data = _ClassData(s, cls)
    return {"class": {"key": key, "name": cls.class_name}, "version": stuckstats.VERSION,
            "silentFails": stuckstats.SILENT_FAILS, "rows": rows,
            "totals": {"students": len(people), "stuck": len(stuck_people),
                       "episodes": sum(len(st.episodes) for st in stats.values()),
                       "recall": pooled("recall"), "passed": pooled("passed"), "transfer": pooled("transfer"),
                       "silent": len(silent_people)},
            "dialogue": _dialogue_vs_outcome(s, data.ids, course, data.subs),
            "helpNext": {"version": helpeffect.VERSION, "rows": help_next}}


# ------------------------------------------------------------ 学生明细
@router.get("/students/{user_id}")
def student_detail(user_id: str, user: m.User = Depends(teacher), s: Session = Depends(get_session),
                   classKey: str | None = None) -> dict:
    student, cls = _student_in_my_classes(s, user, user_id, classKey)
    course = _course_of_class(s, cls)
    problem_set = set(class_problems(s, cls))
    simple = {c: v for c, v in mastery.skill_states(s, user_id, model="v1").items() if in_course(c, course)}
    v2 = mastery.skill_states(s, user_id, model="v2", with_predicted=True)
    post_all = graphmodel.current_posteriors(s, user_id)
    post = {c: r for c, r in post_all.items() if in_course(c, course)}
    gap = config.GRAPH["big_gap"]
    skills = []
    practiced_v2 = {c for c, x in v2.items() if not x.predicted and in_course(c, course)}
    for code in sorted(set(simple) | practiced_v2 | {c for c, r in post.items() if r.has_evidence}):
        st, st2, g = simple.get(code), v2.get(code), post.get(code)
        st2 = st2 if st2 is not None and not st2.predicted else None
        # level、p 用 v2（学生端、智能体用的那个），和学生看到的一致；v1 是附件一的基线，单独给出作对照。
        # 只有 OJ 失败证据的技能 v1 里没有（v1 不用这类证据），以前在这里整个漏掉
        row = {**_skill_view(code), "p": round(st2.p, 2) if st2 else None, "level": st2.level if st2 else "还没练到",
               "v1": round(st.p, 2) if st else None, "v1Level": st.level if st else None,
               "yes": round(st2.s, 2) if st2 else 0, "no": round(st2.f, 2) if st2 else 0,
               "v2": round(st2.p, 2) if st2 else None, "v2Level": st2.level if st2 else "还没练到",
               "v2Interval": [round(x, 2) for x in st2.interval] if st2 else None,
               "halfLife": round(st2.half_life) if st2 and st2.half_life else None,
               "discounted": st2.discounted if st2 else 0, "graph": round(g.p, 2) if g else None}
        row["bigGap"] = bool(st2 and g and g.has_evidence and abs(st2.p - g.p) >= gap)
        skills.append(row)
    kp1 = mastery.kp_states(s, user_id, model="v1")
    kp2 = mastery.kp_states(s, user_id, model="v2", with_predicted=True)
    recs = kpdesc.records(s, [user_id], course, problem_set).get(user_id, {})
    kps = [{"code": code, "name": KNOWLEDGE_POINTS.get(code, ""), "level": kp.level, "p": round(kp.p, 2),
            "score": score(kp.score), "text": kpdesc.describe(recs.get(code), teacher=True),
            "v1": round(kp1[code].p, 2) if code in kp1 else None, "v1Level": kp1[code].level if code in kp1 else None}
           for code, kp in sorted(kp2.items()) if not kp.predicted and in_course(code, course)]
    # 数据结构：还没有证据的知识点，按 C 语言里的先修推出来的准备度；C 语言这边没有这一块
    readiness = [{"code": code, "name": KNOWLEDGE_POINTS.get(code, ""), "p": round(kp.p, 2)}
                 for code, kp in sorted(kp2.items()) if kp.predicted and course == "D" and in_course(code, "D")]
    reviews = [{**_skill_view(r.skill_code), "origin": review.ORIGIN_TEXT.get(r.origin, r.origin), "state": r.state,
                "halfLife": round(r.half_life, 1), "retention": round(review.retention(r), 2), "reps": r.reps,
                "lapses": r.lapses, "streak": r.streak, "due": r.due_at.isoformat()}
               for r in s.scalars(select(m.ReviewItem).where(m.ReviewItem.user_id == user_id)).all()
               if in_course(r.skill_code, course)]
    copies = []
    for f in s.scalars(select(m.CodeFingerprint).where(m.CodeFingerprint.user_id == user_id,
                                                       m.CodeFingerprint.server_id == cls.server_id,
                                                       m.CodeFingerprint.suspicious.is_(True))).all():
        other = s.get(m.Student, f.matched_user_id) if f.matched_user_id else None
        prob = s.get(m.Problem, (f.server_id, f.problem_id))
        copies.append({"problem": prob.title if prob else "", "problemId": f.problem_id, "submissionId": f.submission_id,
                       "matchedName": other.name if other else "", "matchedUserId": f.matched_user_id,
                       "share": f.share, "time": f.submit_time.isoformat() if f.submit_time else None})

    subs = s.scalars(select(m.Submission).where(
        m.Submission.server_id == cls.server_id, m.Submission.user_id == user_id
    ).order_by(m.Submission.submission_id.desc())).all()
    subs = [x for x in subs if (x.server_id, x.problem_id) in problem_set]
    attempt_no: dict[int, int] = {}
    for x in sorted(subs, key=lambda x: x.submission_id):
        attempt_no[x.submission_id] = sum(1 for y in subs if y.problem_id == x.problem_id and y.submission_id <= x.submission_id)
    sub_rows = []
    for x in subs[:80]:
        problem = s.get(m.Problem, (x.server_id, x.problem_id))
        labels = s.scalars(select(m.SubmissionLabel).where(
            m.SubmissionLabel.server_id == x.server_id, m.SubmissionLabel.submission_id == x.submission_id,
            m.SubmissionLabel.is_current.is_(True))).all()
        sub_rows.append({
            "submissionId": x.submission_id, "problemId": x.problem_id, "title": problem.title if problem else "",
            "attemptNo": attempt_no[x.submission_id], "result": x.judgement,
            "resultText": JUDGEMENTS.get(x.judgement, "结果未知"), "grid": catalog.grid(s, x),
            "time": x.submit_time.isoformat() if x.submit_time else None,
            "labels": [{"code": lab.error_code, "name": error_name(lab.error_code), "skill": lab.skill_code,
                        "line": lab.line, "confidence": round(lab.confidence, 2), "source": lab.source}
                       for lab in sorted(labels, key=lambda lab: lab.role != "primary")]})
    stuck = []
    episodes = [e for st in stuckstats.compute(s, [user_id], course).values() for e in st.episodes]
    for e in sorted(episodes, key=lambda e: -e.sp.id):
        sp = e.sp
        problem = s.get(m.Problem, (sp.server_id, sp.problem_id)) if sp.problem_id else None
        stuck.append({**_skill_view(sp.skill_code), "kpCode": e.kp, "nth": sp.nth, "stubborn": sp.stubborn,
                      "resolution": sp.resolution, "problem": problem.title if problem else "", "problemId": sp.problem_id,
                      "error": sp.error_code, "errorName": error_name(sp.error_code),
                      "time": sp.created_at.isoformat(), "passedAfter": e.passed, "triesAfter": e.tries_after,
                      "givenCode": sp.given_code})
    rounds = []
    for r in s.scalars(select(m.ChatRound).where(m.ChatRound.user_id == user_id, m.ChatRound.course == course)
                       .order_by(m.ChatRound.id.desc()).limit(30)).all():
        problem = s.get(m.Problem, (r.server_id, r.problem_id)) if r.problem_id else None
        n_msgs = s.scalar(select(func.count()).select_from(m.ChatMessage).where(
            m.ChatMessage.round_id == r.id, m.ChatMessage.role == "student")) or 0
        rounds.append({"problem": problem.title if problem else "没有绑定到题", "started": r.started_at.isoformat(),
                       "status": r.status, "endReason": r.end_reason, "maxHelp": r.max_help_level,
                       "messages": n_msgs, "quiz": r.quiz_triggered, **({"skill": r.skill_code} if r.skill_code else {})})
    quizzes = []
    for a in s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.user_id == user_id, m.QuizAttempt.skill_code.like(course + "%"))
                       .order_by(m.QuizAttempt.id.desc()).limit(30)).all():
        quizzes.append({**_skill_view(a.skill_code), "type": a.quiz_type, "reason": a.reason, "state": a.state,
                        "confidence": a.confidence, "outcome": a.outcome, "correct": a.correct,
                        "time": a.created_at.isoformat()})
    # 根因：数据结构这边可以追到 C 语言的先修技能（只给估计值，不给记录）
    roots = graphmodel.root_causes({c: st for c, st in v2.items() if in_course(c, course)},
                                   {c: r.p for c, r in post_all.items() if r.has_evidence})
    # 概况的几个数（和学生列表同一个算法）
    by_problem: dict[int, list[m.Submission]] = defaultdict(list)
    for x in sorted(subs, key=lambda x: x.submission_id):
        by_problem[x.problem_id].append(x)
    tries = [k for v in by_problem.values() if (k := tries_to_pass(v)) is not None]
    coverage = assignment_mastery(assigned_topics(s, problem_set, course), subs, kp2)
    last = max((x.submit_time for x in subs if x.submit_time), default=None)
    facts = {"score": coverage["score"],
             "passed": len(tries), "total": len(problem_set),
             "avgToPass": round(sum(tries) / len(tries), 1) if tries else None,
             "weak": sum(1 for k in kps if k["level"] == "待加强"), "stuck": len(stuck),
             "lastActive": last.isoformat() if last else None}
    return {"assignmentMastery": coverage, "student": {"userId": student.user_id, "name": student.name,
                        "class": {"key": class_key(cls.server_id, cls.class_id), "name": cls.class_name}},
            "facts": facts,
            "course": course, "foundation": cross.c_foundation(s, user_id) if course == "D" else [],
            "joint": cross.joint_state(s, user_id) if course == "D" else [],
            "profile": profile.teacher_view(s, user_id, course),
            "assessments": [assess.view(s, a) for a in s.scalars(select(m.AgentAssessment).where(
                m.AgentAssessment.user_id == user_id, m.AgentAssessment.course == course,
                m.AgentAssessment.level.is_not(None)).order_by(m.AgentAssessment.id.desc()).limit(20)).all()],
            "dialogue": profile.dialogue_profile(s, user_id, course),
            "knowledge": kps, "skills": skills, "submissions": sub_rows, "moreSubmissions": max(0, len(subs) - 80),
            "stuck": stuck, "rounds": rounds, "quizzes": quizzes,
            "rootCauses": [{"skill": _skill_view(r["skill"]), "root": _skill_view(r["root"]), "p": r["p"]} for r in roots],
            "reviews": reviews, "copies": copies, "readiness": readiness,
            "graphUpdatedAt": max((r.updated_at for r in post.values()), default=None),
            # 判断、定位、修改三维和最近一次作答的证据（界面改造方案第 5 节）
            "diagnosis": diagnosis.teacher_rows(s, diagnosis.by_student(s, [user_id], course)[user_id])}


def _dialogue_vs_outcome(s: Session, ids: list[str], course: str, subs: dict) -> dict:
    """对话行为和做题表现：建构性提问占比、索要答案占比，各自和“平均交几次才通过”的相关系数
    （描述性，样本少时不算；负数表示这类对话多的学生，通过需要的次数少）。"""
    xs_c, xs_a, ys = [], [], []
    for uid in ids:
        dia = profile.dialogue_profile(s, uid, course)
        tries = [k for v in subs.get(uid, {}).values() if v and (k := tries_to_pass(v)) is not None]
        if dia["total"] < 5 or len(tries) < 3:
            continue
        xs_c.append(dia["constructive"])
        xs_a.append(dia["answerSeeking"])
        ys.append(sum(tries) / len(tries))

    def corr(xs: list[float], ys_: list[float]) -> float | None:
        n = len(xs)
        if n < 8:
            return None
        mx, my = sum(xs) / n, sum(ys_) / n
        sx = sum((x - mx) ** 2 for x in xs) ** 0.5
        sy = sum((y - my) ** 2 for y in ys_) ** 0.5
        if sx == 0 or sy == 0:
            return None
        return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys_)) / (sx * sy), 2)

    return {"students": len(ys), "constructive": corr(xs_c, ys), "answerSeeking": corr(xs_a, ys)}


# ------------------------------------------------------------ 图模型（试验中）
@router.get("/classes/{key}/graph")
@page_cache("graph")
def graph(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    cls = _class(s, user, key)
    course = _course_of_class(s, cls)
    people = {st.user_id: st.name for st in class_students(s, cls)}
    ids = list(people)
    gaps, per_simple, roots_by_skill = [], {}, defaultdict(set)
    root_rows, failure_rows = [], []
    updated = None
    n_post = 0
    repeated_students = 0
    for uid in ids:
        everything = mastery.skill_states(s, uid, model="v2")
        current = {c: st for c, st in everything.items() if in_course(c, course)}
        # 数据结构的跨课程边使用 C 语言和数据结构的实际证据。
        per_simple[uid] = {code: st.p for code, st in everything.items()
                           if st.raw >= 1 and (in_course(code, course) or course == "D")}
        post_rows = graphmodel.current_posteriors(s, uid)
        post = {c: row.p for c, row in post_rows.items() if row.has_evidence}
        for code, row in post_rows.items():
            if in_course(code, course):
                n_post += 1
                updated = row.updated_at if updated is None or row.updated_at > updated else updated
        repeated_students += any(st.unresolved_submissions >= 2 for st in current.values())
        for code, st in current.items():
            if code in post and st.raw >= 1 and abs(st.p - post[code]) >= config.GRAPH["big_gap"]:
                gaps.append({"userId": uid, "name": people[uid], "skill": _skill_view(code),
                             "v2": round(st.p, 2), "graph": round(post[code], 2), "level": st.level,
                             "yes": round(st.s, 2), "no": round(st.f, 2)})
        clues = {}
        for r in graphmodel.root_causes(current, post):
            roots_by_skill[r["root"]].add(uid)
            clues[r["skill"]] = {"userId": uid, "name": people[uid], "skill": _skill_view(r["skill"]),
                                 "root": _skill_view(r["root"]), "p": r["p"]}
        root_rows.extend(clues.values())
        for code, st in current.items():
            if st.unresolved_submissions >= 2:
                clue = clues.get(code)
                failure_rows.append({"userId": uid, "name": people[uid], "skill": _skill_view(code),
                                     "failures": st.unresolved_submissions, "updated": bool(post_rows),
                                     "root": clue["root"] if clue else None, "p": clue["p"] if clue else None})
    gaps.sort(key=lambda r: -abs(r["v2"] - r["graph"]))
    failure_rows.sort(key=lambda r: (-r["failures"], r["userId"], r["skill"]["code"]))
    common = sorted(((code, len(students)) for code, students in roots_by_skill.items()
                     if len(students) >= 2), key=lambda pair: (-pair[1], pair[0]))
    n_evidence = s.scalar(select(func.count()).select_from(m.SkillEvidence).where(
        m.SkillEvidence.user_id.in_(ids), m.SkillEvidence.skill_code.like(course + "%"))) if ids else 0
    comparisons = graphmodel.prediction_overview(s, ids, course)
    edges = [{**e, "parentName": skill_name(e["parent"]), "childName": skill_name(e["child"])}
             for e in graphmodel.edge_stats(per_simple) if in_course(e["child"], course)]
    return {
        "class": {"key": key, "name": cls.class_name},
        "trial": True, "version": config.GRAPH_VERSION, "prereqStatus": PREREQ_STATUS,
        "data": {"students": len(ids), "evidence": n_evidence, "posteriors": n_post, "predictions": comparisons["total"],
                 "updatedAt": updated.isoformat() if updated else None, "repeatedStudents": repeated_students},
        "params": {k: config.GRAPH[k] for k in ("root_prior", "beta", "gamma", "guess", "slip")},
        "gaps": gaps[:30],
        "gapTotal": len(gaps),
        "rootCauses": root_rows,
        "failureRows": failure_rows[:30],
        "failureTotal": len(failure_rows),
        "commonRoots": [{**_skill_view(code), "students": n} for code, n in common[:8]],
        "edges": edges,
        "weekly": comparisons["weekly"],
        "predictionHistory": comparisons["predictionHistory"],
        "fusionWeekly": comparisons["fusionWeekly"],
        "fusion": _fusion_view(s, course),
    }


def _fusion_view(s: Session, course: str) -> dict:
    row = s.get(m.ModelWeights, course)
    w = dict(row.weights) if row is not None and row.weights else dict(config.PROFILE["default_weights"])
    return {"learned": bool(row and row.learned), "n": row.n if row else 0, "minSamples": config.PROFILE["min_samples"],
            "updatedAt": row.updated_at.isoformat() if row else None,
            "weights": [{"dim": k, "name": profile.DIM_NAMES.get(k, "截距"), "w": w.get(k, 0.0),
                         "default": config.PROFILE["default_weights"].get(k, 0.0)} for k in ("bias", *config.PROFILE["dims"])]}


# ------------------------------------------------------------ 数据源
def _source_view(s: Session, source: m.DataSource) -> dict:
    server = s.get(m.Server, source.server_id)
    last_ok = store.get_time(s, source.server_id, "last_ok_poll_at")
    last_meta = store.get_time(s, source.server_id, "last_meta_at")
    n_classes = s.scalar(select(func.count()).select_from(m.ClassSource).where(m.ClassSource.data_source_id == source.id))
    requests = s.scalars(select(m.RequestLog.ok).where(m.RequestLog.server_id == source.server_id)
                         .order_by(m.RequestLog.id.desc()).limit(200)).all()
    return {
        "id": source.id, "server": server.name if server else "", "serverCode": server.code if server else "",
        "account": source.teacher_user_id, "enabled": source.enabled, "fromEnv": source.from_env,
        "demo": bool(server and server.code == "demo"), "classes": n_classes,
        "lastLogin": source.last_login_at.isoformat() if source.last_login_at else None,
        "lastPoll": last_ok.isoformat() if last_ok else None, "lastMeta": last_meta.isoformat() if last_meta else None,
        "pollFailures": int(store.get_state(s, source.server_id, "poll_failures", "0") or 0),
        "submissions": s.scalar(select(func.count()).select_from(m.Submission).where(m.Submission.server_id == source.server_id)),
        "pendingDetails": s.scalar(select(func.count()).select_from(m.Submission).where(
            m.Submission.server_id == source.server_id, m.Submission.detail_status != "done")),
        "backlog": store.queue_backlog(s, source.server_id),
        "requestOkRate": round(100 * sum(requests) / len(requests)) if requests else None,
    }


@router.get("/sources")
def sources_list(user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    q = select(m.DataSource).where(m.DataSource.server_id.in_(course_server_ids(s, user.course) or [-1])) \
        .order_by(m.DataSource.id)
    if user.role != "admin":
        q = q.where(m.DataSource.id.in_(accounts.teacher_source_ids(s, user) or [-1]))
    rows = [_source_view(s, src) for src in s.scalars(q).all()]
    candidates = s.scalars(select(m.LabelCandidate).order_by(m.LabelCandidate.id.desc()).limit(30)).all() \
        if user.role == "admin" else []
    from ..servers import KNOWN

    known = [{"code": k.code, "name": k.name, "course": k.course, "baseUrl": k.base_url, "basis": k.basis}
             for k in KNOWN.values() if k.course == user.course]
    return {"sources": rows, "interval": config.XDOJ_MIN_INTERVAL, "known": known, "daemon": _daemon_status(s),
            "candidates": [{"kind": c.kind, "name": c.name, "description": c.description, "nearest": c.nearest,
                            "time": c.created_at.isoformat()} for c in candidates],
            "libraries": {"errors": len(ERRORS), "skills": len(SKILLS), "knowledge": len(KNOWLEDGE_POINTS),
                          "errorVersion": config.ERROR_LIBRARY_VERSION, "taxonomyVersion": config.TAXONOMY_VERSION}}


def _daemon_status(s: Session) -> dict:
    """后台同步还在不在跑：看主循环的心跳（每 20 秒一次）。"""
    from ..xdoj.daemon import last_beat

    beat, activity = last_beat(s)
    running = beat is not None and (db.now() - beat).total_seconds() < 90
    return {"running": running, "lastBeat": beat.isoformat() if beat else None, "activity": activity}


# ------------------------------------------------------------ 知识图谱：C 语言和数据结构两张图，加跨课程的联系
def _course_of_class(s: Session, cls: m.XClass) -> str:
    server = s.get(m.Server, cls.server_id)
    return server.course if server else "C"


@router.get("/classes/{key}/knowledge-map")
@page_cache("map")
def knowledge_map(key: str, student: str | None = None, user: m.User = Depends(teacher),
                  s: Session = Depends(get_session)) -> dict:
    """知识点一层的图：节点是知识点，边由技能之间的先修边汇总而来。

    C 语言的班只有 C 语言这张图。数据结构的班是数据结构这张图，加上它要用到的 C 语言知识点
    （只有掌握程度，没有答题记录）和跨课程的先修边。
    节点上的数字：这个班每个知识点四档各几人（v2），有证据的人的平均估计；数据结构的知识点没有证据时，
    给的是由 C 语言推出来的平均准备度。传 student 时再带上这个学生自己的。
    """
    from ..taxonomy.prereq import PREREQ_STATUS, kp_edges

    cls = _class(s, user, key)
    course = _course_of_class(s, cls)
    shown = {"C"} if course == "C" else {"C", "D"}
    linked_c = set(cross.feeds(s)) if course == "D" else set()
    people = class_students(s, cls)
    ids = [p.user_id for p in people]
    if student is not None and student not in ids:
        raise HTTPException(status_code=404, detail="这个学生不在这个班里。")
    covered: set[str] = set()
    for server_id, problem_id in class_problems(s, cls):
        label = current_problem_label(s, server_id, problem_id)
        if label is not None:
            covered |= {kp_of_skill(c) for c in [label.primary_skill, *(label.secondary_skills or [])]}
    states = {uid: mastery.kp_states(s, uid, model="v2", with_predicted=True) for uid in ids}
    recs = kpdesc.records(s, [student], course, set(class_problems(s, cls))).get(student, {}) if student is not None else {}
    stuck = stuckstats.compute(s, ids, course, class_problems(s, cls))
    nodes = []
    for code, name in KNOWLEDGE_POINTS.items():
        if course_of(code) not in shown or (course_of(code) != course and code not in linked_c):
            continue   # 数据结构的班只带上它要用到的那些 C 语言知识点
        dist = Counter()
        ps, preds, scores = [], [], []
        for uid in ids:
            st = states[uid].get(code)
            if st is None or st.predicted:
                dist["还没练到"] += 1
                if st is not None:
                    preds.append(st.p)
            else:
                dist[st.level] += 1
                ps.append(st.p)
                if st.score is not None:
                    scores.append(st.score)
        node = {"code": code, "name": name, "course": course_of(code), "topic": code[:3],
                "topicName": TOPICS.get(code[:3], ""), "skills": len(skills_of_kp(code)), "covered": code in covered,
                "foundation": code in linked_c,
                "dist": [dist.get(level, 0) for level in LEVELS], "mean": round(sum(ps) / len(ps), 2) if ps else None,
                "score": score(sum(scores) / len(scores)) if scores else None,   # 全班练过的同学的平均掌握分
                "withEvidence": len(ps), "readiness": round(sum(preds) / len(preds), 2) if preds else None}
        info = stuck.get(code)
        node["stuck"] = len(info.students) if info is not None else 0   # 对话里卡住过的人
        node["silent"] = len([u for u in info.silent if u not in info.students]) if info is not None else 0
        if student is not None:
            st = states[student].get(code)
            node["student"] = None if st is None else {
                "level": st.level, "p": round(st.p, 2), "score": None if st.predicted else score(st.score),
                "predicted": st.predicted,
                # 数据结构的班看 C 语言的知识点：只有档位和估计，不说答题记录
                "text": "" if course_of(code) != course else kpdesc.describe(recs.get(code), teacher=True)}
        nodes.append(node)
    shown_codes = {n["code"] for n in nodes}
    topics = [{"code": c, "name": n, "course": course_of(c)} for c, n in TOPICS.items()
              if any(x["topic"] == c for x in nodes)]
    # 课内的边来自知识图；跨课程的边来自采用的联系（老师可以增删，见“跨课程”页）
    within = [(a, b, n, False) for a, b, n, is_x in kp_edges() if not is_x]
    crossing = [(a, b, n, True) for a, b, n in cross.kp_cross_edges(s)] if course == "D" else []
    edges = [{"from": a, "to": b, "n": n, "cross": is_x} for a, b, n, is_x in within + crossing
             if a in shown_codes and b in shown_codes]
    return {"class": {"key": key, "name": cls.class_name, "course": course},
            "courses": [{"code": c, "name": n} for c, n in COURSE_NAMES.items() if c in shown],
            "topics": topics, "nodes": nodes, "edges": edges,
            "levels": list(LEVELS), "status": PREREQ_STATUS,
            "students": [{"userId": p.user_id, "name": p.name} for p in people]}


@router.get("/classes/{key}/knowledge-map/{kp}")
@page_cache("node")
def knowledge_node(key: str, kp: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    from ..taxonomy.prereq import kp_edges

    cls = _class(s, user, key)
    course = _course_of_class(s, cls)
    if kp not in KNOWLEDGE_POINTS or (course == "C" and course_of(kp) != "C"):
        raise HTTPException(status_code=404, detail="没有这个知识点。")
    other = course_of(kp) != course   # 数据结构的班看 C 语言的知识点：只有掌握程度，不给答题记录
    people = class_students(s, cls)
    ids = [p.user_id for p in people]
    skill_states = {uid: mastery.skill_states(s, uid, model="v2", with_predicted=True) for uid in ids}
    skills = []
    for code in skills_of_kp(kp):
        dist = Counter()
        for uid in ids:
            st = skill_states[uid].get(code)
            dist[st.level if st is not None and not st.predicted else "还没练到"] += 1
        skills.append({**_skill_view(code), "dist": [dist.get(level, 0) for level in LEVELS]})
    recs = kpdesc.records(s, ids, course, set(class_problems(s, cls))) if not other else {}
    weak, everyone = [], []
    order = {"待加强": 0, "基本掌握": 1, "已掌握": 2, "还没练到": 3}
    for p in people:
        kps = mastery.kp_states(s, p.user_id, model="v2", with_predicted=True)
        st = kps.get(kp)
        row = {"userId": p.user_id, "name": p.name, "level": st.level if st else "还没练到",
               "p": round(st.p, 2) if st else None, "score": score(st.score) if st and not st.predicted else None,
               "predicted": bool(st and st.predicted),
               # 数据结构的班看 C 语言的知识点：只有档位和估计，不说答题记录
               "text": "" if other else kpdesc.describe(recs.get(p.user_id, {}).get(kp), teacher=True)}
        everyone.append(row)
        if st is not None and (st.level == "待加强" or (st.predicted and st.p < 0.45)):
            weak.append(row)
    weak.sort(key=lambda r: r["p"])
    everyone.sort(key=lambda r: (order.get(r["level"], 3), r["score"] if r["score"] is not None else 101))
    summary = "" if other else kpdesc.describe_class([recs.get(uid, {}).get(kp) or kpdesc.Record() for uid in ids], len(ids))
    errs = Counter()
    if ids and not other:
        rows = s.execute(
            select(m.SubmissionLabel.error_code)
            .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
                  & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
            .where(m.SubmissionLabel.is_current.is_(True), m.Submission.user_id.in_(ids),
                   m.Submission.server_id == cls.server_id, m.SubmissionLabel.skill_code.like(kp + ".%"))).all()
        errs.update(r[0] for r in rows)
    problems = []
    for server_id, problem_id in class_problems(s, cls):
        label = current_problem_label(s, server_id, problem_id)
        if label is not None and kp in {kp_of_skill(c) for c in [label.primary_skill, *(label.secondary_skills or [])]}:
            prob = s.get(m.Problem, (server_id, problem_id))
            problems.append({"problemId": problem_id, "title": prob.title if prob else "", "brief": label.brief,
                             "primary": kp_of_skill(label.primary_skill) == kp})
    edges = [e for e in kp_edges() if not e[3]] + \
        ([(a, b, n, True) for a, b, n in cross.kp_cross_edges(s)] if course == "D" else [])
    ref = lambda c, is_x: {"code": c, "name": KNOWLEDGE_POINTS.get(c, ""), "course": course_of(c), "cross": is_x}  # noqa: E731
    dims = {} if other else diagnosis.by_student(s, ids, course)
    per = {uid: diagnosis.summary(d.get(kp)) for uid, d in dims.items() if d.get(kp) is not None}
    stuck_rows = [] if other else [r for r in stuckstats.kp_rows(
        s, stuckstats.compute(s, ids, course, class_problems(s, cls)), {p.user_id: p.name for p in people})
        if r["code"] == kp]
    return {"code": kp, "name": KNOWLEDGE_POINTS[kp], "course": course_of(kp), "topicName": TOPICS.get(kp[:3], ""),
            "foundation": other,
            # 全班在这个知识点上的判断、定位、修改（小题首次独立作答）；每个学生的另给，点进学生明细看作答依据
            "diagnosis": None if other else diagnosis.summary(diagnosis.for_class(s, ids, course).get(kp)),
            "studentDims": per,
            # 卡在这里的学生（对话里卡住过，之后过没过）和没来问的（一道题反复没过、没就这道题求助）
            "stuck": {"students": stuck_rows[0]["studentRows"] if stuck_rows else [],
                      "silent": stuck_rows[0]["silentRows"] if stuck_rows else [], "silentFails": stuckstats.SILENT_FAILS},
            "skills": skills, "weak": weak, "students": everyone, "summary": summary,
            "errors": [{"code": c, "name": error_name(c), "count": n}
                                                       for c, n in errs.most_common(6)],
            "problems": problems, "prereqs": [ref(a, is_x) for a, b, _n, is_x in edges if b == kp],
            "next": [ref(b, is_x) for a, b, _n, is_x in edges if a == kp], "levels": list(LEVELS)}


# ------------------------------------------------------------ 跨课程（只在数据结构这边）
def _ds(user: m.User) -> None:
    if user.course != "D":
        raise HTTPException(status_code=404, detail="没有这个页面。")


@router.get("/classes/{key}/cross")
@page_cache("cross")
def cross_page(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    """跨课程页：C 基础预警（按这个班作业涉及的知识点）、全部联系和它们的数据检验。"""
    _ds(user)
    cls = _class(s, user, key)
    cross.ensure_seed(s)
    people = [(st.user_id, st.name) for st in class_students(s, cls)]
    focus = class_kps(s, cls)
    links = s.scalars(select(m.CrossLink).order_by(m.CrossLink.d_skill, m.CrossLink.c_skill)).all()
    d_kps = sorted({code for code in KNOWLEDGE_POINTS if code.startswith("D")})
    return {"class": {"key": key, "name": cls.class_name}, "focus": [{"code": c, "name": KNOWLEDGE_POINTS.get(c, "")}
                                                                    for c in sorted(focus) if c.startswith("D")],
            "warning": cross.class_warning(s, people, {c for c in focus if c.startswith("D")}),
            "links": [cross.link_view(x) for x in links],
            "relations": [{"code": k, "name": v[0], "weight": v[1]} for k, v in config.CROSS["relation"].items()],
            "params": {"n0": config.CROSS["n0"], "mastered": config.CROSS["mastered"], "minN": config.CROSS["min_n"],
                       "supportProb": config.CROSS["support_prob"], "reliability": config.CROSS["reliability"]},
            "dKps": [{"code": c, "name": KNOWLEDGE_POINTS.get(c, "")} for c in d_kps]}


# ------------------------------------------------------------ 老师盲评“理解”（app/rating.py）
class RatingBody(BaseModel):
    assessmentId: int
    level: int | None = None
    shownAt: int | None = None


def _rating_scope(s: Session, user: m.User, key: str) -> tuple[list[str], str]:
    cls = _class(s, user, key)
    return [st.user_id for st in class_students(s, cls)], _course_of_class(s, cls)


@router.get("/classes/{key}/rating")
def rating_next(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    """研究 · 理解评估：盲评的下一条，和一致率（大模型和规则；老师和大模型原始、约束后、规则；老师之间）。"""
    ids, course = _rating_scope(s, user, key)
    rows = list(s.scalars(select(m.AgentAssessment).where(
        m.AgentAssessment.user_id.in_(ids or ["-"]), m.AgentAssessment.course == course)).all())
    return {**rating.next_item(s, user, ids, course),
            "summary": {"agreement": assess.agreement(rows), "human": rating.agreement(s, ids, course)}}


@router.post("/classes/{key}/rating")
def rating_save(key: str, body: RatingBody, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    ids, course = _rating_scope(s, user, key)
    try:
        rating.rate(s, user, ids, course, body.assessmentId, body.level, body.shownAt)
    except rating.RatingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return rating.next_item(s, user, ids, course)


class ReviewLinkBody(BaseModel):
    action: str


@router.post("/cross/{link_id}/review")
def cross_review(link_id: int, body: ReviewLinkBody, user: m.User = Depends(teacher),
                 s: Session = Depends(get_session)) -> dict:
    """老师改一条联系（可选）：采用的进入数据结构的先验，不用的不再参与。平时智能体和数据检验会自动决定，
    老师改过的以老师为准，自动的不再动它。"""
    _ds(user)
    link = s.get(m.CrossLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="没有这条联系。")
    if body.action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="只能采用或不用。")
    link.status = "accepted" if body.action == "accept" else "rejected"
    link.reviewed_by, link.reviewed_at = user.id, db.now()
    return cross.link_view(link)


# ------------------------------------------------------------ 按需申请学生日志（参考 DBEDU 的做法，见 app/logs.py）
class LogBody(BaseModel):
    ids: str = ""
    classKeys: list[str] = []
    dateFrom: str
    dateTo: str
    kinds: list[str]
    includeText: bool = False
    format: str = "csv"
    purpose: str = ""


def _day(text: str, end: bool) -> dt.datetime:
    try:
        d = dt.date.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期的格式应当是 2026-11-17。")
    t = dt.datetime.combine(d, dt.time(23, 59, 59) if end else dt.time(0, 0, 0)).replace(tzinfo=db.BEIJING)
    return t.astimezone(db.UTC)


@router.get("/logs")
def log_requests(user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    q = select(m.LogRequest).where(m.LogRequest.course == user.course).order_by(m.LogRequest.id.desc()).limit(50)
    if user.role != "admin":
        q = q.where(m.LogRequest.requester_id == user.id)
    now = db.now()
    return {"kinds": [{"code": k, "name": v} for k, v in logs.KINDS.items()],
            "classes": [{"key": class_key(c.server_id, c.class_id), "name": c.class_name} for c in my_classes(s, user)],
            "requests": [logs.view(r, now) for r in s.scalars(q).all()],
            "limits": {"ids": logs.MAX_IDS, "days": logs.MAX_DAYS, "daily": logs.DAILY_LIMIT, "ttlDays": logs.TTL_DAYS},
            "admin": user.role == "admin"}


@router.post("/logs")
def log_create(body: LogBody, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    mine = {class_key(c.server_id, c.class_id) for c in my_classes(s, user)}
    bad = [k for k in body.classKeys if k not in mine]
    if bad:
        raise HTTPException(status_code=403, detail="选的班级里有不在你名下的。")
    try:
        req = logs.create(s, user, body.ids, body.classKeys, _day(body.dateFrom, False), _day(body.dateTo, True),
                          body.kinds, body.includeText, body.format, body.purpose, course=user.course)
    except logs.RequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rows = logs.collect(s, list(req.granted_ids), req.date_from, req.date_to, list(req.kinds), req.include_text,
                        req.course)
    preview = [{"userId": r.user_id, "name": r.name, "time": r.time.isoformat(), "kind": logs.KINDS.get(r.kind, r.kind),
                "action": r.action, "problem": r.problem, "problemId": r.problem_id, "samples": r.samples,
                "detail": r.detail} for r in rows[-12:]]
    by_kind = Counter(logs.KINDS.get(r.kind, r.kind) for r in rows)
    return {"request": logs.view(req), "preview": preview, "byKind": dict(by_kind)}


@router.get("/logs/{req_id}/download")
def log_download(req_id: int, token: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)):
    from urllib.parse import quote

    req = s.get(m.LogRequest, req_id)
    if req is None or req.course != user.course or (user.role != "admin" and req.requester_id != user.id) \
            or token != req.token:
        raise HTTPException(status_code=404, detail="没有这个申请，或者链接不对。")
    if logs.state_of(req) != "ready":
        raise HTTPException(status_code=410, detail="这个下载链接已经失效了，请重新申请。")
    data, mime, filename = logs.render(s, req)
    req.downloads += 1
    req.last_download_at = db.now()
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                             "Cache-Control": "no-store"})


@router.post("/logs/{req_id}/revoke")
def log_revoke(req_id: int, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    req = s.get(m.LogRequest, req_id)
    if req is None or req.course != user.course or (user.role != "admin" and req.requester_id != user.id):
        raise HTTPException(status_code=404, detail="没有这个申请。")
    req.status = "revoked"
    return logs.view(req)
