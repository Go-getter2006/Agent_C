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


class TextbookEdition(Base):
    __tablename__ = "textbook_editions"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    published_at: Mapped[Dt] = _ts()


class TextbookSection(Base):
    __tablename__ = "textbook_sections"
    edition_id: Mapped[int] = mapped_column(ForeignKey("textbook_editions.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    pages: Mapped[list] = mapped_column(JSON)
    anchors: Mapped[list] = mapped_column(JSON)


class TextbookFigure(Base):
    __tablename__ = "textbook_figures"
    edition_id: Mapped[int] = mapped_column(ForeignKey("textbook_editions.id"), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    png: Mapped[bytes] = mapped_column(LargeBinary)


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
    removed_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
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
    removed_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    fetched_at: Mapped[Dt] = _ts()


class TeacherSource(Base):
    """一位老师（本系统的账号）关联的 XDOJ 账号：每门课的 XDOJ 是不同的服务器，账号密码可能不同。

    老师用其中任何一个账号登录，进的都是同一个本系统账号，能看到所有关联课程的班级。
    """

    __tablename__ = "teacher_sources"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), primary_key=True)
    created_at: Mapped[Dt] = _ts()


class ClassSource(Base):
    """哪个数据源（教师账号）能看到哪个班。教师端的权限按这张表控制。"""

    __tablename__ = "class_sources"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data_source_id: Mapped[int] = mapped_column(Integer, primary_key=True)


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
    extract_status: Mapped[str] = mapped_column(String(24), default="ok")  # 见 pdftext.STATUS_NAMES；USABLE 里的才能用
    fetched_at: Mapped[Dt] = _ts()
    # 程序从 PDF 文字层抽出来的原文（带 [图N] 占位）：大模型整理时拿它核对；extracted_text 是给人看的
    raw_text: Mapped[str] = mapped_column(Text, default="")
    tidied_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)  # 交给大模型整理过（成没成都记）
    # 按哪一版整理要求整理的（pdftext.TIDY_VERSION）。版本变了，后台把整理过的题面按新要求各重整一次
    tidy_version: Mapped[str | None] = mapped_column(String(16), nullable=True, default="")


class ProblemFigure(Base):
    """题面里的示意图：从 PDF 页面上裁下来的 PNG。题面文字里在对应位置写 [图N]。"""

    __tablename__ = "xdoj_problem_figures"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)  # 图几（从 1 起）
    page: Mapped[int] = mapped_column(Integer, default=1)
    png: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)


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
    notice_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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


class CodeAnalysis(Base):
    """按源码与题面指纹复用的代码画像分析。页面只排队，后台一次返回量规与复杂度。"""
    __tablename__ = "code_analyses"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[str] = mapped_column(String(8))
    server_id: Mapped[int] = mapped_column(Integer)
    problem_id: Mapped[int] = mapped_column(BigInteger)
    submission_id: Mapped[int] = mapped_column(BigInteger)
    fingerprint: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    retry_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[Dt] = _ts()
    updated_at: Mapped[Dt] = _ts()
    __table_args__ = (UniqueConstraint("user_id", "course", "fingerprint", name="uq_code_analysis_input"),
                     Index("ix_code_analysis_pending", "state", "retry_at"))


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
    data_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 教师账号对应的数据源
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / removed
    created_at: Mapped[Dt] = _ts()
    last_login_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    # 这次登录的是哪门课（C / D）。不存库：来自登录凭证，由 web/deps.py 填上
    course = "C"


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
# 通过后的回顾（chat/summary.py）：学生回答“当时卡住的关键是什么”，单独一个回合，回完就以这个原因结束。
# 它进“理解”评估，但不是求助：求助回合的统计、“没来问”都不算它。
REFLECT_END = "回顾"


