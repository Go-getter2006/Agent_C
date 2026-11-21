"""开发者所选时点的逐技能回算。所有模型共享机会、先验和时间边界，只逐步增加证据来源。"""
from __future__ import annotations

import datetime as dt
import random
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, db, mastery, models as m
from .taxonomy import skill_name, skills_for_course

VERSION = "paired-replay-1"
CHANNELS = ("oj", "dialogue", "quiz")


@dataclass
class Counts:
    n: list[int] = field(default_factory=lambda: [0, 0, 0])
    successes: list[float] = field(default_factory=lambda: [0., 0., 0.])

    def add(self, channel, score, delta=1):
        self.n[channel] += delta
        self.successes[channel] += delta * score

    def estimates(self):
        # 三组均为同一 Beta(1,1) 平滑基线，每条观测权重为一，理解等级折为 0～1。
        n, hits = 2, 1.
        values = []
        for count, score in zip(self.n, self.successes):
            n += count
            hits += score
            values.append(hits / n)
        return values


class HelpHistory:
    """按学生、题索引。学生发来的消息与智能体实际给出的帮助级别分开。"""
    def __init__(self, s, ids, server, course, end):
        self.first = {}
        self.steps = defaultdict(list)
        messages = select(m.ChatMessage.user_id, m.ChatMessage.problem_id, m.ChatMessage.created_at).where(
            m.ChatMessage.user_id.in_(ids), m.ChatMessage.server_id == server, m.ChatMessage.course == course,
            m.ChatMessage.role == "student", m.ChatMessage.problem_id.is_not(None), m.ChatMessage.created_at < end)
        for uid, pid, ts in s.execute(messages.order_by(m.ChatMessage.created_at)):
            self.first.setdefault((uid, pid), ts)
        for uid, pid, ts, level in s.execute(select(m.ChatStep.user_id, m.ChatStep.problem_id,
                m.ChatStep.created_at, m.ChatStep.help_level).where(m.ChatStep.user_id.in_(ids),
                m.ChatStep.server_id == server, m.ChatStep.course_id == course, m.ChatStep.problem_id.is_not(None),
                m.ChatStep.help_level.in_(("H0", "H1", "H2", "H3")), m.ChatStep.created_at < end)):
            self.steps[uid, pid].append((ts, level))

    def before(self, uid, pid, at):
        first = self.first.get((uid, pid))
        highest = max((level for ts, level in self.steps.get((uid, pid), []) if ts < at), default=None)
        return {"helpObserved": highest is not None or (first is not None and first < at), "maxHelp": highest}


def paired_brier(losses):
    """同一学生整组重抽，组内保留其所有机会；估计量按机会加权。"""
    if not losses:
        return {"scores": [], "differences": []}
    rows = [losses[uid] for uid in sorted(losses)]
    n = sum(r[0] for r in rows)
    scores = [sum(r[i] for r in rows) / n for i in (1, 2, 3)]
    draws = [[], []]
    if len(rows) > 1:
        rng = random.Random(202611)
        for _ in range(1000):
            sample = rng.choices(rows, k=len(rows))
            size = sum(r[0] for r in sample)
            for i in (0, 1):
                draws[i].append(sum(r[i+2] - r[i+1] for r in sample) / size)
    differences = []
    for i, samples in enumerate(draws):
        samples.sort()
        differences.append({"delta": scores[i+1] - scores[i],
                            "interval": [samples[24], samples[974]] if samples else None})
    return {"scores": scores, "differences": differences}


