"""每个知识点“做得怎么样”的一句话：每个学生一句，全班一句。程序从数据里数出来，不靠大模型。

数的是三样（和掌握程度算法用的是同一批事实）：
- 做题：这个知识点下的题（看题目的标注：大模型标的，或者按 XDOJ 标签兜底标的），一次做对几道、交了几次改对几道、
  还没过几道；
- 会话：智能体评“理解”的结果（对话里他自己说的话，没问过问题的看他怎么改代码），0~3 档的平均；
- 常错：没过的提交里标出来的错误（学生端的说法）。

学生端只用学生自己那一句（没有分数、没有概率）；教师端每个学生一句，再加全班一句。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .taxonomy import course_of, error_name, kp_of_skill


@dataclass
class Record:
    problems: int = 0                                    # 交过的题
    first_try: int = 0                                   # 一次就做对
    fixed_tries: list[int] = field(default_factory=list)  # 改对的题各交了几次
    unsolved: int = 0                                    # 交过、还没过
    talks: list[int] = field(default_factory=list)       # 智能体评“理解”的等级（0~3）
    errors: Counter = field(default_factory=Counter)     # 常错（学生端说法）
    err_fixed: int = 0      # 题不是考这个的，但他在这方面出过错、后来改对了（几道题）
    err_unsolved: int = 0   # 同上，还没改对

    @property
    def fixed(self) -> int:
        return len(self.fixed_tries)


def problem_kps(s: Session, keys: set[tuple[int, int]] | None = None) -> dict[tuple[int, int], set[str]]:
    """每道题涉及的知识点（当前标注的主技能和次技能）。"""
    q = select(m.ProblemLabel).where(m.ProblemLabel.is_current.is_(True))
    out: dict[tuple[int, int], set[str]] = {}
    for label in s.scalars(q).all():
        key = (label.server_id, label.problem_id)
        if keys is not None and key not in keys:
            continue
        out[key] = {kp_of_skill(c) for c in [label.primary_skill, *(label.secondary_skills or [])] if c}
    return out


def records(s: Session, user_ids: list[str], course: str,
            keys: set[tuple[int, int]] | None = None) -> dict[str, dict[str, Record]]:
    """每个学生每个知识点的记录。keys 给了就只看这些题（比如这个班布置过的题）。

    一道题算进它标注的知识点；没通过时标出来的错误属于别的知识点的（题考最值、错在 printf），
    也算进那个知识点的“出过这方面的错”，和掌握程度算法记证据的方式一样。
    """
    out: dict[str, dict[str, Record]] = defaultdict(lambda: defaultdict(Record))
    if not user_ids:
        return out
    kps_of = {k: {kp for kp in v if course_of(kp) == course} for k, v in problem_kps(s, keys).items()}
    in_scope = (lambda k: k in keys) if keys is not None else (lambda k: True)
    subs = s.scalars(select(m.Submission).where(m.Submission.user_id.in_(user_ids))
                     .order_by(m.Submission.submission_id)).all()
    per: dict[tuple[str, int, int], list[m.Submission]] = defaultdict(list)
    for sub in subs:
        if in_scope((sub.server_id, sub.problem_id)):
            per[(sub.user_id, sub.server_id, sub.problem_id)].append(sub)
    # 每道题没通过的那几次（第一次通过之前），和它们标出来的错误
    failed: dict[tuple[int, int], tuple[str, int, int]] = {}
    outcome: dict[tuple[str, int, int], int | None] = {}
    for key, rows in per.items():
        done = [x for x in rows if x.judgement != "PENDING"]
        first_ac = next((i for i, x in enumerate(done) if x.judgement == "AC"), None)
        outcome[key] = first_ac
        for x in done[: first_ac if first_ac is not None else len(done)]:
            failed[(x.server_id, x.submission_id)] = key
    err_kps: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    err_at: dict[tuple[tuple[str, int, int], str], list[int]] = {}   # 这道题上这个知识点出错的那几次提交
    label_kp: dict[tuple[int, int], str] = {}                        # 每次没通过的提交标出的错误属于哪个知识点
    if failed:
        for lab in s.scalars(select(m.SubmissionLabel).where(
                m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.role == "primary",
                m.SubmissionLabel.submission_id.in_({sid for _srv, sid in failed}))).all():
            key = failed.get((lab.server_id, lab.submission_id))
            if key is None or lab.confidence < 0.5:
                continue
            name = error_name(lab.error_code, student_facing=True)
            own = kps_of.get(key[1:], set())
            extra = {kp_of_skill(lab.skill_code)} if lab.skill_code and course_of(lab.skill_code) == course else set()
            for kp in extra - own:
                err_kps[key].add(kp)
                err_at.setdefault((key, kp), []).append(lab.submission_id)
            label_kp[(lab.server_id, lab.submission_id)] = kp_of_skill(lab.skill_code) if lab.skill_code else ""
            for kp in own | extra:
                if name:
                    out[key[0]][kp].errors[name] += 1
    for key, first_ac in outcome.items():
        if not any(x.judgement != "PENDING" for x in per[key]):
            continue
        uid = key[0]
        for kp in kps_of.get(key[1:], set()):
            rec = out[uid][kp]
            rec.problems += 1
            if first_ac == 0:
                rec.first_try += 1
            elif first_ac is not None:
                rec.fixed_tries.append(first_ac + 1)
            else:
                rec.unsolved += 1
        for kp in err_kps.get(key, set()):
            # 和掌握程度算法一样：这道题通过了，或者后来某一次交的时候这个知识点上的错已经改掉了，就算改对
            last_err = max(err_at.get((key, kp), [0]))
            later = [x for x in per[key] if x.submission_id > last_err and x.judgement != "PENDING"]
            fixed = first_ac is not None or any(label_kp.get((x.server_id, x.submission_id), kp) != kp for x in later)
            if fixed:
                out[uid][kp].err_fixed += 1
            else:
                out[uid][kp].err_unsolved += 1
    for a in s.scalars(select(m.AgentAssessment).where(
            m.AgentAssessment.user_id.in_(user_ids), m.AgentAssessment.level.is_not(None))).all():
        if course_of(a.skill_code) == course:
            out[a.user_id][kp_of_skill(a.skill_code)].talks.append(int(a.level))
    return out


def _talk_text(levels: list[int], teacher: bool) -> str:
    if not levels:
        return ""
    mean = sum(levels) / len(levels)
    if mean >= 2:
        text = "对话里能讲清楚为什么"
    elif mean >= 1:
        text = "能照着做，为什么这样做还讲不太清"
    else:
        text = "对话里看，还没弄懂"
    return text + (f"（智能体评过 {len(levels)} 次）" if teacher else "")


def describe(rec: Record | None, teacher: bool = False) -> str:
    """一个学生在一个知识点上的一句话。没有数字分数；题数可以说。"""
    if rec is None or (rec.problems == 0 and not rec.talks and not rec.err_fixed and not rec.err_unsolved):
        return "还没有做过这方面的题"
    parts = []
    if rec.problems:
        bits = []
        if rec.first_try:
            bits.append(f"{rec.first_try} 道一次就做对")
        if rec.fixed:
            tries = sorted(rec.fixed_tries)
            if len(tries) == 1:
                bits.append(f"1 道交了 {tries[0]} 次改对")
            else:
                bits.append(f"{rec.fixed} 道交了几次改对（最多 {tries[-1]} 次）")
        if rec.unsolved:
            bits.append(f"{rec.unsolved} 道还没过")
        parts.append(f"做过 {rec.problems} 道：" + "，".join(bits))
    if rec.err_fixed or rec.err_unsolved:
        bits = ([f"{rec.err_fixed} 道改对了"] if rec.err_fixed else []) + ([f"{rec.err_unsolved} 道还没改对"] if rec.err_unsolved else [])
        parts.append(f"在 {rec.err_fixed + rec.err_unsolved} 道别的题里出过这方面的错：" + "，".join(bits))
    talk = _talk_text(rec.talks, teacher)
    if talk:
        parts.append(talk)
    if rec.errors and (rec.unsolved or teacher):
        name, _n = rec.errors.most_common(1)[0]
        parts.append(f"常错：{name}")
    return "；".join(parts) + "。"


def describe_class(recs: list[Record], n_students: int) -> str:
    """全班在一个知识点上的一句话。"""
    done = [r for r in recs if r.problems]
    if not done:
        talked = sum(1 for r in recs if r.talks)
        return f"全班 {n_students} 人都还没有做过这方面的题" + (f"，{talked} 人在对话里聊过。" if talked else "。")
    total = sum(r.problems for r in done)
    first = sum(r.first_try for r in done)
    fixed = sum(r.fixed for r in done)
    unsolved = sum(r.unsolved for r in done)
    pct = lambda x: round(100 * x / total)  # noqa: E731
    parts = [f"全班 {n_students} 人里 {len(done)} 人做过，一共 {total} 题次：一次做对 {pct(first)}%，改对 {pct(fixed)}%，"
             f"还没过 {pct(unsolved)}%"]
    tries = [t for r in done for t in r.fixed_tries]
    if tries:
        parts.append(f"改对的平均交 {sum(tries) / len(tries):.1f} 次")
    talks = [r for r in recs if r.talks]
    if talks:
        clear = sum(1 for r in talks if sum(r.talks) / len(r.talks) >= 2)
        parts.append(f"智能体评过 {len(talks)} 人的理解，{clear} 人讲得清楚为什么")
    errors = Counter()
    for r in recs:
        errors.update(r.errors)
    if errors:
        parts.append("常错：" + "、".join(n for n, _c in errors.most_common(2)))
    stuck = sum(1 for r in done if r.unsolved and not r.first_try and not r.fixed)
    if stuck:
        parts.append(f"{stuck} 人做过的题都还没过")
    return "；".join(parts) + "。"
