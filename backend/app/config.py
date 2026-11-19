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
    """.env 里写了 KEY= 但没填值，和没写一样，用默认值。"""
    value = os.environ.get(name, "")
    return value if value.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
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
# 网页进程里顺带跑后台同步（一个进程就够用）。docker compose 里同步单独一个容器，那里设成 0。
SYNC_IN_WEB = _env("SYNC_IN_WEB", "1") == "1"
AUTH_MODE = _env("AUTH_MODE", "xdoj")  # xdoj：学生去 XDOJ 核对密码；dev：真实名单里的学生也能用演示口令（只用于调试）
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
# 数据结构那台 XDOJ 的地址（还没给出时留空：登录页只有 C 语言）
XDOJ_DS_URL = _env("XDOJ_DS_URL", "").rstrip("/")
XDOJ_MIN_INTERVAL_FLOOR = 0.2
XDOJ_MIN_INTERVAL = max(XDOJ_MIN_INTERVAL_FLOOR, _env_float("XDOJ_MIN_INTERVAL", 0.2))
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
    "meta_hour": 3,               # 每天夜里的任务（图模型、权重、跨课程检验）在这个钟点以后做
    "meta_interval_minutes": 120, # 名单和作业每两小时自动同步一次（新加的学生、新布置的作业当天就能看到）
    "meta_retry_minutes": 10,     # 同步失败、或者发现了不认识的学生和作业：最快 10 分钟后再同步一次
    "max_retries": 5,
    "trigger_merge_seconds": 5,
    "fail_streak_slowdown": 10,
    "idle_sleep_seconds": 0.5,
}

# ---------------------------------------------------------------- 版本号
TAXONOMY_VERSION = "0.1"      # 知识点表、技能表
ERROR_LIBRARY_VERSION = "1.0"  # 错误类型库
LABELER_VERSION = "2026-11-16a"
PROMPT_VERSION = "2026-11-18d"
CHAT_TEACHING_PROMPT_FILE = _env("CHAT_TEACHING_PROMPT_FILE", "")
WORKFLOW_VERSION = "2026-11-18a"
MASTERY_VERSION = "2026-11-18a"
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
    # 没通过的提交（只在 v2 用）：记到这次错误所属的技能上（程序或大模型标注的主错误，置信度 ≥ 0.5）。
    # 同一道题上同一个技能，第一次没过记 0.5，第二次 0.25，再往后不记：交得多不该被无限扣分。
    "w_submit_failed": (0.5, 0.25),
}

