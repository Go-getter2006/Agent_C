"""数据库连接。正式环境用 PostgreSQL；本机开发和测试可以用 SQLite。"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import BigInteger, DateTime, Integer, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from . import config

UTC = dt.timezone.utc
BEIJING = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """一律存 UTC，取出来带时区。SQLite 不保存时区，这里补上。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("时间必须带时区")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# 自增主键：PostgreSQL 用 bigint，SQLite 只有 INTEGER 主键才会自增。
AutoPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    """建立连接。测试里可以传入别的地址重新初始化。"""
    global _engine, _Session
    url = url or config.DATABASE_URL
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool  # 内存库：所有连接共用同一个
        if ":memory:" not in url and url.startswith("sqlite:///"):
            from pathlib import Path

            Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_pre_ping"] = True
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _record):  # pragma: no cover - 取决于驱动
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def new_session() -> Session:
    if _Session is None:
        init_engine()
    assert _Session is not None
    return _Session()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def create_all() -> None:
    from . import models  # noqa: F401  注册全部表

    Base.metadata.create_all(engine())
