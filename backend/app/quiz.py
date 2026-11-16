"""小题：判断对错（JUDGE）；判断后自己找错并改（FIX）。

改得对不对由运行决定：把学生改后的代码替换进模板，在沙箱里编译运行，用小题自带的数据检查。
学生端只拿得到 view() 返回的内容；有没有错、错在哪一行、标准改法，作答结束前都不给。
"""
from __future__ import annotations

import datetime as dt
import difflib
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config, db, mastery
from . import models as m
from .quizbank import ITEMS, PLACEHOLDER
from .sandbox import run_program
from .sandbox.precheck import check_editable_code, compile_error_zh

DONE = "done"


# ------------------------------------------------------------ 题库
def load_bank(s: Session) -> int:
    """把 quizbank 里的题写进库。内容变了的题要重新验证。"""
    n = 0
    for data in ITEMS:
        row = s.scalar(select(m.QuizItem).where(m.QuizItem.code == data["code"]))
        fields = dict(
            quiz_type=data["quiz_type"], skill_code=data["skill_code"], error_code=data.get("error_code", ""),
            stem=data["stem"], caption=data.get("caption", ""), shown_code=data["shown_code"],
            template=data["template"], has_bug=data["has_bug"], bug_line=data.get("bug_line"),
            bug_lines=data.get("bug_lines", []), judged_code=data.get("judged_code", ""),
            standard_fix=data.get("standard_fix", ""), checks=data["checks"], choices=data.get("choices", []),
            explanation=data.get("explanation", ""), scene=data.get("scene", ""), source="bank")
        if row is None:
            s.add(m.QuizItem(code=data["code"], verified=False, **fields))
            n += 1
        else:
            changed = any(getattr(row, k) != v for k, v in fields.items())
            for k, v in fields.items():
                setattr(row, k, v)
            if changed:
                row.verified = False
    s.flush()
    return n


def build_program(template: str, code: str) -> tuple[str, int]:
    """把显示的代码放回模板。返回 (完整程序, 显示的代码在程序里前面有几行)。"""
    lines = template.split("\n")
    idx = next(i for i, line in enumerate(lines) if PLACEHOLDER in line)
    body = ["    " + line if line.strip() else line for line in code.split("\n")]
    return "\n".join(lines[:idx] + body + lines[idx + 1:]), idx