# 学生端、智能体、复习安排用哪个版本：v1 是附件一第 8 节原样；v2 见 app/mastery.py 开头的说明
MASTERY_MODEL = _env("MASTERY_MODEL", "v2")
MASTERY_V2 = {
    # v2 对几类证据的分量另有调整（v1 照附件一原样）：自己改对的通过，原来只算一次做对的一半；
    # 现在没通过的那几次已经单独记到出错的技能上了，改对本身就是会了的证据，按 0.8 算
    "event_scale": {"pass_self_fixed": 1.6},
    "prior_strength": 1.0,      # 图先验的强度（伪计数）
    "half_life_base": 35.0,     # 证据半衰期的起点（天），和 v1 的 λ=0.98 差不多
    "half_life_growth": 1.4,    # 每多一天间隔开的独立成功乘它；每次失败除以它
    "half_life_min": 10.0,
    "half_life_max": 240.0,
    "min_raw": 0.5,             # 至少有这么多（不衰减的）证据才定档
    "weak_below": 0.5,          # 有还没改对的失败、而且估计低于它，才算待加强
    # 一道题后来改对了：前面的失败只按这个比例算；错在别的技能上的，改对它按失败分量的这个比例算一次“会”
    "resolved_fail_factor": 0.2,
    "resolved_fix_credit": 1.0,
    "mastered_lower": 0.5,      # 80% 区间的下限不低于它，才算已掌握（大约三道题做得好：一次做对或很快改对）
    "min_current_success": 0.3, # 只有成功证据时：衰减后还剩这么多，才算基本掌握；否则算还没练到（太久没练）
    # 雷同：和班里更早的通过代码规范化后一样，这种写法少见（不到这个比例的人这样写），时间挨得近
    "copy_rare_share": 0.3,
    "copy_window_hours": 6.0,
    "copy_min_tokens": 40,
    "copy_reliability": 0.4,
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

# ---------------------------------------------------------------- 复习（回访），见 app/review.py
REVIEW = {
    "spot_check_every": 10,
    "target_retention": 0.75,      # 预测的记忆保持降到这么多时到期
    "initial_half_life": {          # 刚进来时的记忆半衰期（天）：第一次复习大约在 0.4 倍半衰期之后
        "stuck": 2.5, "heavy_help": 2.5, "recurrence": 2.5, "quiz_wrong": 2.5, "lucky": 5.0,
        "failed": 2.5, "corrected": 3.0, "spot_check": 14.0,
    },
    "growth": {"很有把握": 2.5, "一半": 2.0, "没把握": 1.5, "second_try": 1.3,
               "practice_first_try": 2.5, "practice_fixed": 1.5},
    "min_half_life": 1.2,
    "max_half_life": 120.0,
    "min_gap_days": 0.75,           # 两次复习至少隔这么久
    "spacing_hours": 12,            # 隔不到这么久的成功不算“间隔开的”，不加长半衰期
    "graduate_streak": 3,
    "graduate_half_life": 20.0,
    "daily_limit": 2,
}

# ---------------------------------------------------------------- 对话（附件一第 5 节）
# 智能体主动发消息（app/chat/proactive.py）
PROACTIVE = {
    "recent_minutes": 120,   # 只对两小时内交的提交发；回填的旧提交不发
    "quiet_minutes": 10,     # 他在这道题上和智能体说话不到 10 分钟：不插嘴
    "daily_limit": 4,        # 每人每天最多几条
    "stuck_attempts": 3,     # 同一道题第几次还没过、错在同一处，算卡住
}

# 智能体评估“理解”（app/assess.py）。等级 0~3 记成证据：会 / 不会，分量 × 置信度。
# 这些证据只改变估计值，不算“练过”：只有对话、没有做题的技能不会因此有档位。
AGENT_ASSESS = {
    "evidence": {3: (True, 0.8), 2: (True, 0.5), 1: (False, 0.3), 0: (False, 0.6)},
    "min_confidence": 0.4,       # 大模型自己都没把握的不记证据（照样存下来给老师看）
    "batch": 5,                  # 后台空闲时一次评几个回合
    "max_wait_hours": 48,        # 结束超过两天还没评的回合不再评
    "code_days": 14,             # 看代码评：只评最近两周改对的题（第一次部署时不去评很久以前的）
}

# 多维画像和融合（app/profile.py）。默认权重是融合用的起点，数据够了按“下一次新题能不能一次做对”学
PROFILE = {
    "dims": ("perf", "understanding", "independence", "debugging", "retention", "prereq"),
    "default_weights": {"bias": 0.0, "perf": 1.0, "understanding": 0.35, "independence": 0.3, "debugging": 0.3,
                        "retention": 0.2, "prereq": 0.25},
    "min_samples": 30,           # 少于这么多次预测，用默认权重
    "ridge": 2.0,                # 学权重时往默认权重收缩的力度
    "understanding_half_life": 30.0,   # 理解的评估按 30 天半衰期衰减
    "independence_weight": {"pass_first_try": 1.0, "pass_self_fixed": 0.85, "pass_after_h0_h1": 0.6,
                            "pass_after_h2": 0.3, "pass_after_h3": 0.0},
    "calibration_value": {"很有把握": 0.9, "一半": 0.6, "没把握": 0.3},
}

# 跨课程（app/cross.py）。数据结构的技能的先验 = 先修（含 C 语言那头）估计的加权平均，
# 先验的分量 n0 相当于几次作答：数据结构这头的证据越多，C 语言那头的影响越小（η = n0 / (n0 + n)）。
CROSS = {
    "n0": 2.0,
    # 关系类型和 TransKT（Han 等 2025）的四种一致；similar 是以前的类型，只为兼容旧数据保留，不再提出
    "relation": {"prerequisite": ("前置", 1.0), "used_for": ("用到", 0.8), "part_of": ("组成部分", 0.7),
                 "hyponym_of": ("是一种", 0.6), "similar": ("相似", 0.4)},
    "reliability": {"supported": 1.0, "unknown": 0.7, "unsupported": 0.3},   # 数据检验的结论
    "mastered": 0.6,          # C 那头估计到这么多算掌握了
    "min_n": 10,              # 两组合起来至少这么多次新题才下结论
    "min_group": 3,           # 每组至少这么多次
    "support_prob": 0.9,      # “掌握了的更容易一次做对”的后验概率到这么多算数据支持
    "weak": 0.45,             # C 基础预警：估计低于这个算薄弱
    "max_proposals": 8,       # 智能体一次最多提几条
    "propose_per_night": 6,   # 每天夜里智能体自动给几个还没提过的数据结构知识点补充联系
    "auto_accept_confidence": 0.7,  # 智能体提出时把握到这么多，自动采用（还没检验的分量是 0.7）
}

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
