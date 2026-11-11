"""智能体回复：和学生讨论，回复之前已经查过历史（重复提问时先让学生回忆）。"""
import time

from . import llm
from .knowledge import kp_name

SYSTEM_PROMPT = """你是西安电子科技大学C语言课程的AI助教。你的目标是帮学生自己想明白，而不是替他写代码。
规则：
1. 先弄清学生卡在哪：审题、思路、语法、逻辑错误、边界情况，还是某个概念没掌握。
2. 一次只推进一小步：用提问或提示引导学生说出自己的想法。学生说对了要具体地肯定，说错了就指出矛盾之处，不要直接给出答案。
3. 不要给出能直接提交的完整答案代码；最多给出一两行关键语句的写法，或与本题无关的小例子。
4. 如果【历史记录】显示学生以前问过类似问题，而且【本轮提示】要求先回忆，就先请学生回忆上次的关键点，再继续。
5. 学生表示已经明白时，请他用自己的话总结一句，并告诉他可以点“开始诊断”做两三道小题，检查自己是不是真的掌握了。
6. 回答简洁（一般不超过 150 字），用中文，代码放在 ```c 代码块中。"""


def build_messages(kp_id, history_ctx, reask, recent_turns, message, code, analysis):
    hint = []
    if reask:
        day = time.strftime("%m月%d日", time.localtime(reask["started_at"]))
        hint.append(f"学生在{day}问过类似的问题：“{reask['question_summary']}”。这是本轮的第一条消息，"
                    "请先不要直接解答，先请他回忆上次讨论的关键点（例如问他“上次我们发现问题出在哪里？”）。")
    if analysis.get("help_type") == "answer_seeking":
        hint.append("学生在索要答案或完整代码：不要给完整代码，先问他自己的思路或卡在哪一步。")
    if analysis.get("resolved"):
        hint.append("学生表示已经明白：请他用自己的话总结一句，并提示可以做诊断小题。")
    context = (f"【当前知识点】{kp_name(kp_id)}\n【历史记录】\n{history_ctx}\n"
               f"【本轮提示】{'；'.join(hint) if hint else '无'}")
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": context}]
    for t in recent_turns[-8:]:
        role = "user" if t["role"] == "student" else "assistant"
        content = t["content"] + (f"\n```c\n{t['code']}\n```" if t.get("code") else "")
        messages.append({"role": role, "content": content[:3000]})
    user = message + (f"\n```c\n{code}\n```" if code and code.strip() else "")
    messages.append({"role": "user", "content": user[:6000]})
    return messages


def mock_reply(kp_id, reask, analysis, code) -> str:
    """模拟模式下的模板回复，只用来跑通流程。"""
    if reask:
        day = time.strftime("%m月%d日", time.localtime(reask["started_at"]))
        return (f"你在{day}问过一个很像的问题：“{reask['question_summary']}”。"
                "先别急着看答案，试着回忆一下：上次我们发现问题的关键在哪里？")
    if analysis.get("resolved"):
        return "很好！能用你自己的话总结一下刚才的关键点吗？如果愿意，可以点“开始诊断”，做两三道小题检查一下是不是真的掌握了。"
    if analysis.get("help_type") == "answer_seeking":
        return "我先不直接给完整代码。你能说说你现在的思路吗？程序应该先读入什么、再怎么算、最后输出什么？你卡在哪一步？"
    if code and code.strip():
        return f"我看到你的代码了（{kp_name(kp_id)}）。先别急着改：你觉得哪一行最可疑？可以挑一个具体的输入，在纸上跟着代码走一遍，看看变量是怎么变化的。"
    if analysis.get("help_type") in ("explanation_seeking", "concept_question"):
        return f"这是{kp_name(kp_id)}里很关键的一点。你先说说你现在是怎么理解的？我们从你的理解出发，看看哪里需要补充。"
    if analysis.get("icap") in ("constructive", "interactive"):
        return "你的推理方向是对的。那么按照你的想法，换一个边界情况（比如最小的输入）试试，结果还对吗？"
    return "好的。我们一步一步来：你能先用一句话说说这道题要求程序做什么吗？"


def reply(kp_id, history_ctx, reask, recent_turns, message, code, analysis):
    """返回 (回复文本, 来源)。"""
    messages = build_messages(kp_id, history_ctx, reask, recent_turns, message, code, analysis)
    return llm.chat_text(messages, fallback=lambda: mock_reply(kp_id, reask, analysis, code))
