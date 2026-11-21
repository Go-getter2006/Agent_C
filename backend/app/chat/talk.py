"""给大模型看的对话：每条消息的文字，加上消息里的代码块（智能体摘出的他的代码、他贴的代码、示例代码）。

以前只取文字段落，智能体说“看你这几行：”后面贴的代码大模型看不到；对话也只取最近 4 条、每条 300 字，
前面一问的回答常被截在半句。现在所有要读对话的调用（理解他这条消息、写回复、流程图引导、通过后的回顾、
评“理解”）都从这里取，同一套截法。发出去之前去掉学号和姓名。
"""
from __future__ import annotations

from .. import llm
from .. import models as m

CODE_LINES = 15     # 一个代码块最多给几行
TURNS = 8           # 最近几条
PER_TURN = 800      # 每条最多几个字


def message_for_model(msg: m.ChatMessage) -> str:
    parts = []
    for b in (msg.content or {}).get("blocks", []):
        if b.get("t") == "p" and (b.get("text") or "").strip():
            parts.append(b["text"])
        elif b.get("t") == "code" and b.get("lines"):
            lines = list(b["lines"])
            start = int(b.get("start") or 1)
            body = "\n".join(f"{start + i:>3}| {x}" for i, x in enumerate(lines[:CODE_LINES]))
            more = f"\n（后面还有 {len(lines) - CODE_LINES} 行）" if len(lines) > CODE_LINES else ""
            parts.append(f"[代码：{b.get('caption') or ''}]\n{body}{more}")
    return "\n".join(parts)


def history(messages: list[m.ChatMessage], exclude: int | None = None) -> list[tuple[str, str]]:
    """(role, 给大模型看的内容)；exclude：这一条（他刚发的）单独给，不放进“最近的对话”。"""
    return [(x.role, message_for_model(x)) for x in messages if x.kind == "text" and x.id != exclude]


def dialogue(turns: list[tuple[str, str]], user_id: str = "", name: str = "", limit: int = TURNS,
             per: int = PER_TURN) -> str:
    rows = []
    for who, what in turns[-limit:]:
        what = llm.strip_identity(what or "", user_id, name).strip()
        if not what:
            continue
        if len(what) > per:
            what = what[:per] + "…"
        rows.append(f"{'学生' if who == 'student' else '助手'}：{what}")
    return "\n".join(rows)