def _norm(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").replace("\r\n", "\n").rstrip().split("\n")]


def run_checks(item: m.QuizItem, code: str) -> dict:
    source, offset = build_program(item.template, code)
    result = run_program(source, [c["input"] for c in item.checks])
    if result.status == "unavailable":
        return {"status": "unavailable"}
    if result.status == "compile_error":
        return {"status": "compile_error",
                "message": compile_error_zh(result.compile_output, offset, len(code.split("\n")))}
    for check, run in zip(item.checks, result.runs):
        shown = check.get("show") or check["input"].strip().replace("\n", "，")
        if run.status == "timeout":
            return {"status": "fail", "message": f"输入 {shown} 时，程序一直没有结束。"}
        if run.status != "ok":
            return {"status": "fail", "message": f"输入 {shown} 时，程序运行出错了。"}
        if _norm(run.stdout) != _norm(check["expected"]):
            got = " ".join(run.stdout.split()) or "空"
            want = " ".join(check["expected"].split())
            return {"status": "fail", "message": f"输入 {shown} 时，输出 {got[:60]}，应该是 {want[:60]}。"}
    return {"status": "pass"}


def apply_choice(item: m.QuizItem, index: int) -> str | None:
    if not (0 <= index < len(item.choices or [])):
        return None
    lines = item.shown_code.split("\n")
    for line_no, text in item.choices[index].get("edits", {}).items():
        i = int(line_no) - 1
        if 0 <= i < len(lines):
            lines[i] = text
    return "\n".join(lines)


def verify_item(item: m.QuizItem) -> tuple[bool, str]:
    """入库前的验证：有错的代码真的出错；没错的代码和标准改法真的通过；选择题里只有第一个选项是对的。"""
    asked = item.judged_code or item.shown_code
    first = run_checks(item, asked)
    if first["status"] in ("unavailable", "compile_error"):
        return False, f"题目代码：{first['status']}"
    if item.has_bug and first["status"] == "pass":
        return False, "标成有错，但检查数据全过了"
    if not item.has_bug and first["status"] != "pass":
        return False, f"标成没错，但没通过：{first.get('message')}"
    if item.has_bug:
        if not item.standard_fix:
            return False, "缺标准改法"
        fixed = run_checks(item, item.standard_fix)
        if fixed["status"] != "pass":
            return False, f"标准改法没通过：{fixed.get('message')}"
        changed, n = changed_lines(item.shown_code, item.standard_fix)
        if n > config.QUIZ["max_changed_lines"]:
            return False, "标准改法改动超过 3 行"
    for i, _choice in enumerate(item.choices or []):
        r = run_checks(item, apply_choice(item, i) or "")
        if (r["status"] == "pass") != (i == 0):
            return False, f"第 {i + 1} 个选项的对错不符合预期"
    if len(item.shown_code.split("\n")) > 10 or len(item.stem) > 40:
        return False, "题干或代码太长"
    return True, "ok"


def verify_bank(s: Session) -> list[tuple[str, bool, str]]:
    out = []
    for item in s.scalars(select(m.QuizItem).order_by(m.QuizItem.id)).all():
        ok, why = verify_item(item)
        item.verified = ok
        out.append((item.code, ok, why))
    return out


def changed_lines(old: str, new: str) -> tuple[list[int], int]:
    """学生改了原来的哪几行（1 起），一共改动了几行。插入的行记在它相邻的原行上。"""
    a = [line.rstrip() for line in old.split("\n")]
    b = [line.rstrip() for line in new.split("\n")]
    touched: set[int] = set()
    count = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        count += max(i2 - i1, j2 - j1)
        if i2 > i1:
            touched.update(range(i1 + 1, i2 + 1))
        else:  # 纯插入：算在前后两行上
            touched.update(x for x in (i1, i1 + 1) if 1 <= x <= len(a))
    return sorted(touched), count


# ------------------------------------------------------------ 出题
def _day_start(at: dt.datetime) -> dt.datetime:
    local = at.astimezone(db.BEIJING)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(db.UTC)


def can_start(s: Session, user_id: str, skill_code: str, at: dt.datetime | None = None) -> bool:
    """每人每天上限 3 道（待定），同一技能同一天最多 2 道。"""
    start = _day_start(at or db.now())
    today = s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.created_at >= start)).all()
    if len(today) >= config.QUIZ["daily_limit"]:
        return False
    return sum(1 for a in today if a.skill_code == skill_code) < config.QUIZ["per_skill_daily_limit"]


def choose_item(s: Session, user_id: str, skill_code: str, rng: random.Random | None = None,
                exclude_scenes: set[str] | None = None) -> m.QuizItem | None:
    """选一道这个技能的小题。按最近 10 道控制“正确代码”占 40%~50%，具体哪道随机。"""
    rng = rng or random.Random()
    items = s.scalars(select(m.QuizItem).where(
        m.QuizItem.skill_code == skill_code, m.QuizItem.verified.is_(True))).all()
    if not items:
        return None
    recent = s.execute(
        select(m.QuizAttempt.item_id, m.QuizItem.has_bug).join(m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id)
        .where(m.QuizAttempt.user_id == user_id).order_by(m.QuizAttempt.id.desc())
        .limit(config.QUIZ["ratio_window"])).all()
    done_ids = {r[0] for r in recent}
    fresh = [i for i in items if i.id not in done_ids] or list(items)
    if exclude_scenes:
        fresh = [i for i in fresh if i.scene not in exclude_scenes] or fresh
    low, high = config.QUIZ["correct_ratio"]
    ratio = (sum(1 for r in recent if not r[1]) / len(recent)) if recent else (low + high) / 2
    want_correct = True if ratio < low else False if ratio >= high else rng.random() < (low + high) / 2
    pool = [i for i in fresh if (not i.has_bug) == want_correct] or fresh
    return rng.choice(pool)


def start(s: Session, user_id: str, item: m.QuizItem, reason: str, lead: str,
          server_id: int | None = None, problem_id: int | None = None, round_id: int | None = None,
          at: dt.datetime | None = None) -> m.ChatMessage:
    """出一道小题：一条消息只放一道。"""
    at = at or db.now()
    attempt = m.QuizAttempt(user_id=user_id, item_id=item.id, quiz_type=item.quiz_type, skill_code=item.skill_code,
                            reason=reason, state="confidence", created_at=at)
    s.add(attempt)
    s.flush()
    msg = m.ChatMessage(user_id=user_id, round_id=round_id, server_id=server_id, problem_id=problem_id,
                        role="agent", kind="quiz", quiz_attempt_id=attempt.id, created_at=at,
                        content={"blocks": [{"t": "p", "text": lead}] if lead else []})
    s.add(msg)
    s.flush()
    return msg


