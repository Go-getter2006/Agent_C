"""第 7 步的生成：按证据和策略写回复。

有大模型时由大模型写。它能看到没通过的测试点的输入、期望输出和他程序的实际输出（用来判断错在哪、
构造一个不一样的小例子），但被要求绝不原样说给学生；回复校验（validate._leaks）会再拦一道，漏了就重写或退回模板。
没有大模型、或两次都没通过校验时，用这里的固定模板。
"""
from __future__ import annotations

import re

from .. import llm
from ..taxonomy import HELP_ORDER
from . import hints
from .context import Ctx
from .policy import Decision
from .text import dedent, excerpt_range
from .validate import Draft

HELP_ACTIONS = ["think", "hint", "tell"]
BLANK = "______"


# ------------------------------------------------------------ 引用他自己的代码
def default_quote(ctx: Ctx) -> tuple[int, int, list[int]] | None:
    """围绕出问题的那一行，引用相关的 2~8 行，并标出那一行。"""
    rng = excerpt_range(len(ctx.lines), ctx.diag_line, ctx.lines)
    if rng is None:
        return None
    return rng[0], rng[1], [ctx.diag_line]


def quote_caption(ctx: Ctx, a: int, b: int) -> str:
    where = f"第 {a} 行" if a == b else f"第 {a} 到 {b} 行"
    if ctx.source_kind == "pasted":
        return f"你贴的代码，{where}"
    return f"你的第 {ctx.attempt_no} 次提交，{where}"


def to_blocks(draft: Draft, ctx: Ctx) -> list[dict]:
    blocks: list[dict] = []
    for para in [p.strip() for p in draft.before.split("\n") if p.strip()]:
        blocks.append({"t": "p", "text": para})
    if draft.quote is not None and ctx.lines:
        a, b, marks = draft.quote
        blocks.append({"t": "code", "caption": quote_caption(ctx, a, b), "start": a,
                       "lines": dedent(ctx.lines[a - 1:b]), "mark": [x for x in marks if a <= x <= b]})
    if draft.code:
        blocks.append({"t": "code", "caption": draft.code_caption, "start": 1,
                       "lines": draft.code.rstrip("\n").split("\n"), "mark": list(draft.code_mark)})
    for para in [p.strip() for p in draft.after.split("\n") if p.strip()]:
        blocks.append({"t": "p", "text": para})
    return blocks


# ------------------------------------------------------------ 示例代码（H3，用他自己的变量名）
def example_for(ctx: Ctx) -> Draft | None:
    """卡住那一块的示例代码，3~8 行，不给整题。这里只覆盖最常见的几种；其余靠大模型，或退成讲清楚改法。"""
    n = ctx.names()
    var, arr, cnt, i = n["var"], n["arr"] or "a", n["n"] or "n", n["i"] or "i"
    if ctx.root_skill == "C06.03.02":
        direction = n["direction"] or "max"
        what, op = ("最大的", ">") if direction == "max" else ("最小的", "<")
        var = var or direction
        code = (f"{n['type']} {var} = {arr}[0];\n"
                f"for ({i} = 1; {i} < {cnt}; {i}++)\n"
                f"    if ({arr}[{i}] {op} {var}) {var} = {arr}[{i}];")
        return Draft(before=f"你已经把数据读进 `{arr}` 里了，接下来这样找{what}：", code=code, key_line=1,
                     after=f"`{var}` 先等于第一个数，再逐个比较。用它换掉你原来的那几行，输出的部分你自己接上。")
    if ctx.root_error in ("TY02", "IO02") and var:
        code = f"long long {var} = {n['init'] or 0};\nprintf(\"%lld\\n\", {var});"
        return Draft(before="结果会超出 `int` 的范围，要换成更大的类型，输出的写法也要跟着换：", code=code, key_line=1,
                     after="照这两行改你代码里对应的地方，中间的计算不用动。")
    if ctx.root_error == "TY01" and ctx.diag_line:
        m = re.search(r"([A-Za-z_]\w*)\s*/\s*([A-Za-z_]\w*)", ctx.lines[ctx.diag_line - 1])
        if m:
            code = f"double avg = (double){m.group(1)} / {m.group(2)};"
            return Draft(before="两个整数相除会丢掉小数。先把其中一个转成 double：", code=code, key_line=1,
                         after="照这个写法改你的那一行，变量名用你自己的。")
    return None


