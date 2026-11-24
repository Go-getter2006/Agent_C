"""还没做的题，学生说“不会”“怎么做”：先给解题流程图，问他卡在哪一步，再从那一步一步一步引导。

以前直接给一段示例代码（帮助级别最高的那种），学生没机会自己想。现在：
1. 流程图（flow 块）：2～6 步，每步一个标题、一句话说要做什么、这一步最关键的知识点。不写代码。
   步骤由大模型按题面分（程序核对步数、不能有代码和测试数据、知识点必须是这门课的），按（服务器, 题目）
   存一份（problem_plans），所有学生共用。学生实际问到时，才为还没有缓存或材料发生变化的题生成步骤。
   大模型给不出合格的步骤（或者没配大模型）就不出流程图，按平常的引导走：按技能拼出来的步骤会误导，不用。
2. 学生选卡在哪几步（可多选），或者“都不会”：从选中的第一步开始。每一回合只讲一步：点出关键知识点，
   再问一个让他自己说出这一步怎么做的问题；答上了换下一步，没答上就讲得更具体一点，两次还没答上讲清楚原理再往下走。
   这一回合大模型没写出合格的话，才用固定的句子讲这一步（不让他干等）。
3. 进度跟着对话走：每条引导消息带着流程图（当前这一步高亮、做完的打勾）和状态（content["plan"]，学生端看不到）。
   他问了别的、贴了代码、交了提交，就退出流程图，按平常的对话来。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config, db, llm, pdftext
from .. import models as m
from ..taxonomy import KNOWLEDGE_POINTS, course_prompt, skill_name

log = logging.getLogger("chat.plan")

VERSION = "plan-1"
MIN_STEPS, MAX_STEPS = 2, 6
_ASK = re.compile(r"(不会|不懂|怎么做|怎么写|咋做|咋写|没思路|没有思路|无从下手|从哪|不知道.{0,6}(做|写|开始)|毫无头绪|一点.{0,2}(不会|不懂)|帮我.{0,4}(做|写))")
_ALL = re.compile(r"(都不会|全都不会|全不会|每一步都不会|都不懂|全部都不|一步都不|都没思路|全部不会)")
_CODE = re.compile(r"[;{}]|\b(?:printf|scanf|for|while|if)\s*\(|\b(?:int|char|return)\s+[A-Za-z_0-9]", re.ASCII)


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


_SYSTEM = """你是 C 语言课程的助教。一名学生还没开始做下面这道题，不知道从哪下手。
请把解题过程分成 2～6 个有实际区别的步骤，按执行顺序列出。简单题可以只给两步。
每步的 title 用短语写目标，how 用一句话告诉学生该想什么，kp 从给出的知识点名称里选一个，找不到可填空字符串。
可以提到 for 循环等语法名。用自然语言描述，避免代码、完整解法和具体测试数据，用“待比较的数”等名称描述变量。
只输出一个 JSON 对象，结构示例如下。步骤内容要针对本题。
{"steps":[{"title":"整理输入","how":"区分要处理的量和最后要给出的结果","kp":""},
{"title":"找出判断条件","how":"考虑每一种情况分别应该得到什么结果","kp":""}]}"""


def _llm_steps(statement: str, label: m.ProblemLabel | None, course: str, tests: list[dict], document=None) -> list[dict] | None:
    from .validate import _leaks

    names = [v for k, v in KNOWLEDGE_POINTS.items() if k.startswith(course)]

    def validate(d: dict) -> list[dict]:
        steps = d.get("steps")
        if not isinstance(steps, list) or not MIN_STEPS <= len(steps) <= MAX_STEPS:
            raise ValueError(f"要 {MIN_STEPS}～{MAX_STEPS} 步")
        out = []
        for st in steps:
            if not isinstance(st, dict):
                raise ValueError("每一步必须是包含 title、how、kp 的对象")
            title, how, kp = (st.get(k) for k in ("title", "how", "kp"))
            if not isinstance(title, str) or not title.strip() or not isinstance(how, str) or not how.strip():
                raise ValueError("每步要有非空的文字标题和说明")
            title, how = title.strip(), how.strip()
            kp = kp.strip() if isinstance(kp, str) else ""
            content = title + "\n" + how
            if _CODE.search(content):
                raise ValueError("步骤包含代码语句")
            if _leaks(content, tests):
                raise ValueError("步骤包含隐藏测试数据")
            out.append({"title": title, "how": how, "kp": kp if kp in names else ""})
        return out

    skills = "、".join(skill_name(c) for c in ([label.primary_skill, *(label.secondary_skills or [])] if label else []))
    user = ("【题面】\n" + pdftext.for_llm(statement)[:2500] + ("\n【这道题考的技能】" + skills if skills else "")
            + "\n【知识点表】" + "、".join(names))
    pages = pdftext.guide_pages(document, statement)
    if not statement and not pages:
        return None
    return llm.call_json("plan_steps", course_prompt(_SYSTEM, course), user, validate, retries=0, max_tokens=None,
                         pages=pages, text_fallback=False, non_thinking=True)


def _key(s: Session, server_id: int, problem_id: int, statement: str, label: m.ProblemLabel | None):
    """(题面和标注的哈希, 文字整理不出来、要看 PDF 页面图吗, 题面行)。"""
    document = s.get(m.ProblemText, (server_id, problem_id))
    visual = document is not None and pdftext.needs_vision(statement) and pdftext.is_pdf(document.content_type, document.file_bytes)
    h = _hash(statement, label, f"{document.file_hash}:{config.DEEPSEEK_VISION_MODEL}" if visual else "")
    return h, visual, document


def steps_for(s: Session, server_id: int, problem_id: int, statement: str, label: m.ProblemLabel | None,
              course: str, tests: list[dict]) -> list[dict] | None:
    """这道题的解题步骤：大模型按题面（文字整理不出来的 PDF 题面看页面图）分好、存过且题面没变就直接用
    （所有学生共用）；没有就现在生成一次存起来。大模型给不出合格的步骤、或者没配大模型，返回 None（不出流程图）。
    同一版材料的无效输出只记一次。连接或配额问题在学生后续实际提问时按冷却时间重试。"""
    h, visual, document = _key(s, server_id, problem_id, statement, label)
    if s.get_bind().dialect.name == "postgresql":
        # 同一道题同时被问时，只允许一个事务生产并保存共享步骤。
        s.execute(select(func.pg_advisory_xact_lock(server_id, func.hashtext(f"problem_plan:{problem_id}"))))
    row = s.get(m.ProblemPlan, (server_id, problem_id))
    fresh = row is not None and row.content_hash == h and row.version == VERSION
    if fresh and row.source == "llm" and row.steps:
        return list(row.steps)
    if fresh and row.source == "invalid":
        return None
    if fresh and row.source == "failed" and db.as_utc(row.created_at) > db.now() - dt.timedelta(hours=1):
        return None
    if not (statement or visual) or not llm.available():
        return None
    try:
        steps = _llm_steps(statement, label, course, tests, document)
    except llm.LLMUnavailable as exc:
        steps = None
        source = "invalid" if exc.invalid else "failed"
    else:
        if not steps:  # 题面图片暂时不可用，尚未调用模型
            return None
        source = "llm"
    if row is None:
        row = m.ProblemPlan(server_id=server_id, problem_id=problem_id)
        s.add(row)
    row.steps, row.source, row.version, row.content_hash, row.created_at = (
        steps or [], source, VERSION, h, db.now())
    s.flush()
    return steps


# ------------------------------------------------------------ 引导一步
_GUIDE = """你是 C 语言课程的助教。学生在做一道题，解题步骤（流程图）如下。现在要引导他想清楚其中一步。
用一两句话：先点出这一步的关键知识点（用「」括起来），再问他一个具体的问题，让他自己说出这一步怎么做。
不要直接给出答案，不要写完整代码（最多可以提一个很短的写法），不要提测试数据和具体的输入输出。
如果说明他前面没答上，就讲得更具体一点（把原理说清楚，或举一个和本题不同的小例子），仍然以问题结尾。
如果说明这是最后一次，就把这一步怎么做直接讲清楚（仍然不写完整代码），不用再问。
看“最近的对话”：你上一句问了什么、他答了什么，接着他的话说，不重复问同一个问题；他答对了一部分就先肯定那一部分。
看“他的情况”：哪些知识点他还薄弱、以前在别的题上卡过什么，薄弱的地方讲得更细；不要把这些情况原样说给他听。
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


