"""小题：判断对错（JUDGE）；判断后自己找错并改（FIX）。

改得对不对由运行决定：把学生改后的代码替换进模板，在沙箱里编译运行，用小题自带的数据检查。
学生端只拿得到 view() 返回的内容；有没有错、错在哪一行、标准改法，作答结束前都不给。

两种作答协议（QuizAttempt.protocol）：
- 1（以前的）：把握 → 判断 → 判断有错的改代码。旧记录照旧显示、照旧能接着做完。
- 2（界面改造方案第 4 节，现在新出的题都是）：先判断（满足要求 / 存在问题，把握可选，确认前可以改选）
  → 再选位置（判断有问题）或依据（判断满足要求）→ 判断有问题的修改（可以运行公开样例、看提示，
  可以原样提交）→ 反馈。首次判断、首次选行、首稿各记一次不再改，运行、提示、重试按顺序记进 events。
  第一步界面上不给任何能猜出答案的东西；反馈之前不下发错在哪、标准改法、解释。
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import random
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import catalog, config, db, mastery
from . import models as m
from .quizbank import ITEMS, PLACEHOLDER
from .sandbox import run_program
from .sandbox.precheck import check_editable_code, compile_error_zh
from .taxonomy import course_of

DONE = "done"
_rng = random.Random()  # 选题用的随机数；演示数据入库时会固定种子


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
    """把显示的代码放回模板。返回 (完整程序, 显示的代码在程序里前面有几行)。

    占位符那一行前面的空白就是插回去的缩进；占位符顶格写的（题库里的题）按 4 个空格缩进。
    """
    lines = template.split("\n")
    idx = next(i for i, line in enumerate(lines) if PLACEHOLDER in line)
    lead = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())] or "    "
    body = [lead + line if line.strip() else line for line in code.split("\n")]
    return "\n".join(lines[:idx] + body + lines[idx + 1:]), idx


def _norm(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").replace("\r\n", "\n").rstrip().split("\n")]


def run_checks(item: m.QuizItem, code: str, checks: list[dict] | None = None) -> dict:
    checks = item.checks if checks is None else checks
    source, offset = build_program(item.template, code)
    result = run_program(source, [c["input"] for c in checks])
    if result.status == "unavailable":
        return {"status": "unavailable"}
    if result.status == "compile_error":
        return {"status": "compile_error",
                "message": compile_error_zh(result.compile_output, offset, len(code.split("\n")))}
    for check, run in zip(checks, result.runs):
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
                exclude_scenes: set[str] | None = None, has_bug: bool | None = None) -> m.QuizItem | None:
    """选一道这个技能的小题。按最近 10 道控制“正确代码”占 40%~50%，具体哪道随机。"""
    rng = rng or _rng
    items = s.scalars(select(m.QuizItem).where(
        m.QuizItem.skill_code == skill_code, m.QuizItem.verified.is_(True),
        m.QuizItem.owner_user_id.is_(None))).all()   # 专门给别人出的题不拿来用
    if not items:
        return None
    recent = s.execute(
        select(m.QuizAttempt.item_id, m.QuizItem.has_bug).join(m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id)
        .where(m.QuizAttempt.user_id == user_id).order_by(m.QuizAttempt.id.desc())
        .limit(config.QUIZ["ratio_window"])).all()
    done_ids = {r[0] for r in recent}
    if has_bug is not None:
        items = [i for i in items if i.has_bug == has_bug] or items
    fresh = [i for i in items if i.id not in done_ids] or list(items)
    if exclude_scenes:
        fresh = [i for i in fresh if i.scene not in exclude_scenes] or fresh
    low, high = config.QUIZ["correct_ratio"]
    ratio = (sum(1 for r in recent if not r[1]) / len(recent)) if recent else (low + high) / 2
    want_correct = True if ratio < low else False if ratio >= high else rng.random() < (low + high) / 2
    pool = [i for i in fresh if (not i.has_bug) == want_correct] or fresh
    return rng.choice(pool)


def item_version(item: m.QuizItem) -> str:
    """题目内容的哈希：同一道题改过以后，作答记录还能认出做的是哪一版。"""
    raw = json.dumps([item.stem, item.shown_code, item.has_bug, item.bug_lines, item.standard_fix, item.checks],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def new_attempt(s: Session, user_id: str, item: m.QuizItem, reason: str, at: dt.datetime | None = None,
                review_item_id: int | None = None) -> m.QuizAttempt:
    """开始一道小题，不发对话消息（复习页里直接做）。新出的题都按独立作答（protocol 2）做。"""
    at = at or db.now()
    attempt = m.QuizAttempt(user_id=user_id, item_id=item.id, quiz_type=item.quiz_type, skill_code=item.skill_code,
                            reason=reason, state="judge", created_at=at, review_item_id=review_item_id,
                            protocol=2, item_version=item_version(item),
                            events=[{"t": at.isoformat(), "kind": "prepared"}])
    s.add(attempt)
    s.flush()
    return attempt


def start(s: Session, user_id: str, item: m.QuizItem, reason: str, lead: str,
          server_id: int | None = None, problem_id: int | None = None, round_id: int | None = None,
          at: dt.datetime | None = None) -> m.ChatMessage:
    """在对话里出一道小题：一条消息只放一道。"""
    at = at or db.now()
    attempt = new_attempt(s, user_id, item, reason, at)
    course = catalog.server_course(s, server_id) if server_id is not None else course_of(item.skill_code or "C")
    msg = m.ChatMessage(user_id=user_id, round_id=round_id, server_id=server_id, problem_id=problem_id,
                        course=course, role="agent", kind="quiz", quiz_attempt_id=attempt.id, created_at=at,
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
        from . import review
        review.on_quiz_result(s, attempt)
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
    # 复习：更新记忆半衰期、安排下一次；不是复习题但答错了、或没把握却答对，就加进复习
    from . import review

    review.on_quiz_result(s, attempt)


def set_confidence(s: Session, attempt: m.QuizAttempt, value: str, elapsed_ms: int | None) -> None:
    if attempt.protocol == 2 or attempt.state != "confidence" or value not in ("没把握", "一半", "很有把握"):
        return
    attempt.confidence = value
    attempt.confidence_ms = elapsed_ms
    attempt.state = "judge"


def judge(s: Session, attempt: m.QuizAttempt, says_bug: bool) -> None:
    if attempt.protocol == 2 or attempt.state != "judge":
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
    if attempt.protocol == 2 or attempt.state != "edit":
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
    if attempt.protocol == 2:
        _event(attempt, "skip", state=attempt.state)
    _finish(s, attempt, s.get(m.QuizItem, attempt.item_id), "skipped")


# ------------------------------------------------------------ 学生端看到的
_HEADLINES = {
    "right": "改对了。", "right_second": "改对了。", "right_bypass": "改对了。", "right_judge": "判断对了。",
    "wrong_fix": "两次都没改对。", "skipped": "这道先跳过了。",
}


def view(s: Session, attempt: m.QuizAttempt) -> dict:
    item = s.get(m.QuizItem, attempt.item_id)
    if attempt.protocol == 2:
        return view2(s, attempt, item)
    runs = list(attempt.runs or [])
    start_line = item.start_line or 1
    out: dict = {
        "attemptId": attempt.id,
        "stem": item.stem,
        "caption": item.caption,
        "code": item.shown_code.split("\n"),
        "startLine": start_line,
        "state": attempt.state,
        "confidence": attempt.confidence or None,
        "judged": None if attempt.judged_has_bug is None else ("bug" if attempt.judged_has_bug else "ok"),
    }
    mark = (item.origin or {}).get("mark_line")
    if item.quiz_type == "JUDGE" and mark:   # 判断题问的是“改成这样还对吗”：标出改动的那一行（找错题不标）
        out["mark"] = [start_line + mark - 1]
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
            result["bugLine"] = (start_line + item.bug_line - 1) if item.bug_line else None
            result["fix"] = {"lines": fix_lines, "mark": [start_line + x - 1 for x in fixed], "start": start_line}
        if outcome == "wrong_fix" and runs:
            last_fail = next((r for r in reversed(runs) if r["kind"] == "fail"), None)
            if last_fail:
                result["lastRun"] = last_fail["message"]
        out["result"] = result
    return out


# ------------------------------------------------------------ 独立作答（protocol 2）
CONFIDENCE = ("没把握", "一半", "很有把握")


def _event(attempt: m.QuizAttempt, kind: str, **data) -> None:
    attempt.events = [*(attempt.events or []), {"t": db.now().isoformat(), "kind": kind, **data}]


def mark_viewed(attempt: m.QuizAttempt) -> None:
    if attempt.state != DONE and not any(e.get("kind") == "viewed" for e in attempt.events or []):
        _event(attempt, "viewed", state=attempt.state)


def revealing_stem(item: m.QuizItem) -> bool:
    return item.source == "own_code" and bool(re.fullmatch(r"第\s*\d+\s*行改成标出的这样，结果还对吗？", item.stem))


def _note(attempt: m.QuizAttempt, kind: str, message: str, code: str = "", counted: bool = False) -> None:
    attempt.runs = [*(attempt.runs or []), {"kind": kind, "message": message, "counted": counted, "code": code}]


def _bug_set(item: m.QuizItem) -> set[int]:
    return set(item.bug_lines or ([item.bug_line] if item.bug_line else []))


def judge2(s: Session, attempt: m.QuizAttempt, says_bug: bool, confidence: str | None = None,
           elapsed_ms: int | None = None) -> None:
    """第一步：确认判断。确认以后锁定，不能再改（确认前在页面上可以改选）。把握可选。"""
    if attempt.protocol != 2 or attempt.state != "judge":
        return
    item = s.get(m.QuizItem, attempt.item_id)
    attempt.judged_has_bug = says_bug
    attempt.judge_correct = says_bug == item.has_bug
    if confidence in CONFIDENCE:
        attempt.confidence, attempt.confidence_ms = confidence, elapsed_ms
    prepared = next((e for e in attempt.events or [] if e.get("kind") == "prepared"), {})
    timing = {}
    if prepared.get("triggerAt"):
        delta = db.now() - dt.datetime.fromisoformat(prepared["triggerAt"])
        timing["secondsSinceTrigger"] = max(0, int(delta.total_seconds()))
    _event(attempt, "judge", says="bug" if says_bug else "ok", confidence=confidence or "", **timing)
    attempt.state = "locate"


def locate(s: Session, attempt: m.QuizAttempt, lines: list[int]) -> str | None:
    """第二步：判断有问题的选位置，判断满足要求的选依据（支撑判断的关键行）。一行或连续几行。

    返回要提示学生的话（选得不对时）；正常返回 None。全选代码、选太多行不算命中。
    """
    if attempt.protocol != 2 or attempt.state != "locate":
        return None
    item = s.get(m.QuizItem, attempt.item_id)
    start = item.start_line or 1
    n = len(item.shown_code.split("\n"))
    rel = sorted({x - start + 1 for x in lines if isinstance(x, int) and start <= x < start + n})
    if not rel:
        return "先点行号，选一行或连续的几行。"
    if rel != list(range(rel[0], rel[-1] + 1)):
        return "选一行，或者连续的几行。"
    attempt.first_lines = [start + x - 1 for x in rel]
    if item.has_bug and attempt.judged_has_bug:
        attempt.locate_hit = bool(_bug_set(item) & set(rel)) and (n == 1 or len(rel) < n) and len(rel) <= max(3, n // 2)
    _event(attempt, "locate", lines=attempt.first_lines, role="where" if attempt.judged_has_bug else "why")
    if attempt.judged_has_bug:
        attempt.state = "edit"
        return None
    _finish(s, attempt, item, "right_judge" if attempt.judge_correct else "wrong_judge")
    _event(attempt, "feedback")
    return None


def hint_texts(item: m.QuizItem) -> list[str]:
    """修改时可以看的提示，一条比一条具体。代码本来就对的题，提示不说破。"""
    start = item.start_line or 1
    if not item.has_bug:
        return ["再对照题目要求读一遍：每一行都符合要求吗？都符合的话，可以原样提交。"]
    line = item.bug_line or (sorted(_bug_set(item)) or [None])[0]
    out = [f"看看第 {start + line - 1} 行。" if line else "对照题目要求，一行一行看。"]
    if item.explanation:
        out.append(item.explanation)
    return out


def hint(s: Session, attempt: m.QuizAttempt) -> None:
    if attempt.protocol != 2 or attempt.state != "edit":
        return
    item = s.get(m.QuizItem, attempt.item_id)
    if attempt.hints >= len(hint_texts(item)):
        return
    attempt.hints += 1
    _event(attempt, "hint", n=attempt.hints)


def run_sample(s: Session, attempt: m.QuizAttempt, code: str) -> None:
    """运行公开样例（第一组检查数据）：只看这一组，不算一次提交，记一次运行。"""
    if attempt.protocol != 2 or attempt.state != "edit":
        return
    item = s.get(m.QuizItem, attempt.item_id)
    code = (code or "").replace("\r\n", "\n").rstrip("\n")
    problem = check_editable_code(code, config.QUIZ["max_code_chars"])
    if problem:
        return _note(attempt, "hint", problem, code)
    sample = (item.checks or [])[:1]
    if not sample:
        return _note(attempt, "hint", "这道题没有公开样例。", code)
    result = run_checks(item, code, sample)
    if result["status"] == "unavailable":
        return _note(attempt, "hint", "运行环境暂时用不了，过一会儿再试。这次不算。", code)
    attempt.sample_runs += 1
    shown = sample[0].get("show") or sample[0]["input"].strip()
    if result["status"] == "pass":
        msg = f"公开样例通过：输入 {shown}，输出 {' '.join(sample[0]['expected'].split())[:60]}。"
    else:
        msg = "公开样例：" + result["message"]
    _event(attempt, "sample", ok=result["status"] == "pass")
    _note(attempt, "sample", msg, code)


def submit_fix(s: Session, attempt: m.QuizAttempt, code: str) -> None:
    """提交修改。可以原样提交（认为代码没问题）。编译不过、改动太多、运行环境不可用都不算一次提交。"""
    if attempt.protocol != 2 or attempt.state != "edit":
        return
    item = s.get(m.QuizItem, attempt.item_id)
    code = (code or "").replace("\r\n", "\n").rstrip("\n")
    lines, n_changed = changed_lines(item.shown_code, code)
    if n_changed > config.QUIZ["max_changed_lines"]:
        return _note(attempt, "hint", "只需要改一两处，改动太多了。", code)
    problem = check_editable_code(code, config.QUIZ["max_code_chars"])
    if problem:
        return _note(attempt, "hint", problem, code)
    result = run_checks(item, code)
    if result["status"] == "unavailable":
        return _note(attempt, "hint", "运行环境暂时用不了，过一会儿再试。这次不算。", code)
    if result["status"] == "compile_error":
        return _note(attempt, "compile", result["message"] + "这次不算。", code)
    first = attempt.tries == 0
    attempt.tries += 1
    attempt.changed_lines = lines
    passed = result["status"] == "pass"
    if first:
        attempt.first_fix, attempt.first_fix_pass = code, passed
        attempt.first_fix_clean = attempt.hints == 0 and attempt.sample_runs == 0
    if not item.has_bug:
        # 误报：代码本来就对。原样提交算自己纠正了判断（首次判断照样记错）；改了还对是多余的修改；改坏了另记
        kind = "kept" if n_changed == 0 else ("extra" if passed else "broke")
        _event(attempt, "submit", ok=passed, changed=n_changed, first=first, misjudge=kind)
        _note(attempt, "misjudge", "这段代码本来就是对的。", code, counted=True)
        _finish(s, attempt, item, "wrong_judge")
        _event(attempt, "feedback")
        return
    _event(attempt, "submit", ok=passed, changed=n_changed, first=first)
    if passed:
        _note(attempt, "pass", "改对了。", code, counted=True)
        hit = bool(set(lines) & _bug_set(item))
        clean_first = first and attempt.hints == 0
        outcome = ("right" if hit else "right_bypass") if clean_first else "right_second"
        _finish(s, attempt, item, outcome)
        _event(attempt, "feedback")
        return
    _note(attempt, "fail", "代码没有改动，问题还在。" if n_changed == 0 else result["message"], code, counted=True)
    if attempt.tries >= config.QUIZ["max_tries"]:
        _finish(s, attempt, item, "wrong_fix")
        _event(attempt, "feedback")


def _origin(s: Session, attempt: m.QuizAttempt, item: m.QuizItem) -> dict | None:
    """反馈以后开放的“回看相关作业”：这道小题出自哪道作业题（看得到的才给）。"""
    server_id, problem_id = item.source_server_id, item.source_problem_id
    if attempt.review_item_id:
        ri = s.get(m.ReviewItem, attempt.review_item_id)
        if ri is not None and ri.origin_problem_id is not None:
            server_id, problem_id = ri.origin_server_id, ri.origin_problem_id
    if problem_id is None or server_id is None or not catalog.is_visible(s, attempt.user_id, server_id, problem_id):
        return None
    info = catalog.problem_info(s, server_id, problem_id)
    return {"key": info.key, "title": info.title} if info else None


def _where(lines: list[int]) -> str:
    if not lines:
        return ""
    return f"第 {lines[0]} 行" if len(lines) == 1 else f"第 {lines[0]}～{lines[-1]} 行"


def feedback2(s: Session, attempt: m.QuizAttempt, item: m.QuizItem) -> dict:
    """反馈：判断、依据或定位、修改各一句，真实情况和解释这时才给。"""
    start = item.start_line or 1
    bug_lines = sorted(start + x - 1 for x in _bug_set(item))
    judged_bug = bool(attempt.judged_has_bug)
    mine = list(attempt.first_lines or [])
    if attempt.outcome == "skipped":
        return {"outcome": "skipped", "headline": "这道先跳过了。"}
    if not item.has_bug:
        headline = "这段代码本来是对的" if judged_bug else "你识别出了一段正确的代码"
    elif not judged_bug:
        headline = "这段代码其实有错"
    else:
        fixed = attempt.outcome in ("right", "right_bypass", "right_second")
        headline = "你找到并改对了错误" if fixed else "这次没改对"
    out: dict = {
        "outcome": "right" if attempt.correct else "wrong",
        "headline": headline,
        "lines": mine,
        "explanation": item.explanation,
        "origin": None if attempt.reason == "review" else _origin(s, attempt, item),
    }
    runs = list(attempt.runs or [])
    passed_code = next((r["code"] for r in reversed(runs) if r.get("kind") == "pass" and r.get("code")), "")
    if passed_code:
        out["finalCode"] = passed_code.split("\n")
    if item.has_bug:
        out["bugLines"] = bug_lines
        if attempt.outcome not in ("right", "right_bypass", "right_second"):
            fixed_lines, _n = changed_lines(item.shown_code, item.standard_fix)
            out["fix"] = {"lines": item.standard_fix.split("\n"), "mark": [start + x - 1 for x in fixed_lines], "start": start}
    return out


def view2(s: Session, attempt: m.QuizAttempt, item: m.QuizItem) -> dict:
    """学生端看到的独立作答。反馈之前不给有没有错、错在哪、标准改法、解释。"""
    start = item.start_line or 1
    runs = list(attempt.runs or [])
    out: dict = {
        "attemptId": attempt.id,
        "protocol": 2,
        "stem": f"请判断这段代码能否满足「{item.scene}」的要求。" if revealing_stem(item) else item.stem,
        "caption": item.caption,
        "code": item.shown_code.split("\n"),
        "startLine": start,
        "state": attempt.state,
        "confidence": attempt.confidence or None,
        "judged": None if attempt.judged_has_bug is None else ("bug" if attempt.judged_has_bug else "ok"),
        "lines": list(attempt.first_lines or []),
    }
    # 独立作答不标改动的那一行：学生要自己找位置，第一步界面上不能有能猜出答案的高亮
    if attempt.state == "edit":
        texts = hint_texts(item)
        out["draft"] = runs[-1]["code"] if runs and runs[-1].get("code") else item.shown_code
        out["message"] = {"kind": runs[-1]["kind"], "text": runs[-1]["message"]} if runs else None
        out["hints"] = texts[:attempt.hints]
        out["moreHints"] = attempt.hints < len(texts)
        out["triesLeft"] = config.QUIZ["max_tries"] - attempt.tries
        out["hasSample"] = bool(item.checks)
    if attempt.state == DONE:
        out["result"] = feedback2(s, attempt, item)
    return out