class ChatRound(Base):
    __tablename__ = "chat_rounds"
    __table_args__ = (Index("ix_chat_round_conversation", "user_id", "course", "conversation_key", "status"),
                      {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 空：没有绑定到题
    course: Mapped[str] = mapped_column(String(8), default="C")  # 两门课的对话分开
    conversation_key: Mapped[str] = mapped_column(String(64), default="", server_default="")
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
    asked_back: Mapped[int] = mapped_column(Integer, default=0)  # 本回合反问过几次
    failed_replies: Mapped[int] = mapped_column(Integer, default=0)  # 反问后没答上的次数
    located: Mapped[bool] = mapped_column(Boolean, default=False)  # 已经把出问题的行和哪类情况指给他了
    help_count: Mapped[int] = mapped_column(Integer, default=0)  # 本回合给过几次帮助
    recall_stage: Mapped[str] = mapped_column(String(16), default="")  # 再次卡住时：asked / blanked
    quiz_triggered: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatTopic(Base):
    """知识问答里的一个问答主题（学生新建）。以前不绑定题目的“综合”对话没有主题（topic_id 为空），列表里叫“以前的问答”。"""

    __tablename__ = "chat_topics"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")
    title: Mapped[str] = mapped_column(String(40), default="")   # 取第一个问题的前一截
    created_at: Mapped[Dt] = _ts()
    last_message_at: Mapped[Dt] = _ts()


class ChatMessage(Base):
    failure_submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notice_read_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 线程：某道题（server_id + problem_id），或不绑定题目的通用线程（都为空）
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 知识问答的主题（chat_topics.id）；为空：题目对话，或以前的“综合”对话
    topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")  # 两门课的对话分开（“综合”对话也各有一个）
    role: Mapped[str] = mapped_column(String(8))  # student / agent
    kind: Mapped[str] = mapped_column(String(8), default="text")  # text / quiz
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # {"blocks": [...], "actions": [...]}
    quiz_attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (Index("ix_msg_thread", "user_id", "server_id", "problem_id", "id"),
                      Index("ux_msg_failure", "server_id", "failure_submission_id", unique=True),
                      {"sqlite_autoincrement": True})


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
    # 对话行为（参考 StudyChat 的对话行为分类）：concept / debug_specific / debug_vague / answer_seeking /
    # self_explain / short_reply / stuck / task / other。由意图、有没有自己的想法、按了哪个按钮定，见 profile.dialogue_act
    act: Mapped[str] = mapped_column(String(16), default="")
    answer_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 回答智能体的问题时答到点上没有
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
    key_line: Mapped[int] = mapped_column(Integer, default=1)  # 给过的代码里最关键的一行，再次卡住时挖空
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    bug_line: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 公布答案时指出的那一行
    bug_lines: Mapped[list] = mapped_column(JSON, default=list)  # 改到其中任何一行都算“定位对”
    judged_code: Mapped[str] = mapped_column(Text, default="")  # 题干问的是改动后的代码时，这里存改动后的样子
    standard_fix: Mapped[str] = mapped_column(Text, default="")  # 改对后的显示代码
    checks: Mapped[list] = mapped_column(JSON, default=list)  # [{"input": "...", "expected": "..."}]
    choices: Mapped[list] = mapped_column(JSON, default=list)  # 手机上的改法选项
    explanation: Mapped[str] = mapped_column(String(160), default="")
    scene: Mapped[str] = mapped_column(String(24), default="")  # 场景，用来区分“换了场景的变式题”
    source: Mapped[str] = mapped_column(String(16), default="bank")  # bank / own_code / llm / template
    source_problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 专门给某个学生出的
    start_line: Mapped[int] = mapped_column(Integer, default=1)  # 显示的代码从第几行开始（用他自己的代码出题时）
    origin: Mapped[dict] = mapped_column(JSON, default=dict)  # 出题的方式和依据（内部用）
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[Dt] = _ts()


class QuizAttempt(Base):
    discussion: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 本题反馈后的答疑，独立于测量事件和作业会话
    recap: Mapped[dict | None] = mapped_column(JSON, nullable=True)       # 做完以后回顾他以前在这里出过的错（app/recap.py）
    __tablename__ = "quiz_attempts"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("quiz_items.id"))
    quiz_type: Mapped[str] = mapped_column(String(8))
    skill_code: Mapped[str] = mapped_column(String(16), default="")
    reason: Mapped[str] = mapped_column(String(16), default="after_pass")  # after_pass / recall / revisit / review
    review_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # 独立作答（界面改造方案第 4 节，protocol 2）：先判断 → 再选位置/依据 → 判断有问题的再修改。
    # 首次判断、首次选行、首稿各记一次就不再改；之后的运行、提示、重试按顺序记进 events。
    protocol: Mapped[int] = mapped_column(Integer, default=1)  # 1：以前的“把握→判断→改”；2：现在的独立作答
    item_version: Mapped[str] = mapped_column(String(16), default="")  # 作答时题目内容的哈希（题目改了也能认出是哪一版）
    events: Mapped[list] = mapped_column(JSON, default=list)  # [{"t": 时间, "kind": shown/judge/locate/sample/hint/submit/feedback, ...}]
    first_lines: Mapped[list] = mapped_column(JSON, default=list)  # 首次选的行（显示的行号）
    locate_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 有错的题、判断有问题时：首次选行命中有效位置没有
    first_fix: Mapped[str] = mapped_column(Text, default="")  # 首稿
    first_fix_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_fix_clean: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 首稿之前没看提示、没运行公开样例
    hints: Mapped[int] = mapped_column(Integer, default=0)
    sample_runs: Mapped[int] = mapped_column(Integer, default=0)


class ReviewItem(Base):
    """复习（回访）：一个学生的一个技能。按记忆半衰期安排下一次什么时候复习，见 app/review.py。"""

    __tablename__ = "review_items"
    trigger_submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trigger_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    skill_code: Mapped[str] = mapped_column(String(16))
    # 为什么进来：stuck 卡住 / recurrence 改正后又出现 / quiz_wrong 小题答错 / heavy_help 讲了原理或给了代码才过 / lucky 蒙对
    origin: Mapped[str] = mapped_column(String(16), default="stuck")
    origin_server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(String(12), default="active")  # active / graduated / dropped
    half_life: Mapped[float] = mapped_column(Float, default=1.5)  # 记忆半衰期（天）
    last_success_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    due_at: Mapped[Dt] = _ts()
    reps: Mapped[int] = mapped_column(Integer, default=0)       # 复习过几次（含独立做对新题）
    lapses: Mapped[int] = mapped_column(Integer, default=0)     # 复习时没做对几次
    streak: Mapped[int] = mapped_column(Integer, default=0)     # 连续做对几次
    last_outcome: Mapped[str] = mapped_column(String(16), default="")
    attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 正在做的那道小题
    created_at: Mapped[Dt] = _ts()
    updated_at: Mapped[Dt] = _ts()
    __table_args__ = (Index("ix_review_user_state", "user_id", "state", "due_at"),)


class CodeFingerprint(Base):
    """通过的代码规范化之后的指纹：去掉注释和空白，标识符按出现顺序换成统一的名字。用来发现雷同。"""

    __tablename__ = "code_fingerprints"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32))
    problem_id: Mapped[int] = mapped_column(BigInteger)
    submit_time: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    norm_hash: Mapped[str] = mapped_column(String(64), index=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    matched_submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 雷同的那条更早的提交
    matched_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    share: Mapped[float | None] = mapped_column(Float, nullable=True)  # 这种写法在已通过的人里占多少
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_fp_problem", "server_id", "problem_id"),)


class LogRequest(Base):
    """教师按需申请学生的行为日志。每次申请都留记录（谁、什么时候、要了谁的、什么时间段、哪些内容）。"""

    __tablename__ = "log_requests"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    requester_id: Mapped[int] = mapped_column(Integer, index=True)
    requester_name: Mapped[str] = mapped_column(String(64), default="")
    requested_ids: Mapped[list] = mapped_column(JSON, default=list)   # 填的学号，原样记下
    granted_ids: Mapped[list] = mapped_column(JSON, default=list)     # 核对后属于他班里的
    denied_ids: Mapped[list] = mapped_column(JSON, default=list)      # 不在他班里的，没给
    class_keys: Mapped[list] = mapped_column(JSON, default=list)
    course: Mapped[str] = mapped_column(String(8), default="C")  # 申请时登录的课程：只导出这门课的记录
    date_from: Mapped[Dt] = mapped_column(UTCDateTime)
    date_to: Mapped[Dt] = mapped_column(UTCDateTime)
    kinds: Mapped[list] = mapped_column(JSON, default=list)
    include_text: Mapped[bool] = mapped_column(Boolean, default=False)
    include_code: Mapped[bool] = mapped_column(Boolean, default=False)   # 再带提交的源码（要先有 include_text）
    deidentified: Mapped[bool] = mapped_column(Boolean, default=False)   # 不再使用（导出一律带学号）；列留着，免得再迁移
    fmt: Mapped[str] = mapped_column(String(8), default="csv")
    purpose: Mapped[str] = mapped_column(String(200), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="ready")  # ready / expired / revoked
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Dt] = _ts()
    expires_at: Mapped[Dt] = _ts()
    last_download_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)