# ------------------------------------------------------------ 作答
def confidence_reliable(s: Session, user_id: str) -> bool:
    """乱点检查：连续 10 题都选同一项，或点选用时不到 1 秒，这个学生的把握数据暂不使用。"""
    rows = s.scalars(select(m.QuizAttempt).where(
        m.QuizAttempt.user_id == user_id, m.QuizAttempt.confidence != ""
    ).order_by(m.QuizAttempt.id.desc()).limit(config.QUIZ["careless_same_choice_run"])).all()
    if not rows:
        return True
    if rows[0].confidence_ms is not None and rows[0].confidence_ms < config.QUIZ["careless_min_ms"]:
        return False
    return not (len(rows) >= config.QUIZ["careless_same_choice_run"] and len({r.confidence for r in rows}) == 1)


def _finish(s: Session, attempt: m.QuizAttempt, item: m.QuizItem, outcome: str) -> None:
    attempt.state = DONE
    attempt.outcome = outcome
    attempt.correct = outcome in ("right", "right_judge", "right_second", "right_bypass")
    attempt.finished_at = db.now()
    attempt.duration_ms = int((attempt.finished_at - attempt.created_at).total_seconds() * 1000)
    if outcome == "skipped":
        return
    key = f"quiz:{attempt.id}"
    reliable = confidence_reliable(s, attempt.user_id)
    mastery.record_quiz(s, attempt.user_id, attempt.skill_code, outcome, attempt.confidence, attempt.reason,
                        key, reliable, attempt.finished_at)
    # 连续两道都很有把握地答对，并且是不同的代码：奖励
    if outcome in ("right", "right_judge") and attempt.confidence == "很有把握" and reliable:
        prev = s.scalars(select(m.QuizAttempt).where(
            m.QuizAttempt.user_id == attempt.user_id, m.QuizAttempt.skill_code == attempt.skill_code,
            m.QuizAttempt.state == DONE, m.QuizAttempt.id != attempt.id, m.QuizAttempt.skipped.is_(False)
        ).order_by(m.QuizAttempt.id.desc())).first()
        if (prev is not None and prev.outcome in ("right", "right_judge") and prev.confidence == "很有把握"
                and prev.item_id != attempt.item_id):
            mastery.record_streak_bonus(s, attempt.user_id, attempt.skill_code, f"streak:{attempt.id}",
                                        attempt.finished_at)
    # 回访队列里对应的那一条
    for rv in s.scalars(select(m.RevisitItem).where(m.RevisitItem.attempt_id == attempt.id)).all():
        rv.status = "done"
        if not attempt.correct:  # 答错了：之后几天再安排一次
            s.add(m.RevisitItem(user_id=attempt.user_id, skill_code=attempt.skill_code, stage=rv.stage,
                                due_at=db.now() + dt.timedelta(days=2)))
        elif rv.stage == "recall":  # 回忆题答对了：再出一道换了场景的变式题
            s.add(m.RevisitItem(user_id=attempt.user_id, skill_code=attempt.skill_code, stage="variant",
                                due_at=db.now()))


def set_confidence(s: Session, attempt: m.QuizAttempt, value: str, elapsed_ms: int | None) -> None:
    if attempt.state != "confidence" or value not in ("没把握", "一半", "很有把握"):
        return
    attempt.confidence = value
    attempt.confidence_ms = elapsed_ms
    attempt.state = "judge"


def judge(s: Session, attempt: m.QuizAttempt, says_bug: bool) -> None:
    if attempt.state != "judge":
        return
    item = s.get(m.QuizItem, attempt.item_id)
    attempt.judged_has_bug = says_bug
    attempt.judge_correct = says_bug == item.has_bug
    if says_bug and item.quiz_type == "FIX":
        attempt.state = "edit"  # 整段可改，不提示是哪一行；代码本来是对的也让他改，看他误会了哪种写法
        return
    _finish(s, attempt, item, "right_judge" if attempt.judge_correct else "wrong_judge")


