"""数据库迁移。数据库地址来自环境变量 DATABASE_URL（和应用用的是同一个）。"""
from alembic import context
from sqlalchemy import create_engine

from app import config, models  # noqa: F401  注册全部表
from app.db import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.DATABASE_URL, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=config.DATABASE_URL.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def _configure(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=connection.dialect.name == "sqlite", compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    given = context.config.attributes.get("connection")
    if given is not None:   # 程序里调用（db.migrate）：用应用自己的连接
        _configure(given)
        return
    engine = create_engine(config.DATABASE_URL)
    with engine.connect() as connection:
        _configure(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
