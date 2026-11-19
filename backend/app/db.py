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


def as_utc(value) -> dt.datetime:
    """聚合查询（max 之类）在 SQLite 上可能拿回不带时区的时间或字符串：统一成带时区的 UTC。"""
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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


# 每个迁移版本在库里留下的痕迹（只用迁移才会加的列，或者那个版本新建的表）。
# 以前的部署用 create_all 建表，没有记版本；create_all 只会建缺的表、不会给已有的表加列，
# 所以按顺序找到第一个“痕迹不在”的版本，就知道库停在哪一版。
_REVISION_MARKS: list[tuple[str, str, str | None]] = [
    ("0002", "quiz_items", "origin"),
    ("0003", "teacher_sources", None),
    ("0004", "chat_messages", "course"),
    ("0005", "chat_steps", "act"),
    ("0006", "cross_links", None),
    ("0007", "agent_assessments", "basis"),
    ("0008", "xdoj_problem_figures", None),
    ("0009", "chat_messages", "topic_id"),
    ("0010", "quiz_attempts", "protocol"),
    ("0011", "log_requests", "deidentified"),
]


def _guess_revision(insp) -> str:
    tables = set(insp.get_table_names())
    rev = "0001"
    for version, table, column in _REVISION_MARKS:
        if table not in tables:
            break
        if column is not None and column not in {c["name"] for c in insp.get_columns(table)}:
            break
        rev = version
    return rev


def migrate() -> str:
    """把库升到最新版本，返回做了什么。启动时调用（run.sh 的 init-db、网页进程启动）。

    - 新库：建全部表，记为最新版本；
    - 用 Alembic 管的库：升级到最新；
    - 以前用 create_all 建的库（没有版本记录）：按 _REVISION_MARKS 认出停在哪一版，记上，再升级。
      迁移里建表的地方都先看表在不在，所以 create_all 先建过的表不会冲突。
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from . import models  # noqa: F401

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    with engine().begin() as conn:
        cfg.attributes["connection"] = conn
        insp = inspect(conn)
        tables = set(insp.get_table_names())
        if "alembic_version" in tables:
            command.upgrade(cfg, "head")
            return "upgraded"
        if not tables & set(Base.metadata.tables):
            Base.metadata.create_all(conn)
            command.stamp(cfg, "head")
            return "created"
        rev = _guess_revision(insp)
        command.stamp(cfg, rev)
        command.upgrade(cfg, "head")
        Base.metadata.create_all(conn)   # 以防万一：模型里有、迁移里没有的表
        return f"adopted:{rev}"
