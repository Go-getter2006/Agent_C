"""启动时把库升到最新：以前用 create_all 建的库（没有版本记录、缺新加的列）也要能升级。"""
from __future__ import annotations

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

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
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0025"
    assert "discussion" in {c["name"] for c in insp.get_columns("quiz_attempts")}
    assert "include_code" in {c["name"] for c in insp.get_columns("log_requests")}
    assert "tidy_version" in {c["name"] for c in insp.get_columns("xdoj_problem_texts")}
    assert db.migrate() == "upgraded"            # 再启动一次：什么都不用做
    db.engine().dispose()


def test_new_database_is_created_and_stamped(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path / 'new.sqlite3'}")
    assert db.migrate() == "created"
    with db.engine().connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0025"
    db.engine().dispose()


def _old_round(s, rid, **extra):
    table = Table("chat_rounds", MetaData(), autoload_with=s.connection())
    now = db.now()
    values = dict(id=rid, user_id="student", course="C", started_at=now, last_message_at=now,
                  skill_code="", error_code="", status="open", end_reason="", max_help_level="NA",
                  last_help_level="NA", asked_back=0, failed_replies=0, located=False, help_count=0,
                  recall_stage="", quiz_triggered=False)
    s.execute(table.insert().values(**{**values, **extra}))


def test_chat_id_migration_preserves_rows_and_prevents_id_reuse(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'chat.sqlite3'}"
    monkeypatch.setattr("app.config.DATABASE_URL", url)
    command.upgrade(_alembic(url), "0022")
    db.init_engine(url)
    with db.session_scope() as s:
        s.add(m.ChatTopic(id=42, user_id="student", title="保留标题"))
        _old_round(s, 42)
        s.add(m.ChatMessage(id=42, user_id="student", topic_id=42, round_id=42, role="student"))

    assert db.migrate() == "upgraded"
    with db.session_scope() as s:
        assert s.get(m.ChatTopic, 42).title == "保留标题"
        msg = s.get(m.ChatMessage, 42)
        assert (msg.topic_id, msg.round_id) == (42, 42)
        assert s.get(m.ChatRound, 42).conversation_key == "t42"
        for model in (m.ChatTopic, m.ChatRound, m.ChatMessage):
            s.delete(s.get(model, 42))

    with db.session_scope() as s:
        rows = (m.ChatTopic(user_id="student"), m.ChatRound(user_id="student"),
                m.ChatMessage(user_id="student", role="student"))
        s.add_all(rows)
        s.flush()
        assert all(row.id > 42 for row in rows)
    db.engine().dispose()


def test_migrate_splits_mixed_rounds_and_invalidates_their_assessments(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mixed.sqlite3'}"
    monkeypatch.setattr("app.config.DATABASE_URL", url)
    command.upgrade(_alembic(url), "0023")
    db.init_engine(url)
    first_skill, hw_skill = "C01.01.01", "C06.03.02"
    with db.session_scope() as s:
        _old_round(s, 41, server_id=1, problem_id=406, skill_code=hw_skill, max_help_level="H3", help_count=1)
        _old_round(s, 42, skill_code=first_skill, status="closed")
        scopes = [{"topic_id": 11}, {"topic_id": 12}, {}, {"server_id": 1, "problem_id": 406}, {"topic_id": 11}]
        msgs = [m.ChatMessage(user_id="student", role="student", round_id=rid, **scope)
                for rid, scope in zip((41, 41, 41, 41, 42), scopes)]
        s.add_all(msgs)
        s.flush()
        ids = [msg.id for msg in msgs]
        s.add_all(m.ChatStep(user_id="student", message_id=msg.id, round_id=msg.round_id, help_level=help_level,
                             root_skill_code=skill)
                  for msg, help_level, skill in zip(msgs, ("NA", "NA", "NA", "H3", "NA"),
                                                   (first_skill, "", "", hw_skill, first_skill)))
        s.add_all(m.AgentAssessment(user_id="student", round_id=rid, skill_code=skill, evidence=["原话"])
                  for rid, skill in ((41, hw_skill), (42, first_skill)))
        s.add_all(m.SkillEvidence(user_id="student", skill_code=skill, event_type="agent_understanding",
                                  success=True, weight=1, source_key=f"assess:{rid}")
                  for rid, skill in ((41, hw_skill), (42, first_skill)))

    assert db.migrate() == "upgraded"
    assert db.migrate() == "upgraded"
    with db.session_scope() as s:
        rounds = [s.get(m.ChatRound, s.get(m.ChatMessage, mid).round_id) for mid in ids]
        assert [r.conversation_key for r in rounds] == ["t11", "t12", "general", "p1-406", "t11"]
        assert len({r.id for r in rounds}) == 5
        assert rounds[3].id == 41 and rounds[4].id == 42
        for mid, rnd in zip(ids, rounds):
            assert s.scalar(select(m.ChatStep.round_id).where(m.ChatStep.message_id == mid)) == rnd.id
        assert all(r.status == "closed" and r.end_reason == "会话拆分" for r in rounds[:4])
        assert rounds[0].skill_code == first_skill and rounds[0].help_count == 0
        assert rounds[3].skill_code == hw_skill and rounds[3].max_help_level == "H3"
        assert list(s.scalars(select(m.AgentAssessment.round_id))) == [42]
        assert list(s.scalars(select(m.SkillEvidence.source_key))) == ["assess:42"]
    db.engine().dispose()