_GUIDING: ContextVar[dict] = ContextVar("plan_guiding", default={})


@contextmanager
def guiding(recent: str = "", situation: list[str] | None = None, who: tuple[str, str] = ("", "")):
    """包住几次 guide 调用：这几次引导要给大模型看的最近对话（chat/talk.py）、他的情况（学情、以前卡过的地方），
    以及发出去之前要去掉的 (学号, 姓名)。"""
    token = _GUIDING.set({"recent": recent, "situation": list(situation or []), "who": who})
    try:
        yield
    finally:
        _GUIDING.reset(token)


def guide(steps: list[dict], i: int, tries: int, said: str, statement: str, course: str,
          tests: list[dict], document=None) -> tuple[str, str]:
    """引导第 i 步（从 0 数）：(话, llm / template)。tries：这一步他已经没答上几次。
    最近的对话和他的情况由 guiding() 给。"""
    from .validate import _leaks

    extra = _GUIDING.get()
    recent, situation, who = extra.get("recent", ""), extra.get("situation") or [], extra.get("who") or ("", "")

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
            + ("\n【他的情况】\n" + "\n".join(situation) if situation else "")
            + ("\n【最近的对话】\n" + recent if recent else "")
            + ("\n【他刚才说的】\n" + llm.strip_identity(said, *who) if said else ""))
    try:
        return llm.call_json("plan_guide", course_prompt(_GUIDE, course), user, validate, retries=1, max_tokens=None,
                             pages=pdftext.guide_pages(document, statement, said), non_thinking=True), "llm"
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
