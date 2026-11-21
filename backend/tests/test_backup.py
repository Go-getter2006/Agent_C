"""每天自动备份整个库：完整拷贝、压缩、一天一份、旧的不删。"""
from __future__ import annotations

import datetime as dt
import gzip
import sqlite3

import pytest

from app import backup, config, db
from app import models as m


@pytest.fixture(autouse=True)
def _back_to_memory():
    yield
    db.init_engine("sqlite:///:memory:")   # 别把后面的测试留在这里的文件库上


def _file_db(tmp_path):
    engine = db.init_engine(f"sqlite:///{tmp_path / 'agent.sqlite3'}")
    db.Base.metadata.create_all(engine)
    return engine


def test_daily_backup_is_a_full_copy_and_keeps_old_ones(tmp_path, monkeypatch):
    _file_db(tmp_path)
    folder = tmp_path / "backup"
    with db.session_scope() as s:
        for i in range(3):
            s.add(m.ChatMessage(user_id=f"25000000{i:02d}", role="student", kind="text",
                                content={"blocks": [{"t": "p", "text": f"第 {i} 条"}]}))
    monkeypatch.setattr(config, "BACKUP_HOUR", 3)
    early = dt.datetime(2026, 11, 20, 17, 0, tzinfo=dt.timezone.utc)   # 北京时间 21 日凌晨 1 点：还没到钟点
    assert backup.run(now=early, folder=folder) is None
    day1 = dt.datetime(2026, 11, 20, 20, 0, tzinfo=dt.timezone.utc)    # 北京时间 21 日 4 点
    first = backup.run(now=day1, folder=folder)
    assert first is not None and first.name == "agent-20261121.sqlite3.gz"
    assert backup.run(now=day1 + dt.timedelta(hours=5), folder=folder) is None   # 同一天只做一份

    with db.session_scope() as s:   # 备份时网页照常写
        s.add(m.ChatMessage(user_id="2500000099", role="student", kind="text", content={"blocks": []}))
    second = backup.run(now=day1 + dt.timedelta(days=1), folder=folder)
    assert second is not None and first.exists()          # 旧的不删
    assert [x["day"] for x in backup.listing(folder)] == [dt.date(2026, 11, 22), dt.date(2026, 11, 21)]
    assert not [p for p in folder.iterdir() if p.name.startswith(".")]   # 没留下半截文件

    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(gzip.decompress(second.read_bytes()))
    con = sqlite3.connect(restored)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT count(*) FROM chat_messages").fetchone()[0] == 4    # 整个库，不只是当天的
    finally:
        con.close()


def test_no_backup_for_in_memory_db(tmp_path):
    db.init_engine("sqlite:///:memory:")
    assert backup.database_path() is None
    assert backup.run(force=True, folder=tmp_path) is None
