"""全部数据表。

约定：
- 来自 XDOJ 的表名以 xdoj_ 开头，主键都带服务器标识 server_id（两台服务器的编号各自独立）。
- 原始数据只追加：重判是新行；题面、测试点有改动时，旧内容先存进 xdoj_archive。
- 学号（students.user_id）全局唯一，是两门课之间的纽带。
- 用于统计的字段只存固定编码（见 app/taxonomy）。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import AutoPK, Base, UTCDateTime, now

Dt = dt.datetime


def _ts() -> Mapped[Dt]:
    return mapped_column(UTCDateTime, default=now)


# ============================================================ XDOJ 原始数据
class Server(Base):
    __tablename__ = "xdoj_servers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    course: Mapped[str] = mapped_column(String(8), default="C")  # C / D
    base_url: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[Dt] = _ts()


class DataSource(Base):
    """一个数据源 = 一台服务器 + 一个教师账号。密码加密保存，或试点阶段来自环境变量。"""

    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("xdoj_servers.id"))
    teacher_user_id: Mapped[str] = mapped_column(String(64))
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_env: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (UniqueConstraint("server_id", "teacher_user_id"),)


class XClass(Base):
    __tablename__ = "xdoj_classes"
    server_id: Mapped[int] = mapped_column(ForeignKey("xdoj_servers.id"), primary_key=True)
    class_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    class_name: Mapped[str] = mapped_column(String(128), default="")
    owner_id: Mapped[str] = mapped_column(String(64), default="")
    data_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[Dt] = _ts()


class Student(Base):
    __tablename__ = "students"
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 学号
    name: Mapped[str] = mapped_column(String(64), default="")
    first_seen_at: Mapped[Dt] = _ts()
    fetched_at: Mapped[Dt] = _ts()


class Enrollment(Base):
    """学号 × 班级 × 学期 × 课程 × 服务器。换班、移出都保留旧记录。"""

    __tablename__ = "enrollments"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("students.user_id"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("xdoj_servers.id"))
    class_id: Mapped[int] = mapped_column(BigInteger)
    term: Mapped[str] = mapped_column(String(32), default="")
    course: Mapped[str] = mapped_column(String(8), default="C")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / moved / removed
    since: Mapped[Dt] = _ts()
    until: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "server_id", "class_id", "term"),)


class Contest(Base):
    """XDOJ 里的比赛或上机练习，学生端叫“作业”。"""

    __tablename__ = "xdoj_contests"
    server_id: Mapped[int] = mapped_column(ForeignKey("xdoj_servers.id"), primary_key=True)
    contest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    start_time: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    end_time: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    type: Mapped[str] = mapped_column(String(32), default="")
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[Dt] = _ts()


class ContestClass(Base):
    __tablename__ = "xdoj_contest_classes"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    class_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class ContestProblem(Base):
    __tablename__ = "xdoj_contest_problems"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)


class Problem(Base):
    __tablename__ = "xdoj_problems"
    server_id: Mapped[int] = mapped_column(ForeignKey("xdoj_servers.id"), primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(128), default="")
    difficulty: Mapped[str] = mapped_column(String(32), default="")
    time_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    meta_hash: Mapped[str] = mapped_column(String(64), default="")
    # 测试点列表（编号、分值、说明）的哈希，用来发现题目改动
    testcase_list_hash: Mapped[str] = mapped_column(String(64), default="")
    assets_fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[Dt] = _ts()


class ProblemTag(Base):
    __tablename__ = "xdoj_problem_tags"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)


class ProblemText(Base):
    __tablename__ = "xdoj_problem_texts"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content_type: Mapped[str] = mapped_column(String(64), default="")
    file_bytes: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    extract_status: Mapped[str] = mapped_column(String(24), default="ok")  # ok / empty_needs_ocr / failed
    fetched_at: Mapped[Dt] = _ts()


class Testcase(Base):
    __tablename__ = "xdoj_testcases"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    testcase_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    input_encoding: Mapped[str] = mapped_column(String(32), default="")
    output_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    output_encoding: Mapped[str] = mapped_column(String(32), default="")
    io_hash: Mapped[str] = mapped_column(String(64), default="")
    fetched_at: Mapped[Dt] = _ts()


class Submission(Base):
    __tablename__ = "xdoj_submissions"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    problem_id: Mapped[int] = mapped_column(BigInteger)
    contest_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    class_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submit_time: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    language: Mapped[str] = mapped_column(String(32), default="")
    judgement_text: Mapped[str] = mapped_column(String(64), default="")
    judgement: Mapped[str] = mapped_column(String(16), default="UNKNOWN")  # 固定枚举，见 taxonomy.enums
    points: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / done / failed
    fetched_at: Mapped[Dt] = _ts()
    __table_args__ = (Index("ix_sub_user_problem", "server_id", "user_id", "problem_id"),)


class SourceSnapshot(Base):
    __tablename__ = "xdoj_source_snapshots"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_bytes: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    encoding: Mapped[str] = mapped_column(String(32), default="")
    fetched_at: Mapped[Dt] = _ts()


class Judge(Base):
    __tablename__ = "xdoj_judges"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    submission_id: Mapped[int] = mapped_column(BigInteger, index=True)
    result_text: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    points: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_start: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    judge_end: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    compile_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[Dt] = _ts()


class JudgeRun(Base):
    __tablename__ = "xdoj_judge_runs"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    judge_id: Mapped[int] = mapped_column(BigInteger, index=True)
    testcase_id: Mapped[int] = mapped_column(BigInteger)
    result_text: Mapped[str] = mapped_column(String(64), default="")
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    run_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_kb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[Dt] = _ts()


class RunOutput(Base):
    __tablename__ = "xdoj_run_outputs"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    program_output_bytes: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    encoding: Mapped[str] = mapped_column(String(32), default="")
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[Dt] = _ts()


class Archive(Base):
    """题面或测试点内容变了：覆盖之前把旧内容存这里，保证原始数据不丢。"""

    __tablename__ = "xdoj_archive"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))  # problem_text / testcase / problem_meta
    target_id: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 二进制内容用 base64
    archived_at: Mapped[Dt] = _ts()


# ============================================================ 同步状态
class SyncState(Base):
    __tablename__ = "sync_state"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[Dt] = _ts()


class FetchTask(Base):
    __tablename__ = "sync_fetch_queue"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer)
    # detail / recheck / pending_judge / label_submission / label_problem
    kind: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[int] = mapped_column(BigInteger)
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 0 最高
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued / done / failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try_at: Mapped[Dt] = _ts()
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (
        Index("ix_fetch_pick", "status", "priority", "next_try_at"),
        Index("ix_fetch_target", "server_id", "kind", "target_id"),
    )


class Trigger(Base):
    __tablename__ = "sync_triggers"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24))  # student_active / submission_priority / sync_meta
    target_id: Mapped[str] = mapped_column(String(64))
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Dt] = _ts()
    handled_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True, index=True)


class RequestLog(Base):
    """只记接口、状态码、耗时。不记参数、源码、输出。"""

    __tablename__ = "sync_request_log"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer)
    ts: Mapped[Dt] = _ts()
    endpoint: Mapped[str] = mapped_column(String(128))
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)


# ============================================================ 账号
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(16))  # admin / teacher / student
    login_name: Mapped[str] = mapped_column(String(64), unique=True)
    student_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / removed
    created_at: Mapped[Dt] = _ts()
    last_login_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)


# ============================================================ 标注
class ProblemLabel(Base):
    """题目标注。改了不删旧的，记新版本；is_current 标出当前用的那一版。"""

    __tablename__ = "problem_labels"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer)
    problem_id: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    brief: Mapped[str] = mapped_column(String(255), default="")  # 题目简述
    primary_skill: Mapped[str] = mapped_column(String(16))
    secondary_skills: Mapped[list] = mapped_column(JSON, default=list)  # 最多 4 个
    source: Mapped[str] = mapped_column(String(16), default="llm")  # llm / seed
    taxonomy_version: Mapped[str] = mapped_column(String(16), default="")
    labeler_version: Mapped[str] = mapped_column(String(24), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")  # 标注时题面和测试点的哈希
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (
        UniqueConstraint("server_id", "problem_id", "version"),
        Index("ix_plabel_current", "server_id", "problem_id", "is_current"),
    )


class TestcaseLabel(Base):
    __tablename__ = "testcase_labels"
    __test__ = False
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    problem_label_id: Mapped[int] = mapped_column(ForeignKey("problem_labels.id"), index=True)
    server_id: Mapped[int] = mapped_column(Integer)
    testcase_id: Mapped[int] = mapped_column(BigInteger)
    skill_code: Mapped[str] = mapped_column(String(16))
    # 能对学生说的“哪类情况”，如“全是负数”“只有一个数”。不含测试点的原始数据。
    situation: Mapped[str] = mapped_column(String(64), default="")
    is_ordinary: Mapped[bool] = mapped_column(Boolean, default=False)


class ReferenceSolution(Base):
    __tablename__ = "reference_solutions"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer)
    problem_id: Mapped[int] = mapped_column(BigInteger, index=True)
    origin: Mapped[str] = mapped_column(String(16))  # peer / llm
    source_code: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # 用全部测试点跑通
    created_at: Mapped[Dt] = _ts()


class SubmissionLabel(Base):
    __tablename__ = "submission_labels"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(Integer)
    submission_id: Mapped[int] = mapped_column(BigInteger)
    error_code: Mapped[str] = mapped_column(String(8))
    skill_code: Mapped[str] = mapped_column(String(16), default="")
    role: Mapped[str] = mapped_column(String(12), default="primary")  # primary / secondary
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str] = mapped_column(String(255), default="")
    testcase_ids: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(8), default="rule")  # rule / llm / seed
    library_version: Mapped[str] = mapped_column(String(16), default="")
    labeler_version: Mapped[str] = mapped_column(String(24), default="")
    problem_label_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (Index("ix_slabel_sub", "server_id", "submission_id", "is_current"),)


class LabelCandidate(Base):
    """库里没有的错误或技能：只记候选。怎么入库待确认，这里不自动入库。"""

    __tablename__ = "label_candidates"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8))  # error / skill
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255), default="")
    nearest: Mapped[list] = mapped_column(JSON, default=list)
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[Dt] = _ts()


class LlmCall(Base):
    """大模型调用记录：提示词编号和版本、模型、耗时、用量、费用。不存正文。"""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    ts: Mapped[Dt] = _ts()
    prompt_id: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(48))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_yuan: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(String(255), default="")


# ============================================================ 对话
class ChatRound(Base):
    __tablename__ = "chat_rounds"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 空：没有绑定到题
    submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    skill_code: Mapped[str] = mapped_column(String(16), default="")
    error_code: Mapped[str] = mapped_column(String(8), default="")
    started_at: Mapped[Dt] = _ts()
    last_message_at: Mapped[Dt] = _ts()
    ended_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open / closed
    end_reason: Mapped[str] = mapped_column(String(16), default="")
    max_help_level: Mapped[str] = mapped_column(String(4), default="NA")
    last_help_level: Mapped[str] = mapped_column(String(4), default="NA")
    asked_back: Mapped[int] = mapped_column(Integer, default=0)  # 本回合反问过几次还没答上
    recall_stage: Mapped[str] = mapped_column(String(16), default="")  # 再次卡住时：asked / blanked
    quiz_triggered: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 线程：某道题（server_id + problem_id），或不绑定题目的通用线程（都为空）
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role: Mapped[str] = mapped_column(String(8))  # student / agent
    kind: Mapped[str] = mapped_column(String(8), default="text")  # text / quiz
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # {"blocks": [...], "actions": [...]}
    quiz_attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (Index("ix_msg_thread", "user_id", "server_id", "problem_id", "id"),)


class ChatStep(Base):
    """每条学生消息对应的 8 步结果。学生端任何接口都不返回这张表。"""

    __tablename__ = "chat_steps"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, index=True)
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[str] = mapped_column(String(32))
    # 1 消息分析
    intent: Mapped[str] = mapped_column(String(8), default="OTHER")
    skill_code: Mapped[str] = mapped_column(String(16), default="")
    has_attempt: Mapped[bool] = mapped_column(Boolean, default=False)
    has_code: Mapped[bool] = mapped_column(Boolean, default=False)
    icap: Mapped[str] = mapped_column(String(12), default="")
    analysis_source: Mapped[str] = mapped_column(String(8), default="rule")  # llm / rule / button
    # 2 绑定
    course_id: Mapped[str] = mapped_column(String(8), default="")
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bind_by: Mapped[str] = mapped_column(String(16), default="")
    # 3 取证
    evidence_card: Mapped[dict] = mapped_column(JSON, default=dict)
    # 4 诊断
    root_error_code: Mapped[str] = mapped_column(String(8), default="")
    root_skill_code: Mapped[str] = mapped_column(String(16), default="")
    diag_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # 5 状态
    willingness: Mapped[str] = mapped_column(String(8), default="UNKNOWN")
    base_level: Mapped[str] = mapped_column(String(8), default="")
    stuck_count: Mapped[int] = mapped_column(Integer, default=0)
    # 6 策略
    rule_id: Mapped[str] = mapped_column(String(4), default="")
    help_level: Mapped[str] = mapped_column(String(4), default="NA")
    reveal_level: Mapped[str] = mapped_column(String(2), default="甲")
    # 7 生成与校验
    reply: Mapped[dict] = mapped_column(JSON, default=dict)
    reply_source: Mapped[str] = mapped_column(String(8), default="")  # llm / template
    check_result: Mapped[dict] = mapped_column(JSON, default=dict)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    # 8 收尾
    round_status: Mapped[str] = mapped_column(String(8), default="open")
    end_reason: Mapped[str] = mapped_column(String(16), default="")
    quiz_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    stuck_point_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 版本和耗时
    prompt_version: Mapped[str] = mapped_column(String(24), default="")
    model_version: Mapped[str] = mapped_column(String(48), default="")
    workflow_version: Mapped[str] = mapped_column(String(24), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Dt] = _ts()


class StuckPoint(Base):
    __tablename__ = "stuck_points"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    skill_code: Mapped[str] = mapped_column(String(16), index=True)
    error_code: Mapped[str] = mapped_column(String(8), default="")
    nth: Mapped[int] = mapped_column(Integer, default=1)  # 第几次卡在这个技能上
    # recalled 自己回忆出来 / blank_filled 挖空补上 / example_given 给了示例代码 / exercise 小练习 / pending
    resolution: Mapped[str] = mapped_column(String(16), default="pending")
    given_code: Mapped[str] = mapped_column(Text, default="")
    passed_after: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stubborn: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[Dt] = _ts()


# ============================================================ 小题
class QuizItem(Base):
    __tablename__ = "quiz_items"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # 题库里的固定编号
    quiz_type: Mapped[str] = mapped_column(String(8))  # JUDGE / FIX
    skill_code: Mapped[str] = mapped_column(String(16), index=True)
    error_code: Mapped[str] = mapped_column(String(8), default="")
    stem: Mapped[str] = mapped_column(String(80))  # 题干，不超过 40 字
    caption: Mapped[str] = mapped_column(String(64), default="")  # 代码块标题条
    shown_code: Mapped[str] = mapped_column(Text)  # 显示给学生的代码，不超过 10 行
    template: Mapped[str] = mapped_column(Text)  # 完整程序，显示部分用占位符
    has_bug: Mapped[bool] = mapped_column(Boolean)
    bug_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    standard_fix: Mapped[str] = mapped_column(Text, default="")  # 改对后的显示代码
    checks: Mapped[list] = mapped_column(JSON, default=list)  # [{"input": "...", "expected": "..."}]
    choices: Mapped[list] = mapped_column(JSON, default=list)  # 手机上的改法选项
    explanation: Mapped[str] = mapped_column(String(160), default="")
    scene: Mapped[str] = mapped_column(String(24), default="")  # 场景，用来区分“换了场景的变式题”
    source: Mapped[str] = mapped_column(String(16), default="bank")  # bank / own_code / peer
    source_problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[Dt] = _ts()


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("quiz_items.id"))
    quiz_type: Mapped[str] = mapped_column(String(8))
    skill_code: Mapped[str] = mapped_column(String(16), default="")
    reason: Mapped[str] = mapped_column(String(16), default="after_pass")  # after_pass / recall / revisit
    # confidence → judge → edit → done
    state: Mapped[str] = mapped_column(String(12), default="confidence")
    confidence: Mapped[str] = mapped_column(String(8), default="")
    confidence_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judged_has_bug: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    judge_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    changed_lines: Mapped[list] = mapped_column(JSON, default=list)
    runs: Mapped[list] = mapped_column(JSON, default=list)  # 每次运行的结果
    tries: Mapped[int] = mapped_column(Integer, default=0)
    # right / right_second / right_bypass / wrong_fix / wrong_judge / skipped
    outcome: Mapped[str] = mapped_column(String(16), default="")
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Dt] = _ts()
    finished_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)


class RevisitItem(Base):
    """回访队列：在某个技能上卡住两次及以上后，之后几天主动出题检验。"""

    __tablename__ = "revisit_queue"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    skill_code: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str] = mapped_column(String(8), default="recall")  # recall 回忆题 / variant 变式题
    due_at: Mapped[Dt] = _ts()
    status: Mapped[str] = mapped_column(String(8), default="due")  # due / done / dropped
    attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Dt] = _ts()


class UiEvent(Base):
    __tablename__ = "ui_events"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # 固定枚举，见 taxonomy.enums.UI_EVENTS
    target: Mapped[str] = mapped_column(String(64), default="")
    ts: Mapped[Dt] = _ts()


# ============================================================ 学情证据
class SkillEvidence(Base):
    """每条进入掌握程度计算的证据。source_key 保证同一事件不重复记。"""

    __tablename__ = "skill_evidence"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    skill_code: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(24))  # 固定枚举，见 taxonomy.enums.EVIDENCE_EVENTS
    success: Mapped[bool] = mapped_column(Boolean)  # 算“会”还是“不会”
    weight: Mapped[float] = mapped_column(Float)
    independent: Mapped[bool] = mapped_column(Boolean, default=False)  # 是不是独立成功
    ts: Mapped[Dt] = _ts()
    source_key: Mapped[str] = mapped_column(String(96))
    algo_version: Mapped[str] = mapped_column(String(24), default="")
    __table_args__ = (
        UniqueConstraint("source_key", "skill_code", "success"),
        Index("ix_evidence_user_skill", "user_id", "skill_code"),
    )
