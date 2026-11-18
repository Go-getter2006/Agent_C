"""跨课程：数据结构这边怎么联系学生在 C 语言里的掌握情况。

两门课分开登录、分开显示。数据结构这边可以看 C 语言里相关知识点掌握得怎么样（只有档位和估计），
但不出现 C 语言的答题记录（提交、对话、卡点、小题）。C 语言这边不出现数据结构的任何东西。
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from . import mastery
from .taxonomy import KNOWLEDGE_POINTS, LEVEL_NONE, kp_of_skill
from .taxonomy.prereq import CROSS_EDGES


def feeds() -> dict[str, set[str]]:
    """C 语言知识点 → 它支撑的数据结构知识点（由技能一层的跨课程先修边汇总）。"""
    out: dict[str, set[str]] = defaultdict(set)
    for a, b in CROSS_EDGES:
        out[kp_of_skill(a)].add(kp_of_skill(b))
    return out


def c_foundation(s: Session, user_id: str) -> list[dict]:
    """这个学生在数据结构要用到的 C 语言知识点上的掌握程度。只有档位和估计，没有任何答题记录。"""
    states = mastery.kp_states(s, user_id, model="v2")
    rows = []
    for c_kp, d_kps in sorted(feeds().items()):
        st = states.get(c_kp)
        known = st is not None and not st.predicted
        rows.append({"code": c_kp, "name": KNOWLEDGE_POINTS.get(c_kp, ""),
                     "level": st.level if known else LEVEL_NONE, "p": round(st.p, 2) if known else None,
                     "supports": [{"code": d, "name": KNOWLEDGE_POINTS.get(d, "")} for d in sorted(d_kps)]})
    return rows
