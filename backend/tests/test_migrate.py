"""启动时把库升到最新：以前用 create_all 建的库（没有版本记录、缺新加的列）也要能升级。"""
from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app import db, models as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic(url: str) -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    return cfg


def test_adopts_old_create_all_database(tmp_path, monkeypatch):
    """模拟虚拟机上现在的库：停在 0003 那一版、没有版本记录；新代码的 create_all 还先建过几张新表。"""
    url = f"sqlite:///{tmp_path / 'old.sqlite3'}"
    monkeypatch.setattr("app.config.DATABASE_URL", url)
    command.upgrade(_alembic(url), "0003")
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("INSERT INTO xdoj_servers (id, code, name, course, base_url, created_at) "
                          "VALUES (1, 'c', 'C', 'C', 'http://x', '2026-01-01')"))
        conn.execute(text("CREATE TABLE cross_links (id INTEGER PRIMARY KEY)"))   # 假装 create_all 先建过
    eng.dispose()

    db.init_engine(url)
    assert db.migrate() == "adopted:0003"
    insp = inspect(db.engine())
    assert "course" in {c["name"] for c in insp.get_columns("chat_messages")}
    assert "act" in {c["name"] for c in insp.get_columns("chat_steps")}
    assert {"agent_assessments", "model_weights", "cross_links",
            "textbook_editions", "textbook_sections", "textbook_figures"} <= set(insp.get_table_names())
    with db.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0023"
    assert "discussion" in {c["name"] for c in insp.get_columns("quiz_attempts")}
    assert "include_code" in {c["name"] for c in insp.get_columns("log_requests")}
    assert "tidy_version" in {c["name"] for c in insp.get_columns("xdoj_problem_texts")}
    assert db.migrate() == "upgraded"            # 再启动一次：什么都不用做
    db.engine().dispose()


def test_new_database_is_created_and_stamped(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path / 'new.sqlite3'}")
    assert db.migrate() == "created"
    with db.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0023"
    db.engine().dispose()


def test_chat_id_migration_preserves_rows_and_prevents_id_reuse(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'chat.sqlite3'}"
    monkeypatch.setattr("app.config.DATABASE_URL", url)
    command.upgrade(_alembic(url), "0022")
    db.init_engine(url)
    with db.session_scope() as s:
        s.add(m.ChatTopic(id=42, user_id="student", title="保留标题"))
        s.add(m.ChatRound(id=42, user_id="student"))
        s.add(m.ChatMessage(id=42, user_id="student", topic_id=42, round_id=42, role="student"))

    assert db.migrate() == "upgraded"
    with db.session_scope() as s:
        assert s.get(m.ChatTopic, 42).title == "保留标题"
        msg = s.get(m.ChatMessage, 42)
        assert (msg.topic_id, msg.round_id) == (42, 42)
        for model in (m.ChatTopic, m.ChatRound, m.ChatMessage):
            s.delete(s.get(model, 42))

    with db.session_scope() as s:
        rows = (m.ChatTopic(user_id="student"), m.ChatRound(user_id="student"),
                m.ChatMessage(user_id="student", role="student"))
        s.add_all(rows)
        s.flush()
        assert all(row.id > 42 for row in rows)
    db.engine().dispose()
