"""还没做的题，学生说“不会”“怎么做”：先给解题流程图，问他卡在哪一步，再从那一步一步一步引导。

以前直接给一段示例代码（帮助级别最高的那种），学生没机会自己想。现在：
1. 流程图（flow 块）：3～6 步，每步一个标题、一句话说要做什么、这一步最关键的知识点。不写代码。
   步骤按（服务器, 题目）存一份（problem_plans），所有学生共用，题面变了才重新生成；有大模型时让它按题面分步，
   程序核对（步数、长度、不能有代码和测试数据），不合格或没有大模型时按题目标注的技能用模板分步。
2. 学生选卡在哪几步（可多选），或者“都不会”：从选中的第一步开始。每一回合只讲一步：点出关键知识点，
   再问一个让他自己说出这一步怎么做的问题；答上了换下一步，没答上就讲得更具体一点，两次还没答上讲清楚原理再往下走。
3. 进度跟着对话走：每条引导消息带着流程图（当前这一步高亮、做完的打勾）和状态（content["plan"]，学生端看不到）。
   他问了别的、贴了代码、交了提交，就退出流程图，按平常的对话来。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re

from sqlalchemy.orm import Session

from .. import config, db, llm, pdftext
from .. import models as m
from ..taxonomy import KNOWLEDGE_POINTS, SKILLS, course_prompt, kp_of_skill, skill_name

log = logging.getLogger("chat.plan")

VERSION = "plan-1"
MIN_STEPS, MAX_STEPS = 3, 6
_ASK = re.compile(r"(不会|不懂|怎么做|怎么写|咋做|咋写|没思路|没有思路|无从下手|从哪|不知道.{0,6}(做|写|开始)|毫无头绪|一点.{0,2}(不会|不懂)|帮我.{0,4}(做|写))")
_ALL = re.compile(r"(都不会|全都不会|全不会|每一步都不会|都不懂|全部都不|一步都不|都没思路|全部不会)")
_CODE = re.compile(r"[;{}]|\b(printf|scanf|for|while|if|int|char|return)\s*[(\w]")


def wants_plan(text: str, intent: str) -> bool:
    """还没提交的题，他是不是在说“不会做 / 怎么做”（回答智能体的问题时说“不会”也算；问概念的不算）。"""
    return intent in ("STUCK", "ANSWER", "TASK", "DEBUG", "REPLY", "OTHER") and bool(_ASK.search(text))


def picked(text: str, n: int) -> list[int] | None:
    """他选了卡在哪几步（从 0 数）。“都不会”是全部；一个也认不出返回 None。"""
    if _ALL.search(text):
        return list(range(n))
    cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
    num = r"(?:\d+|[一二两三四五六])"
    run = rf"{num}(?:\s*[、，,和与及跟]\s*(?:第\s*)?{num})*"   # “第 1、3 步”“第二步和第三步”“2 和 4 步”
    spans = re.findall(rf"第\s*({run})", text) + re.findall(rf"(?<![\d第一二两三四五六])(\d+(?:\s*[、，,和与及跟]\s*\d+)*)\s*步", text)
    nums = {int(x) if x.isdigit() else cn[x] for sp in spans for x in re.findall(num, sp)}
    out = sorted(i - 1 for i in nums if 1 <= i <= n)
    return out or None


# ------------------------------------------------------------ 步骤（按题共用）
def _hash(statement: str, label: m.ProblemLabel | None, visual_key: str = "") -> str:
    inputs = [statement, label.primary_skill if label else "", list(label.secondary_skills or []) if label else []]
    if visual_key:
        inputs.append(visual_key)
    key = json.dumps(inputs, ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


_INPUT = ("C01.03", "C01.04", "C05.08", "C08.02")   # 读入数据用到的技能：并进第一步


def template_steps(label: m.ProblemLabel | None, course: str) -> list[dict]:
    """没有大模型时：读入 → 题目标注的技能（按知识点去重，按课程里的先后排）→ 输出。
    标题是技能（要做的事），关键知识点是它所属的知识点；模板说不出“怎么做”，how 留空。"""
    codes = [c for c in ([label.primary_skill, *(label.secondary_skills or [])] if label else []) if c in SKILLS]
    read = next((c for c in codes if c.startswith(_INPUT)), None)
    io = course == "C"   # 读入、输出两步的知识点只在 C 语言这边写出来
    steps = [{"title": "读入数据", "how": skill_name(read) if read else "按输入格式把数据读进来，先想好用什么类型存",
              "kp": KNOWLEDGE_POINTS.get(kp_of_skill(read) if read else "C01.03", "") if io else ""}]
    chosen: dict[str, str] = {}   # 知识点 → 代表它的技能（主技能优先）
    for code in codes:
        kp = KNOWLEDGE_POINTS.get(kp_of_skill(code), "")
        if code != read and not code.startswith(("C01.01", "C01.02", "C01.03")) and kp != steps[0]["kp"]:
            chosen.setdefault(kp, code)
    for kp, code in sorted(chosen.items(), key=lambda x: x[1])[:MAX_STEPS - 2]:
        steps.append({"title": skill_name(code), "how": "", "kp": kp})
    if len(steps) == 1:
        steps.append({"title": "按题意计算", "how": "把题目要求的计算或判断一步步写出来", "kp": ""})
    steps.append({"title": "输出结果", "how": "按输出格式打印，注意空格和换行",
                  "kp": KNOWLEDGE_POINTS.get("C01.02", "") if io else ""})
    return steps


_SYSTEM = """你是 C 语言课程的助教。一名学生还没开始做下面这道题，不知道从哪下手。
请把解题过程分成 3～6 步，像流程图一样从上到下。每一步：
- title：不超过 10 个字的动宾短语（如“读入 n 和成绩”）；
- how：一句话说这一步要做什么，不超过 30 个字；
- kp：这一步最关键的知识点，从给出的知识点表里选一个名称照抄。
不要写代码，不要把完整解法讲透（关键的想法点到为止，留给学生自己想），不要提测试数据和具体的输入输出。
只输出 JSON：{"steps":[{"title":"…","how":"…","kp":"…"}]}"""


def _llm_steps(statement: str, label: m.ProblemLabel | None, course: str, tests: list[dict], document=None) -> list[dict] | None:
    from .validate import _leaks

    names = [v for k, v in KNOWLEDGE_POINTS.items() if k.startswith(course)]

    def validate(d: dict) -> list[dict]:
        steps = d.get("steps") or []
        if not MIN_STEPS <= len(steps) <= MAX_STEPS:
            raise ValueError(f"要 {MIN_STEPS}～{MAX_STEPS} 步")
        out = []
        for st in steps:
            title, how, kp = (str(st.get(k) or "").strip() for k in ("title", "how", "kp"))
            if not title or not how or len(title) > 12 or len(how) > 40:
                raise ValueError("每步要有 title、how，而且不能太长")
            if _CODE.search(title + how) or _leaks(title + how, tests):
                raise ValueError("不能写代码，也不能出现测试数据")
            out.append({"title": title, "how": how, "kp": kp if kp in names else ""})
        return out

    skills = "、".join(skill_name(c) for c in ([label.primary_skill, *(label.secondary_skills or [])] if label else []))
    user = ("【题面】\n" + pdftext.for_llm(statement)[:2500] + ("\n【这道题考的技能】" + skills if skills else "")
            + "\n【知识点表】" + "、".join(names))
    pages = pdftext.guide_pages(document, statement)
    if not statement and not pages:
        return None
    try:
        return llm.call_json("plan_steps", course_prompt(_SYSTEM, course), user, validate, retries=1, max_tokens=500,
                             pages=pages, text_fallback=False)
    except llm.LLMUnavailable:
        return None


def steps_for(s: Session, server_id: int, problem_id: int, statement: str, label: m.ProblemLabel | None,
              course: str, tests: list[dict]) -> list[dict]:
    """这道题的解题步骤：存过且题面没变就直接用（所有学生共用）；没有就生成一次存起来。"""
    document = s.get(m.ProblemText, (server_id, problem_id))
    visual = document is not None and pdftext.needs_vision(statement) and pdftext.is_pdf(document.content_type, document.file_bytes)
    h = _hash(statement, label, f"{document.file_hash}:{config.DEEPSEEK_VISION_MODEL}" if visual else "")
    row = s.get(m.ProblemPlan, (server_id, problem_id))
    fresh = row is not None and row.content_hash == h and row.version == VERSION and bool(row.steps)
    if fresh and row.source == "llm":
        return list(row.steps)
    # 上次大模型没给出合格的步骤：一小时内不再试（每个学生来问都调一次太浪费），用模板
    recent = fresh and db.as_utc(row.created_at) > db.now() - dt.timedelta(hours=1)
    steps, source = None, "template"
    tried = bool(statement or visual) and llm.available() and not recent
    if tried:
        steps = _llm_steps(statement, label, course, tests, document)
        source = "llm" if steps else "template"
    steps = steps or template_steps(label, course)
    if fresh and not tried and steps == row.steps:
        return steps
    if row is None:
        row = m.ProblemPlan(server_id=server_id, problem_id=problem_id)
        s.add(row)
    row.steps, row.source, row.version, row.content_hash, row.created_at = steps, source, VERSION, h, db.now()
    s.flush()
    return steps


# ------------------------------------------------------------ 引导一步
_GUIDE = """你是 C 语言课程的助教。学生在做一道题，解题步骤（流程图）如下。现在要引导他想清楚其中一步。
用一两句话：先点出这一步的关键知识点（用「」括起来），再问他一个具体的问题，让他自己说出这一步怎么做。
不要直接给出答案，不要写完整代码（最多可以提一个很短的写法），不要提测试数据和具体的输入输出。
如果说明他前面没答上，就讲得更具体一点（把原理说清楚，或举一个和本题不同的小例子），仍然以问题结尾。
如果说明这是最后一次，就把这一步怎么做直接讲清楚（仍然不写完整代码），不用再问。
只输出 JSON：{"reply":"……"}"""


def _what(st: dict) -> str:
    return f"「{st['title']}」" + (f"：{st['how']}" if st.get("how") else "")


def guide_template(steps: list[dict], i: int, tries: int) -> str:
    st = steps[i]
    kp = f"关键是「{st['kp']}」。" if st.get("kp") and st["kp"] != st["title"] else ""
    if tries >= 2:
        return (f"第 {i + 1} 步{_what(st)}。{kp}拿题目的样例在纸上走一遍，"
                "写下每个变量每一轮怎么变，想清楚了再写成代码。")
    if tries == 1:
        return f"再具体一点：第 {i + 1} 步{_what(st)}。{kp}要用到哪些变量？先做什么、后做什么？"
    return f"看第 {i + 1} 步{_what(st)}。{kp}这一步你打算怎么做？先说说你的想法。"


def guide(steps: list[dict], i: int, tries: int, said: str, statement: str, course: str,
          tests: list[dict], document=None) -> tuple[str, str]:
    """引导第 i 步（从 0 数）：(话, llm / template)。tries：这一步他已经没答上几次。"""
    from .validate import _leaks

    fallback = guide_template(steps, i, tries)
    if not llm.available():
        return fallback, "template"
    last = tries >= 2

    def validate(d: dict) -> str:
        out = str(d.get("reply") or "").strip()
        if not out or len(out) > 200:
            raise ValueError("太长或是空的")
        if "```" in out or _leaks(out, tests):
            raise ValueError("不能贴代码、不能出现测试数据")
        if not last and not out.rstrip().endswith(("？", "?")):
            raise ValueError("要以一个问题结尾")
        return out

    flow = "\n".join(f"{k + 1}. {st['title']}" + (f"：{st['how']}" if st.get("how") else "")
                     + (f"（关键知识点：{st['kp']}）" if st.get("kp") else "") for k, st in enumerate(steps))
    user = ("【题面】\n" + pdftext.for_llm(statement)[:1500] + "\n【解题步骤】\n" + flow
            + f"\n【现在引导第 {i + 1} 步】" + ("\n【说明】这是最后一次，直接讲清楚这一步" if last
                                              else "\n【说明】他前面没答上，讲得更具体一点" if tries else "")
            + ("\n【他刚才说的】\n" + llm.strip_identity(said) if said else ""))
    try:
        return llm.call_json("plan_guide", course_prompt(_GUIDE, course), user, validate, retries=1, max_tokens=300,
                             pages=pdftext.guide_pages(document, statement, said)), "llm"
    except llm.LLMUnavailable:
        return fallback, "template"


def active(msg: m.ChatMessage | None) -> dict | None:
    """上一条智能体消息带着的流程图状态（还在进行的）。"""
    if msg is None:
        return None
    state = (msg.content or {}).get("plan")
    if not isinstance(state, dict) or state.get("ended") or not state.get("steps"):
        return None
    return state


def block(state: dict) -> dict:
    """给学生看的流程图块：步骤、哪一步正在讲、哪几步做完了、能不能选。"""
    return {"t": "flow", "steps": [{"title": x["title"], "how": x["how"], "kp": x.get("kp", "")} for x in state["steps"]],
            "pick": bool(state.get("picking")), "current": state.get("current"), "done": list(state.get("done", []))}
