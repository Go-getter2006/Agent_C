"""登录。

学生：账号来自老师登录后同步下来的班级名单（不能自己注册）。登录时把学号和密码转发给他所在班级的
XDOJ 试登一次，成功就放行；密码只在这一次请求里用，不保存、不写日志（《总说明》第 11 节第 1 条的做法）。
演示数据里的学生连不了真实的 XDOJ，用服务器上配置的演示口令（DEV_LOGIN_PASSWORD）。
AUTH_MODE=dev 时，真实名单里的学生也可以用演示口令登录，只用于开发调试，正式环境不要这样配。
教师登录见 teacher.py：用自己的 XDOJ 账号，由系统去 XDOJ 试登。
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


def set_login_cookie(response: Response, user: m.User) -> None:
    response.set_cookie(COOKIE, crypto.sign_session(user.id), max_age=config.SESSION_HOURS * 3600, httponly=True,
                        samesite="lax", secure=config.APP_ENV == "prod", path="/")


class LoginBody(BaseModel):
    loginName: str
    password: str


def _real_servers(s: Session) -> bool:
    return s.scalar(select(m.Server.id).where(m.Server.code != "demo", m.Server.base_url != "")) is not None


@router.get("/info")
def info(s: Session = Depends(get_session)) -> dict:
    """登录页要显示什么。不返回任何口令。"""
    demo = s.scalar(select(m.Server.id).where(m.Server.code == "demo")) is not None
    return {"mode": config.AUTH_MODE, "ready": bool(config.DEV_LOGIN_PASSWORD) or _real_servers(s), "demo": demo,
            "demoAccount": "2500000001" if demo else None, "teacherLogin": True}


def _dev_password_ok(password: str) -> bool:
    return bool(config.DEV_LOGIN_PASSWORD) and hmac.compare_digest(
        password.encode("utf-8"), config.DEV_LOGIN_PASSWORD.encode("utf-8"))


def _student_servers(s: Session, user: m.User) -> list[m.Server]:
    ids = {sid for sid, _cid in accounts.active_class_keys(s, user.student_id or user.login_name)}
    return [srv for srv in (s.get(m.Server, i) for i in sorted(ids)) if srv is not None]


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
    key = f"{request.client.host if request.client else ''}:{name}"
    throttle(key)
    user = s.scalar(select(m.User).where(m.User.login_name == name))
    if user is None:
        record_failure(key)
        raise HTTPException(status_code=401, detail="这个学号还不在任何班级名单里。要等任课老师登录本系统、"
                                                    "同步完名单以后才能登录；老师请在登录页选“教师”。")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="这个账号已经不在班级名单里了，请联系老师。")

    if user.role == "student":
        servers = _student_servers(s, user)
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
        s.add(m.UiEvent(user_id=user.student_id, kind="login", target=""))
    set_login_cookie(response, user)
    return {"role": user.role, "name": user.display_name}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: m.User = Depends(current_user)) -> dict:
    return {"role": user.role, "name": user.display_name, "loginName": user.login_name}
