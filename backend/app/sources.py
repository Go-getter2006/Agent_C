"""数据源：一台 XDOJ 服务器的地址 + 一个教师账号。

试点阶段的数据源来自环境变量（XDOJ_BASE_URL、XDOJ_USER、XDOJ_PASS）；
教师在系统里登记的数据源，密码加密保存在库里。同一台服务器的所有数据源共用一个限速器。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, crypto, db
from . import models as m
from .xdoj.client import XdojClient, limiter_for
from .xdoj.store import SyncContext, request_logger

log = logging.getLogger("sources")


def ensure_server(s: Session, code: str, name: str, base_url: str, course: str = "C") -> m.Server:
    server = s.scalar(select(m.Server).where(m.Server.code == code))
    if server is None:
        server = m.Server(code=code, name=name, base_url=base_url, course=course)
        s.add(server)
        s.flush()
    elif base_url and server.base_url != base_url:
        server.base_url = base_url
    return server


def ensure_env_source(s: Session) -> m.DataSource | None:
    """环境变量里配了 XDOJ 账号时，登记成一个数据源。密码不进库。"""
    if not (config.XDOJ_BASE_URL and config.XDOJ_USER and config.XDOJ_PASS):
        return None
    server = ensure_server(s, config.XDOJ_SERVER_CODE, config.XDOJ_SERVER_NAME, config.XDOJ_BASE_URL)
    source = s.scalar(select(m.DataSource).where(
        m.DataSource.server_id == server.id, m.DataSource.teacher_user_id == config.XDOJ_USER))
    if source is None:
        source = m.DataSource(server_id=server.id, teacher_user_id=config.XDOJ_USER, from_env=True, enabled=True)
        s.add(source)
        s.flush()
    return source


def password_of(source: m.DataSource) -> str | None:
    if source.from_env:
        return config.XDOJ_PASS if source.teacher_user_id == config.XDOJ_USER else None
    return crypto.decrypt_secret(source.password_enc) if source.password_enc else None


def build_contexts() -> list[SyncContext]:
    """给主循环用：每个启用的数据源一个会话。"""
    out: list[SyncContext] = []
    with db.session_scope() as s:
        ensure_env_source(s)
        rows = s.execute(select(m.DataSource, m.Server).join(m.Server, m.Server.id == m.DataSource.server_id)
                         .where(m.DataSource.enabled.is_(True), m.Server.code != "demo")).all()
        for source, server in rows:
            try:
                password = password_of(source)
            except ValueError:
                log.error("数据源 %s 的密码解不开，已跳过", source.id)
                continue
            if not password or not server.base_url:
                continue
            client = XdojClient(server.base_url, source.teacher_user_id, password,
                                limiter=limiter_for(server.base_url), on_request=request_logger(server.id))
            out.append(SyncContext(server_id=server.id, client=client, source_id=source.id,
                                   term=config.XDOJ_TERM, course=server.course))
    return out