# ------------------------------------------------------------ 固定模板
def template(ctx: Ctx, decision: Decision, flow: str, text: str = "", prior=None, prior_ref: str = "") -> Draft:
    """flow：policy 的 kind，或卡点流程里的具体一步（example / recall / blank / recalled / reveal_line / stubborn / explain）。"""
    hint = ctx.hint()
    quote = default_quote(ctx)
    names = ctx.names()

    if flow == "ack":
        return Draft(before="好，你先想。想到什么，或者卡住了，随时接着说。", actions=["hint", "tell"])
    if flow == "other":
        return Draft(before="这个我帮不上忙。学习上的问题可以接着问，比如哪道题卡住了、哪个概念没弄清。")
    if flow == "concept":
        for pattern, note in hints.CONCEPT_NOTES:
            if re.search(pattern, text):
                return Draft(before=note)
        return Draft(before="这个概念我现在讲不了，讲解功能暂时用不了。可以换一个和你的代码有关的具体问题问我。")
    if flow == "task":
        brief = ctx.problem.brief if ctx.problem else ""
        return Draft(before=(f"这题要做的是：{brief}。" if brief else "") +
                     "具体的输入输出格式以题面为准。哪一句没看懂，把那句话发过来。")
    if flow == "ask_which":
        if ctx.candidates:
            refs = "和".join(c.ref for c in ctx.candidates[:2])
            return Draft(before=f"你最近有 {len(ctx.candidates)} 道题没通过：{refs}，问的是哪一道？")
        if ctx.problem is not None and ctx.submission is None:
            return Draft(before="这道题你还没有提交过。先在 XDOJ 上交一次，或者把代码贴过来，我再帮你看。")
        return Draft(before="你问的是哪一道题？在题目列表里选一道，或者把代码贴过来。")
    if flow == "passed":
        return Draft(before="这道题你已经通过了。还有哪里想弄清楚，可以直接问。")
    if flow == "pending":
        return Draft(before="这次提交的详细结果还在取，稍等几秒再问一次。")
    if flow == "affirm":
        if "懂" in text or "明白" in text or "过了" in text or "会了" in text:
            return Draft(before="好。改完再交一次，有新的问题随时接着说。")
        return Draft(before="对，就是这个原因。你来改这一处，改完再交一次看看。")
    if flow == "locate":
        if ctx.situation:
            return Draft(before=f"我还不能确定问题出在哪。没过的是{ctx.situation}的情况，这种情况下你的程序打算怎么处理？",
                         actions=HELP_ACTIONS)
        return Draft(before="我还不能确定问题出在哪。没过的那一组和通过的有什么不同，你有想法吗？比如数据很少、很多，或者有负数。",
                     actions=HELP_ACTIONS)

    if flow == "help":
        level = decision.help_level
        if level == "H0":
            return Draft(before="看你这几行：" if quote else "", quote=quote, after=ctx.fill(hint.ask), actions=HELP_ACTIONS)
        if level == "H1":
            return Draft(before="看你这几行：" if quote and not ctx_located(ctx) else "",
                         quote=None if ctx_located(ctx) else quote, after=ctx.fill(hint.where), actions=HELP_ACTIONS)
        body = explain(ctx, hint, decision.reveal_level == "乙")
        if re.search(r"没关系|不影响|没问题|没事", text):
            body = "有关系。" + body
        return Draft(before="", quote=None if ctx_located(ctx) else quote, after=body, actions=HELP_ACTIONS)

    # ---- 卡点流程（5.6）
    if flow == "example":
        ex = example_for(ctx)
        if ex is not None:
            return ex
        body = explain(ctx, hint, True)
        tail = f"照这个思路改第 {ctx.diag_line} 行，改完再交一次。" if ctx.diag_line else "照这个思路改一下，改完再交一次。"
        return Draft(before="这一处我直接说。", quote=None if ctx_located(ctx) else quote, after=f"{body}{tail}")
    if flow == "recall":
        if prior_ref:
            return Draft(before=f"上次{prior_ref}你也卡在这里。还记得当时是怎么解决的吗？先说说思路。")
        return Draft(before="之前你也在这类地方卡过。还记得当时是怎么解决的吗？先说说思路。")
    if flow == "blank" and prior is not None and prior.given_code:
        lines = prior.given_code.split("\n")
        k = min(max(prior.key_line or 1, 1), len(lines))
        indent = lines[k - 1][:len(lines[k - 1]) - len(lines[k - 1].lstrip())]
        lines[k - 1] = indent + BLANK
        tail = "空着的那一行，换成这道题的变量该怎么写？"
        if names["arr"] and names["n"]:
            tail = f"这题的数组是 `{names['arr']}`、个数是 `{names['n']}`。" + tail
        return Draft(before="这是上次那段：", code="\n".join(lines), code_caption="上次给你的那段", code_is_old=True,
                     code_mark=[k], after=tail)
    if flow == "blank":  # 上次没有留下代码：直接讲原理
        return Draft(before="那我再讲一遍。", after=explain(ctx, hint, True))
    if flow == "recalled":
        return Draft(before="对，就是这个思路。你自己写写看，写完交一次。")
    if flow == "reveal_line" and prior is not None and prior.given_code:
        lines = prior.given_code.split("\n")
        k = min(max(prior.key_line or 1, 1), len(lines))
        return Draft(before="那一行是这样写的：", code=lines[k - 1].strip(), code_caption="上次给你的那段", code_is_old=True,
                     after="把变量名换成这道题的，写进你的代码里，再交一次。")
    if flow == "stubborn":
        ex = example_for(ctx)
        code = ex.code if ex is not None else (prior.given_code if prior is not None and prior.given_code else None)
        if code:
            return Draft(before="这个点已经卡过几次了，这次先看一个完整的小例子：", code=code, key_line=1,
                         after="看懂之后做下面这道小题，它换了一个场景。")
        return Draft(before="这个点已经卡过几次了。" + ctx.fill(hint.why), after="建议找老师当面讲一遍。下面这道小题先试试。")
    if flow == "explain":
        return Draft(before="先把上面那段照着写进你的代码里，交一次看看。哪一行没看懂，把那一行发给我。")
    return Draft(before="再把你的想法说具体一点，我接着帮你看。")


