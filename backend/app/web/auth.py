"""登录。

学生：账号来自老师登录后同步下来的班级名单（不能自己注册）。登录时把学号和密码转发给他所在班级的
XDOJ 试登一次，成功就放行；密码只在这一次请求里用，不保存、不写日志（《总说明》第 11 节第 1 条的做法）。
演示数据里的学生连不了真实的 XDOJ，用服务器上配置的演示口令（DEV_LOGIN_PASSWORD）。
AUTH_MODE=dev 时，真实名单里的学生也可以用演示口令登录，只用于开发调试，正式环境不要这样配。
教师登录见 teacher.py：用自己的 XDOJ 账号，由系统去 XDOJ 试登。

C 语言和数据结构分开登录：登录时选课程，登录凭证里记着这门课，之后各页面只看这门课的东西。
数据结构那台 XDOJ 还没配地址时，登录页不出现课程选择，只有 C 语言。
"""
from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import accounts, config, crypto
from .. import models as m
from ..taxonomy import COURSE_NAMES
from ..xdoj.client import SessionError, XdojClient, XdojError, limiter_for
from .deps import COOKIE, current_user, get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

_failures: dict[str, list[float]] = {}
MAX_FAILURES, WINDOW = 5, 60.0


def throttle(key: str) -> None:
    now = time.time()
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW]
    _failures[key] = recent
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(status_code=429, detail="试的次数太多了，过一分钟再试。")


def record_failure(key: str) -> None:
    _failures.setdefault(key, []).append(time.time())


def set_login_cookie(response: Response, user: m.User, course: str = "C") -> None:
    response.set_cookie(COOKIE, crypto.sign_session(user.id, course), max_age=config.SESSION_HOURS * 3600,
                        httponly=True, samesite="lax", secure=config.APP_ENV == "prod", path="/")


class LoginBody(BaseModel):
    loginName: str
    password: str
    course: str = "C"


def _real_servers(s: Session) -> bool:
    return s.scalar(select(m.Server.id).where(m.Server.code != "demo", m.Server.base_url != "")) is not None


def courses(s: Session) -> list[dict]:
    """可以登录的课程：有地址的 XDOJ 服务器（演示数据算 C 语言）。C 语言在前。"""
    rows = s.scalars(select(m.Server).where(m.Server.base_url != "").order_by(m.Server.id)).all()
    out: dict[str, dict] = {}
    for srv in rows:
        if srv.course in COURSE_NAMES and srv.course not in out:
            real = s.scalar(select(m.Server.code).where(m.Server.course == srv.course, m.Server.code != "demo",
                                                         m.Server.base_url != ""))
            out[srv.course] = {"code": srv.course, "name": COURSE_NAMES[srv.course], "server": real}
    return [out[c] for c in COURSE_NAMES if c in out]


def check_course(course: str) -> str:
    if course not in COURSE_NAMES:
        raise HTTPException(status_code=400, detail="没有这门课。")
    return course


@router.get("/info")
def info(s: Session = Depends(get_session)) -> dict:
    """登录页要显示什么。不返回任何口令。"""
    demo = s.scalar(select(m.Server.id).where(m.Server.code == "demo")) is not None
    return {"mode": config.AUTH_MODE, "ready": bool(config.DEV_LOGIN_PASSWORD) or _real_servers(s), "demo": demo,
            "demoAccount": "2500000001" if demo else None, "teacherLogin": True, "courses": courses(s)}


def _dev_password_ok(password: str) -> bool:
    return bool(config.DEV_LOGIN_PASSWORD) and hmac.compare_digest(
        password.encode("utf-8"), config.DEV_LOGIN_PASSWORD.encode("utf-8"))


def _student_servers(s: Session, user: m.User, course: str | None = None) -> list[m.Server]:
    ids = {sid for sid, _cid in accounts.active_class_keys(s, user.student_id or user.login_name)}
    rows = [srv for srv in (s.get(m.Server, i) for i in sorted(ids)) if srv is not None]
    return [srv for srv in rows if course is None or srv.course == course]


