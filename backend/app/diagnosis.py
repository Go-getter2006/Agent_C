"""判断、定位、修改三维（界面改造方案第 5 节）。每个数都能从作答记录复算。

单位是一名学生对某一版题目的一次作答（quiz_attempts 一行）。
- 判断：首次判断正确数 / 提交了首次判断的题数。错题识别、正确代码识别分开算分子分母。
- 定位：首次选中有效位置数 / 真实错题里判断有问题、提交了首次选行的次数（只算独立作答，protocol 2）。
- 修改：第一稿未经提示、未经运行公开样例就通过数 / 真实错题里提交过这种第一稿的次数（只算 protocol 2）。
  先运行了公开样例、看了提示再交的，不进分母，另记在“提示后或运行后才改对”里。
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
from .taxonomy import KNOWLEDGE_POINTS, course_of, kp_of_skill

VERSION = "dims-1"   # 定义改了就改版本号（研究导出里带上，docs/研究数据与指标.md）


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
    edit: Ratio = field(default_factory=Ratio)
    shown: int = 0           # 应答机会
    locate_entered: int = 0
    edit_entered: int = 0
    after_help: int = 0      # 看了提示、运行了公开样例或重试以后才改对
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
        d.shown += 1
        if a.skipped:
            d.skipped += 1
            continue
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
            if real_bug_path:
                d.locate_entered += 1
                if a.first_lines:
                    d.locate.add(bool(a.locate_hit))
                    d.edit_entered += 1
                if a.tries >= 1 and a.first_fix_pass is not None and a.first_fix_clean:
                    d.edit.add(bool(a.first_fix_pass))   # 分母只算没看提示、没运行公开样例就交的第一稿
                if a.outcome in ("right", "right_bypass", "right_second") and not (a.first_fix_pass and a.first_fix_clean):
                    d.after_help += 1
        if a.judged_has_bug is not None and (d.latest is None or a.id > d.latest.id):
            d.latest, d.latest_item = a, item
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


def describe(d: Dims) -> str:
    """学生端：一句基于实际作答的说明。"""
    if not d.judge.d:
        return "还没有做过这方面的小题。"
    text = f"{d.judge.d} 道小题里，你有 {d.judge.n} 次判断正确。"
    if d.locate.d or d.edit.d:
        both = d.locate.d == d.edit.d and d.locate.n == d.edit.n
        if both:
            text += f"在 {d.locate.d} 道进入定位和修改的错题里，你各有 {d.locate.n} 次首次完成。"
        else:
            if d.locate.d:
                text += f"在 {d.locate.d} 道进入定位的错题里，{d.locate.n} 次第一次就选中出错的地方。"
            if d.edit.d:
                text += f"{d.edit.d} 次修改里，{d.edit.n} 次第一稿就改对。"
    elif d.legacy:
        text += "以前的小题没有单独记选行。"
    return text


def suggest(d: Dims) -> str:
    """学生端：最近一次怎么样、下次怎么做（不打分）。"""
    a, item = d.latest, d.latest_item
    if a is None or item is None or a.state != "done":
        return ""
    if not a.judge_correct:
        if item.has_bug:
            return "最近一次没发现代码里的问题。下次先对照题目要求，一行一行读。"
        return "最近一次把正确的代码当成了有问题。下次先找到具体不符合要求的那一行，再下结论。"
    if a.protocol != 2 or not item.has_bug:
        return "最近一次判断对了。"
    if a.locate_hit and a.first_fix_pass and a.first_fix_clean:
        return "最近一次自己找到了问题，第一稿就改对了。"
    if a.locate_hit:
        return "最近一次能指出出错的那一行。下次可以换一段代码，再看看能否独立改对。"
    return "最近一次发现了有问题，但没选中出错的地方。下次先说清楚哪一行不符合要求，再动手改。"


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
    order = [e.get("kind") for e in (a.events or []) if e.get("kind") not in ("shown",)]
    return {
        "title": item.stem, "time": (a.finished_at or a.created_at).isoformat(),
        "steps": steps,
        "code": {"start": lo, "lines": lines[lo - start:hi - start + 1], "picked": picked, "bug": bug_lines},
        "order": order, "hints": a.hints, "sampleRuns": a.sample_runs, "legacy": a.protocol != 2,
    }


def student_rows(d: dict[str, Dims]) -> list[dict]:
    """学生端：只给知识点名称、几比几、一句话。不给技能编码、概率。"""
    rows = sorted(d.values(), key=lambda x: (-(x.latest.id if x.latest else 0)))
    return [{"key": f"k{i}", "name": KNOWLEDGE_POINTS.get(x.kp, ""), "judge": x.judge.view(), "locate": x.locate.view(),
             "edit": x.edit.view(), "text": describe(x), "latest": suggest(x)}
            for i, x in enumerate(rows) if x.judge.d]


def teacher_rows(s: Session, d: dict[str, Dims]) -> list[dict]:
    rows = sorted(d.values(), key=lambda x: x.kp)
    out = []
    for x in rows:
        if not x.shown:
            continue
        out.append({
            "code": x.kp, "name": KNOWLEDGE_POINTS.get(x.kp, ""),
            "judge": x.judge.view(), "judgeBug": x.judge_bug.view(), "judgeOk": x.judge_ok.view(),
            "locate": x.locate.view(), "edit": x.edit.view(),
            "counts": {"shown": x.shown, "locateEntered": x.locate_entered, "editEntered": x.edit_entered,
                       "afterHelp": x.after_help, "skipped": x.skipped, "open": x.open, "legacy": x.legacy},
            "latest": evidence(s, x.latest, x.latest_item) if x.latest is not None else None,
        })
    return out


def summary(d: Dims | None) -> dict:
    """知识图谱节点详情：全班三行。"""
    if d is None:
        return {"judge": None, "locate": None, "edit": None, "shown": 0}
    return {"judge": d.judge.view(), "locate": d.locate.view(), "edit": d.edit.view(), "shown": d.shown,
            "judgeBug": d.judge_bug.view(), "judgeOk": d.judge_ok.view()}
