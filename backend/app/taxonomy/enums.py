"""固定枚举（附件一 5.3）。只能改代码来增加取值。"""
from __future__ import annotations

# 意图
INTENTS = {
    "DEBUG": "调试求助",
    "CONCEPT": "概念提问",
    "TASK": "题意澄清",
    "ANSWER": "索要答案",
    "REPLY": "回答智能体的问题",
    "STUCK": "表示不会、没思路",
    "OTHER": "其他",
}
WILLINGNESS = ("HIGH", "LOW", "UNKNOWN")
ICAP = ("passive", "active", "constructive", "interactive", "")

# 掌握档位
LEVEL_MASTERED = "已掌握"
LEVEL_BASIC = "基本掌握"
LEVEL_WEAK = "待加强"
LEVEL_NONE = "还没练到"
LEVELS = (LEVEL_MASTERED, LEVEL_BASIC, LEVEL_WEAK, LEVEL_NONE)
LEVEL_SEGMENTS = {LEVEL_MASTERED: 3, LEVEL_BASIC: 2, LEVEL_WEAK: 1, LEVEL_NONE: 0}

# 帮助级别
HELP_LEVELS = ("NA", "H0", "H1", "H2", "H3")
HELP_ORDER = {"NA": -1, "H0": 0, "H1": 1, "H2": 2, "H3": 3}

# 透露级别：甲 只说哪类情况；乙 给一个自己构造的简单例子
REVEAL_LEVELS = ("甲", "乙")

END_REASONS = ("通过", "学生表示懂了", "换题", "超时", "放弃")
QUIZ_TYPES = ("JUDGE", "FIX")
CONFIDENCE = ("没把握", "一半", "很有把握")
STUCK_RESOLUTIONS = ("pending", "recalled", "blank_filled", "example_given", "exercise")

# 评测结果（XDOJ 的文字结果归一化成固定编码；认不出的原样保存并告警）
JUDGEMENTS = {
    "AC": "通过",
    "WA": "答案错误",
    "CE": "编译错误",
    "RE": "运行错误",
    "TLE": "超时",
    "MLE": "内存超限",
    "PE": "格式错误",
    "OLE": "输出超限",
    "PENDING": "评测中",
    "UNKNOWN": "结果未知",
}

_JUDGEMENT_KEYWORDS = [
    ("PENDING", ("未评判", "等待", "评测中", "排队", "pending", "judging", "queue", "running", "waiting")),
    ("CE", ("编译", "compile")),
    ("TLE", ("超时", "时间超限", "time limit")),
    ("MLE", ("内存", "memory limit")),
    ("OLE", ("输出超限", "output limit")),
    ("PE", ("格式", "presentation")),
    ("RE", ("运行错误", "运行时", "runtime", "段错误", "异常")),
    ("WA", ("答案错误", "错误", "wrong", "不正确", "未通过")),
    ("AC", ("正确", "通过", "accepted", "答案正确")),
]


def normalize_judgement(text: str | None) -> str:
    """把 XDOJ 的文字结果归到固定编码。顺序有讲究：先认“等待”“编译”等，再认“错误”，最后认“正确”。"""
    if not text:
        return "UNKNOWN"
    low = str(text).strip().lower()
    if not low:
        return "UNKNOWN"
    for code, words in _JUDGEMENT_KEYWORDS:
        if any(w in low for w in words):
            return code
    return "UNKNOWN"


# 界面事件
UI_EVENTS = (
    "help_think",        # 点“我想自己想想”
    "help_hint",         # 点“再给点提示”
    "help_tell",         # 点“直接讲给我”
    "open_submission",   # 点开某条提交
    "ask_why",           # 点“问问为什么错”
    "copy_code",         # 复制了回复里的代码
    "open_diff",         # 打开差异视图
    "open_problem",      # 切到某道题
    "practice",          # 学情页点“去练一题”
    "login",             # 登录
    "open_review",       # 打开复习页
    "review_start",      # 开始一道复习题
    "review_wait",       # 自动出题暂不可用，短暂退避
    "review_seed",       # 补排历史错误技能的复习
    "open_problem_text", # 打开题目
    "ask_with_problem",  # 知识问答里选了“结合当前题”再问
    "open_code",         # 作业引导右栏点“看代码”
    "open_knowledge",    # 打开知识问答
    "open_records",      # 学情页点“全部提交记录”
)

# 进入掌握程度计算的事件
EVIDENCE_EVENTS = (
    "pass_first_try",       # 新题一次提交就独立通过
    "pass_self_fixed",      # 自己改了几次后独立通过
    "pass_after_h0_h1",
    "pass_after_h2",
    "pass_after_h3",
    "stuck_first",
    "stuck_again",
    "quiz_judge_right",
    "quiz_fix_right",
    "quiz_fix_second_try",
    "quiz_fix_bypass",
    "quiz_fix_failed",
    "quiz_judge_wrong",
    "quiz_streak_bonus",
)
