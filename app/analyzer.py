"""学生每条发言的分析：属于哪个知识点、是不是新问题、ICAP 投入等级、求助类型等。"""
import re

from . import llm
from .knowledge import KP_BY_ID, guess_kp, kp_list_text

ICAP_LEVELS = ["passive", "active", "constructive", "interactive"]
HELP_TYPES = ["answer_seeking", "explanation_seeking", "verification", "debugging_with_attempt",
              "concept_question", "response_to_tutor", "other"]

SYSTEM_PROMPT = """你是编程教育的数据分析助手。根据学生在C语言学习中对AI助教的最新一条发言（以及上下文），输出 json，字段如下：
{
  "kp_id": "从知识点列表中选一个最相关的ID；与C语言无关时填 other",
  "is_new_question": true 或 false，表示学生是否开启了一个新的问题（而不是对当前问题的追问、回答或补充）,
  "question_summary": "用一句话规范地概括学生现在想解决的问题，去掉寒暄；如果是追问，概括当前问题",
  "icap": "passive | active | constructive | interactive",
  "help_type": "answer_seeking | explanation_seeking | verification | debugging_with_attempt | concept_question | response_to_tutor | other",
  "has_own_attempt": true 或 false，表示学生是否给出了自己的代码、思路、推理或改法,
  "resolved": true 或 false，表示学生是否明确表示已经明白或解决,
  "evidence": "一句话说明 icap 判定的依据"
}
ICAP 判定（针对学生对AI助教的发言）：
- passive：只是接收或附和，例如“好的”“嗯”“直接给我代码”；
- active：复述、照做、提出操作性问题，或贴出代码问“哪里错了”但没有自己的分析；
- constructive：给出自己的解释、推理、假设或改法，例如“我觉得是因为i从1开始，少加了一次”；
- interactive：在AI的说法基础上继续推进、质疑或修正，例如“你说是越界，但我改成<=n后又多输出一个，是不是循环条件和数组长度要一起改？”
求助类型：answer_seeking=索要答案或完整代码；explanation_seeking=问为什么、要解释；verification=请AI检查自己的答案或想法对不对；
debugging_with_attempt=带着自己的代码或尝试来调试；concept_question=问概念或区别；response_to_tutor=回答AI的提问；other=其他。
知识点列表：
""" + kp_list_text()

RESOLVED_WORDS = ["懂了", "明白了", "会了", "解决了", "已解决", "知道了", "原来如此", "理解了", "搞定"]
ANSWER_WORDS = ["直接给", "给我代码", "给出代码", "答案", "帮我写", "完整代码", "写一个", "写出来", "代码发我"]
VERIFY_WORDS = ["对不对", "对吗", "是不是对", "这样可以吗", "检查一下", "帮我看看对"]
WHY_WORDS = ["为什么", "怎么理解", "什么意思", "原理", "为啥"]
CONCEPT_WORDS = ["什么是", "区别", "是什么", "有什么不同"]
DEBUG_WORDS = ["报错", "错误", "不对", "结果不", "输出", "运行", "崩溃", "死循环", "编译"]
ATTEMPT_WORDS = ["我觉得", "我认为", "我试了", "我改成", "我的思路", "是不是因为", "因为", "所以", "我想", "我猜", "应该是"]
BUILD_ON_WORDS = ["你说的", "按你说的", "那如果", "但是", "可是", "那是不是", "照你说的"]
NEW_Q_WORDS = ["新问题", "另一个问题", "还有一个问题", "再问", "换个问题", "下一题", "另外一题"]


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def heuristic_analysis(message: str, code: str, current_episode) -> dict:
    """规则版分析：模拟模式和大模型失败时使用。"""
    text = (message or "") + "\n" + (code or "")
    has_code = bool(code and code.strip())
    kp = guess_kp(text)
    cur_kp = current_episode["kp_id"] if current_episode else None
    if kp == "other" and cur_kp:
        kp = cur_kp
    resolved = _has_any(message, RESOLVED_WORDS) and len(message) <= 40
    if current_episode is None:
        is_new = True
    elif resolved:
        is_new = False
    elif _has_any(message, NEW_Q_WORDS):
        is_new = True
    elif kp != cur_kp and kp != "other" and ("?" in message or "？" in message or has_code):
        is_new = True
    else:
        is_new = False

    if _has_any(message, ANSWER_WORDS):
        help_type = "answer_seeking"
    elif _has_any(message, VERIFY_WORDS):
        help_type = "verification"
    elif has_code or (_has_any(message, DEBUG_WORDS) and current_episode is None):
        help_type = "debugging_with_attempt" if has_code else "other"
    elif _has_any(message, WHY_WORDS):
        help_type = "explanation_seeking"
    elif _has_any(message, CONCEPT_WORDS):
        help_type = "concept_question"
    elif current_episode is not None:
        help_type = "response_to_tutor"
    else:
        help_type = "other"

    attempt = has_code or _has_any(message, ATTEMPT_WORDS)
    short = len(re.sub(r"\s", "", message or "")) < 6
    if (help_type == "answer_seeking" and not attempt) or (short and not attempt):
        icap = "passive"
    elif attempt and _has_any(message, BUILD_ON_WORDS):
        icap = "interactive"
    elif attempt and _has_any(message, ATTEMPT_WORDS):
        icap = "constructive"
    else:
        icap = "active"
    summary = re.sub(r"\s+", " ", message or "").strip()[:80] or "（只提交了代码）"
    if not is_new and current_episode:
        summary = current_episode["question_summary"]
    return {
        "kp_id": kp, "is_new_question": is_new, "question_summary": summary, "icap": icap,
        "help_type": help_type, "has_own_attempt": bool(attempt), "resolved": bool(resolved),
        "evidence": "规则判定",
    }


def _coerce(out: dict, fallback: dict) -> dict:
    res = dict(fallback)
    if out.get("kp_id") in KP_BY_ID or out.get("kp_id") == "other":
        res["kp_id"] = out["kp_id"]
    for key, allowed in (("icap", ICAP_LEVELS), ("help_type", HELP_TYPES)):
        if out.get(key) in allowed:
            res[key] = out[key]
    for key in ("is_new_question", "has_own_attempt", "resolved"):
        if isinstance(out.get(key), bool):
            res[key] = out[key]
    if isinstance(out.get("question_summary"), str) and out["question_summary"].strip():
        res["question_summary"] = out["question_summary"].strip()[:120]
    if isinstance(out.get("evidence"), str):
        res["evidence"] = out["evidence"][:200]
    res["_source"] = out.get("_source", "llm")
    return res


def analyze(message: str, code: str, current_episode, recent_turns) -> dict:
    fallback = heuristic_analysis(message, code, current_episode)
    if llm.is_mock():
        fallback["_source"] = "mock"
        return fallback
    ctx = []
    if current_episode:
        ctx.append(f"当前讨论的问题：{current_episode['question_summary']}（知识点 {current_episode['kp_id']}）")
    else:
        ctx.append("当前没有正在讨论的问题。")
    for t in recent_turns[-6:]:
        who = "学生" if t["role"] == "student" else "AI助教"
        ctx.append(f"{who}：{t['content'][:300]}")
    user = "\n".join(ctx) + f"\n\n【学生最新发言】{message}"
    if code and code.strip():
        user += f"\n【学生附带的代码】\n{code[:3000]}"
    out = llm.chat_json(SYSTEM_PROMPT, user, fallback=lambda: dict(fallback))
    if out.get("_source") != "llm":
        return out
    return _coerce(out, fallback)