def _quiz_events(s, ids, course, skills, cutoff):
    from .quiz import revealing_stem

    events = []
    quizzes = select(m.QuizAttempt, m.QuizItem).join(m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id).where(
        m.QuizAttempt.user_id.in_(ids), m.QuizAttempt.skill_code.like(course + "%"), m.QuizAttempt.protocol >= 2,
        m.QuizAttempt.judge_correct.is_not(None), m.QuizAttempt.created_at < cutoff)
    for q, item in s.execute(quizzes):
        if q.skill_code not in skills:
            continue
        # 一道小题共占一份证据。每个独立步骤只有在当时完成后才更新这份证据的均值。
        parts, measured = [], set()
        hinted = viewed = False
        for e in q.events or []:
            if not e.get("t"):
                continue
            t = dt.datetime.fromisoformat(e["t"])
            if t.tzinfo is None or t >= cutoff:
                continue
            kind = e.get("kind")
            hinted |= kind == "hint"
            viewed |= kind == "viewed" and e.get("state") == "judge"
            if kind in measured:
                continue
            if kind == "judge":
                score = q.judge_correct
            elif not parts or not (q.judged_has_bug and q.judge_correct):
                continue
            elif kind == "locate" and (viewed or not revealing_stem(item)) and not hinted:
                score = q.locate_hit
            elif kind == "submit" and e.get("first") and not hinted:
                score = e.get("ok")
            else:
                continue
            if score is None:
                continue
            if parts:
                events.append((t, 3, q.user_id, q.skill_code, sum(parts) / len(parts), -1))
            measured.add(kind)
            parts.append(int(score))
            events.append((t, 3, q.user_id, q.skill_code, sum(parts) / len(parts), 1))

    return events


def _first_attempts(s, ids, server_id, course, cutoff, skills, labels, events, exclusions):
    versions = {pid: [v.created_at for v in rows] for pid, rows in labels.items()}
    helps = HelpHistory(s, ids, server_id, course, cutoff)
    fps = dict(s.execute(select(m.CodeFingerprint.submission_id, m.CodeFingerprint.suspicious).where(
        m.CodeFingerprint.server_id == server_id, m.CodeFingerprint.user_id.in_(ids),
        m.CodeFingerprint.tokens >= config.MASTERY_V2["copy_min_tokens"])).all())
    seen = set()
    query = select(m.Submission).where(m.Submission.server_id == server_id, m.Submission.user_id.in_(ids),
              m.Submission.submit_time < cutoff).order_by(m.Submission.submit_time, m.Submission.submission_id)
    for sub in s.scalars(query):
        uid, pid = sub.user_id, sub.problem_id
        if (uid, pid) in seen:
            continue
        seen.add((uid, pid))
        reference = {"studentId": uid, "problemId": pid, "submissionId": sub.submission_id}
        if sub.judgement in ("PENDING", "UNKNOWN", ""):
            exclusions["unjudged"].append(reference)
            continue
        ok = int(sub.judgement == "AC")
        history = labels[pid]
        idx = bisect_left(versions.get(pid, []), sub.submit_time) - 1
        if sub.fetched_at >= cutoff:
            exclusions["resultUnavailableAtCutoff"].append(reference)
        elif idx >= 0 and history[idx].primary_skill in skills:
            lab = history[idx]
            ref = {**reference, "labelVersion": lab.version, "passed": bool(ok),
                   **helps.before(uid, pid, sub.submit_time),
                   "suspicious": fps.get(sub.submission_id), "cutoff": sub.submit_time.isoformat()}
            events.append((sub.submit_time, 0, uid, lab.primary_skill, ok, ref))
        else:
            exclusions["noLabelAtSubmission"].append(reference)
        # 抓取/标注可能迟于作答。若修订了标签，在修订日把贡献从旧技能转到新技能。
        available = max(sub.fetched_at, sub.submit_time)
        if available >= cutoff:
            continue
        previous = None
        for j, lab in enumerate(history):
            t = max(available, lab.created_at)
            if j + 1 < len(history) and history[j+1].created_at <= t:
                continue
            skill = lab.primary_skill if lab.primary_skill in skills else None
            if previous != skill:
                if previous:
                    events.append((t, 1, uid, previous, ok, -1))
                if skill:
                    events.append((t, 1, uid, skill, ok, 1))
                previous = skill
    return seen