class UiEvent(Base):
    __tablename__ = "ui_events"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # 固定枚举，见 taxonomy.enums.UI_EVENTS
    target: Mapped[str] = mapped_column(String(64), default="")
    course: Mapped[str] = mapped_column(String(8), default="C")  # 在哪门课的页面上
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
    reliability: Mapped[float] = mapped_column(Float, default=1.0)  # 证据可信度，只有 v2 用
    __table_args__ = (
        UniqueConstraint("source_key", "skill_code", "success"),
        Index("ix_evidence_user_skill", "user_id", "skill_code"),
    )


# ============================================================ 图模型（只在教师端显示）
class GraphPosterior(Base):
    """贝叶斯网络反推出的“会”的概率。每天夜里全图更新一次。学生端和智能体都不读这张表。"""

    __tablename__ = "graph_posteriors"
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    p: Mapped[float] = mapped_column(Float)
    has_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str] = mapped_column(String(24), default="")
    updated_at: Mapped[Dt] = _ts()


class PredictionLog(Base):
    """几个模型各自对“下一次能不能独立做对”的预测，和实际结果。用来按周比较谁猜得准。

    p_fused：多维融合（profile.py），用当时学到的权重；p_v2_base：v2 去掉智能体评估的证据（消融对照）；
    features：预测那一刻各个维度的值，用来学维度的权重。
    """

    __tablename__ = "prediction_log"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    skill_code: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(96), unique=True)  # 哪一次“新题的第一次提交”
    ts: Mapped[Dt] = _ts()
    outcome: Mapped[bool] = mapped_column(Boolean)  # 这次是不是一次就独立做对了
    p_simple: Mapped[float] = mapped_column(Float)   # v1
    p_v2: Mapped[float] = mapped_column(Float, default=0.5)
    p_graph: Mapped[float] = mapped_column(Float)
    p_fused: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_v2_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(24), default="")
    # 掌握分（档位就按它定）：这一周学到的参数算的，和经验值参数算的，同时记下来比谁准（app/learn.py）
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 当时可用的版本、首交条件和回算时间
    p_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_score_fixed: Mapped[float | None] = mapped_column(Float, nullable=True)
    params_version: Mapped[str] = mapped_column(String(24), default="")


