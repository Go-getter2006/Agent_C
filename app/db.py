"""SQLite 存储。所有原始记录都保留，方便以后做离线分析和消融实验。"""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

-- 一个“回合”(episode)：学生围绕同一个问题和智能体的连续讨论
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    kp_id TEXT NOT NULL,
    question_summary TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    reask_of INTEGER,              -- 如果是重复提问，指向以前那个回合
    reask_sim REAL,
    n_student_turns INTEGER DEFAULT 0,
    mean_icap REAL,
    attempt_ratio REAL,
    answer_seek_ratio REAL,
    engagement_index REAL,
    resolved INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    role TEXT NOT NULL,            -- student / tutor
    content TEXT NOT NULL,
    code TEXT,
    created_at REAL NOT NULL,
    latency_sec REAL,              -- 学生这条消息距上一条智能体回复的秒数
    analysis_json TEXT             -- 学生发言的分析结果（知识点、ICAP、求助类型等）
);

CREATE TABLE IF NOT EXISTS diag_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    kp_id TEXT NOT NULL,
    episode_id INTEGER,
    started_at REAL NOT NULL,
    ended_at REAL,
    prior_mu REAL, prior_sd REAL,
    prior_json TEXT,               -- 先验用到的历史特征与投入特征（原始值）
    post_mu REAL, post_sd REAL, p_mastered REAL,
    profile_json TEXT,
    n_items INTEGER DEFAULT 0,
    stop_reason TEXT,
    status TEXT DEFAULT 'in_progress',
    current_item_id TEXT,
    current_step INTEGER,
    selection_log TEXT             -- 每次选题时各候选题的得分
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    kp_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_order INTEGER NOT NULL,   -- 本次诊断中的第几道题
    step INTEGER NOT NULL,         -- 1..5
    answer TEXT,
    passed INTEGER NOT NULL,
    feedback TEXT,
    grader TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mastery (
    student_id TEXT NOT NULL,
    kp_id TEXT NOT NULL,
    mu REAL, sd REAL, p_mastered REAL,
    profile_json TEXT,
    session_id INTEGER,
    updated_at REAL,
    PRIMARY KEY (student_id, kp_id)
);

CREATE INDEX IF NOT EXISTS idx_episodes_student_kp ON episodes(student_id, kp_id);
CREATE INDEX IF NOT EXISTS idx_turns_episode ON turns(episode_id);
CREATE INDEX IF NOT EXISTS idx_responses_session ON responses(session_id);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> float:
    return time.time()


def row_to_dict(row):
    return dict(row) if row is not None else None


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def ensure_student(conn, student_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO students(id, created_at) VALUES (?, ?)", (student_id, now()))