def _xdoj_check(server: m.Server, user_id: str, password: str) -> bool:
    """去 XDOJ 试登一次。返回是否成功；连不上抛 XdojError。密码不落盘、不进日志。"""
    from . import teacher as teacher_api  # 测试时在那里注入假的 XDOJ

    client = XdojClient(server.base_url, user_id, password, limiter=limiter_for(server.base_url),
                        transport=teacher_api._test_transport, max_retries=1)
    try:
        client.login()
        return True
    except SessionError:
        return False
    finally:
        client.close()


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, s: Session = Depends(get_session)) -> dict:
    name = body.loginName.strip()
    course = check_course(body.course)
    key = f"{request.client.host if request.client else ''}:{name}"
    throttle(key)
    user = s.scalar(select(m.User).where(m.User.login_name == name))
    if user is None:
        record_failure(key)
        # 名单每两小时自动同步一次；有人用不认识的学号登录，多半是刚加进班的：马上自动再同步一次
        asked = False
        if name.isdigit():
            from .. import db
            from ..xdoj.api import request_meta_sync_if_stale

            servers = [srv.id for srv in s.scalars(select(m.Server).where(
                m.Server.course == course, m.Server.code != "demo", m.Server.base_url != "")).all()
                if s.scalar(select(m.DataSource.id).where(m.DataSource.server_id == srv.id,
                                                          m.DataSource.enabled.is_(True))) is not None]
            with db.session_scope() as s2:   # 单独提交：下面要报错返回，这个请求自己的事务会回滚
                for sid in servers:
                    asked = request_meta_sync_if_stale(s2, sid) or asked
        raise HTTPException(status_code=401, detail=(
            "名单里还没有这个学号，系统正在自动重新同步名单，一两分钟后再试一次。" if asked else
            "这个学号还不在班级名单里。名单会自动同步；如果你刚换了班，请联系任课老师。老师请在登录页选“教师”。"))
    if user.status != "active":
        raise HTTPException(status_code=403, detail="这个账号已经不在班级名单里了，请联系老师。")

    if user.role == "student":
        servers = _student_servers(s, user, course)
        if not servers:
            record_failure(key)
            other = [srv for srv in _student_servers(s, user) if srv.course != course]
            raise HTTPException(status_code=401, detail=(
                f"这个学号不在{COURSE_NAMES[course]}的班级名单里，是不是选错了课程？" if other else
                "这个学号还不在任何班级名单里。要等任课老师登录本系统、同步完名单以后才能登录。"))
        real = [srv for srv in servers if srv.code != "demo" and srv.base_url]
        if not real or (config.AUTH_MODE == "dev" and _dev_password_ok(body.password)):
            ok = _dev_password_ok(body.password)
            if not ok and not config.DEV_LOGIN_PASSWORD and not real:
                raise HTTPException(status_code=503, detail="服务器还没有设置演示口令（DEV_LOGIN_PASSWORD），请联系管理员。")
        else:
            ok, unreachable = False, False
            for srv in real:
                try:
                    if _xdoj_check(srv, user.student_id or name, body.password):
                        ok = True
                        break
                except XdojError:
                    unreachable = True
            if not ok and unreachable:
                raise HTTPException(status_code=502, detail="XDOJ 暂时连不上，没法核对密码，过一会儿再试。")
        if not ok:
            record_failure(key)
            raise HTTPException(status_code=401, detail="学号或密码不对。用的是 XDOJ 的学号和密码。")
    else:
        # 管理员、演示教师用演示口令；真实的老师在登录页选“教师”，用 XDOJ 账号登录（teacher.py）
        if not _dev_password_ok(body.password):
            record_failure(key)
            raise HTTPException(status_code=401, detail="账号或密码不对。")

    accounts.touch_login(s, user)
    if user.role == "student" and user.student_id:
        s.add(m.UiEvent(user_id=user.student_id, kind="login", target="", course=course))
    set_login_cookie(response, user, course)
    return {"role": user.role, "name": user.display_name, "course": course}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: m.User = Depends(current_user)) -> dict:
    return {"role": user.role, "name": user.display_name, "loginName": user.login_name,
            "course": user.course, "courseName": COURSE_NAMES.get(user.course, "")}
