"""配置：全部来自环境变量（也读项目根目录的 .env）。

密码、密钥一律不写进代码和日志。可调的算法参数集中放在本文件末尾，都带版本号。
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """读 KEY=VALUE 形式的 .env，不覆盖已有的环境变量。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


APP_ENV = _env("APP_ENV", "dev")  # dev / prod
DATA_DIR = Path(_env("DATA_DIR", str(ROOT_DIR / "data")))
DATABASE_URL = _env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'agent.sqlite3'}")

# 会话签名、教师 XDOJ 密码加密都从这把密钥派生。生产环境必须设置。
SECRET_KEY = _env("SECRET_KEY", "")
DEV_SECRET_FALLBACK = "dev-only-not-for-production"

# 登录方式。dev：学号 + 统一口令，只供开发和演示。
# 学生正式怎么登录（转发 XDOJ 试登）还在待确认，确认前不实现。
AUTH_MODE = _env("AUTH_MODE", "dev")
DEV_LOGIN_PASSWORD = _env("DEV_LOGIN_PASSWORD", "")
SESSION_HOURS = _env_int("SESSION_HOURS", 12)

# 大模型（DeepSeek，OpenAI 兼容接口）。不填 key 时，对话走规则分析和固定模板。
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat")
# 每百万 token 的价格（元），用于记录费用；按实际计费标准填写。
LLM_PRICE_IN = _env_float("LLM_PRICE_IN", 2.0)
LLM_PRICE_OUT = _env_float("LLM_PRICE_OUT", 8.0)

# XDOJ：试点阶段的数据源从环境变量读；以后由教师在系统里录入、加密保存。
# 服务器地址见 app/servers.py；XDOJ_SERVER_CODE=c 时不填 XDOJ_BASE_URL 就用 C 语言那台。
XDOJ_BASE_URL = _env("XDOJ_BASE_URL", "").rstrip("/")
XDOJ_USER = _env("XDOJ_USER", "")
XDOJ_PASS = _env("XDOJ_PASS", "")
XDOJ_SERVER_CODE = _env("XDOJ_SERVER_CODE", "c")
XDOJ_SERVER_NAME = _env("XDOJ_SERVER_NAME", "C 语言")
XDOJ_TERM = _env("XDOJ_TERM", "")
XDOJ_MIN_INTERVAL_FLOOR = 0.3
XDOJ_MIN_INTERVAL = max(XDOJ_MIN_INTERVAL_FLOOR, _env_float("XDOJ_MIN_INTERVAL", 0.5))
XDOJ_CONNECT_TIMEOUT = 10.0
XDOJ_READ_TIMEOUT = 30.0

# 沙箱：local 在本进程所在机器上限时限内存运行（开发用）；socket 连独立的沙箱容器。
SANDBOX_MODE = _env("SANDBOX_MODE", "local")
SANDBOX_SOCKET = _env("SANDBOX_SOCKET", "/run/sandbox/sandbox.sock")
SANDBOX_CPU_SECONDS = 1
SANDBOX_MEMORY_MB = 64
SANDBOX_OUTPUT_LIMIT = 64 * 1024

FRONTEND_DIST = Path(_env("FRONTEND_DIST", str(ROOT_DIR / "frontend" / "dist")))

# ---------------------------------------------------------------- 同步参数
SYNC = {
    "page_rows": 100,
    "poll_active_seconds": 10,
    "poll_normal_seconds": 60,
    "poll_night_seconds": 300,
    "active_window_minutes": 10,
    "night_hours": (1, 7),
    "night_quiet_minutes": 30,
    "pending_judge_delays": [2, 5, 10, 30, 60],
    "recheck_interval_seconds": 3600,
    "recheck_window_hours": 24,
    "meta_hour": 3,
    "max_retries": 5,
    "trigger_merge_seconds": 5,
    "fail_streak_slowdown": 10,
    "idle_sleep_seconds": 0.5,
}