def run_fix(s: Session, attempt: m.QuizAttempt, code: str | None = None, choice: int | None = None) -> None:
    """运行检查。编译不过、没改动、改动太多都不算一次机会。"""
    if attempt.state != "edit":
        return
    item = s.get(m.QuizItem, attempt.item_id)
    if choice is not None:
        code = apply_choice(item, choice)
    if code is None:
        return
    code = code.replace("\r\n", "\n").rstrip("\n")
    runs = list(attempt.runs or [])

    def note(kind: str, message: str, counted: bool = False) -> None:
        runs.append({"kind": kind, "message": message, "counted": counted, "code": code, "choice": choice})
        attempt.runs = runs

    lines, n_changed = changed_lines(item.shown_code, code)
    if n_changed == 0:
        return note("hint", "还没有改动。找到有问题的地方，改完再运行。")
    if n_changed > config.QUIZ["max_changed_lines"]:
        return note("hint", "只需要改一两处，改动太多了。")
    problem = check_editable_code(code, config.QUIZ["max_code_chars"])
    if problem:
        return note("hint", problem)
    result = run_checks(item, code)
    if result["status"] == "unavailable":
        return note("hint", "运行环境暂时用不了，过一会儿再试。")
    if result["status"] == "compile_error":
        return note("compile", result["message"])  # 这次不算机会
    attempt.changed_lines = lines
    attempt.tries += 1
    if not item.has_bug:
        note("misjudge", "这段代码本来就是对的。", counted=True)
        return _finish(s, attempt, item, "wrong_judge")
    if result["status"] == "pass":
        note("pass", "改对了。", counted=True)
        hit = bool(set(lines) & set(item.bug_lines or ([item.bug_line] if item.bug_line else [])))
        outcome = "right_second" if attempt.tries >= 2 else ("right" if hit else "right_bypass")
        return _finish(s, attempt, item, outcome)
    note("fail", result["message"], counted=True)
    if attempt.tries >= config.QUIZ["max_tries"]:
        _finish(s, attempt, item, "wrong_fix")


def skip(s: Session, attempt: m.QuizAttempt) -> None:
    if attempt.state == DONE:
        return
    attempt.skipped = True
    _finish(s, attempt, s.get(m.QuizItem, attempt.item_id), "skipped")


# ------------------------------------------------------------ 学生端看到的
_HEADLINES = {
    "right": "改对了。", "right_second": "改对了。", "right_bypass": "改对了。", "right_judge": "判断对了。",
    "wrong_fix": "两次都没改对。", "skipped": "这道先跳过了。",
}


def view(s: Session, attempt: m.QuizAttempt) -> dict:
    item = s.get(m.QuizItem, attempt.item_id)
    runs = list(attempt.runs or [])
    out: dict = {
        "attemptId": attempt.id,
        "stem": item.stem,
        "caption": item.caption,
        "code": item.shown_code.split("\n"),
        "state": attempt.state,
        "confidence": attempt.confidence or None,
        "judged": None if attempt.judged_has_bug is None else ("bug" if attempt.judged_has_bug else "ok"),
    }
    if attempt.state == "edit":
        order = list(range(len(item.choices or [])))
        random.Random(attempt.id).shuffle(order)
        out["triesLeft"] = config.QUIZ["max_tries"] - attempt.tries
        out["choices"] = [{"index": i, "label": item.choices[i]["label"]} for i in order]
        out["draft"] = runs[-1]["code"] if runs and runs[-1].get("code") else item.shown_code
        out["message"] = {"kind": runs[-1]["kind"], "text": runs[-1]["message"]} if runs else None
    if attempt.state == DONE:
        outcome = attempt.outcome
        result: dict = {"outcome": "skipped" if outcome == "skipped" else ("right" if attempt.correct else "wrong")}
        if outcome == "wrong_judge":
            result["headline"] = "这段代码其实有错。" if item.has_bug else "这段代码本来是对的。"
        else:
            result["headline"] = _HEADLINES.get(outcome, "")
        if outcome != "skipped":
            result["explanation"] = item.explanation
        if runs and runs[-1].get("code") and outcome in ("right", "right_second", "right_bypass"):
            result["finalCode"] = runs[-1]["code"].split("\n")
        if item.has_bug and outcome in ("wrong_fix", "wrong_judge"):
            # 没做对：指出错在哪一行，给出正确改法
            fix_lines = item.standard_fix.split("\n")
            fixed, _n = changed_lines(item.shown_code, item.standard_fix)
            result["bugLine"] = item.bug_line
            result["fix"] = {"lines": fix_lines, "mark": fixed}
        if outcome == "wrong_fix" and runs:
            last_fail = next((r for r in reversed(runs) if r["kind"] == "fail"), None)
            if last_fail:
                result["lastRun"] = last_fail["message"]
        out["result"] = result
    return out
