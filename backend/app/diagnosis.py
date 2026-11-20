"""判断、定位、修改三维（界面改造方案第 5 节）。每个数都能从作答记录复算。

单位是一名学生对某一版题目的一次作答（quiz_attempts 一行）。
- 判断：首次判断正确数 / 提交了首次判断的题数。错题识别、正确代码识别分开算分子分母。
- 定位：首次选中有效位置数 / 真实错题里判断有问题、提交了首次选行的次数（只算独立作答，protocol 2）。
- 修改（dims-2）：看提示之前交的第一稿通过数 / 真实错题里交过这种第一稿的次数（只算 protocol 2）。
  运行公开样例是自己测一下，不算帮助，进分母；其中“没运行样例就交的”另列（editClean，就是 dims-1 的修改）。
  看了提示才交第一稿的不进分母，记在“提示后或重试后才改对”里。
定位和修改的分母受前面判断的影响：1/2 只说明实际进入这一步的两次，不能读成对所有题的能力估计。

另列（教师端）：应答机会（出过几道）、进入定位、进入修改、提示后或重试后才改对、跳过、还没做完、
旧版记录（protocol 1，有判断、没有独立的选行，标“旧版未测定位”，不能拿后来改了哪行补首次定位）。
不和掌握分硬配权重：掌握程度的估计照旧（mastery.py），三维只用来解释。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .quiz import revealing_stem
from .taxonomy import KNOWLEDGE_POINTS, course_of, kp_of_skill

VERSION = "dims-2"   # 定义改了就改版本号（研究导出里带上，docs/研究数据与指标.md）。dims-2：修改的分母含运行过样例的第一稿


@dataclass
class Ratio:
    n: int = 0
    d: int = 0

    def add(self, ok: bool) -> None:
        self.d += 1
        self.n += 1 if ok else 0

    def view(self) -> dict | None:
        return {"n": self.n, "d": self.d} if self.d else None


@dataclass
class Dims:
    kp: str
    judge: Ratio = field(default_factory=Ratio)
    judge_bug: Ratio = field(default_factory=Ratio)    # 错题识别
    judge_ok: Ratio = field(default_factory=Ratio)     # 正确代码识别
    locate: Ratio = field(default_factory=Ratio)
    edit: Ratio = field(default_factory=Ratio)         # 看提示之前交的第一稿
    edit_clean: Ratio = field(default_factory=Ratio)   # 其中没运行公开样例就交的
    missed: int = 0          # 有错的代码判断成没问题（没进入定位）
    shown: int = 0           # 应答机会
    locate_entered: int = 0
    edit_entered: int = 0
    after_help: int = 0      # 看了提示或重试以后才改对
    skipped: int = 0
    open: int = 0            # 还没做完
    legacy: int = 0          # 旧版作答（未测定位）
    latest: m.QuizAttempt | None = None
    latest_item: m.QuizItem | None = None


def _attempts(s: Session, user_ids: list[str], course: str, since: dt.datetime | None = None):
    q = select(m.QuizAttempt, m.QuizItem).join(m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id).where(
        m.QuizAttempt.user_id.in_(user_ids or ["-"]))
    if since is not None:
        q = q.where(m.QuizAttempt.created_at >= since)
    rows = s.execute(q.order_by(m.QuizAttempt.id)).all()
    return [(a, i) for a, i in rows if a.skill_code and course_of(a.skill_code) == course]


def count(pairs) -> dict[str, Dims]:
    """按知识点汇总。pairs：(作答, 题目)。"""
    out: dict[str, Dims] = {}
    for a, item in pairs:
        kp = kp_of_skill(a.skill_code)
        d = out.setdefault(kp, Dims(kp=kp))
        viewed = any(e.get("kind") == "viewed" for e in a.events or [])
        if not (viewed or a.skipped or a.judged_has_bug is not None or a.confidence):
            continue
        d.shown += 1
        if a.skipped:
            d.skipped += 1
        if a.state != "done":
            d.open += 1
        if a.judged_has_bug is not None:
            ok = bool(a.judge_correct)
            d.judge.add(ok)
            (d.judge_bug if item.has_bug else d.judge_ok).add(ok)
        if a.protocol != 2:
            if a.judged_has_bug is not None:
                d.legacy += 1
        else:
            real_bug_path = item.has_bug and a.judged_has_bug is True
            if item.has_bug and a.judged_has_bug is False:
                d.missed += 1
            if real_bug_path:
                d.locate_entered += 1
                if a.first_lines:
                    if not revealing_stem(item) or any(e.get("kind") == "viewed" and e.get("state") == "judge" for e in a.events or []):
                        d.locate.add(bool(a.locate_hit))
                    d.edit_entered += 1
                hinted, sampled = _before_first_submit(a)
                if a.tries >= 1 and a.first_fix_pass is not None and not hinted:
                    d.edit.add(bool(a.first_fix_pass))   # 看提示之前交的第一稿（运行过样例的也算）
                    if not sampled:
                        d.edit_clean.add(bool(a.first_fix_pass))
                if a.outcome in ("right", "right_bypass", "right_second") and not (a.first_fix_pass and not hinted):
                    d.after_help += 1
        if a.judged_has_bug is not None and (d.latest is None or a.id > d.latest.id):
            d.latest, d.latest_item = a, item
    return out


def _before_first_submit(a: m.QuizAttempt) -> tuple[bool, bool]:
    """第一次提交修改之前，看过提示没有、运行过公开样例没有（按 events 的先后）。"""
    hinted = sampled = False
    for e in a.events or []:
        kind = e.get("kind")
        if kind == "submit":
            break
        hinted = hinted or kind == "hint"
        sampled = sampled or kind == "sample"
    if not any(e.get("kind") == "submit" for e in a.events or []):   # 旧记录没有事件：按 first_fix_clean
        return (not a.first_fix_clean and bool(a.hints)), (not a.first_fix_clean and bool(a.sample_runs))
    return hinted, sampled


def why_empty(x: Dims) -> dict[str, str]:
    """哪一格没有数时，说清楚为什么（不是没记下来，而是还没进到这一步）。"""
    out: dict[str, str] = {}
    if not x.judge_bug.d:
        out["judgeBug"] = "还没做过有错的代码"
    if not x.judge_ok.d:
        out["judgeOk"] = "还没做过本来正确的代码"
    if not x.locate.d:
        if not x.judge_bug.d:
            out["locate"] = "还没做过有错的代码"
        elif x.missed and not x.locate_entered:
            out["locate"] = "没看出有错，没进入定位"
        elif x.locate_entered:
            out["locate"] = "还没选行"
        else:
            out["locate"] = "旧版小题没有选行这一步" if x.legacy else "还没判断"
    if not x.edit.d:
        if x.after_help:
            out["edit"] = f"看提示或重试后才改对 {x.after_help} 次"
        elif out.get("locate"):
            out["edit"] = out["locate"]
        elif x.open:
            out["edit"] = "还没交修改"
        else:
            out["edit"] = "没进入修改"
    return out


def by_student(s: Session, user_ids: list[str], course: str, since: dt.datetime | None = None) -> dict[str, dict[str, Dims]]:
    out: dict[str, list] = {u: [] for u in user_ids}
    for a, item in _attempts(s, user_ids, course, since):
        out[a.user_id].append((a, item))
    return {u: count(pairs) for u, pairs in out.items()}


def for_class(s: Session, user_ids: list[str], course: str, since: dt.datetime | None = None) -> dict[str, Dims]:
    return count(_attempts(s, user_ids, course, since))


def frac(r: Ratio) -> str:
    return f"{r.n}/{r.d}" if r.d else "尚无记录"


def _where(lines: list[int]) -> str:
    if not lines:
        return ""
    return f"第 {lines[0]} 行" if len(lines) == 1 else f"第 {lines[0]}～{lines[-1]} 行"


def evidence(s: Session, a: m.QuizAttempt, item: m.QuizItem) -> dict:
    """教师端：最近一次作答的证据链。判断 → 首次所选行 → 修改与提示，原代码和学生所选的行。"""
    start = item.start_line or 1
    lines = item.shown_code.split("\n")
    picked = list(a.first_lines or [])
    bug_lines = sorted(start + x - 1 for x in (item.bug_lines or ([item.bug_line] if item.bug_line else [])))
    judged = "发现问题" if a.judged_has_bug else "认为满足要求"
    steps = [{"label": "判断", "value": judged,
              "note": f"首次选择「{'存在问题' if a.judged_has_bug else '满足要求'}」，" + ("判断正确" if a.judge_correct else "判断有误")}]
    if a.protocol != 2:
        steps.append({"label": "定位", "value": "旧版未测定位", "note": "以前的小题没有单独选行这一步"})
    elif picked:
        if item.has_bug and a.judged_has_bug:
            note = "首次选择命中评分范围" if a.locate_hit else f"没有命中（有效位置：{_where(bug_lines)}）"
            steps.append({"label": "定位", "value": f"选中{_where(picked)}", "note": note})
        else:
            steps.append({"label": "依据" if not a.judged_has_bug else "定位", "value": f"选中{_where(picked)}",
                          "note": "支撑判断的关键行" if not a.judged_has_bug else "代码本来正确，选的位置其实没有问题"})
    if a.protocol == 2 and a.judged_has_bug:
        if not item.has_bug:
            kind = next((e.get("misjudge") for e in reversed(a.events or []) if e.get("kind") == "submit"), None)
            value = {"kept": "原样提交", "extra": "多余修改", "broke": "改坏了"}.get(kind or "", "未提交")
            steps.append({"label": "修改", "value": value, "note": "代码本来正确：这一步不计入修改成功率"})
        elif a.first_fix_pass and a.first_fix_clean:
            steps.append({"label": "修改", "value": "第一稿通过", "note": "没看提示、没运行公开样例"})
        elif a.first_fix_pass:
            steps.append({"label": "修改", "value": "运行后通过", "note": "运行公开样例后，第一稿通过"})
        elif a.outcome in ("right", "right_bypass", "right_second"):
            steps.append({"label": "修改", "value": "提示后通过" if a.hints else "重试后通过",
                          "note": "首次独立修改未通过，" + ("查看提示后再次提交" if a.hints else "再次提交后通过")})
        elif a.state == "done":
            steps.append({"label": "修改", "value": "没有改对", "note": f"提交了 {a.tries} 次"})
        else:
            steps.append({"label": "修改", "value": "还没做完", "note": ""})
    elif a.protocol == 2 and item.has_bug and not a.judged_has_bug:
        steps.append({"label": "修改", "value": "未进入", "note": "判断时没发现问题"})
    focus = picked or bug_lines
    lo = max(start, (focus[0] if focus else start) - 1)
    hi = min(start + len(lines) - 1, (focus[-1] if focus else start) + 1)
    order = [e.get("kind") for e in (a.events or []) if e.get("kind") not in ("shown", "prepared", "viewed")]
    return {
        "title": item.stem, "time": (a.finished_at or a.created_at).isoformat(),
        "steps": steps,
        "code": {"start": lo, "lines": lines[lo - start:hi - start + 1], "picked": picked, "bug": bug_lines},
        "order": order, "hints": a.hints, "sampleRuns": a.sample_runs, "legacy": a.protocol != 2,
    }


def teacher_rows(s: Session, d: dict[str, Dims]) -> list[dict]:
    rows = sorted(d.values(), key=lambda x: x.kp)
    out = []
    for x in rows:
        if not x.shown:
            continue
        out.append({
            "code": x.kp, "name": KNOWLEDGE_POINTS.get(x.kp, ""),
            "judge": x.judge.view(), "judgeBug": x.judge_bug.view(), "judgeOk": x.judge_ok.view(),
            "locate": x.locate.view(), "edit": x.edit.view(), "editClean": x.edit_clean.view(),
            "why": why_empty(x),
            "counts": {"shown": x.shown, "locateEntered": x.locate_entered, "editEntered": x.edit_entered,
                       "afterHelp": x.after_help, "skipped": x.skipped, "open": x.open, "legacy": x.legacy,
                       "missed": x.missed},
            "latest": evidence(s, x.latest, x.latest_item) if x.latest is not None else None,
        })
    return out


def summary(d: Dims | None) -> dict:
    """知识图谱节点详情：全班三行。"""
    if d is None:
        return {"judge": None, "locate": None, "edit": None, "shown": 0, "why": {}}
    return {"judge": d.judge.view(), "locate": d.locate.view(), "edit": d.edit.view(), "shown": d.shown,
            "judgeBug": d.judge_bug.view(), "judgeOk": d.judge_ok.view(), "editClean": d.edit_clean.view(),
            "why": why_empty(d)}
