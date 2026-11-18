"""智能体评估“理解”：一个求助回合结束后，看学生在这个回合里自己说的话，判断他对这个技能理解到哪一步。

为什么要有它：做题表现（评测结果、提交次数、要了多少帮助）只能说明“做没做出来”，说明不了“懂没懂”。
会照着提示改对、但说不出为什么的学生，下次换个样子很可能又错。对话里学生回答智能体的问题、解释自己的想法，
正好是“懂没懂”的证据，但只有读懂这些话才能判断，程序判断不了，所以交给大模型。

怎么防止大模型乱评（参考“以证据为中心的评价设计”：每个结论都要指着证据）：
1. 量规固定，四档：0 不理解、1 能照做、2 能解释、3 能迁移。
2. 评到 2、3 档必须附上学生的原话，原话要一字不差地出现在他这个回合的消息里，否则作废重评。
3. 和程序能确定的事实冲突时下调：回答智能体的问题一次也没答对过、却评“能解释”，降到“能照做”；
   看了示例代码才改对、却评“能迁移”，降到“能解释”。下调的原因记下来给老师看。
4. 同时用规则算一个等级（只看答没答对、通没通过、要了多少帮助），记在 rule_level，用来算两者的一致率。
   没有大模型、或者大模型两次都不合格时，就用规则的等级。
5. 发给大模型的内容不带学号、姓名；学生端看不到评估结果，只有教师端能看到。

没有问过问题的学生也要评（他可能就是会，不需要问）：一道题没过、后来自己改对了，智能体看他是怎么改的——
改动是不是正对着出错的原因，还是大段重写、反复试出来的（basis = code）。证据要求一样：评到 2、3 档要摘抄
改动里真实存在的代码行；反复试出来的（急着重交、同一错误反复出现）下调。规则也给一个等级对照。

评估结果记成掌握程度的证据（mastery.py 的第 5 条）：分量不大，乘上置信度，只改估计值、不算练过。
只有大模型读了学生原话得出的评估才记证据；规则得出的等级只用来展示和对照，不另记证据——
它用的事实（要了多少帮助、通没通过）已经在做题的证据里了，再记一次就重复计算了。
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

import difflib

from . import catalog, config, db, llm, mastery, process
from . import models as m
from .xdoj.assemble import source_text
from .taxonomy import HELP_ORDER, KNOWLEDGE_POINTS, course_of, course_prompt, error_name, kp_of_skill, skill_name

log = logging.getLogger("assess")

LEVEL_NAMES = {0: "不理解", 1: "能照做", 2: "能解释", 3: "能迁移"}
LEVEL_HINTS = {
    0: "说不出问题在哪，或者说法本身是错的",
    1: "按提示改对了，但说不出为什么",
    2: "能用自己的话说出原因或原理",
    3: "能说出别的情况也会怎样，或者自己发现了同类问题",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _text(msg: m.ChatMessage) -> str:
    return "\n".join(b.get("text", "") for b in (msg.content or {}).get("blocks", []) if b.get("t") == "p")


class Material:
    """一个回合里评估要用的东西。"""

    def __init__(self, s: Session, rnd: m.ChatRound):
        self.rnd = rnd
        self.messages = list(s.scalars(select(m.ChatMessage).where(m.ChatMessage.round_id == rnd.id)
                                       .order_by(m.ChatMessage.id)).all())
        self.steps = list(s.scalars(select(m.ChatStep).where(m.ChatStep.round_id == rnd.id)
                                    .order_by(m.ChatStep.id)).all())
        self.student_texts = [_text(x) for x in self.messages if x.role == "student" and x.kind == "text"]
        self.correct = sum(1 for st in self.steps if st.answer_correct is True)
        self.wrong = sum(1 for st in self.steps if st.answer_correct is False)
        self.explained = any(st.act == "self_explain" for st in self.steps)
        self.stuck = any(st.intent == "STUCK" for st in self.steps)
        self.help = HELP_ORDER.get(rnd.max_help_level, -1)
        self.passed = rnd.end_reason == "通过" or self._passed_after(s)
        self.recalled = s.scalar(select(m.StuckPoint.id).where(
            m.StuckPoint.round_id == rnd.id, m.StuckPoint.resolution == "recalled")) is not None

    def _passed_after(self, s: Session) -> bool:
        if self.rnd.problem_id is None:
            return False
        return s.scalar(select(m.Submission.submission_id).where(
            m.Submission.server_id == self.rnd.server_id, m.Submission.problem_id == self.rnd.problem_id,
            m.Submission.user_id == self.rnd.user_id, m.Submission.judgement == "AC",
            m.Submission.submit_time >= self.rnd.started_at)) is not None

    @property
    def said_something(self) -> bool:
        """他自己说过有内容的话（不只是点按钮、说“懂了”）。"""
        return any(st.has_attempt or st.intent in ("REPLY", "CONCEPT", "DEBUG") for st in self.steps
                   if st.analysis_source != "button")


def rule_level(mat: Material) -> int | None:
    """只用程序能确定的事实给的等级。不会给 3（能不能迁移，规则看不出来）。"""
    if not mat.steps:
        return None
    if mat.correct >= 1 or mat.recalled:
        return 2
    if mat.wrong >= 2 or (mat.stuck and not mat.passed and mat.rnd.end_reason == "超时"):
        return 0
    if mat.passed:
        return 1
    return None


_SYSTEM = """你是 C 语言课程的助教，任务是评估一名学生在一次答疑对话里，对某个技能理解到哪一步。只看学生自己说的话。
量规（只能选一个）：
0 不理解：说不出问题在哪，或者说法本身是错的
1 能照做：按提示改对了，但说不出为什么
2 能解释：能用自己的话说出原因或原理
3 能迁移：能说出别的情况也会怎样，或者自己发现了同类问题
学生说的话太少、看不出来时 level 填 null。
输出 JSON：{"level":2,"evidence":["学生的原话，一字不差地摘抄，每条不超过 40 个字"],"misconception":"看出来的错误想法，没有就空字符串，不超过 40 字","confidence":0.7}
要求：level 是 2 或 3 时 evidence 至少一条；evidence 只能摘抄“学生说的话”里的原文，不能改写，不能摘助手的话；
confidence 是你对这个判断有多大把握（0 到 1）；不要输出学号、姓名。"""


def _llm_level(mat: Material, skill: str, root_error: str) -> tuple[int | None, list[str], str, float]:
    joined = _norm("".join(mat.student_texts))
    lines = []
    for msg in mat.messages:
        if msg.kind != "text":
            continue
        who = "学生" if msg.role == "student" else "助手"
        text = llm.strip_identity(_text(msg))[:400]
        if text.strip():
            lines.append(f"{who}：{text}")
    facts = [f"这次评估的技能：{skill_name(skill)}（属于“{KNOWLEDGE_POINTS.get(kp_of_skill(skill), '')}”）",
             f"程序诊断的错误：{error_name(root_error)}" if root_error else "",
             f"这道题后来{'通过了' if mat.passed else '还没有通过'}",
             f"助手给到的最多帮助：{ {-1: '没有', 0: '反问', 1: '指出位置', 2: '讲原理', 3: '给了示例代码'}.get(mat.help, '没有') }"]
    user = "【已知情况】\n" + "\n".join(f for f in facts if f) + "\n\n【对话】\n" + "\n".join(lines[-16:])

    def validate(d: dict) -> tuple[int | None, list[str], str, float]:
        level = d.get("level")
        if level is not None and level not in (0, 1, 2, 3):
            raise ValueError("level 只能是 0、1、2、3 或 null")
        evidence = d.get("evidence") or []
        if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
            raise ValueError("evidence 要是字符串列表")
        evidence = [x.strip()[:60] for x in evidence if x.strip()][:3]
        for quote in evidence:
            if _norm(quote) not in joined:
                raise ValueError(f"“{quote[:20]}”不是学生的原话")
        if level in (2, 3) and not evidence:
            raise ValueError("评到 2 或 3 要附上学生的原话")
        try:
            conf = float(d.get("confidence", 0))
        except (TypeError, ValueError):
            raise ValueError("confidence 要是 0 到 1 的数")
        if not 0 <= conf <= 1:
            raise ValueError("confidence 要在 0 到 1 之间")
        miscon = llm.strip_identity(str(d.get("misconception") or "").strip())[:60]
        return level, evidence, miscon, conf

    return llm.call_json("assess_understanding", course_prompt(_SYSTEM, course_of(skill)), user, validate,
                         retries=1, max_tokens=400)


def guard(level: int | None, mat: Material) -> tuple[int | None, str]:
    """和程序能确定的事实冲突时下调。"""
    if level is None:
        return None, ""
    if level >= 2 and mat.correct == 0 and mat.wrong >= 2 and not mat.recalled:
        return 1, "回答助手的问题没有答对过"
    if level == 3 and mat.help >= HELP_ORDER["H3"]:
        return 2, "看了示例代码才改对"
    return level, ""


def assess_round(s: Session, rnd: m.ChatRound, use_llm: bool = True) -> m.AgentAssessment | None:
    """评一个已经结束的回合。评过的不重评；没有技能的回合不评。"""
    if rnd.status != "closed" or not rnd.skill_code:
        return None
    done = s.scalar(select(m.AgentAssessment).where(m.AgentAssessment.round_id == rnd.id,
                                                    m.AgentAssessment.skill_code == rnd.skill_code))
    if done is not None:
        return done
    mat = Material(s, rnd)
    rule = rule_level(mat)
    row = m.AgentAssessment(user_id=rnd.user_id, course=rnd.course or course_of(rnd.skill_code),
                            skill_code=rnd.skill_code, round_id=rnd.id, rule_level=rule,
                            created_at=rnd.ended_at or db.now(), prompt_version=config.PROMPT_VERSION)
    used_llm = False
    if use_llm and llm.available() and mat.student_texts and mat.said_something:
        try:
            level, evidence, miscon, conf = _llm_level(mat, rnd.skill_code, rnd.error_code)
            final, why = guard(level, mat)
            row.raw_level, row.level, row.adjusted = level, final, why
            row.evidence, row.misconception, row.confidence, row.source = evidence, miscon, conf, "llm"
            used_llm = True
        except llm.LLMUnavailable:
            log.info("理解评估：大模型两次都不合格，用规则")
    if not used_llm:
        row.level, row.raw_level, row.confidence, row.source = rule, rule, 0.6 if rule is not None else 0.0, "rule"
    s.add(row)
    s.flush()
    record_evidence(s, row)
    llm.flush_records(s)
    return row


def record_evidence(s: Session, row: m.AgentAssessment) -> bool:
    """把评估记成掌握程度的证据。只有大模型的评估记（见开头的说明）。"""
    if row.source != "llm" or row.level is None or row.confidence < config.AGENT_ASSESS["min_confidence"]:
        return False
    success, weight = config.AGENT_ASSESS["evidence"][row.level]
    key = f"assess:{row.round_id}" if row.basis == "chat" else f"assess-code:{row.server_id}:{row.submission_id}"
    return mastery.add_evidence(s, row.user_id, row.skill_code, "agent_understanding", success,
                                round(weight * row.confidence, 3), key, row.created_at, independent=False)


# ------------------------------------------------------------ 看代码评（没问过问题也评）
_CODE_SYSTEM = """你是 C 语言课程的助教。一名学生在 OJ 上交一道题没通过，后来自己改对了，没有问过助教。
看他是怎么改的，判断他对出错的地方理解到哪一步。只根据代码的修改判断。量规（只能选一个）：
0 不理解：改来改去没改到点子上，同一个错误反复出现
1 能照做/靠试：改对了，但是大段重写，或者反复试出来的
2 能解释：改动正对着出错的原因，干净利落
3 能迁移：除了出错的地方，同类的隐患也一起改了（比如别处同样的边界也处理了）
看不出来时 level 填 null。
输出 JSON：{"level":2,"evidence":["改动里的代码行，原样摘抄"],"misconception":"从代码看出来的错误想法，没有就空字符串，不超过 40 字","confidence":0.7}
要求：level 是 2 或 3 时 evidence 至少一条；evidence 只能摘抄“改动”里以 + 或 - 开头的行（不带 + -），每条不超过 60 个字符；不要输出学号、姓名。"""


def _lines(text: str | None) -> list[str]:
    return text.replace("\r\n", "\n").rstrip("\n").split("\n") if text else []


def code_rule_level(ep: process.Episode, changed: int) -> int | None:
    """只看提交过程的等级。改得干净（交得不多、没有反复同一个错、没急着重交、改动少）2；明显靠试 1；其余看不出来。"""
    fails = len(ep.failed)
    if fails <= 2 and ep.same_repeats == 0 and ep.rapid == 0 and changed <= 6:
        return 2
    if changed > 20 or fails >= 4 or ep.rapid >= 2 or ep.same_repeats >= 2:
        return 1
    return None


def code_guard(level: int | None, ep: process.Episode, changed: int) -> tuple[int | None, str]:
    if level is None:
        return None, ""
    if level >= 2 and (ep.rapid >= 2 or ep.same_repeats >= 2):
        return 1, "反复试出来的（急着重交或同一个错误反复出现）"
    if level == 3 and changed > 20:
        return 2, "大段重写，看不出是不是举一反三"
    return level, ""


def _diff(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    """统一格式的改动（带少量上下文），和其中改了的行（不带 + -）。改动完整给出，不截断。"""
    lines = [x for x in difflib.unified_diff(before, after, lineterm="", n=2)][2:]
    changed = [x[1:] for x in lines if x[:1] in "+-" and x[1:].strip()]
    return lines, changed


def assess_code(s: Session, sub: m.Submission, use_llm: bool = True) -> m.AgentAssessment | None:
    """sub 是一道题第一次通过的那次提交，之前没过过。看他怎么改的。代码还没取到时返回 None（下次再评）。"""
    done = s.scalar(select(m.AgentAssessment).where(m.AgentAssessment.basis == "code",
                                                    m.AgentAssessment.server_id == sub.server_id,
                                                    m.AgentAssessment.submission_id == sub.submission_id))
    if done is not None:
        return done
    ep = process.episode_for(s, sub.user_id, sub.server_id, sub.problem_id)
    if ep is None or ep.state != "fixed" or ep.passed is None or ep.passed.submission_id != sub.submission_id:
        return None
    before = _lines(source_text(s, sub.server_id, ep.failed[-1].submission_id))
    after = _lines(source_text(s, sub.server_id, sub.submission_id))
    if not before or not after:
        return None
    diff, changed_lines = _diff(before, after)
    changed = len(changed_lines)
    skill = next((c for c in reversed(ep.error_skills) if c), "") or (ep.problem_skills[0] if ep.problem_skills else "")
    if not skill:
        return None
    rule = code_rule_level(ep, changed)
    row = m.AgentAssessment(user_id=sub.user_id, course=catalog.server_course(s, sub.server_id), skill_code=skill,
                            basis="code", round_id=None, server_id=sub.server_id, problem_id=sub.problem_id,
                            submission_id=sub.submission_id, rule_level=rule,
                            created_at=sub.submit_time or db.now(), prompt_version=config.PROMPT_VERSION)
    used_llm = False
    if use_llm and llm.available():
        allowed = {_norm(x) for x in changed_lines}
        info = catalog.problem_info(s, sub.server_id, sub.problem_id)
        errs = [error_name(e) for e in ep.errors if e]
        facts = [f"这道题：{info.title}（{info.brief}）" if info else "",
                 f"他交了 {len(ep.failed) + 1} 次才通过；没通过时的问题：{'、'.join(dict.fromkeys(errs)) or '没标出来'}",
                 f"同一个错误紧接着又出现 {ep.same_repeats} 次；不到一分钟就重交 {ep.rapid} 次",
                 f"这次评估的技能：{skill_name(skill)}（属于“{KNOWLEDGE_POINTS.get(kp_of_skill(skill), '')}”）"]
        user = ("【已知情况】\n" + "\n".join(f for f in facts if f) + "\n\n【改动：从最后一次没通过的代码到通过的代码】\n" +
                llm.strip_identity("\n".join(diff)))

        def validate(d: dict) -> tuple[int | None, list[str], str, float]:
            level = d.get("level")
            if level is not None and level not in (0, 1, 2, 3):
                raise ValueError("level 只能是 0、1、2、3 或 null")
            evidence = d.get("evidence") or []
            if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
                raise ValueError("evidence 要是字符串列表")
            evidence = [x.strip()[:80] for x in evidence if x.strip()][:3]
            for line in evidence:
                if _norm(line.lstrip("+-")) not in allowed:
                    raise ValueError(f"“{line[:20]}”不是改动里的行")
            if level in (2, 3) and not evidence:
                raise ValueError("评到 2 或 3 要摘抄改动里的代码行")
            try:
                conf = float(d.get("confidence", 0))
            except (TypeError, ValueError):
                raise ValueError("confidence 要是 0 到 1 的数")
            if not 0 <= conf <= 1:
                raise ValueError("confidence 要在 0 到 1 之间")
            return level, evidence, llm.strip_identity(str(d.get("misconception") or "").strip())[:60], conf

        try:
            level, evidence, miscon, conf = llm.call_json(
                "assess_code", course_prompt(_CODE_SYSTEM, course_of(skill)), user, validate, retries=1, max_tokens=400)
            final, why = code_guard(level, ep, changed)
            row.raw_level, row.level, row.adjusted = level, final, why
            row.evidence, row.misconception, row.confidence, row.source = evidence, miscon, conf, "llm"
            used_llm = True
        except llm.LLMUnavailable:
            log.info("看代码评理解：大模型两次都不合格，用规则")
    if not used_llm:
        row.level, row.raw_level, row.confidence, row.source = rule, rule, 0.6 if rule is not None else 0.0, "rule"
    s.add(row)
    s.flush()
    record_evidence(s, row)
    llm.flush_records(s)
    return row


def pending_code(s: Session, limit: int, now: dt.datetime | None = None) -> list[m.Submission]:
    """最近两周里“没过、后来自己改对了”的题，还没看代码评过的；这期间在这道题上问过智能体的不再看代码评（对话里评过）。"""
    now = now or db.now()
    since = now - dt.timedelta(days=config.AGENT_ASSESS["code_days"])
    done = select(m.AgentAssessment.submission_id).where(m.AgentAssessment.basis == "code",
                                                         m.AgentAssessment.submission_id.is_not(None))
    out = []
    for sub in s.scalars(select(m.Submission).where(
            m.Submission.judgement == "AC", m.Submission.submit_time >= since, m.Submission.detail_status == "done",
            m.Submission.submission_id.not_in(done)).order_by(m.Submission.submission_id)).all():
        earlier = s.scalars(select(m.Submission).where(
            m.Submission.server_id == sub.server_id, m.Submission.user_id == sub.user_id,
            m.Submission.problem_id == sub.problem_id, m.Submission.submission_id < sub.submission_id)).all()
        if not earlier or any(x.judgement == "AC" for x in earlier):
            continue   # 一次就过的不用看；不是第一次通过的不看
        asked = s.scalar(select(m.ChatRound.id).where(
            m.ChatRound.user_id == sub.user_id, m.ChatRound.server_id == sub.server_id,
            m.ChatRound.problem_id == sub.problem_id, m.ChatRound.help_count > 0,
            m.ChatRound.started_at <= sub.submit_time))
        if asked is not None:
            continue
        out.append(sub)
        if len(out) >= limit:
            break
    return out


def pending_rounds(s: Session, limit: int, now: dt.datetime | None = None) -> list[m.ChatRound]:
    now = now or db.now()
    since = now - dt.timedelta(hours=config.AGENT_ASSESS["max_wait_hours"])
    done = select(m.AgentAssessment.round_id).where(m.AgentAssessment.round_id.is_not(None))   # NOT IN 遇到空值会全部落空
    return list(s.scalars(select(m.ChatRound).where(
        m.ChatRound.status == "closed", m.ChatRound.skill_code != "", m.ChatRound.ended_at >= since,
        m.ChatRound.id.not_in(done)).order_by(m.ChatRound.id).limit(limit)).all())


def run_pending(limit: int | None = None) -> int:
    """后台空闲时调用：评几个刚结束的对话回合，和几道没问过问题、自己改对的题。返回评了几个。"""
    n = 0
    limit = limit or config.AGENT_ASSESS["batch"]
    with db.session_scope() as s:
        for rnd in pending_rounds(s, limit):
            try:
                if assess_round(s, rnd) is not None:
                    n += 1
            except Exception:  # 一个回合出错不影响别的
                log.exception("理解评估出错（回合 %s）", rnd.id)
    with db.session_scope() as s:
        for sub in pending_code(s, limit):
            try:
                if assess_code(s, sub) is not None:
                    n += 1
            except Exception:
                log.exception("看代码评理解出错（提交 %s）", sub.submission_id)
    return n


# ------------------------------------------------------------ 给教师端看
def view(s: Session, row: m.AgentAssessment) -> dict:
    rnd = s.get(m.ChatRound, row.round_id) if row.round_id is not None else None
    server_id, problem_id = (rnd.server_id, rnd.problem_id) if rnd is not None else (row.server_id, row.problem_id)
    title = ""
    if problem_id is not None:
        info = catalog.problem_info(s, server_id, problem_id)
        title = info.title if info else ""
    return {"id": row.id, "skill": row.skill_code, "skillName": skill_name(row.skill_code),
            "kp": KNOWLEDGE_POINTS.get(kp_of_skill(row.skill_code), ""), "level": row.level,
            "levelName": LEVEL_NAMES.get(row.level, "证据不够") if row.level is not None else "证据不够",
            "rawLevel": row.raw_level, "ruleLevel": row.rule_level, "confidence": round(row.confidence, 2),
            "evidence": row.evidence or [], "misconception": row.misconception, "adjusted": row.adjusted,
            "source": row.source, "problem": title, "time": row.created_at.isoformat(),
            "basis": row.basis, "basisName": "看对话" if row.basis == "chat" else "看代码怎么改的"}


def agreement(rows: list[m.AgentAssessment]) -> dict:
    """大模型和规则都给了等级的那些：完全一致的比例，和 Cohen's kappa（扣掉碰巧一致的部分）。"""
    pairs = [(r.raw_level, r.rule_level) for r in rows
             if r.source == "llm" and r.raw_level is not None and r.rule_level is not None]
    n = len(pairs)
    if n == 0:
        return {"n": 0, "exact": None, "kappa": None, "within1": None}
    exact = sum(1 for a, b in pairs if a == b) / n
    within1 = sum(1 for a, b in pairs if abs(a - b) <= 1) / n
    cats = range(4)
    pa = {c: sum(1 for a, _ in pairs if a == c) / n for c in cats}
    pb = {c: sum(1 for _, b in pairs if b == c) / n for c in cats}
    pe = sum(pa[c] * pb[c] for c in cats)
    kappa = (exact - pe) / (1 - pe) if pe < 1 else None
    return {"n": n, "exact": round(exact, 2), "kappa": round(kappa, 2) if kappa is not None else None,
            "within1": round(within1, 2)}