def ctx_located(ctx: Ctx) -> bool:
    return bool(getattr(ctx, "_located", False))


def explain(ctx: Ctx, hint: hints.Hint, with_example: bool) -> str:
    """讲原理：有结论句（so）时，例子代替原理句，再接结论；否则原理句后面跟例子。"""
    why, example, so = ctx.fill(hint.why), ctx.fill(hint.example), ctx.fill(hint.so)
    if so:
        return (example if (with_example and example) else why) + so
    return why + (example if with_example else "")


# ------------------------------------------------------------ 大模型
_LEVEL_RULES = {
    "H0": "只反问一次，引导他自己发现问题。可以引用他的代码。不讲原因，不给改法，不给新代码。",
    "H1": "指出问题的大致位置，或者说明是哪类情况下出错。不讲原因，不给改法，不给新代码。",
    "H2": "讲清楚原理和触发条件。可以给一个你自己构造的很小的例子（不超过一行数据）。新写的代码最多 2 行。",
    "H3": "给他卡住的那一块的示例代码，3 到 8 行，用他代码里的变量名，不给整题。代码前一句说这块做什么，代码后一句说接下来他自己要做什么。",
}
_FLOW_RULES = {
    "other": "这条消息和学习无关。一两句回应，引回学习。",
    "concept": "这是概念问题。直接讲清楚，带一个不超过 3 行的小例子；如果给出了他以前犯过的相关错误，顺带指出。不超过 300 字。",
    "task": "他在问题意。用你自己的话解释题意和输入输出格式，不涉及解法。",
    "affirm": "他答对了或表示懂了。简短肯定，让他自己去改、再交一次。不要夸张的表扬。",
    "locate": "现在还不能确定问题在哪。问一个帮助定位的问题，只说是哪类情况，不给例子。",
    "recall": "他以前在同一个地方卡过。用给出的那道题的写法提醒他上次在哪里卡过，请他先说思路。不给新代码。",
    "recalled": "他回忆出了思路。肯定，让他自己写，不给代码。",
    "explain": "示例代码已经给过了，他还不明白。针对他的话再解释一下那段代码，不给新代码。",
}
_SYSTEM = """你是大学 C 语言课程的答疑助手，在帮一名学生看他自己的作业。说话像一位认真的助教：平实、简短、直接。
输出 JSON：{"before":"代码块前面的话","quote":{"from":10,"to":12,"mark":[10]},"code":null,"after":"代码块后面的话","keyLine":null}
- quote：要引用他自己的代码时填行号范围（2 到 8 行）和要标出的行，由系统把那几行原样贴出来；不引用填 null。你不要自己抄他的代码。
- code：只有这一级允许给新代码时才填，否则填 null。keyLine 是 code 里最关键的那一行的序号。
- 提到变量名用反引号。提到行号必须是他代码里真实的行号。
硬性规则：
1. 学生看不到测试点。下面给你的测试点输入、期望输出、实际输出只用来帮你判断错在哪；
   回复里绝不能出现这些数据（哪怕一部分），也不能说“第几组的输入是……”。需要举例时，自己构造一个不同的小例子，
   只说“哪类情况”（比如“全是负数的时候”）。
2. 提到当前这道题说“这题”；提到别的题必须用给出的“「题目名」（简述）”原样写法，不说题号。
3. 一般不超过 150 字（代码不计）。
4. 不出现任何内部用语（编码、级别、学情、标注之类），不用 emoji，不用感叹号堆砌，不说教。
5. 只做下面“这次的任务”要求的事，不多给。"""


