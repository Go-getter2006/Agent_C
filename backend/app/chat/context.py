"""第 2~5 步：绑定、取证、诊断、状态。全部由程序完成。

Ctx 里的 evidence（证据 JSON）含测试点原始数据，只在后台用：不发给学生，也不发给写回复的大模型。
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog, config, db, llm, mastery
from .. import models as m
from ..labeling import labeler
from ..taxonomy import LEVEL_NONE, error_name, skill_name
from ..xdoj.assemble import assemble_submission, source_text
from . import hints
from .text import declared_vars, line_assignment


@dataclass
class Ctx:
    user_id: str
    user_name: str = ""
    thread: tuple[int, int] | None = None        # 消息所在的线程：某道题，或通用线程（None）
    course: str = "C"                            # 这次登录的课程：只在这门课的题里绑定
    profile_note: str = ""                       # 多维画像的一句话，给大模型看（profile.tutor_note）
    # 下面这些只给大模型看（学生看不到）：题目、全部测试点、这道题以前的提交、学情、在别的题上纠结过什么
    statement: str = ""
    all_tests: list[dict] = field(default_factory=list)   # [{"standardInput", "standardOutput"}]，也用于回复的泄露检查
    history: list[str] = field(default_factory=list)      # 这道题每次提交：第几次、结果、错误、改了几行
    learning: list[str] = field(default_factory=list)     # 学情：薄弱的知识点和提示
    struggles: list[str] = field(default_factory=list)    # 在别的题上卡过、纠结过的地方
    problem: catalog.ProblemInfo | None = None   # 绑定到的题
    bind_by: str = ""
    candidates: list[catalog.ProblemInfo] = field(default_factory=list)
    plabel: m.ProblemLabel | None = None
    submission: m.Submission | None = None
    attempt_no: int = 0
    source: str | None = None                    # 最近一次提交的源码，或消息里贴的代码
    source_kind: str = ""                        # submission / pasted
    evidence: dict | None = None
    labels: list = field(default_factory=list)
    root_error: str = ""
    root_skill: str = ""
    diag_confidence: float = 0.0
    diag_line: int | None = None
    situation: str = ""                          # 能对学生说的“哪类情况”
    level: str = LEVEL_NONE
    prior_stuck: list = field(default_factory=list)   # 以前（别的回合）在这个技能上的卡点
    round_stuck: m.StuckPoint | None = None           # 本回合已经记下的卡点
    same_error_resubmit: bool = False
    no_progress: bool = False
    reference: str | None = None

    @property
    def lines(self) -> list[str]:
        return (self.source or "").replace("\r\n", "\n").split("\n") if self.source else []

    @property
    def has_failure(self) -> bool:
        return self.submission is not None and self.submission.judgement not in ("AC", "PENDING")

    def names(self) -> dict:
        """模板里用的变量名：出问题那一行的变量和初值、数组名、个数、循环变量。"""
        src = self.source or ""
        out = {"var": "", "init": "", "arr": "", "n": "", "i": "", "type": "int", "direction": ""}
        if self.diag_line and 1 <= self.diag_line <= len(self.lines):
            line = self.lines[self.diag_line - 1]
            got = line_assignment(line)
            if got:
                out["var"], out["init"] = got
            mt = re.match(r"\s*(long long|long|int|double|float)\b", line)
            if mt:
                out["type"] = mt.group(1)
        decl = declared_vars(src)
        arrays = [d["name"] for d in decl if d["array"]]
        out["arr"] = arrays[0] if arrays else ""
        mn = re.search(r'scanf\s*\(\s*"\s*%l{0,2}d\s*"\s*,\s*&\s*([A-Za-z_]\w*)\s*\)', src)
        out["n"] = mn.group(1) if mn else ""
        mi = re.search(r"for\s*\(\s*(?:int\s+)?([A-Za-z_]\w*)\s*=", src)
        out["i"] = mi.group(1) if mi else ""
        var = out["var"]
        if var:
            if re.search(r">\s*" + re.escape(var) + r"\b", src) or re.search(r"max|big|high|large", var, re.I):
                out["direction"] = "max"
            elif re.search(r"<\s*" + re.escape(var) + r"\b", src) or re.search(r"min|small|low", var, re.I):
                out["direction"] = "min"
        return out

    def hint(self) -> hints.Hint:
        names = self.names()
        if self.root_skill == "C06.03.02" and names["var"] and names["direction"] and self.root_error in (
                "TY09", "AL02", "AL03", ""):
            op = ">" if names["direction"] == "max" else "<"
            cmp_line = next((i for i, line in enumerate(self.lines, start=1)
                             if i > (self.diag_line or 0) and re.search(re.escape(op) + r"\s*" + re.escape(names["var"]) + r"\b", line)), None)
            return hints.extreme_hint(names["var"], names["init"], names["direction"], names["arr"], cmp_line)
        return hints.hint_for(self.root_error, self.diag_line is not None)

    def fill(self, template: str) -> str:
        names = self.names()
        sit = self.situation
        gap = " " if sit and sit[-1].isascii() and sit[-1].isalnum() else ""  # “n 等于 1 的时候”
        if sit.startswith("全是"):
            or_all = f"输入的数{sit}"
        elif sit:
            or_all = f"遇到{sit}{gap}的情况"
        else:
            or_all = "输入的数都比这个初值小（或者都比它大）"
        values = {
            "var": names["var"] or "这个变量", "init": names["init"] or "这个值", "line": self.diag_line or "",
            "situation": sit, "situation_when": f"{sit}{gap}的时候，" if sit else "遇到特殊的输入时，",
            "situation_or_all": or_all,
            "situation_sentence": f"{sit}{gap}的时候会出错。" if sit else "",
            "error_name": error_name(self.root_error, student_facing=True) or "这类错误",
            "skill_name": skill_name(self.root_skill),
        }
        out = template
        for key, value in values.items():
            out = out.replace("{" + key + "}", str(value))
        return out.replace("``", "").strip()


def _visible_infos(s: Session, user_id: str, course: str = "C") -> dict[tuple[int, int], catalog.ProblemInfo]:
    out = {}
    for (server_id, problem_id) in catalog.visible_problems(s, user_id, course):
        info = catalog.problem_info(s, server_id, problem_id)
        if info is not None:
            out[(server_id, problem_id)] = info
    return out


def bind(s: Session, user_id: str, thread: tuple[int, int] | None, submission_key: str | None,
         text: str, pasted: str, now: dt.datetime,
         course: str = "C") -> tuple[tuple[int, int] | None, m.Submission | None, str, list]:
    """第 2 步：绑定到哪道题、哪次提交。只在这个学生可见的、这门课的题里绑定。"""
    visible = _visible_infos(s, user_id, course)
    if thread is not None:
        if thread not in visible:
            return None, None, "", []
        subs = catalog.user_submissions(s, user_id, *thread, until=now)
        picked = catalog.parse_key(submission_key or "", "s")
        if picked is not None:
            for sub in subs:
                if (sub.server_id, sub.submission_id) == picked:
                    return thread, sub, "ask_why", []
        return thread, (subs[-1] if subs else None), "thread", []

    # 通用线程：消息里提到的题目名 → 贴的代码和哪次提交最像 → 最近 30 分钟内提交过的题
    for key, info in sorted(visible.items(), key=lambda kv: -len(kv[1].title)):
        if info.title and info.title in text:
            subs = catalog.user_submissions(s, user_id, *key, until=now)
            return key, (subs[-1] if subs else None), "name", []
    recent = s.scalars(select(m.Submission).where(m.Submission.user_id == user_id,
                                                  m.Submission.submit_time <= now)
                       .order_by(m.Submission.submission_id.desc()).limit(20)).all()
    recent = [sub for sub in recent if (sub.server_id, sub.problem_id) in visible]
    if pasted.strip():
        best, best_ratio = None, 0.0
        for sub in recent:
            src = source_text(s, sub.server_id, sub.submission_id) or ""
            ratio = difflib.SequenceMatcher(None, pasted, src, autojunk=False).quick_ratio() if src else 0.0
            if ratio > best_ratio:
                best, best_ratio = sub, ratio
        if best is not None and best_ratio >= 0.6:
            return (best.server_id, best.problem_id), best, "code", []
    window = now - dt.timedelta(minutes=config.CHAT["bind_recent_minutes"])
    fresh = [sub for sub in recent if sub.submit_time and sub.submit_time >= window and sub.judgement != "AC"]
    keys = list(dict.fromkeys((sub.server_id, sub.problem_id) for sub in fresh))
    if len(keys) == 1:
        return keys[0], fresh[0], "recent", []
    return None, None, "", [visible[k] for k in keys[:3]]


def build(s: Session, user_id: str, thread: tuple[int, int] | None, submission_key: str | None,
          text: str, pasted: str, rnd: m.ChatRound | None, now: dt.datetime | None = None, course: str = "C") -> Ctx:
    now = now or db.now()
    student = s.get(m.Student, user_id)
    ctx = Ctx(user_id=user_id, user_name=student.name if student else "", thread=thread, course=course)
    key, sub, ctx.bind_by, ctx.candidates = bind(s, user_id, thread, submission_key, text, pasted, now, course)
    if key is not None:
        ctx.problem = catalog.problem_info(s, *key)
        ctx.plabel = labeler.current_problem_label(s, *key)
        ref = s.scalar(select(m.ReferenceSolution).where(
            m.ReferenceSolution.server_id == key[0], m.ReferenceSolution.problem_id == key[1],
            m.ReferenceSolution.verified.is_(True)))
        ctx.reference = ref.source_code if ref else None
    ctx.submission = sub

    # 第 3 步：取证
    if sub is not None:
        subs = catalog.user_submissions(s, user_id, sub.server_id, sub.problem_id, until=now)
        ctx.attempt_no = [x.submission_id for x in subs].index(sub.submission_id) + 1
        if sub.detail_status == "done":
            ctx.evidence = assemble_submission(s, sub.server_id, sub.submission_id, max_output_chars=2000)
            ctx.source = ctx.evidence.get("sourceCode")
            ctx.source_kind = "submission"
            ctx.labels = labeler.label_submission(s, sub.server_id, sub.submission_id)  # 还没标的，立即标
        fails = [x for x in subs if x.judgement not in ("AC", "PENDING")]
        last5 = subs[-config.CHAT["no_progress_submissions"]:]
        if len(last5) >= config.CHAT["no_progress_submissions"] and all(x.judgement != "AC" for x in last5):
            def passed(x):
                cells = catalog.grid(s, x)
                return sum(cells) if cells else 0
            ctx.no_progress = passed(last5[-1]) <= passed(last5[0])
        del fails
    if pasted.strip() and (ctx.source is None or ctx.bind_by not in ("ask_why",)):
        if ctx.source is None:
            ctx.source, ctx.source_kind = pasted, "pasted"

    # 第 4 步：诊断。程序优先：读已有的错误标注
    tlabels = labeler.testcase_labels(s, ctx.plabel)
    failed_ids = [t["testcaseId"] for t in (ctx.evidence or {}).get("testCases", []) if t.get("passed") is False]
    primary = next((lab for lab in ctx.labels if lab.role == "primary"), None)
    if ctx.has_failure and primary is not None:
        ctx.root_error, ctx.root_skill = primary.error_code, primary.skill_code
        ctx.diag_confidence, ctx.diag_line = primary.confidence, primary.line
    elif ctx.has_failure:
        skills = [tlabels[t].skill_code for t in failed_ids if t in tlabels and not tlabels[t].is_ordinary]
        ctx.root_skill = skills[0] if skills else (ctx.plabel.primary_skill if ctx.plabel else "")
        ctx.diag_confidence = 0.3
    else:
        ctx.root_skill = ctx.plabel.primary_skill if ctx.plabel else ""
        ctx.diag_confidence = 1.0
    special = [tlabels[t] for t in failed_ids if t in tlabels and not tlabels[t].is_ordinary]
    match = [t for t in special if t.skill_code == ctx.root_skill] or special
    ctx.situation = match[0].situation if match else ""

    # 第 5 步：状态
    if ctx.root_skill:
        ctx.level = mastery.skill_state(s, user_id, ctx.root_skill).level
        stuck = s.scalars(select(m.StuckPoint).where(
            m.StuckPoint.user_id == user_id, m.StuckPoint.skill_code == ctx.root_skill
        ).order_by(m.StuckPoint.id)).all()
        ctx.prior_stuck = [p for p in stuck if rnd is None or p.round_id != rnd.id]
        ctx.round_stuck = next((p for p in reversed(stuck) if rnd is not None and p.round_id == rnd.id), None)
    if ctx.root_skill and llm.available():
        from .. import profile

        ctx.profile_note = profile.tutor_note(s, user_id, ctx.root_skill)
    if llm.available():
        tutor_context(s, ctx, now)
    if rnd is not None and sub is not None and rnd.submission_id and rnd.submission_id != sub.submission_id \
            and rnd.error_code and rnd.error_code == ctx.root_error and sub.submission_id > rnd.submission_id:
        ctx.same_error_resubmit = True  # 带着同一个错误又交了一次
    return ctx


def evidence_card(ctx: Ctx) -> dict:
    """存进 chat_steps 的证据卡（内部用）。"""
    cells = []
    for t in (ctx.evidence or {}).get("testCases", []):
        cells.append({"testcaseId": t["testcaseId"], "passed": t.get("passed"), "result": t.get("resultCode")})
    return {
        "problem": {"title": ctx.problem.title, "brief": ctx.problem.brief} if ctx.problem else None,
        "submissionId": ctx.submission.submission_id if ctx.submission else None,
        "attemptNo": ctx.attempt_no or None,
        "judgement": ctx.submission.judgement if ctx.submission else None,
        "testcases": cells,
        "labels": [{"error": lab.error_code, "skill": lab.skill_code, "role": lab.role, "confidence": lab.confidence,
                    "line": lab.line, "source": lab.source} for lab in ctx.labels],
        "situation": ctx.situation,
        "variables": ctx.names(),
        "level": ctx.level,
        "priorStuck": [p.id for p in ctx.prior_stuck],
        "sameErrorResubmit": ctx.same_error_resubmit,
        "noProgress": ctx.no_progress,
        "sourceKind": ctx.source_kind,
    }


# ------------------------------------------------------------ 给大模型的完整情况（学生看不到）
def tutor_context(s: Session, ctx: Ctx, now: dt.datetime) -> None:
    """题目、全部测试点、这道题以前的提交、学情、在别的题上纠结过什么。只放进给大模型的提示里。"""
    from .. import changes, profile
    from ..taxonomy import KNOWLEDGE_POINTS, kp_of_skill
    from ..xdoj.util import decode_bytes

    if ctx.problem is not None:
        sid, pid = ctx.problem.server_id, ctx.problem.problem_id
        text = s.get(m.ProblemText, (sid, pid))
        ctx.statement = (text.extracted_text or "")[:1500] if text is not None else ""
        for tc in s.scalars(select(m.Testcase).where(m.Testcase.server_id == sid, m.Testcase.problem_id == pid)
                            .order_by(m.Testcase.seq, m.Testcase.testcase_id).limit(8)).all():
            inp = decode_bytes(tc.input_bytes, tc.input_encoding)[0] if tc.input_bytes is not None else ""
            out = decode_bytes(tc.output_bytes, tc.output_encoding)[0] if tc.output_bytes is not None else ""
            ctx.all_tests.append({"standardInput": inp or "", "standardOutput": out or ""})
        subs = catalog.user_submissions(s, ctx.user_id, sid, pid, until=now)
        prev_lines = None
        for i, sub in enumerate(subs[-8:], start=max(1, len(subs) - 7)):
            lines = changes.code_lines(s, sub)
            errs = "、".join(error_name(c) for c in changes.error_codes(s, sub)) or "没标出错误"
            moved = changes.compare(prev_lines, lines) if prev_lines else None
            ctx.history.append(f"第 {i} 次：{sub.judgement_text or sub.judgement}"
                               + (f"，问题：{errs}" if sub.judgement != "AC" else "")
                               + (f"，比上一次改了 {moved.changed} 行" if moved and moved.available else ""))
            prev_lines = lines
    course = ctx.course
    prof = profile.kp_profile(s, ctx.user_id, course)
    weak = sorted(((kp, d) for kp, d in prof.items() if d.level == "待加强" or d.flags), key=lambda kv: (kv[1].perf or 0.5))
    for kp, d in weak[:4]:
        tips = "；".join(profile.FLAG_TEXT[f] for f in d.flags[:2])
        ctx.learning.append(f"{KNOWLEDGE_POINTS.get(kp, kp)}：{d.level}" + (f"（{tips}）" if tips else ""))
    # 在别的题上卡过的地方、智能体看出来的错误想法
    current = (ctx.problem.server_id, ctx.problem.problem_id) if ctx.problem else None
    for sp in s.scalars(select(m.StuckPoint).where(m.StuckPoint.user_id == ctx.user_id)
                        .order_by(m.StuckPoint.id.desc()).limit(12)).all():
        if sp.problem_id is None or (sp.server_id, sp.problem_id) == current or catalog.server_course(s, sp.server_id) != course:
            continue
        info = catalog.problem_info(s, sp.server_id, sp.problem_id)
        how = {"recalled": "后来自己想起来了", "blank_filled": "挖空补上的", "example_given": "给了示例代码",
               "exercise": "做了小练习"}.get(sp.resolution, "")
        ctx.struggles.append(f"在「{info.title if info else '另一道题'}」卡在{skill_name(sp.skill_code)}"
                             + (f"（{error_name(sp.error_code)}）" if sp.error_code else "") + (f"，{how}" if how else ""))
        if len(ctx.struggles) >= 4:
            break
    for a in s.scalars(select(m.AgentAssessment).where(
            m.AgentAssessment.user_id == ctx.user_id, m.AgentAssessment.course == course,
            m.AgentAssessment.misconception != "").order_by(m.AgentAssessment.id.desc()).limit(3)).all():
        ctx.struggles.append(f"以前看出来的错误想法（{KNOWLEDGE_POINTS.get(kp_of_skill(a.skill_code), '')}）：{a.misconception}")