class MasteryParams(Base):
    """每周学一次的掌握程度参数（app/learn.py）：每类证据的系数、半衰期的起点和增长率。学完冻结，下一周都用这一版。
    learned 为假表示数据不够、用的还是经验值。nll_prior / nll_fit：拟合用的那些机会上，经验值和新参数的平均对数损失。"""

    __tablename__ = "mastery_params"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    course: Mapped[str] = mapped_column(String(8), index=True)
    version: Mapped[str] = mapped_column(String(24), unique=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    n: Mapped[int] = mapped_column(Integer, default=0)
    nll_prior: Mapped[float | None] = mapped_column(Float, nullable=True)
    nll_fit: Mapped[float | None] = mapped_column(Float, nullable=True)
    learned: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    fitted_at: Mapped[Dt] = _ts()


# ============================================================ 智能体评估、多维画像
class AgentAssessment(Base):
    """智能体对“理解”的评估：一个求助回合结束后，看学生自己说的话，按 0~3 的量规打分。

    0 不理解（说不出、或说法有错）；1 能照做（按提示改对了，说不出为什么）；2 能解释（能用自己的话说出原因）；
    3 能迁移（能说出别的情况也会怎样，或者自己发现同类问题）。
    大模型给的等级要附上学生原话（evidence），原话必须真的出现在他的消息里；和程序能确定的事实冲突时下调（adjusted）。
    rule_level 是只用规则得出的等级，用来算两者的一致率。
    """

    __tablename__ = "agent_assessments"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")
    skill_code: Mapped[str] = mapped_column(String(16), index=True)
    # 评的依据：chat 对话里他说的话（round_id）；code 他在 OJ 上怎么改的代码（没问过问题的学生也有）
    basis: Mapped[str] = mapped_column(String(8), default="chat")
    round_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    server_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submission_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # code：评的是哪次提交（改对的那次）
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)       # 最后采用的等级；None：证据不够，不评
    raw_level: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 大模型原来给的
    rule_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)              # 学生原话
    misconception: Mapped[str] = mapped_column(String(120), default="")    # 看出来的错误想法
    adjusted: Mapped[str] = mapped_column(String(80), default="")          # 为什么下调
    source: Mapped[str] = mapped_column(String(8), default="rule")         # llm / rule
    prompt_version: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (UniqueConstraint("round_id", "skill_code", name="uq_assessment_round_skill"),)


