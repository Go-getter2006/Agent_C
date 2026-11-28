"""四图画像：真实事实、代码作品量规和可复算的统计。与掌握模型分开，不写回掌握证据。

每周每题一份代表稿：本周有通过取最后一份通过，否则取最新提交。跨周按原提交时间归属。
代码量规按题等权汇总；历史内外轮廓是各维度周均值的极值，不是置信区间。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select, tuple_
from sqlalchemy.orm import defer

from . import codequality, db, diagnosis, models as m
from .taxonomy import KNOWLEDGE_POINTS, course_of, error_name, kp_of_skill
from .xdoj.assemble import source_text

VERSION = "portrait-2"
DOMAINS_C = [("语言基础", ("C02", "C03")), ("输入输出", ("C01",)), ("分支判断", ("C04",)),
             ("循环迭代", ("C05",)), ("数组字符串", ("C06", "C07", "C08")), ("函数递归", ("C09",)),
             ("指针结构", ("C10", "C11")), ("算法应用", ("C12",))]
DOMAINS_D = [("基本概念", ("D01",)), ("线性表", ("D02",)), ("栈与队列", ("D03",)), ("串", ("D04",)),
             ("数组矩阵", ("D05",)), ("树与二叉树", ("D06",)), ("图", ("D07",)), ("查找排序", ("D08", "D09"))]
GROUPS_C = [("语言基础", ("C01", "C02", "C03")), ("流程控制", ("C04", "C05")),
            ("数据组织", ("C06", "C07", "C08", "C10", "C11")), ("函数算法", ("C09", "C12"))]
GROUPS_D = [("基本概念", ("D01",)), ("线性结构", ("D02", "D03", "D04", "D05")),
            ("树与图", ("D06", "D07")), ("查找排序", ("D08", "D09"))]
PATTERNS = {"concept": "概念语法", "boundary": "边界遗漏", "logic": "逻辑关系", "order": "执行顺序",
            "state": "状态更新", "type": "类型精度", "io": "读写约定", "strategy": "题意策略"}
# Curated mappings of already-labelled error types, not classification of a student's message.
ERROR_PATTERNS = {
    "boundary": {"BR01", "LP01", "AR01", "AR02", "AR04", "ST02", "ST05"},
    "logic": {"BR02", "BR03", "BR04", "BR05", "LP04"},
    "order": {"BR07", "BR08", "LP05", "LP06", "LP09", "FN04"},
    "state": {"LP02", "LP03", "LP07", "LP08", "AR03", "AR07", "ST01"},
    "type": {"CE03", "IO02", "IO06"},
}


def category(kp: str, course: str, broad: bool = False) -> str:
    groups = (GROUPS_C if course == "C" else GROUPS_D) if broad else (DOMAINS_C if course == "C" else DOMAINS_D)
    return next((name for name, prefixes in groups if kp.startswith(prefixes)), "其他")


def knowledge_meta(kp: str, state, course: str) -> dict:
    return {"id": "k" + str(list(KNOWLEDGE_POINTS).index(kp)), "group": category(kp, course, True),
            "domain": category(kp, course), "degree": round(100 * state.score) if state and state.score is not None else None}


def pattern(error: str) -> str:
    for name, codes in ERROR_PATTERNS.items():
        if error in codes:
            return name
    if error.startswith("IO"):
        return "io"
    if error.startswith("TY"):
        return "type"
    if error.startswith("AL") or error.startswith("DA"):
        return "strategy"
    return "concept"


def mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def representative_submissions(snap):
    weeks = defaultdict(dict)
    for key, subs in snap.subs.items():
        grouped = defaultdict(list)
        for sub in subs:
            if sub.submit_time and sub.submit_time <= snap.now and sub.judgement not in ("PENDING", "UNKNOWN"):
                grouped[codequality.monday(sub.submit_time)].append(sub)
        for week, candidates in grouped.items():
            passed = [sub for sub in candidates if sub.judgement == "AC"]
            weeks[week][key] = max(passed or candidates, key=lambda sub: (sub.submit_time, sub.submission_id))
    return weeks


def _metric(row, name):
    if row is None or not row.result:
        return None
    item = (row.result or {}).get("metrics", {}).get(name, {})
    value = item.get("level")
    return value / 3 if value is not None and item.get("confidence", 0) >= .5 else None


def build(snap, knowledge: list[dict]) -> dict:
    s, course, uid = snap.s, snap.course, snap.user_id
    weeks = representative_submissions(snap)
    # Keep ORM identities alive: source and statement reads below hit this request's identity map, not one query per code.
    subkeys = [(sub.server_id, sub.submission_id) for subs in snap.subs.values() for sub in subs]
    s.info["portrait_submissions"] = snap.subs
    loaded_sources = list(s.scalars(select(m.SourceSnapshot).where(tuple_(m.SourceSnapshot.server_id,
        m.SourceSnapshot.submission_id).in_(subkeys)))) if subkeys else []
    loaded_texts = list(s.scalars(select(m.ProblemText).options(defer(m.ProblemText.file_bytes)).where(tuple_(m.ProblemText.server_id,
        m.ProblemText.problem_id).in_(list(snap.info))))) if snap.info else []
    analyses, latest, pending, weekly, rows_by_id = {}, {}, set(), [], {}
    for week in sorted(weeks):
        selected = []
        for key, sub in weeks[week].items():
            row = codequality.enqueue(s, sub, course, snap.info[key])
            selected.append((key, sub, row))
            if row is not None:
                analyses[(sub.server_id, sub.submission_id)] = row
                rows_by_id[row.id] = row
                if row.state in ("pending", "retry", "running"):
                    pending.add(row.id)
            latest[key] = (sub, row)
        weekly.append({"start": week.date().isoformat(), "count": len(selected),
                       "analysed": sum(row is not None and row.state == "ready" for _, _, row in selected),
                       "values": {name: mean(_metric(row, name) for _, _, row in selected) for name in codequality.STYLE}})

    def evidence(key, sub, row, metric=None):
        submissions = [item for item in snap.subs[key] if item.submit_time and item.submit_time <= sub.submit_time
                       and item.judgement not in ("PENDING", "UNKNOWN")]
        entry = {"title": snap.info[key].title, "source": "OJ 提交", "time": sub.submit_time.isoformat() if sub.submit_time else None,
                 "problem": snap.problem_view(key), "text": f"已提交 {len(submissions)} 次，"
                    + ("这次通过了评测。" if sub.judgement == "AC" else "这次还未通过评测。")}
        if row and metric and metric in (row.result or {}).get("metrics", {}):
            result = row.result["metrics"].get(metric, {})
            entry.update(text=result.get("reason", ""), lines=(source_text(s, sub.server_id, sub.submission_id) or "").splitlines(),
                         mark=result.get("lines", []), level=result.get("level"), next=result.get("next", ""),
                         timeComplexity=row.result.get("time"), spaceComplexity=row.result.get("space"))
        return entry

    current_start = codequality.monday(snap.now).date().isoformat()
    current = next((w for w in weekly if w["start"] == current_start), None)
    latest_week = weekly[-1] if weekly else None
    shown = current or latest_week
    style = []
    for name, label in codequality.STYLE.items():
        values = [w["values"][name] for w in weekly if w["values"][name] is not None]
        supporting = [(key, sub, row) for week, reps in weeks.items() for key, sub in reps.items()
                      if shown and week.date().isoformat() == shown["start"]
                      and (row := analyses.get((sub.server_id, sub.submission_id))) and _metric(row, name) is not None]
        explanations = supporting or [(key, sub, row) for week, reps in weeks.items() for key, sub in reps.items()
                      if shown and week.date().isoformat() == shown["start"]
                      and (row := analyses.get((sub.server_id, sub.submission_id)))
                      and name in (row.result or {}).get("metrics", {})]
        style.append({"id": name, "name": label, "value": shown["values"][name] if shown else None,
                      "low": min(values) if values else None, "high": max(values) if values else None,
                      "count": len(supporting), "state": "查看说明" if explanations and not supporting else None,
                      "records": [evidence(key, sub, row, name) for key, sub, row in explanations]})

    pairs = diagnosis._attempts(s, [uid], course)
    pairs = [(a, item) for a, item in pairs if a.state == "done" and a.finished_at and a.finished_at <= snap.now]
    dims = diagnosis.count(pairs)
    judges = [bool(a.judge_correct) for a, _ in pairs if a.judged_has_bug is not None and a.judge_correct is not None]
    edits = [value.edit for value in dims.values()]
    assessments = list(s.scalars(select(m.AgentAssessment).where(m.AgentAssessment.user_id == uid,
        m.AgentAssessment.course == course, m.AgentAssessment.level.is_not(None), m.AgentAssessment.confidence >= .5)))
    assessments = [a for a in assessments if (a.server_id is None or (a.server_id, a.problem_id) in snap.info)
                   and a.created_at <= snap.now]
    code_assessments = [a for a in assessments if a.basis == "code"]
    understanding = code_assessments or assessments
    tried = [(key, subs) for key, subs in snap.subs.items() if subs]
    failed = [(key, subs) for key, subs in tried if any(x.judgement != "AC" for x in subs)]

    def ratio(n, d):
        return n / d if d else None

    product_understanding = mean(_metric(row, "understand") for sub, row in latest.values())
    review_records = [{"title": "复习中的首次判断", "source": "复习提升", "time": a.created_at.isoformat(),
                       "text": "首次判断为“" + ("有错误" if a.judged_has_bug else "满足要求") + "”，"
                               + ("判断正确。" if a.judge_correct else "这次判断有误。")}
                      for a, item in pairs if a.judged_has_bug is not None and a.judge_correct is not None]
    abilities = [
        {"id": "understand", "name": "程序理解", "value": mean(a.level / 3 for a in understanding) if understanding else
                   product_understanding if product_understanding is not None else mean(judges),
         "count": len(understanding) if understanding else sum(_metric(row, "understand") is not None for sub, row in latest.values()) or len(judges),
         "facts": [{"label": "复习首次判断正确", "n": sum(judges), "d": len(judges)}] if judges else [],
         "records": [{"title": "代码理解" if a.basis == "code" else "对话理解", "source": "OJ 改错评估" if a.basis == "code" else "对话",
                      "text": "；".join(a.evidence or []), "time": a.created_at.isoformat()} for a in understanding] if understanding else
                    ([evidence(key, sub, row, "understand") for key, (sub, row) in latest.items() if _metric(row, "understand") is not None]
                     or review_records)},
        {"id": "design", "name": "方案设计", "value": mean(_metric(row, "design") for sub, row in latest.values()),
         "count": sum(_metric(row, "design") is not None for sub, row in latest.values()), "facts": [],
         "records": [evidence(key, sub, row, "design") for key, (sub, row) in latest.items() if _metric(row, "design") is not None]},
        {"id": "implement", "name": "编码实现", "value": ratio(sum(snap.passed(key) for key, _ in tried), len(tried)),
         "count": len(tried), "facts": [{"label": "做过的题中已通过", "n": sum(snap.passed(key) for key, _ in tried), "d": len(tried)},
                                        {"label": "首次提交通过", "n": sum(snap.first_try(key) for key, _ in tried), "d": len(tried)}],
         "records": [evidence(key, sub, row) for key, (sub, row) in latest.items()]},
        {"id": "test", "name": "测试验证", "value": mean(_metric(row, "test") for sub, row in latest.values())
                   if any(_metric(row, "test") is not None for sub, row in latest.values()) else mean(judges),
         "count": sum(_metric(row, "test") is not None for sub, row in latest.values()) or len(judges),
         "facts": [{"label": "复习首次判断正确", "n": sum(judges), "d": len(judges)}] if judges else [],
         "records": [evidence(key, sub, row, "test") for key, (sub, row) in latest.items() if _metric(row, "test") is not None]+review_records},
        {"id": "debug", "name": "调试修复", "value": ratio(sum(snap.passed(key) for key, _ in failed), len(failed)),
         "count": len(failed), "facts": [{"label": "出错后后来改对", "n": sum(snap.passed(key) for key, _ in failed), "d": len(failed)},
            {"label": "复习独立首稿通过", "n": sum(r.n for r in edits), "d": sum(r.d for r in edits)}],
         "records": [evidence(key, *latest[key]) for key, _ in failed if key in latest]},
        {"id": "efficiency", "name": "效率分析", "value": mean(_metric(row, "efficiency") for sub, row in latest.values()),
         "count": sum(_metric(row, "efficiency") is not None for sub, row in latest.values()), "facts": [],
         "records": [evidence(key, sub, row, "efficiency") for key, (sub, row) in latest.items() if _metric(row, "efficiency") is not None]},
    ]
    for ability in abilities:
        value = ability["value"]
        ability["state"] = "暂无记录" if value is None else "表现较稳" if value >= .75 else "继续巩固" if value >= .5 else "优先练习"
    analysis = {"total": len(rows_by_id), "ready": sum(row.state == "ready" for row in rows_by_id.values()),
                "pending": len(pending), "failed": sum(row.state == "failed" for row in rows_by_id.values())}
    return {"version": VERSION, "abilities": abilities,
            "analysis": analysis,
            "style": {"dimensions": style, "weeks": weekly, "currentWeek": current_start,
                      "shownWeek": shown["start"] if shown else None, "isCurrent": current is not None,
                      "count": shown["count"] if shown else 0, "pending": len(pending)},
            "misconceptions": misconception_matrix(snap, pairs, assessments), "updatedAt": snap.now.isoformat()}


def misconception_matrix(snap, pairs, assessments):
    records, seen = [], set()
    observed = {category(kp_of_skill(skill), snap.course) for key, skills in snap.skills.items() if snap.subs.get(key) for skill in skills}
    domains = [name for name, _ in (DOMAINS_C if snap.course == "C" else DOMAINS_D)]
    def add(ident, skill, error, at, source, text, key=None):
        if not skill or course_of(skill) != snap.course or error == "OT99" or at is None or at > snap.now:
            return
        domain = category(kp_of_skill(skill), snap.course)
        if domain not in domains:
            return
        records.append({"id": ident, "domain": domain, "pattern": pattern(error), "time": at.isoformat(),
                        "source": source, "text": text, "problem": snap.problem_view(key) if key in snap.info else None})
        observed.add(domain)
    subs = { (sub.server_id, sub.submission_id): (key, sub) for key, values in snap.subs.items() for sub in values}
    if subs:
        labels = snap.s.scalars(select(m.SubmissionLabel).join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id) &
                    (m.Submission.submission_id == m.SubmissionLabel.submission_id)).where(
                        m.Submission.user_id == snap.user_id, m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.confidence >= .5))
        for lab in labels:
            pair = subs.get((lab.server_id, lab.submission_id))
            if pair is None or pair[1].judgement == "AC":
                continue
            key, sub = pair
            event = (sub.server_id, sub.submission_id, lab.error_code, category(kp_of_skill(lab.skill_code), snap.course))
            if event in seen:
                continue
            seen.add(event)
            add("s"+str(lab.id), lab.skill_code, lab.error_code, sub.submit_time, "OJ 提交标注", error_name(lab.error_code, student_facing=True), key)
    for a, item in pairs:
        if item.has_bug and a.judged_has_bug is False:
            add("q"+str(a.id), a.skill_code, item.error_code or "", a.created_at, "复习首次判断", "有错误的代码，首次判断为满足要求")
    for a in assessments:
        if not a.misconception:
            continue
        sub = snap.s.get(m.Submission, (a.server_id, a.submission_id)) if a.server_id and a.submission_id else None
        rnd = snap.s.get(m.ChatRound, a.round_id) if a.round_id else None
        at = sub.submit_time if sub else rnd.last_message_at if rnd else None
        add("a"+str(a.id), a.skill_code, rnd.error_code if rnd else "", at, "理解评估中的误解", a.misconception,
            (a.server_id, a.problem_id))
    return {"domains": domains, "patterns": [{"id": key, "name": label} for key, label in PATTERNS.items()],
            "observed": sorted(observed), "weekStart": codequality.monday(snap.now).isoformat(), "records": records}