def exam_report(s: Session, people, server_id: int, course: str, exam: dt.datetime, computed: dt.datetime):
    cutoff = min(exam, computed)
    ids = [p.user_id for p in people]
    names = {p.user_id: p.name for p in people}
    skills = skills_for_course(course)
    # 先登记要预测的首交机会，再按实际可用的时间写入证据。同一时刻的结果互不可见。
    events = []
    exclusions = defaultdict(list)
    labels = defaultdict(list)
    for lab in s.scalars(select(m.ProblemLabel).where(m.ProblemLabel.server_id == server_id,
                         m.ProblemLabel.created_at < cutoff).order_by(m.ProblemLabel.created_at, m.ProblemLabel.version)):
        labels[lab.problem_id].append(lab)
    seen = _first_attempts(s, ids, server_id, course, cutoff, skills, labels, events, exclusions)
    for a in s.scalars(select(m.AgentAssessment).where(m.AgentAssessment.user_id.in_(ids),
            m.AgentAssessment.course == course, m.AgentAssessment.basis.in_(("chat", "reflect")),
            m.AgentAssessment.created_at < cutoff, m.AgentAssessment.level.is_not(None))):
        if a.skill_code in skills:
            events.append((a.created_at, 2, a.user_id, a.skill_code, a.level / 3, 1))
    events.extend(_quiz_events(s, ids, course, skills, cutoff))

    counts = defaultdict(Counts)
    losses = defaultdict(lambda: [0, 0., 0., 0.])
    paired, covered = [], [set(), set(), set()]
    for t, kind, uid, skill, value, detail in sorted(events, key=lambda e: (e[0], e[1])):
        state = counts[uid, skill]
        if kind:
            state.add(kind-1, value, detail)
            continue
        scores = state.estimates()
        paired.append({**detail, "skill": skill, "estimates": scores, "evidenceCounts": list(state.n)})
        row = losses[uid]
        row[0] += 1
        for i, score in enumerate(scores):
            row[i+1] += (score - value) ** 2
            if state.n[i]:
                covered[i].add(uid)
    rows = [{"studentId": uid, "name": names[uid], "skill": skill, "skillName": skill_name(skill),
             "evidenceCounts": list(st.n), "estimates": st.estimates()}
            for (uid, skill), st in sorted(counts.items()) if any(st.n)]
    no_data = set(ids) - {r["studentId"] for r in rows}
    passed_rows = [r for r in paired if r["passed"]]
    conditions = {"total": len(paired), "passed": len(passed_rows),
                  "passedNoHelpCheckedClear": sum(not r["helpObserved"] and r["suspicious"] is False for r in passed_rows),
                  "passedSimilarityUnknown": sum(r["suspicious"] is None for r in passed_rows)}
    used_pids = {pid for _, pid in seen}
    return {"version": VERSION, "examTime": exam.isoformat(), "computedAt": computed.isoformat(),
            "cutoffExclusive": cutoff.isoformat(), "mode": "historical-replay", "course": course,
            "modelOrder": list(CHANNELS), "students": len(ids), "pairedStudents": len(losses),
            "pairedOpportunities": len(paired), "missingDialogue": len(set(ids) - covered[1]),
            "missingQuiz": len(set(ids) - covered[2]), "paired": paired, **paired_brier(losses),
            "firstSubmission": conditions, "rows": rows, "noEvidenceStudents": sorted(no_data), "exclusions": dict(exclusions),
            "roster": names, "labelVersions": {pid: [(l.version, l.primary_skill, l.created_at.isoformat()) for l in hist]
                for pid, hist in labels.items() if pid in used_pids},
            "estimator": "每组使用相同的 Beta(1,1) 平滑，依次纳入不同题的首次提交、对话理解等级/3、复习独立判断、定位和首稿的题内均值；无任何证据的技能省略。",
            "population": "所选班级当前在册学生；对话和小题以本课程内同一学生同一技能归集",
            "cutoffPolicy": "event time < cutoff; OJ evidence from max(submit,fetched,label availability)",
            "legacyParamsAtCutoff": mastery.params_as_of(s, course, cutoff-dt.timedelta(microseconds=1)).version}