class AgentReview(Base):
    """以前给老师写的学情评语，已经不写了（老师没时间逐个看，大模型用在分析上）。表留着，免得再迁移。"""

    __tablename__ = "agent_reviews"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")
    profile_hash: Mapped[str] = mapped_column(String(64), default="")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(8), default="rule")         # llm / rule
    requested_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Dt] = _ts()


class FusionWeightsHistory(Base):
    __tablename__ = "fusion_weights_history"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    course: Mapped[str] = mapped_column(String(8), index=True)
    fitted_at: Mapped[Dt] = _ts()
    weights: Mapped[dict] = mapped_column(JSON)


class ModelWeights(Base):
    """多维融合的维度权重：按“下一次新题能不能一次做对”学出来（每门课一组），每天夜里更新。"""

    __tablename__ = "model_weights"
    course: Mapped[str] = mapped_column(String(8), primary_key=True)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    n: Mapped[int] = mapped_column(Integer, default=0)          # 用了多少次预测
    brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    learned: Mapped[bool] = mapped_column(Boolean, default=False)  # 数据不够时用的是默认权重
    updated_at: Mapped[Dt] = _ts()


# ============================================================ 跨课程的联系（数据结构这边用）
class CrossLink(Base):
    """C 语言的技能和数据结构的技能之间带类型的联系。

    来源：seed 人工初稿（taxonomy/prereq.py 的跨课程边）、llm 智能体提出、teacher 老师加的。
    状态：proposed 待检验、accepted 采用、rejected 不用。只有采用的联系进入数据结构的先验（mastery v2）。
    采用与否每天夜里自动决定（cross.auto_links）；reviewed_by 不为空表示老师改过，自动的不再动它。
    数据检验（每天夜里）：C 那头掌握了（估计 ≥ 0.6）和没掌握的学生，数据结构这头新题一次做对的比例，
    以及“前者更高”的后验概率；结论 supported / unsupported / unknown 决定这条联系在先验里的分量。
    """

    __tablename__ = "cross_links"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    c_skill: Mapped[str] = mapped_column(String(16), index=True)
    d_skill: Mapped[str] = mapped_column(String(16), index=True)
    relation: Mapped[str] = mapped_column(String(16), default="prerequisite")
    source: Mapped[str] = mapped_column(String(8), default="seed")
    status: Mapped[str] = mapped_column(String(10), default="accepted")
    rationale: Mapped[str] = mapped_column(String(120), default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[Dt] = _ts()
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    n_with: Mapped[int] = mapped_column(Integer, default=0)       # C 那头掌握了的学生的新题次数
    n_without: Mapped[int] = mapped_column(Integer, default=0)
    rate_with: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_without: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_support: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(12), default="unknown")
    checked_at: Mapped[Dt | None] = mapped_column(UTCDateTime, nullable=True)
    __table_args__ = (UniqueConstraint("c_skill", "d_skill", name="uq_cross_link"),)