def generate_llm(ctx: Ctx, decision: Decision, flow: str, text: str, history: list[tuple[str, str]],
                 prior_ref: str = "", feedback: str = "") -> Draft:
    """让大模型写回复。结构不对就抛 LLMUnavailable，由调用方退回模板。"""
    n_lines = len(ctx.lines)
    allow_code = decision.help_level in ("H2", "H3") or flow == "concept"

    def validate(d: dict) -> Draft:
        before, after = str(d.get("before") or "").strip(), str(d.get("after") or "").strip()
        if not (before or after):
            raise ValueError("回复是空的")
        quote = None
        q = d.get("quote")
        if isinstance(q, dict) and n_lines:
            try:
                a, b = int(q["from"]), int(q["to"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("quote 的行号不对")
            if not (1 <= a <= b <= n_lines) or b - a + 1 > 8:
                raise ValueError("quote 的范围不在他的代码里，或超过 8 行")
            marks = [int(x) for x in (q.get("mark") or []) if isinstance(x, int) and a <= x <= b]
            quote = (a, b, marks)
        code = d.get("code")
        code = str(code).strip("\n") if isinstance(code, str) and code.strip() else None
        if code and not allow_code:
            raise ValueError("这一级不能给新代码")
        key = d.get("keyLine") if isinstance(d.get("keyLine"), int) else None
        return Draft(before=before, quote=quote, code=code, after=after, key_line=key)

    task = _FLOW_RULES.get(flow) or _LEVEL_RULES.get(decision.help_level, "")
    if flow == "example":
        task = _LEVEL_RULES["H3"]
    numbered = "\n".join(f"{i + 1:>3}| {line}" for i, line in enumerate(ctx.lines)) if ctx.lines else "（没有代码）"
    numbered = llm.strip_identity(numbered, ctx.user_id, ctx.user_name)
    from ..taxonomy import error_name, skill_name

    facts = [
        f"这道题：{ctx.problem.title}（{ctx.problem.brief}）" if ctx.problem else "没有绑定到具体的题",
        f"他的第 {ctx.attempt_no} 次提交，结果：{ctx.submission.judgement}" if ctx.submission else "",
        f"问题类型：{error_name(ctx.root_error)}" if ctx.root_error else "",
        f"涉及的技能：{skill_name(ctx.root_skill)}" if ctx.root_skill else "",
        f"出问题的行：第 {ctx.diag_line} 行" if ctx.diag_line else "",
        f"没通过的是哪类情况：{ctx.situation}" if ctx.situation else "",
        f"能不能给自己构造的小例子：{'能' if decision.reveal_level == '乙' else '不能，只说是哪类情况'}",
        f"他以前卡过的那道题：{prior_ref}" if prior_ref else "",
    ]
    talk = "\n".join(f"{'学生' if who == 'student' else '助手'}：{what[:300]}" for who, what in history[-4:])
    failed = [t for t in (ctx.evidence or {}).get("testCases", []) if t.get("passed") is False][:2]
    cases = "\n\n".join(
        f"输入：\n{(t.get('standardInput') or '')[:300]}\n期望输出：\n{(t.get('standardOutput') or '')[:300]}\n"
        f"他的程序输出：\n{(t.get('programOutput') if t.get('programOutput') is not None else '（没取到）')[:300]}"
        for t in failed) or "（没有，或者是编译错误）"
    user = ("【这次的任务】" + task + "\n\n【已知情况（学生看不到这些）】\n" + "\n".join(f for f in facts if f) +
            f"\n\n【他的代码】\n{numbered[:5000]}\n\n"
            f"【没通过的测试点（只给你分析用，绝不能把这些数据告诉学生）】\n{cases}\n\n"
            f"【最近的对话】\n{talk or '（无）'}\n\n"
            f"【学生这条消息】\n{llm.strip_identity(text, ctx.user_id, ctx.user_name)[:1500]}")
    if feedback:
        user += f"\n\n【上一次写的不合格】{feedback}。请改正后重新输出。"
    draft = llm.call_json("reply", _SYSTEM, user, validate, retries=1, max_tokens=700)
    if HELP_ORDER.get(decision.help_level, -1) >= 0 and flow in ("help", "locate"):
        draft.actions = list(HELP_ACTIONS)
    return draft
