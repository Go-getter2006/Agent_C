"""第 1 步：消息分析。输出固定字段：意图、技能、有没有自己的尝试、有没有贴代码、ICAP。

有大模型时由大模型做，输出的每个字段都要落在固定枚举里，不在就重试；
没有大模型、或重试仍不合格时，用规则。点求助按钮不需要分析。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import llm
from ..taxonomy import ICAP, INTENTS, course_prompt, is_skill_code


@dataclass
class Analysis:
    intent: str = "OTHER"
    skill_code: str = ""
    has_attempt: bool = False
    has_code: bool = False
    icap: str = "passive"
    answer_correct: bool | None = None
    understood: bool = False
    source: str = "rule"


_UNDERSTOOD = re.compile(r"(懂了|明白了|我会了|知道了|原来如此|原来是这样|搞定|通过了|过了|[Aa][Cc]\s*了|改对了|可以了)")
_STUCK = re.compile(r"(不会|不知道|没思路|想不出|想不到|不懂|看不懂|忘了|不记得|没头绪|不明白|还是不|不太会|不清楚怎么)")
_ANSWER = re.compile(r"(直接给|给我代码|完整代码|标准答案|要答案|帮我写|帮我改|发我|直接告诉|直接讲|把代码|代码给我|怎么写)")
_TASK = re.compile(r"(题意|(?:题目|这(?:道)?题).*(什么意思|意思是|要求|让|干什么)|输入格式|输出格式|样例|题面|要输出什么|要输入什么)")
_CONCEPT = re.compile(r"(什么是|是什么|有什么区别|区别|有什么用|怎么用|什么时候用|的作用|是什么意思|为什么要用)")
_DEBUG = re.compile(r"(错|不对|为什么|过不了|没过|不过|运行错误|超时|编译|哪里|bug|有问题|出问题|挂了|[Ww][Aa]|没通过)")
_OWN_PROBLEM = re.compile(r"(这题|这道题|我的代码|我这|我写|我交|提交|一直错|测试点|那一组|有一组)")
_ATTEMPT = re.compile(r"(我觉得|我想|我认为|我猜|是不是|应该是|因为|所以|我试|我改|我把|我用|我写|会不会是|感觉是|可能是)")
_QUESTIONING = re.compile(r"(为什么|怎么回事|怎么会|怎么办)")
# 消息里提到了代码：变量名、关键字、运算符
_CODEISH = re.compile(r"(`|\b(max|min|sum|cnt|count|int|long|double|char|for|while|if|else|return|scanf|printf|"
                      r"main|i|j|n|a)\b|\[[^\]]*\]|==|!=|<=|>=|\+\+|--|%[dfsc])")


def factual_question(text: str) -> bool:
    """明确的定义、语法和用法请求；不把调试原因或题意澄清当作事实提问。"""
    if re.search(r"(?:我的|这段|这个|这题|这道题|程序|代码).{0,12}(?:为什么|哪里|什么问题|错在哪|过不了)|题意|(?:这题|这道题|题目)(?:是)?什么意思", text):
        return False
    return bool(re.search(r"什么是|是什么|有什么区别|有何区别|有什么用|怎么用|如何使用|的作用|是什么意思|"
                          r"(?:占|有|需要)(?:几|多少)(?:个)?字节|取值范围|语法(?:是|怎么)|返回值(?:是|什么)|参数(?:是|含义)|"
                          r"(?:语法|用法|定义|含义|区别|作用)(?:是什么|是啥|[？?]|$)|"
                          r"(?:scanf|printf|sizeof|malloc|strlen|strcpy|strcmp|break|continue|return|for|while|switch).{0,10}(?:用法|怎么用|怎么写|什么意思)", text))


def analyze_rules(text: str, has_code: bool, awaiting_answer: bool, bound: bool = False) -> Analysis:
    t = (text or "").strip()
    a = Analysis(has_code=has_code, source="rule")
    a.has_attempt = has_code or bool(_ATTEMPT.search(t))
    a.understood = bool(_UNDERSTOOD.search(t)) and not _STUCK.search(t) and not re.search(r"没|不", t[:4])
    if a.understood:
        a.intent = "REPLY"
    elif factual_question(t):
        a.intent = "CONCEPT"
    elif _STUCK.search(t) and not has_code:
        a.intent = "STUCK"
    elif _ANSWER.search(t):
        a.intent = "ANSWER"
    elif awaiting_answer and not (_QUESTIONING.search(t) and t.endswith(("?", "？"))):
        a.intent = "REPLY"
        a.has_attempt = a.has_attempt or len(t) >= 6
    elif _TASK.search(t):
        a.intent = "TASK"
    elif _CONCEPT.search(t) and not _OWN_PROBLEM.search(t) and not has_code:
        a.intent = "CONCEPT"
    elif has_code or _DEBUG.search(t) or _OWN_PROBLEM.search(t):
        a.intent = "DEBUG"
    elif bound and (a.has_attempt or _CODEISH.search(t)):
        a.intent = "DEBUG"  # 在一道题的对话里、说的是他的代码（比如“max 是 0 也没关系吧”）：不当成无关的话
    else:
        a.intent = "OTHER"
    if a.intent == "REPLY" and a.has_attempt:
        a.icap = "interactive"
    elif a.has_attempt:
        a.icap = "constructive"
    elif a.intent in ("DEBUG", "CONCEPT", "TASK"):
        a.icap = "active"
    else:
        a.icap = "passive"
    return a


# ------------------------------------------------------------ 对话行为
# 参考 StudyChat（McNichols 等 2026）的对话行为分类：概念性提问和学习表现正相关，索要答案负相关。
# 这里由意图、有没有自己的想法、按了哪个按钮定，不再另外调用大模型。
ACTS = {
    "concept": "概念提问", "debug_specific": "带着自己的想法问", "debug_vague": "只说错了",
    "answer_seeking": "索要答案", "hint_request": "要提示", "self_explain": "解释自己的想法",
    "self_regulate": "想自己想", "short_reply": "简短回应", "stuck": "说不会", "task": "问题意", "other": "其他",
}
CONSTRUCTIVE_ACTS = frozenset({"concept", "debug_specific", "self_explain", "self_regulate"})


def dialogue_act(intent: str, has_attempt: bool, action: str | None = None) -> str:
    if action == "tell":
        return "answer_seeking"
    if action == "hint":
        return "hint_request"
    if action == "think":
        return "self_regulate"
    if intent == "CONCEPT":
        return "concept"
    if intent == "DEBUG":
        return "debug_specific" if has_attempt else "debug_vague"
    if intent == "ANSWER":
        return "answer_seeking"
    if intent == "REPLY":
        return "self_explain" if has_attempt else "short_reply"
    return {"STUCK": "stuck", "TASK": "task"}.get(intent, "other")


_SYSTEM = """你在分析一名学 C 语言课程的学生发给答疑助手的一条消息。只输出 JSON：
{"intent":"DEBUG","skillCode":"","hasAttempt":true,"icap":"constructive","answerCorrect":null,"understood":false}
intent 只能取：DEBUG 调试求助；CONCEPT 概念提问；TASK 题意澄清；ANSWER 索要答案或要代码；
REPLY 回答助手刚才的问题（包括表示懂了）；STUCK 表示不会、没思路；OTHER 和学习无关。
“前面的对话”只用来理解指代和上下文（比如“那数组呢”“还是不行”接的是哪件事），判断的对象是学生这一条消息。
定义、事实、语法、函数作用或参数含义归 CONCEPT，即使它出现在某道题的对话里；当前代码为何失败才归 DEBUG。
skillCode 只能从“可选技能”里选一个，选不出填空字符串。
hasAttempt：消息里有没有他自己的尝试或想法。
icap 只能取 passive、active、constructive、interactive。
answerCorrect：只有 intent 是 REPLY 且助手刚才问了问题时才判断，答到点上填 true，没答对填 false，否则填 null。
understood：学生是否表示已经懂了或已经通过。"""


def analyze(text: str, has_code: bool, awaiting_answer: bool, last_agent_text: str = "",
            diagnosis_note: str = "", skill_options: list[tuple[str, str]] | None = None,
            bound: bool = False, course: str = "C", recent: str = "") -> Analysis:
    fallback = analyze_rules(text, has_code, awaiting_answer, bound)
    if factual_question(text):
        return fallback  # 事实类直接解答，不受“正在反问”或已绑定题目的影响。
    if not llm.available():
        return fallback
    options = skill_options or []
    allowed = {code for code, _ in options}

    def validate(d: dict) -> Analysis:
        intent = str(d.get("intent", ""))
        if intent not in INTENTS:
            raise ValueError(f"intent {intent} 不在枚举里")
        icap = str(d.get("icap", ""))
        if icap not in ICAP:
            raise ValueError(f"icap {icap} 不在枚举里")
        skill = str(d.get("skillCode") or "")
        if skill and (not is_skill_code(skill) or (allowed and skill not in allowed)):
            raise ValueError(f"skillCode {skill} 不在可选技能里")
        correct = d.get("answerCorrect")
        if correct not in (True, False, None):
            raise ValueError("answerCorrect 只能是 true、false 或 null")
        return Analysis(intent=intent, skill_code=skill, has_attempt=bool(d.get("hasAttempt")) or has_code,
                        has_code=has_code, icap=icap,
                        answer_correct=correct if (intent == "REPLY" and awaiting_answer) else None,
                        understood=bool(d.get("understood")), source="llm")

    user = (f"【可选技能】\n" + ("\n".join(f"{c} {n}" for c, n in options) or "（无）") +
            f"\n\n【前面的对话】\n{recent or '（无）'}"
            f"\n\n【助手上一条消息】\n{last_agent_text[:1200] or '（无）'}\n\n"
            f"【助手那个问题的正确方向（学生看不到）】\n{diagnosis_note[:300] or '（无）'}\n\n"
            f"【学生的消息】\n{text[:1500]}\n\n【消息里是否贴了代码】{'是' if has_code else '否'}")
    try:
        return llm.call_json("analyze", course_prompt(_SYSTEM, course), user, validate, retries=1, max_tokens=200)
    except llm.LLMUnavailable:
        return fallback