# ---------------------------------------------------------------- 版本号
TAXONOMY_VERSION = "0.1"      # 知识点表、技能表
ERROR_LIBRARY_VERSION = "1.0"  # 错误类型库
LABELER_VERSION = "2026-11-16a"
PROMPT_VERSION = "2026-11-16a"
WORKFLOW_VERSION = "2026-11-16a"
MASTERY_VERSION = "2026-11-16a"
GRAPH_VERSION = "2026-11-17a"

# ---------------------------------------------------------------- 学情（附件一第 8 节）
# 权重都是初始经验值，待一学期后用“预测下一次能不能独立做对”校准。
MASTERY = {
    "a0": 1.0,
    "b0": 1.0,
    "decay": 0.98,
    "w_first_try_pass": 1.0,
    "w_self_fixed_pass": 0.5,
    "w_pass_after_h0_h1": 0.5,
    "w_pass_after_h2": 0.2,
    "w_pass_after_h3": 0.0,
    "w_stuck_first": 1.0,
    "w_stuck_again": 1.5,
    "w_quiz_right": 1.0,
    "w_quiz_fix_second_try": (0.5, 0.5),
    "w_quiz_fix_bypass": (0.5, 0.5),
    "w_quiz_wrong": 1.0,
    "revisit_bonus": 1.5,
    "unsure_right_factor": 0.5,
    "sure_wrong_factor": 1.5,
    "streak_bonus": 1.0,
    "streak_bonus_days": 7,
    "level_weak_below": 0.6,
    "level_mastered_from": 0.8,
    "mastered_min_evidence": 3.0,
    "min_evidence": 1.0,
    # 一道题的证据记到它的主技能和次技能上。次技能按这个比例折算；1.0 表示和主技能一样算。
    # 文档没有规定，这是暂定值，见 docs/问题清单.md。
    "secondary_skill_factor": 1.0,
}

# ---------------------------------------------------------------- 小题（附件一第 6 节）
QUIZ = {
    "daily_limit": _env_int("QUIZ_DAILY_LIMIT", 3),  # 待定
    "per_skill_daily_limit": 2,
    "max_changed_lines": 3,
    "max_tries": 2,
    "max_code_chars": 1200,
    "correct_ratio": (0.4, 0.5),
    "ratio_window": 10,
    "pass_after_submissions": 5,
    "careless_same_choice_run": 10,
    "careless_min_ms": 1000,
}

# ---------------------------------------------------------------- 对话（附件一第 5 节）
CHAT = {
    "round_timeout_minutes": 30,
    "bind_recent_minutes": 30,
    "quick_hint_seconds": 10,
    "no_progress_submissions": 5,
    "h3_max_lines": 8,
    "other_max_lines": 2,
    "reply_max_chars": 150,
    "concept_max_chars": 300,
    "quote_min_lines": 2,
    "quote_max_lines": 8,
    "low_confidence": 0.5,
}


def secret_key() -> str:
    if SECRET_KEY:
        return SECRET_KEY
    if APP_ENV == "prod":
        raise RuntimeError("生产环境必须设置 SECRET_KEY")
    return DEV_SECRET_FALLBACK


# ---------------------------------------------------------------- 图模型（附件一第 9 节，只在教师端显示）
# 参数先用经验值起步，攒够一学期数据后再用 EM 估计。
GRAPH = {
    "root_prior": 0.5,     # 没有先修的技能，事先“会”的概率
    "beta": -1.0,          # 先修都不会时，仍然掌握这个技能的倾向
    "gamma": 2.0,          # 每个先修技能的影响（所有边先取同一个值）
    "guess": 0.2,          # 没掌握也碰巧做对
    "slip": 0.1,           # 掌握了也没做对
    "sweeps": 600,
    "burn_in": 150,
    "seed": 20261117,
    "big_gap": 0.3,        # 两个模型相差多少算“差别大”
}
