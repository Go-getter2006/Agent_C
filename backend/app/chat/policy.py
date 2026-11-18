"""第 6 步：策略规则表（附件一 5.4）。从上往下，第一条满足的生效。纯函数，不查库。"""
from __future__ import annotations

from dataclasses import dataclass

from ..taxonomy import HELP_ORDER, LEVEL_NONE, LEVEL_WEAK

LEVELS = ["H0", "H1", "H2", "H3"]


@dataclass
class PolicyInput:
    intent: str                      # DEBUG / CONCEPT / TASK / ANSWER / REPLY / STUCK / OTHER
    action: str | None = None        # think / hint / tell：点了哪个求助按钮
    bound: bool = False              # 绑定到了具体的题
    has_code: bool = False           # 有可看的代码（提交过，或消息里贴了）
    has_failure: bool = False        # 绑定的提交没通过
    diag_confidence: float = 0.0
    answer_correct: bool | None = None
    understood: bool = False         # 学生表示懂了
    willingness: str = "UNKNOWN"
    base_level: str = LEVEL_NONE
    last_help: str = "NA"            # 本回合上一次的帮助级别
    help_count: int = 0              # 本回合已经帮过几次
    failed_replies: int = 0          # 本回合反问后没答上的次数
    located: bool = False            # 上一次已经指出了位置和哪类情况
    same_error_resubmit: bool = False
    no_progress: bool = False        # 同一题连续 5 次提交没有进展
    needs_example: bool = False      # 明确完全不会或希望看局部示例


@dataclass
class Decision:
    rule_id: str
    help_level: str = "NA"
    reveal_level: str = "甲"
    kind: str = "help"   # other / concept / task / ask_which / locate / affirm / stuck / help / ack


def is_weak(level: str) -> bool:
    """“待加强”和“还没练到”都按基础弱处理。"""
    return level in (LEVEL_WEAK, LEVEL_NONE)


def escalate(last: str, located: bool) -> str:
    """在本回合上一次的基础上升一级，最高 H3。

    反问时如果已经把出问题的行和哪类情况都指出来了（相当于做过 H1），下一级直接到 H2，
    和附件一例二一致。
    """
    if last in ("NA", ""):
        return "H1"
    if last == "H0":
        return "H2" if located else "H1"
    return LEVELS[min(HELP_ORDER[last] + 1, 3)]


def _reveal(level: str) -> str:
    # 默认甲，只说是哪类情况；到 H2 时可以升到乙，给一个自己构造的简单例子
    return "乙" if HELP_ORDER.get(level, -1) >= 2 else "甲"


def _help(rule: str, level: str) -> Decision:
    if level == "H3":
        return Decision(rule, "H3", "乙", "stuck")  # 要给示例代码时，一律按卡点处理（5.6）
    return Decision(rule, level, _reveal(level), "help")


def decide(p: PolicyInput) -> Decision:
    if p.action == "think":
        return Decision("R00", "NA", "甲", "ack")
    if p.bound and p.needs_example:
        return Decision("R07", "H3", "乙", "stuck")
    if p.action is None:
        if p.intent == "OTHER":
            return Decision("R01", "NA", "甲", "other")
        if p.intent == "CONCEPT":
            return Decision("R02", "NA", "乙", "concept")
        if p.intent == "TASK" and p.bound:
            return Decision("R03", "NA", "甲", "task")
    if not p.bound or (p.intent == "DEBUG" and not p.has_code):
        return Decision("R04", "NA", "甲", "ask_which")
    if p.understood and p.action is None:
        return Decision("R06", "NA", "甲", "affirm")
    if p.has_failure and p.diag_confidence < 0.5 and p.intent not in ("STUCK", "ANSWER") and p.action is None:
        return Decision("R05", "H0", "甲", "locate")
    if p.intent == "REPLY" and p.answer_correct and p.action is None:
        return Decision("R06", "NA", "甲", "affirm")
    if p.action is None and (p.intent == "STUCK"
                             or (p.intent == "REPLY" and p.answer_correct is False and p.failed_replies >= 1)):
        return Decision("R07", "H3", "乙", "stuck")
    if (p.intent == "REPLY" and p.action is None) or p.action == "hint" or p.same_error_resubmit:
        return _help("R08", escalate(p.last_help, p.located))
    if p.no_progress and p.action is None and p.intent != "ANSWER":
        level = "H2" if HELP_ORDER.get(p.last_help, -1) < 2 else p.last_help
        return _help("R09", level)
    if p.willingness == "LOW" or p.intent == "ANSWER" or p.action == "tell":
        level = "H2" if is_weak(p.base_level) else "H1"
        if HELP_ORDER.get(p.last_help, -1) >= HELP_ORDER[level]:
            level = escalate(p.last_help, p.located)  # 已经到过这一级：再往上一级
        return _help("R10", level)
    if p.help_count == 0:
        return _help("R11", "H1" if is_weak(p.base_level) else "H0")
    # 兜底：回合中途又问了一个调试问题，保持当前的帮助级别，不升级
    return _help("R00", p.last_help if p.last_help in LEVELS else "H0")


def willingness_of(intent: str, has_attempt: bool, action: str | None, seconds_since_reply: float | None,
                   quick_hint_seconds: int) -> str:
    """意愿（规则）：有自己的尝试或回答了反问是 HIGH；直接要答案、很快就点“再给点提示”是 LOW。"""
    if action == "tell" or intent == "ANSWER":
        return "LOW"
    if action == "hint" and seconds_since_reply is not None and seconds_since_reply < quick_hint_seconds:
        return "LOW"
    if action == "think" or has_attempt or intent == "REPLY":
        return "HIGH"
    return "UNKNOWN"
