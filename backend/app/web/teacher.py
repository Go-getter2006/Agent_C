"""教师端接口：班级概况、学生列表和明细、顽固卡点、图模型结果（试验中）、数据源。

教师只看自己的班（按 class_sources 控制）；管理员看全部。教师端可以看到题号、内部编码和概率数值。
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import catalog, config, crypto, db, graphmodel, mastery
from .. import models as m
from ..labeling.labeler import current_problem_label
from ..taxonomy import (ERRORS, JUDGEMENTS, KNOWLEDGE_POINTS, LEVELS, SKILLS, TOPICS, error_name, kp_of_skill,
                        skill_name)
from ..taxonomy.prereq import PREREQ_STATUS
from ..xdoj import api as sync_api
from ..xdoj import store
from ..xdoj.client import SessionError, XdojClient, XdojError, limiter_for
from .auth import record_failure, set_login_cookie, throttle
from .deps import get_session, teacher

router = APIRouter(prefix="/api/teacher", tags=["teacher"])
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


@router.post("/login")
def login(body: TeacherLogin, request: Request, response: Response, s: Session = Depends(get_session)) -> dict:
    """教师用自己的 XDOJ 账号登录：系统去 XDOJ 试登，成功后建立账号、登记数据源。"""
    user_id = body.userId.strip()
    key = f"t:{request.client.host if request.client else ''}:{user_id}"
    throttle(key)
    server = s.scalar(select(m.Server).where(m.Server.code == body.server, m.Server.code != "demo"))
    if server is None or not server.base_url:
        raise HTTPException(status_code=400, detail="没有这台 XDOJ 服务器，请联系管理员先登记。")
    client = XdojClient(server.base_url, user_id, body.password, limiter=limiter_for(server.base_url),
                        transport=_test_transport, max_retries=1)
    try:
        client.login()
    except SessionError:
        record_failure(key)
        raise HTTPException(status_code=401, detail="XDOJ 账号或密码不对。")
    except XdojError:
        raise HTTPException(status_code=502, detail="XDOJ 暂时连不上，过一会儿再试。")
    finally:
        client.close()
    source = s.scalar(select(m.DataSource).where(
        m.DataSource.server_id == server.id, m.DataSource.teacher_user_id == user_id))
    if source is None:
        source = m.DataSource(server_id=server.id, teacher_user_id=user_id)
        s.add(source)
    if not source.from_env:
        source.password_enc = crypto.encrypt_secret(body.password)  # 后台同步要长期使用，加密保存
    source.enabled, source.last_login_at = True, db.now()
    s.flush()
    login_name = f"{server.code}:{user_id}"
    user = s.scalar(select(m.User).where(m.User.login_name == login_name))
    if user is None:
        user = m.User(role="teacher", login_name=login_name, display_name=user_id, data_source_id=source.id)
        s.add(user)
    user.data_source_id, user.status, user.last_login_at = source.id, "active", db.now()
    s.flush()
    sync_api.request_meta_sync(s, server.id)  # 让主循环马上同步他名下的班级和名单
    set_login_cookie(response, user)
    return {"role": user.role, "name": user.display_name}


# ------------------------------------------------------------ 权限
def class_key(server_id: int, class_id: int) -> str:
    return f"c{server_id}-{class_id}"


def my_classes(s: Session, user: m.User) -> list[m.XClass]:
    if user.role == "admin":
        return list(s.scalars(select(m.XClass).order_by(m.XClass.server_id, m.XClass.class_id)).all())
    if user.data_source_id is None:
        return []
    rows = s.scalars(
        select(m.XClass).join(m.ClassSource, (m.ClassSource.server_id == m.XClass.server_id)
                              & (m.ClassSource.class_id == m.XClass.class_id))
        .where(m.ClassSource.data_source_id == user.data_source_id)
        .order_by(m.XClass.server_id, m.XClass.class_id)).all()
    return list(rows)


def _class(s: Session, user: m.User, key: str) -> m.XClass:
    parsed = catalog.parse_key(key, "c")
    found = next((c for c in my_classes(s, user) if parsed == (c.server_id, c.class_id)), None)
    if found is None:
        raise HTTPException(status_code=404, detail="没有这个班，或者它不在你名下。")
    return found


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


def _student_in_my_classes(s: Session, user: m.User, user_id: str) -> tuple[m.Student, m.XClass]:
    for cls in my_classes(s, user):
        e = s.scalar(select(m.Enrollment.id).where(
            m.Enrollment.user_id == user_id, m.Enrollment.server_id == cls.server_id,
            m.Enrollment.class_id == cls.class_id))
        if e is not None:
            student = s.get(m.Student, user_id)
            if student is not None:
                return student, cls
    raise HTTPException(status_code=404, detail="没有这个学生，或者他不在你的班里。")


def _skill_view(code: str) -> dict:
    return {"code": code, "name": skill_name(code), "kp": KNOWLEDGE_POINTS.get(kp_of_skill(code), "")}


# ------------------------------------------------------------ 班级
@router.get("/classes")
def classes(user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    out = []
    for c in my_classes(s, user):
        server = s.get(m.Server, c.server_id)
        out.append({"key": class_key(c.server_id, c.class_id), "name": c.class_name,
                    "server": server.name if server else "", "students": len(class_students(s, c))})
    return {"classes": out, "name": user.display_name, "role": user.role}


class _ClassData:
    """一个班的学生和他们的提交、学情，一次取齐，几个接口共用。"""

    def __init__(self, s: Session, cls: m.XClass):
        self.s, self.cls = s, cls
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
        self.skills = {uid: mastery.skill_states(s, uid) for uid in self.ids}
        self.kps = {uid: mastery.kp_states(s, uid) for uid in self.ids}


@router.get("/classes/{key}/overview")
def overview(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    cls = _class(s, user, key)
    data = _ClassData(s, cls)
    now = db.now()
    # 这个班布置过的题涉及的知识点，每个知识点四档各有多少人
    kp_codes: list[str] = []
    for server_id, problem_id in data.problems:
        label = current_problem_label(s, server_id, problem_id)
        if label is not None:
            kp_codes += [kp_of_skill(c) for c in [label.primary_skill, *(label.secondary_skills or [])]]
    knowledge = []
    for kp in sorted(set(kp_codes)):
        counts = Counter(data.kps[uid][kp].level if kp in data.kps[uid] else "还没练到" for uid in data.ids)
        knowledge.append({"code": kp, "name": KNOWLEDGE_POINTS.get(kp, ""), "topic": TOPICS.get(kp[:3], ""),
                          "counts": [counts.get(level, 0) for level in LEVELS]})
    # 全班最常见的错误（最近 30 天）
    since = now - dt.timedelta(days=30)
    err_students: dict[str, set[str]] = defaultdict(set)
    err_count: Counter = Counter()
    if data.ids:
        rows = s.execute(
            select(m.SubmissionLabel.error_code, m.Submission.user_id)
            .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
                  & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
            .where(m.SubmissionLabel.is_current.is_(True), m.Submission.server_id == cls.server_id,
                   m.Submission.user_id.in_(data.ids), m.Submission.submit_time >= since)).all()
        for code, uid in rows:
            err_students[code].add(uid)
            err_count[code] += 1
    errors = [{"code": code, "name": error_name(code), "students": len(err_students[code]), "count": n}
              for code, n in err_count.most_common(10)]
    # 全班“自以为会”的技能：小题里选了“很有把握”却答错
    sure: dict[str, list[bool]] = defaultdict(list)
    if data.ids:
        for a in s.scalars(select(m.QuizAttempt).where(
                m.QuizAttempt.user_id.in_(data.ids), m.QuizAttempt.state == "done", m.QuizAttempt.skipped.is_(False),
                m.QuizAttempt.confidence == "很有把握")).all():
            sure[a.skill_code].append(bool(a.correct))
    overconfident = [{**_skill_view(code), "n": len(v), "wrong": v.count(False)} for code, v in sure.items() if v.count(False)]
    overconfident.sort(key=lambda r: (-r["wrong"] / r["n"], -r["n"]))
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
    return {"class": {"key": key, "name": cls.class_name, "students": len(data.ids), "activeThisWeek": active},
            "levels": list(LEVELS), "knowledge": knowledge, "errors": errors, "overconfident": overconfident[:8],
            "homeworks": homeworks}


@router.get("/classes/{key}/students")
def students(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    cls = _class(s, user, key)
    data = _ClassData(s, cls)
    stuck = Counter()
    stubborn = Counter()
    if data.ids:
        for sp in s.scalars(select(m.StuckPoint).where(m.StuckPoint.user_id.in_(data.ids))).all():
            stuck[sp.user_id] += 1
            stubborn[sp.user_id] += 1 if sp.stubborn else 0
        rounds = dict(s.execute(select(m.ChatRound.user_id, func.count()).where(
            m.ChatRound.user_id.in_(data.ids)).group_by(m.ChatRound.user_id)).all())
    else:
        rounds = {}
    rows = []
    for st in data.students:
        per = data.subs[st.user_id]
        tried = [k for k, v in per.items() if v]
        passed = sum(1 for k in tried if any(x.judgement == "AC" for x in per[k]))
        first = sum(1 for k in tried if per[k][0].judgement == "AC")
        total = sum(len(per[k]) for k in tried)
        times = [x.submit_time for k in tried for x in per[k] if x.submit_time]
        levels = Counter(kp.level for kp in data.kps[st.user_id].values())
        rows.append({
            "userId": st.user_id, "name": st.name, "tried": len(tried), "passed": passed, "submissions": total,
            "firstTryRate": round(100 * first / len(tried)) if tried else None,
            "weak": levels.get("待加强", 0), "mastered": levels.get("已掌握", 0),
            "stuck": stuck.get(st.user_id, 0), "stubborn": stubborn.get(st.user_id, 0),
            "rounds": rounds.get(st.user_id, 0), "lastActive": max(times).isoformat() if times else None})
    return {"class": {"key": key, "name": cls.class_name}, "total": len(data.problems), "students": rows}


@router.get("/classes/{key}/stuck")
def stuck_list(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    """卡点名单：同一个技能卡住两次及以上的，顽固卡点（第三次及以上）排在前面，建议当面讲。"""
    cls = _class(s, user, key)
    people = {st.user_id: st.name for st in class_students(s, cls)}
    groups: dict[tuple[str, str], list[m.StuckPoint]] = defaultdict(list)
    if people:
        for sp in s.scalars(select(m.StuckPoint).where(m.StuckPoint.user_id.in_(list(people)))
                            .order_by(m.StuckPoint.id)).all():
            groups[(sp.user_id, sp.skill_code)].append(sp)
    rows, single = [], 0
    for (uid, skill), points in groups.items():
        if len(points) < 2 and not any(p.stubborn for p in points):
            single += 1
            continue
        last = points[-1]
        problem = s.get(m.Problem, (last.server_id, last.problem_id)) if last.problem_id else None
        rows.append({"userId": uid, "name": people[uid], "skill": _skill_view(skill), "times": len(points),
                     "stubborn": any(p.stubborn for p in points), "lastTime": last.created_at.isoformat(),
                     "lastProblem": problem.title if problem else "", "lastProblemId": last.problem_id,
                     "resolution": last.resolution, "passedAfter": last.passed_after,
                     "error": last.error_code, "errorName": error_name(last.error_code)})
    rows.sort(key=lambda r: (not r["stubborn"], -r["times"], r["lastTime"]))
    by_skill = Counter(skill for (_uid, skill) in groups)
    common = [{**_skill_view(code), "students": n} for code, n in by_skill.most_common(8)]
    return {"class": {"key": key, "name": cls.class_name}, "rows": rows, "onceOnly": single, "common": common}


# ------------------------------------------------------------ 学生明细
@router.get("/students/{user_id}")
def student_detail(user_id: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    student, cls = _student_in_my_classes(s, user, user_id)
    problem_set = set(class_problems(s, cls))
    simple = mastery.skill_states(s, user_id)
    post = {r.skill_code: r for r in s.scalars(select(m.GraphPosterior).where(m.GraphPosterior.user_id == user_id)).all()}
    gap = config.GRAPH["big_gap"]
    skills = []
    for code in sorted(set(simple) | {c for c, r in post.items() if r.has_evidence}):
        st = simple.get(code)
        g = post.get(code)
        row = {**_skill_view(code), "p": round(st.p, 2) if st else None, "level": st.level if st else "还没练到",
               "yes": round(st.s, 2) if st else 0, "no": round(st.f, 2) if st else 0,
               "graph": round(g.p, 2) if g else None}
        row["bigGap"] = bool(st and g and abs(st.p - g.p) >= gap)
        skills.append(row)
    kps = [{"code": code, "name": KNOWLEDGE_POINTS.get(code, ""), "level": kp.level, "p": round(kp.p, 2)}
           for code, kp in sorted(mastery.kp_states(s, user_id).items())]

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
    for sp in s.scalars(select(m.StuckPoint).where(m.StuckPoint.user_id == user_id).order_by(m.StuckPoint.id.desc())).all():
        problem = s.get(m.Problem, (sp.server_id, sp.problem_id)) if sp.problem_id else None
        stuck.append({**_skill_view(sp.skill_code), "nth": sp.nth, "stubborn": sp.stubborn, "resolution": sp.resolution,
                      "problem": problem.title if problem else "", "problemId": sp.problem_id,
                      "error": sp.error_code, "errorName": error_name(sp.error_code),
                      "time": sp.created_at.isoformat(), "passedAfter": sp.passed_after, "givenCode": sp.given_code})
    rounds = []
    for r in s.scalars(select(m.ChatRound).where(m.ChatRound.user_id == user_id).order_by(m.ChatRound.id.desc()).limit(30)).all():
        problem = s.get(m.Problem, (r.server_id, r.problem_id)) if r.problem_id else None
        n_msgs = s.scalar(select(func.count()).select_from(m.ChatMessage).where(
            m.ChatMessage.round_id == r.id, m.ChatMessage.role == "student")) or 0
        rounds.append({"problem": problem.title if problem else "没有绑定到题", "started": r.started_at.isoformat(),
                       "status": r.status, "endReason": r.end_reason, "maxHelp": r.max_help_level,
                       "messages": n_msgs, "quiz": r.quiz_triggered, **({"skill": r.skill_code} if r.skill_code else {})})
    quizzes = []
    for a in s.scalars(select(m.QuizAttempt).where(m.QuizAttempt.user_id == user_id).order_by(m.QuizAttempt.id.desc()).limit(30)).all():
        quizzes.append({**_skill_view(a.skill_code), "type": a.quiz_type, "reason": a.reason, "state": a.state,
                        "confidence": a.confidence, "outcome": a.outcome, "correct": a.correct,
                        "time": a.created_at.isoformat()})
    roots = graphmodel.root_causes(simple, {c: r.p for c, r in post.items()})
    return {"student": {"userId": student.user_id, "name": student.name,
                        "class": {"key": class_key(cls.server_id, cls.class_id), "name": cls.class_name}},
            "knowledge": kps, "skills": skills, "submissions": sub_rows, "moreSubmissions": max(0, len(subs) - 80),
            "stuck": stuck, "rounds": rounds, "quizzes": quizzes,
            "rootCauses": [{"skill": _skill_view(r["skill"]), "root": _skill_view(r["root"]), "p": r["p"]} for r in roots],
            "graphUpdatedAt": max((r.updated_at for r in post.values()), default=None)}


# ------------------------------------------------------------ 图模型（试验中）
@router.get("/classes/{key}/graph")
def graph(key: str, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    cls = _class(s, user, key)
    people = {st.user_id: st.name for st in class_students(s, cls)}
    ids = list(people)
    gaps, per_simple, roots_by_skill = [], {}, Counter()
    root_rows = []
    updated = None
    n_post = 0
    for uid in ids:
        simple = mastery.skill_states(s, uid)
        per_simple[uid] = {code: st.p for code, st in simple.items() if st.raw >= 1}
        post_rows = s.scalars(select(m.GraphPosterior).where(m.GraphPosterior.user_id == uid)).all()
        post = {r.skill_code: r.p for r in post_rows}
        n_post += len(post_rows)
        for r in post_rows:
            updated = r.updated_at if updated is None or r.updated_at > updated else updated
        for code, st in simple.items():
            if code in post and st.raw >= 1 and abs(st.p - post[code]) >= config.GRAPH["big_gap"]:
                gaps.append({"userId": uid, "name": people[uid], "skill": _skill_view(code),
                             "simple": round(st.p, 2), "graph": round(post[code], 2), "level": st.level})
        for r in graphmodel.root_causes(simple, post):
            roots_by_skill[r["root"]] += 1
            root_rows.append({"userId": uid, "name": people[uid], "skill": _skill_view(r["skill"]),
                              "root": _skill_view(r["root"]), "p": r["p"]})
    gaps.sort(key=lambda r: -abs(r["simple"] - r["graph"]))
    n_evidence = s.scalar(select(func.count()).select_from(m.SkillEvidence).where(m.SkillEvidence.user_id.in_(ids))) if ids else 0
    n_pred = s.scalar(select(func.count()).select_from(m.PredictionLog).where(m.PredictionLog.user_id.in_(ids))) if ids else 0
    edges = [{**e, "parentName": skill_name(e["parent"]), "childName": skill_name(e["child"])}
             for e in graphmodel.edge_stats(per_simple)]
    return {
        "class": {"key": key, "name": cls.class_name},
        "trial": True, "version": config.GRAPH_VERSION, "prereqStatus": PREREQ_STATUS,
        "data": {"students": len(ids), "evidence": n_evidence, "posteriors": n_post, "predictions": n_pred,
                 "updatedAt": updated.isoformat() if updated else None},
        "params": {k: config.GRAPH[k] for k in ("root_prior", "beta", "gamma", "guess", "slip")},
        "gaps": gaps[:40],
        "rootCauses": root_rows,
        "commonRoots": [{**_skill_view(code), "students": n} for code, n in roots_by_skill.most_common(8)],
        "edges": edges,
        "weekly": graphmodel.weekly_accuracy(s, ids),
    }


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
    q = select(m.DataSource).order_by(m.DataSource.id)
    if user.role != "admin":
        q = q.where(m.DataSource.id == (user.data_source_id or -1))
    rows = [_source_view(s, src) for src in s.scalars(q).all()]
    candidates = s.scalars(select(m.LabelCandidate).order_by(m.LabelCandidate.id.desc()).limit(30)).all() \
        if user.role == "admin" else []
    return {"sources": rows, "interval": config.XDOJ_MIN_INTERVAL,
            "candidates": [{"kind": c.kind, "name": c.name, "description": c.description, "nearest": c.nearest,
                            "time": c.created_at.isoformat()} for c in candidates],
            "libraries": {"errors": len(ERRORS), "skills": len(SKILLS), "knowledge": len(KNOWLEDGE_POINTS),
                          "errorVersion": config.ERROR_LIBRARY_VERSION, "taxonomyVersion": config.TAXONOMY_VERSION}}


@router.post("/sources/{source_id}/sync")
def source_sync(source_id: int, user: m.User = Depends(teacher), s: Session = Depends(get_session)) -> dict:
    """立即同步名单和作业：只是通知主循环，这里不直接向 XDOJ 发请求。"""
    source = s.get(m.DataSource, source_id)
    if source is None or (user.role != "admin" and source.id != user.data_source_id):
        raise HTTPException(status_code=404, detail="没有这个数据源。")
    if not source.enabled:
        raise HTTPException(status_code=409, detail="这个数据源没有启用（演示数据不连 XDOJ）。")
    sync_api.request_meta_sync(s, source.server_id)
    return {"ok": True}
