"""登录。

现在只有开发用的登录方式（AUTH_MODE=dev）：账号 + 服务器上配置的统一口令，供开发和演示。
学生正式怎么登录（把学号和密码转发给 XDOJ 试登、不保存密码）还在待确认，确认前不实现。
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


@router.get("/info")
def info(s: Session = Depends(get_session)) -> dict:
    """登录页要显示什么。不返回任何口令。"""
    demo = s.scalar(select(m.Server.id).where(m.Server.code == "demo")) is not None
    return {"mode": config.AUTH_MODE, "ready": bool(config.DEV_LOGIN_PASSWORD), "demo": demo,
            "demoAccount": "2500000001" if demo else None, "teacherLogin": True}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, s: Session = Depends(get_session)) -> dict:
    name = body.loginName.strip()
    key = f"{request.client.host if request.client else ''}:{name}"
    throttle(key)
    if config.AUTH_MODE != "dev":
        raise HTTPException(status_code=501, detail="这种登录方式还没有开通。")
    if not config.DEV_LOGIN_PASSWORD:
        raise HTTPException(status_code=503, detail="服务器还没有设置登录口令（DEV_LOGIN_PASSWORD），请联系管理员。")
    user = s.scalar(select(m.User).where(m.User.login_name == name))
    ok = hmac.compare_digest(body.password.encode("utf-8"), config.DEV_LOGIN_PASSWORD.encode("utf-8"))
    if user is None or not ok:
        record_failure(key)
        raise HTTPException(status_code=401, detail="账号或密码不对。")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="这个账号已经不在班级名单里了，请联系老师。")
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
