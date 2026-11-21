"""每天自动备份整个库，全部保留，不删旧的。

- 每份都是整个库的完整拷贝（所有学生、所有时间的对话、作答、提交），不是只备份当天的；
- 用 SQLite 自带的在线备份（sqlite3 的 backup），不用停服务：WAL 模式下备份时网页和同步照常读写；
- 每天（北京时间）一份，凌晨 BACKUP_HOUR 点以后的第一个空闲时做；那时候没开机，开机后当天补做；
- 拷完先 quick_check，坏了不留；再 gzip 压缩（文本为主，能小很多），写完才改成正式文件名，
  所以目录里只会有完整的备份；
- 放在 data/backup/（`.env` 的 BACKUP_DIR 可以改），文件名 agent-20261121.sqlite3.gz；
- 恢复：停服务（./run.sh stop），gunzip -k 解压，换掉 data/agent.sqlite3，再启动。

备份和库在同一台机器上，硬盘坏了会一起没：要定期把 data/backup/ 拷到别处。
只备份 SQLite；用 PostgreSQL 的话用它自己的备份（pg_dump），这里不做。
"""
from __future__ import annotations

import datetime as dt
import gzip
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path

from . import config, db

log = logging.getLogger("backup")

NAME = re.compile(r"^agent-(\d{8})\.sqlite3\.gz$")


def backup_dir() -> Path:
    return Path(config.BACKUP_DIR)


def database_path() -> Path | None:
    """正在用的 SQLite 库文件；不是文件库（PostgreSQL、内存库）返回 None。"""
    engine = db.init_engine() if db._engine is None else db._engine
    if engine.url.get_backend_name() != "sqlite":
        return None
    name = engine.url.database or ""
    if not name or name == ":memory:" or name.startswith("file::memory:"):
        return None
    return Path(name)


def target_for(day: dt.date, folder: Path | None = None) -> Path:
    return (folder or backup_dir()) / f"agent-{day:%Y%m%d}.sqlite3.gz"


def run(now: dt.datetime | None = None, force: bool = False, folder: Path | None = None) -> Path | None:
    """今天还没备份就备份一份，返回备份文件；不用做（已有、还没到钟点、不是 SQLite）返回 None。"""
    src = database_path()
    if src is None or not src.exists():
        return None
    local = (now or db.now()).astimezone(db.BEIJING)
    if not force and local.hour < config.BACKUP_HOUR:
        return None
    folder = folder or backup_dir()
    target = target_for(local.date(), folder)
    if target.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / f".{target.name[:-3]}.part"
    packed = folder / f".{target.name}.part"
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        dest = sqlite3.connect(raw)
        try:
            source.backup(dest)   # 一次拷完：读的是同一时刻的库，WAL 下不挡写
            check = dest.execute("PRAGMA quick_check").fetchone()
        finally:
            dest.close()
            source.close()
        if not check or check[0] != "ok":
            raise RuntimeError(f"备份出来的库检查没通过：{check}")
        with open(raw, "rb") as fin, gzip.open(packed, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
        os.replace(packed, target)
    finally:
        for p in (raw, packed):
            p.unlink(missing_ok=True)
    log.info("已备份整个库（%s，%.1f MB）", target.name, target.stat().st_size / 1e6)
    return target


def listing(folder: Path | None = None) -> list[dict]:
    """已有的备份，新的在前（cli doctor 用）。"""
    folder = folder or backup_dir()
    if not folder.exists():
        return []
    out = []
    for p in folder.iterdir():
        mt = NAME.match(p.name)
        if mt:
            out.append({"day": dt.datetime.strptime(mt.group(1), "%Y%m%d").date(), "path": p,
                        "bytes": p.stat().st_size})
    return sorted(out, key=lambda x: x["day"], reverse=True)