class DecisionLog(Base):
    """系统替学生做的决定（研究用，app/decisions.py）：复习先出哪个技能、出正确还是有错的代码、提交没过时发不发主动消息。
    记下当时的候选和各自的依据、规则版本、选了哪个、选中的概率、结果在哪。学生端任何接口都不返回这张表。"""

    __tablename__ = "decision_logs"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")
    kind: Mapped[str] = mapped_column(String(16), index=True)        # review_pick / quiz_plan / notice
    ts: Mapped[Dt] = _ts()
    rule: Mapped[str] = mapped_column(String(32), default="")        # 按哪条规则、哪一版定的
    candidates: Mapped[list] = mapped_column(JSON, default=list)     # 当时的候选，每个带依据和分数
    chosen: Mapped[str] = mapped_column(String(64), default="")      # 选了哪个
    probability: Mapped[float] = mapped_column(Float, default=1.0)   # 按规则，选中它的概率（固定排序是 1）
    context: Mapped[dict] = mapped_column(JSON, default=dict)        # 模型版本和别的依据
    ref: Mapped[dict] = mapped_column(JSON, default=dict)            # 结果在哪：复习项、作答、小题、消息、提交的编号


class ProblemPlan(Base):
    """一道题的解题步骤（作业引导：还没做的题问“怎么做”时给的流程图，app/chat/plan.py）。
    按（服务器, 题目）存一份，所有学生共用；题面变了（content_hash）才重新生成。"""

    __tablename__ = "problem_plans"
    server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)   # [{"title","how","kp"}]
    source: Mapped[str] = mapped_column(String(16), default="llm")   # llm：已生成；failed：暂时失败；invalid：内容校验不通过
    version: Mapped[str] = mapped_column(String(16), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[Dt] = _ts()


class HumanRating(Base):
    """不再使用（盲评不做了，2026-11-20）；表留着，免得再写迁移。原来是：老师盲评“理解”：看和大模型同样的材料（不显示学生是谁、不显示大模型和规则的等级），按同一个 0~3 量规评。
    用来算老师和大模型（原始、约束后）、规则的一致率（app/rating.py）。level 为空：老师看不出来。"""

    __tablename__ = "human_ratings"
    id: Mapped[int] = mapped_column(AutoPK, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(Integer, index=True)
    rater_id: Mapped[int] = mapped_column(Integer, index=True)
    course: Mapped[str] = mapped_column(String(8), default="C")
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 从看到材料到评完用了几秒
    created_at: Mapped[Dt] = _ts()
    __table_args__ = (UniqueConstraint("assessment_id", "rater_id", name="uq_human_rating"),)
