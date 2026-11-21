"""AI 按学生的情况出小题。

分两步：程序先定“考什么、怎么考”（规划），再由生成器出具体的题。所有题都要在沙箱里验证过才给学生，
期望输出一律由程序运行正确的那一版算出来，不信任何一方“说”的结果。

规划（make_plan）看这个学生：
- 考哪个技能：调用方给（刚通过的题的主技能、复习项、卡住的技能）；
- 考哪种错：他在这个技能上真犯过的错误类型（最近的优先）；
- 对还是错：最近 10 道里正确代码占 40%~50%，由程序决定这道是对是错（附件一 6.5）；
- 题型：这个技能还在“待加强”、或复习刚答错过 → 判断题（JUDGE）；否则找错并改（FIX）；
- 用谁的代码：刚通过、第一次复习 → 用他自己通过的代码；之后的复习 → 换一个场景（迁移）；
- 把握习惯：常“很有把握却答错”的学生，对错都可以时出有错的题，帮他校准。

生成器，按规划的顺序试，前一个出不了就用下一个：
1. own_code：拿他自己通过的代码，在和这个技能有关的那一行做一处改动（变异），比如把 max = a[0] 改成 max = 0、
   把 < 改成 <=、把 i = 1 改成 i = 0。改动后是对是错由运行决定：在程序自己造的输入上和他原来的代码比输出。
   显示其中相关的 ≤10 行（用原来的行号），整个程序就是模板。附件一例二“第 6 行改成 i = 0，结果还对吗”就是这种题。
2. llm：大模型按规划写一道题（题干、代码、模板、几组输入）。有错的那一版必须真的出错，否则作废。
3. template：题库里同一技能的题，把变量名换成他自己代码里用的名字。
4. bank：题库原题。

检查数据（make_inputs）：在这道题测试点输入的基础上改数字（不放大数值、不改表示个数的数），
用他自己通过的代码和参考解各跑一遍，两者一致才算合法；和任何一个测试点的输入都不一样。
这些数据是我们自己造的，答错时可以给学生看其中一组；XDOJ 的测试点仍然不会给学生看到。
"""
from __future__ import annotations

import json
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, config, db, llm, mastery, quiz
from . import models as m
from .labeling.labeler import current_problem_label
from .quizbank import PLACEHOLDER
from .sandbox import run_program
from .sandbox.precheck import check_editable_code, check_reference_solution
from .taxonomy import LEVEL_NONE, LEVEL_WEAK, SKILLS, course_of, course_prompt, error_name, skill_name
from .xdoj.assemble import source_text
from .xdoj.util import decode_bytes

MAX_SHOWN_LINES = 10


# ============================================================ 规划
@dataclass
class Plan:
    user_id: str
    skill: str
    reason: str
    quiz_type: str
    has_bug: bool
    level: str
    errors: list[str] = field(default_factory=list)
    origin: tuple[int, int] | None = None
    prefer_own: bool = True
    overconfident: bool = False
    notes: list[str] = field(default_factory=list)
    # 决策记录（decisions.py）：正误怎么定的、按规则出“有错的代码”的概率、最近正确代码的占比
    bug_rule: str = ""
    p_bug: float = 0.5
    correct_ratio: float | None = None
    window: int = 0


def _student_errors(s: Session, user_id: str, skill: str) -> list[str]:
    rows = s.execute(
        select(m.SubmissionLabel.error_code)
        .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
              & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
        .where(m.Submission.user_id == user_id, m.SubmissionLabel.skill_code == skill,
               m.SubmissionLabel.is_current.is_(True))
        .order_by(m.Submission.submission_id.desc())).all()
    stuck = s.scalars(select(m.StuckPoint.error_code).where(
        m.StuckPoint.user_id == user_id, m.StuckPoint.skill_code == skill).order_by(m.StuckPoint.id.desc())).all()
    return [c for c in dict.fromkeys([r[0] for r in rows] + list(stuck)) if c]


def make_plan(s: Session, user_id: str, skill: str, reason: str, origin: tuple[int, int] | None = None,
              review_item: m.ReviewItem | None = None, rng: random.Random | None = None) -> Plan:
    rng = rng or quiz._rng
    level = mastery.skill_state(s, user_id, skill).level
    history = select(m.QuizItem.has_bug, m.QuizAttempt.confidence, m.QuizAttempt.correct).join(
        m.QuizItem, m.QuizItem.id == m.QuizAttempt.item_id).where(m.QuizAttempt.user_id == user_id)
    if reason == "review":
        history = history.where(m.QuizAttempt.skill_code == skill, m.QuizAttempt.reason == "review")
    recent = s.execute(history.order_by(m.QuizAttempt.id.desc()).limit(config.QUIZ["ratio_window"])).all()
    sure = [r for r in recent if r[1] == "很有把握" and r[2] is not None]
    overconfident = len([r for r in sure if not r[2]]) >= 2 and len([r for r in sure if not r[2]]) / len(sure) >= 0.3
    low, high = config.QUIZ["correct_ratio"]
    if reason == "review":
        low, high = .4, .6
    ratio = (sum(1 for r in recent if not r[0]) / len(recent)) if recent else (low + high) / 2
    notes = []
    if ratio < low:
        has_bug, bug_rule, p_bug = False, "ratio_low", 0.0
        notes.append(f"最近的小题里正确代码只占 {ratio:.0%}，这道出正确的")
    elif ratio >= high:
        has_bug, bug_rule, p_bug = True, "ratio_high", 1.0
        notes.append(f"最近的小题里正确代码已占 {ratio:.0%}，这道出有错的")
    elif overconfident and reason != "review":
        has_bug, bug_rule, p_bug = True, "overconfident", 1.0
        notes.append("他常常很有把握却答错，这道出有错的帮他校准")
    else:
        bug_rule, p_bug = "random", 1 - (low + high) / 2
        has_bug = rng.random() >= (low + high) / 2
    lapse = review_item is not None and review_item.last_outcome in ("wrong", "lapse")
    quiz_type = "JUDGE" if (level in (LEVEL_WEAK, LEVEL_NONE) or lapse) else "FIX"
    notes.append(f"这个技能现在是“{level}”{'，上次复习没做对' if lapse else ''}，出{'判断题' if quiz_type == 'JUDGE' else '找错并改的题'}")
    if review_item is not None:
        origin = origin or ((review_item.origin_server_id, review_item.origin_problem_id)
                            if review_item.origin_problem_id is not None else None)
        prefer_own = False
        notes.append("同一技能换一个场景，独立检验判断、定位和修改")
    else:
        prefer_own = reason != "review"
    return Plan(user_id=user_id, skill=skill, reason=reason, quiz_type=quiz_type, has_bug=has_bug, level=level,
                errors=_student_errors(s, user_id, skill), origin=origin, prefer_own=prefer_own,
                overconfident=overconfident, notes=notes, bug_rule=bug_rule, p_bug=p_bug,
                correct_ratio=round(ratio, 4) if recent else None, window=len(recent))


# ============================================================ 自造检查数据
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _perturb_value(tok: str, mode: str, rng: random.Random) -> str:
    if "." in tok:
        v = float(tok)
        decimals = len(tok.split(".")[1])
        lo, hi = (min(v, v / 2), max(v, v / 2))
        nv = rng.uniform(lo, hi) if lo != hi else v
        if mode == "negative":
            nv = -abs(nv) - 0.5
        return f"{nv:.{decimals}f}"
    v = int(tok)
    if mode == "negative":
        return str(-rng.randint(1, max(2, abs(v) + 3)))
    if mode == "positive":
        return str(rng.randint(1, max(2, abs(v) + 3)))
    # 不放大数值：保证还在题目给的范围里
    a, b = sorted((v, v // 2 if v >= 0 else -((-v) // 2)))
    if a == b:
        a, b = v - 2, v + 2 if v > 2 else v
    return str(rng.randint(a, b))


def _variants(text: str, rng: random.Random) -> list[str]:
    """一组测试输入 → 几组改过数字的输入。表示个数的数（下一行正好有这么多个数）不改，必要时整体缩短。"""
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    toks = [line.split() for line in lines]
    counts = set()
    for i in range(len(lines) - 1):
        if len(toks[i]) == 1 and re.fullmatch(r"\d+", toks[i][0] or "") and int(toks[i][0]) == len(toks[i + 1]):
            counts.add(i)
    if len(lines) == 1 and len(toks[0]) >= 3 and re.fullmatch(r"\d+", toks[0][0]) and int(toks[0][0]) == len(toks[0]) - 1:
        counts.add(-1)  # “n a1 a2 … an” 写在同一行

    def rebuild(mode: str, shrink: int | None) -> str:
        out = []
        for i, line in enumerate(lines):
            ts = list(toks[i])
            if i in counts:
                if shrink is not None:
                    ts = [str(shrink)]
                out.append(" ".join(ts))
                continue
            if (i - 1) in counts and shrink is not None:
                ts = ts[:shrink]
            if -1 in counts and i == 0:
                n = int(ts[0])
                body = ts[1:1 + (shrink or n)]
                ts = [str(len(body))] + body
                ts = [ts[0]] + [_perturb_value(t, mode, rng) if _NUM.fullmatch(t) else t for t in ts[1:]]
                out.append(" ".join(ts))
                continue
            single = len(lines) == 1 and len(ts) == 1
            ts = [(_perturb_value(t, "keep" if single else mode, rng) if _NUM.fullmatch(t) else t) for t in ts]
            out.append(" ".join(ts))
        return "\n".join(out) + "\n"

    has_list = bool(counts) or any(len(t) >= 3 for t in toks)
    out = []
    for mode in ("keep", "keep", "negative", "positive") if has_list else ("keep", "keep", "keep"):
        n_list = None
        for i in counts:
            if i >= 0:
                n_list = int(toks[i][0])
        if -1 in counts:
            n_list = int(toks[0][0])
        shrink = None
        if n_list is not None and n_list > 6:
            shrink = rng.randint(3, 6)
        out.append(rebuild(mode, shrink))
    for i in counts:  # 只有一个数的情况
        if (i >= 0 and int(toks[i][0]) > 1) or (i == -1 and int(toks[0][0]) > 1):
            out.append(rebuild("keep", 1))
    return out


def _decoded_tests(s: Session, server_id: int, problem_id: int) -> list[str]:
    rows = s.scalars(select(m.Testcase).where(
        m.Testcase.server_id == server_id, m.Testcase.problem_id == problem_id)).all()
    out = []
    for t in rows:
        text, ok = decode_bytes(t.input_bytes, t.input_encoding)
        if ok and text is not None:
            out.append(text)
    return out


def make_inputs(s: Session, server_id: int, problem_id: int, own_source: str, rng: random.Random,
                want: int = 6) -> list[dict]:
    """造几组检查数据。返回 [{"input", "expected", "show"}]，期望输出由他自己通过的代码算出来。"""
    tests = _decoded_tests(s, server_id, problem_id)
    if not tests:
        return []
    raw = {_squash(t) for t in tests}
    small = sorted(tests, key=len)[:8]
    cands: list[str] = []
    for t in small:
        for v in _variants(t, rng):
            if _squash(v) not in raw and _squash(v) not in {_squash(c) for c in cands} and len(v) <= 160:
                cands.append(v)
    if not cands:
        return []
    mine = run_program(own_source, cands, cpu_seconds=1, memory_mb=128)
    if mine.status != "ok":
        return []
    ref = s.scalar(select(m.ReferenceSolution).where(
        m.ReferenceSolution.server_id == server_id, m.ReferenceSolution.problem_id == problem_id,
        m.ReferenceSolution.verified.is_(True)).order_by(m.ReferenceSolution.id.desc()))
    theirs = run_program(ref.source_code, cands, cpu_seconds=1, memory_mb=128) if ref is not None else None
    out = []
    for i, (inp, run) in enumerate(zip(cands, mine.runs)):
        if run.status != "ok" or not run.stdout.strip() or len(run.stdout) > 200:
            continue
        if theirs is not None and (theirs.status != "ok" or theirs.runs[i].status != "ok"
                                   or quiz._norm(theirs.runs[i].stdout) != quiz._norm(run.stdout)):
            continue  # 和参考解不一致：可能超出了题目的范围，不用
        out.append({"input": inp, "expected": run.stdout.strip(), "show": inp.strip().replace("\n", "，")})
    # 输出多样一点：期望输出不同的排在前面
    seen, ordered, rest = set(), [], []
    for c in out:
        (rest if c["expected"] in seen else ordered).append(c)
        seen.add(c["expected"])
    return (ordered + rest)[:want]


# ============================================================ 变异
@dataclass
class Mutation:
    op: str
    line: int      # 在整个源码里的行号（0 起）
    old: str
    new: str


def _sub_once(line: str, pattern: str, repl: Callable[[re.Match], str]) -> str | None:
    mt = re.search(pattern, line)
    if not mt:
        return None
    return line[:mt.start()] + repl(mt) + line[mt.end():]


def _op_init_first(line: str) -> list[str]:
    mt = re.search(r"([A-Za-z_]\w*\s*=\s*)([A-Za-z_]\w*)\[\s*0\s*\]", line)
    return [line[:mt.start(2)] + "0" + line[mt.end():]] if mt and "for" not in line else []


def _op_loop_start(line: str) -> list[str]:
    mt = re.search(r"for\s*\(\s*(?:int\s+)?[A-Za-z_]\w*\s*=\s*([01])\s*;", line)
    if not mt:
        return []
    return [line[:mt.start(1)] + ("1" if mt.group(1) == "0" else "0") + line[mt.end(1):]]


def _op_loop_bound(line: str) -> list[str]:
    mt = re.search(r"for\s*\([^;]*;\s*[A-Za-z_][\w\[\]]*\s*(<=|<|>=|>)\s*[^;]+;", line)
    if not mt:
        return []
    alt = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}[mt.group(1)]
    return [line[:mt.start(1)] + alt + line[mt.end(1):]]


def _op_if_cmp(line: str) -> list[str]:
    head = re.search(r"\b(if|while)\s*\(", line)
    if not head or "for" in line:
        return []
    rest = line[head.end():]
    mt = re.search(r"(?<![<>=!])(<=|>=|<|>)(?![<>=])", rest)
    if not mt:
        return []
    alt = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}[mt.group(1)]
    pos = head.end() + mt.start(1)
    return [line[:pos] + alt + line[pos + len(mt.group(1)):]]


def _op_and_or(line: str) -> list[str]:
    if "&&" in line:
        return [line.replace("&&", "||", 1)]
    if "||" in line:
        return [line.replace("||", "&&", 1)]
    return []


def _op_drop_cast(line: str) -> list[str]:
    for pat in (r"\(\s*(double|float)\s*\)\s*", r"1\.0\s*\*\s*", r"\s*\*\s*1\.0\b"):
        new = _sub_once(line, pat, lambda mt: "")
        if new is not None:
            return [new]
    return []


def _op_long_to_int(line: str) -> list[str]:
    if re.match(r"\s*(long\s+long)\b", line):
        return [re.sub(r"long\s+long", "int", line, count=1)]
    return []


def _op_fmt(line: str) -> list[str]:
    if "%lld" in line:
        return [line.replace("%lld", "%d", 1)]
    mt = re.search(r"%\.(\d)f", line)
    if mt and mt.group(1) != "0":
        return [line[:mt.start(1)] + str(int(mt.group(1)) - 1) + line[mt.end(1):]]
    return []


def _op_acc_init(line: str) -> list[str]:
    if "for" in line or "[" in line:
        return []
    mt = re.search(r"\b([A-Za-z_]\w*)\s*=\s*([01])\s*([;,])", line)
    if not mt or mt.group(1) in ("i", "j", "k"):
        return []
    return [line[:mt.start(2)] + ("1" if mt.group(2) == "0" else "0") + line[mt.end(2):]]


def _op_ok_flag(line: str) -> list[str]:
    new = _sub_once(line, r"=\s*([A-Za-z_]\w*)\s*>\s*1\b", lambda mt: "= 1")
    return [new] if new else []


def _op_char_case(line: str) -> list[str]:
    for a, b in (("'a'", "'A'"), ("'A'", "'a'"), ("+ 32", "- 32"), ("- 32", "+ 32")):
        if a in line:
            return [line.replace(a, b, 1)]
    return []


def _op_incr_prefix(line: str) -> list[str]:
    mt = re.search(r"for\s*\([^;]*;[^;]*;\s*([A-Za-z_]\w*)\+\+\s*\)", line)
    return [line[:mt.start(1)] + "++" + mt.group(1) + line[mt.end(1) + 2:]] if mt else []


def _op_compound(line: str) -> list[str]:
    mt = re.search(r"\b([A-Za-z_]\w*)\s*=\s*\1\s*([+\-*])\s*([^;]+);", line)
    if not mt:
        return []
    return [line[:mt.start()] + f"{mt.group(1)} {mt.group(2)}= {mt.group(3).strip()};" + line[mt.end():]]


OPS: dict[str, tuple[Callable[[str], list[str]], set[str], set[str]]] = {
    # 名称: (变异函数, 相关技能, 相关错误类型)
    "init_first": (_op_init_first, {"C06.03.02", "C06.03.03"}, {"TY09", "AL03"}),
    "loop_start": (_op_loop_start, {"C06.03.02", "C05.04.01", "C06.02.02", "C06.02.01", "C06.03.03"}, {"LP01", "AR04"}),
    "loop_bound": (_op_loop_bound, {"C05.04.01", "C05.04.02", "C06.02.03", "C06.02.01", "C05.03.01", "C06.03.01"},
                   {"LP01", "LP04", "AR01"}),
    "if_cmp": (_op_if_cmp, {"C04.01.02", "C06.03.03", "C04.02.01", "C04.02.02", "C03.02.01", "C06.04.01"}, {"BR04"}),
    "and_or": (_op_and_or, {"C03.03.01", "C03.03.02", "C03.02.02"}, {"BR03", "BR02"}),
    "drop_cast": (_op_drop_cast, {"C06.03.05", "C03.01.01", "C02.06.01", "C02.06.02"}, {"TY01", "TY04"}),
    "long_to_int": (_op_long_to_int, {"C02.01.02", "C02.01.01", "C12.05.01"}, {"TY02"}),
    "fmt": (_op_fmt, {"C01.02.02", "C01.03.02", "C01.02.03"}, {"IO02", "IO06"}),
    "acc_init": (_op_acc_init, {"C05.05.01", "C05.05.02", "C06.03.01", "C05.04.03"}, {"LP02", "AL02"}),
    "ok_flag": (_op_ok_flag, {"C05.04.03", "C12.04.01"}, {"AL02"}),
    "char_case": (_op_char_case, {"C08.04.02", "C08.04.03", "C08.04.01"}, {"ST05", "ST06"}),
    "incr_prefix": (_op_incr_prefix, set(), set()),   # 通常改了也还对：用来出“正确代码”的题
    "compound": (_op_compound, set(), set()),
}

REASON_BUG = {
    "init_first": "初值用 0 的话，数据都比 0 小（或都比 0 大）时，比较一次都不成立。初值要取数组里真实存在的数。",
    "loop_start": "循环从哪里开始，决定了第一个数有没有被处理到。",
    "loop_bound": "循环条件多一个或少一个等号，会多处理或少处理一项。",
    "if_cmp": "< 和 <= 只差在正好相等的那个值上，端点算不算在内要看题意。",
    "and_or": "&& 要求两边都成立，|| 只要一边成立，范围完全不同。",
    "drop_cast": "两个整数相除会先丢掉小数，要先转成 double 再除。",
    "long_to_int": "结果超过了 int 能表示的范围，要用 long long。",
    "fmt": "格式说明符要和变量的类型、题目要求的位数对上。",
    "acc_init": "累加的初值是 0，累乘的初值是 1，初值不对后面全错。",
    "ok_flag": "1 不是素数。n 等于 1 时循环一次都不执行，要单独处理。",
    "char_case": "大小写字母的编码差 32，减去的基准字母要和字符的大小写一致。",
}
REASON_OK = {
    "loop_start": "还对。从 0 开始只是多拿第一个数和自己比一次，结果不变。",
    "incr_prefix": "还对。这里 i++ 和 ++i 单独成一个表达式，效果一样。",
    "compound": "还对。x = x + y 和 x += y 是同一个意思。",
}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block_end(lines: list[str], start: int) -> int:
    """从 start 这一行开始的语句（可能是带花括号或不带花括号的块）到哪一行结束。"""
    line = lines[start]
    depth = line.count("{") - line.count("}")
    if depth > 0:
        j = start
        while j + 1 < len(lines) and depth > 0:
            j += 1
            depth += lines[j].count("{") - lines[j].count("}")
        return j
    if re.match(r"\s*(for|while|if|else)\b", line) and not line.rstrip().endswith(";"):
        j = start + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines):
            return _block_end(lines, j)
    return start


def excerpt_bounds(lines: list[str], idx: int) -> tuple[int, int] | None:
    """改动的那一行所在的一小段：包住它的循环或判断，再带上紧挨着的、给这些变量赋初值的一两行。"""
    start = idx
    for _ in range(2):   # 往外最多找两层块的开头
        k = start - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0 and _indent(lines[k]) < _indent(lines[start]) and re.match(r"\s*(for|while|if|else)\b", lines[k]):
            start = k
        else:
            break
    end = max(_block_end(lines, start), idx)
    for j in range(start, end + 1):   # 块里还有更深的块
        end = max(end, _block_end(lines, j))
    # 改动的是赋初值的语句：把紧接着用到这些变量的循环或判断一起带上，题目才完整
    assigned = set(re.findall(r"([A-Za-z_]\w*)\s*=(?!=)", "\n".join(lines[start:end + 1])))
    j = end + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if (assigned and j < len(lines) and _indent(lines[j]) == _indent(lines[start])
            and re.match(r"\s*(for|while|if)\b", lines[j])):
        new_end = _block_end(lines, j)
        for x in range(j, new_end + 1):
            new_end = max(new_end, _block_end(lines, x))
        used = set(re.findall(r"[A-Za-z_]\w*", "\n".join(lines[j:new_end + 1])))
        if (assigned - {"i", "j", "k"}) & used and new_end - start + 1 <= MAX_SHOWN_LINES:
            end = new_end
    names = set(re.findall(r"[A-Za-z_]\w*", "\n".join(lines[start:end + 1])))
    k = start - 1
    added = 0
    while k >= 0 and added < 2 and lines[k].strip() and _indent(lines[k]) == _indent(lines[start]):
        t = lines[k]
        lhs = re.findall(r"([A-Za-z_]\w*)\s*=(?!=)", t)
        if not lhs or re.match(r"\s*(for|while|if|return|printf|scanf)\b", t) or not (set(lhs) & names) or "{" in t:
            break
        start, k, added = k, k - 1, added + 1
    seg = lines[start:end + 1]
    if len(seg) > MAX_SHOWN_LINES or sum(x.count("{") for x in seg) != sum(x.count("}") for x in seg):
        return None
    return start, end


def _dedent(seg: list[str]) -> tuple[list[str], int]:
    cut = min((_indent(x) for x in seg if x.strip()), default=0)
    return [x[cut:] if x.strip() else "" for x in seg], cut


def _outputs(source: str, inputs: list[str]) -> list[str] | None:
    """运行，返回每组输入的输出；编译不过返回 None；运行出错的那组记成特殊值。"""
    res = run_program(source, inputs, cpu_seconds=1, memory_mb=128)
    if res.status != "ok":
        return None
    return [("\n".join(quiz._norm(r.stdout)) if r.status == "ok" else f"<{r.status}>") for r in res.runs]


def _relevant_ops(plan: Plan) -> list[str]:
    by_error = [op for op, (_f, _sk, errs) in OPS.items() if errs & set(plan.errors)]
    related = set(SKILLS.get(plan.skill, ("", ()))[1])
    by_skill = [op for op, (_f, sk, errs) in OPS.items() if plan.skill in sk or (errs & related)]
    generic = ["loop_bound", "if_cmp", "incr_prefix", "compound"]
    return list(dict.fromkeys(by_error + by_skill + (generic if plan.has_bug is False or not by_skill else [])))


def _own_sources(s: Session, plan: Plan) -> list[tuple[int, int, int, str, catalog.ProblemInfo]]:
    visible = catalog.visible_problems(s, plan.user_id, course_of(plan.skill) if plan.skill else None)
    subs = s.scalars(select(m.Submission).where(
        m.Submission.user_id == plan.user_id, m.Submission.judgement == "AC", m.Submission.detail_status == "done"
    ).order_by(m.Submission.submission_id.desc())).all()
    out, seen = [], set()
    for sub in subs:
        key = (sub.server_id, sub.problem_id)
        if key in seen or key not in visible:
            continue
        seen.add(key)
        label = current_problem_label(s, *key)
        skills = [label.primary_skill, *(label.secondary_skills or [])] if label else []
        if plan.skill not in skills:
            continue
        text = source_text(s, *key[:1], sub.submission_id)
        info = catalog.problem_info(s, *key)
        if text and info is not None:
            out.append((sub.server_id, sub.problem_id, sub.submission_id, text, info))
    out.sort(key=lambda x: ((x[0], x[1]) != plan.origin,))
    return out[:3]


def _save(s: Session, plan: Plan, **fields) -> m.QuizItem | None:
    item = m.QuizItem(code=f"gen-{fields.get('source', 'x')}-{uuid.uuid4().hex[:12]}", skill_code=plan.skill,
                      owner_user_id=plan.user_id, verified=False, **fields)
    ok, why = quiz.verify_item(item)
    if not ok:
        return None
    item.verified = True
    s.add(item)
    s.flush()
    return item


def from_own_code(s: Session, plan: Plan, rng: random.Random) -> m.QuizItem | None:
    for server_id, problem_id, submission_id, source, info in _own_sources(s, plan):
        lines = source.replace("\r\n", "\n").expandtabs(4).split("\n")
        source = "\n".join(lines)
        checks = make_inputs(s, server_id, problem_id, source, rng)
        if len(checks) < 3:
            continue
        expected = ["\n".join(quiz._norm(c["expected"])) for c in checks]
        inputs = [c["input"] for c in checks]
        muts: list[Mutation] = []
        for op in _relevant_ops(plan):
            fn = OPS[op][0]
            for idx, line in enumerate(lines):
                if not line.strip() or line.lstrip().startswith(("#", "//", "/*")):
                    continue
                for new in fn(line):
                    if new != line:
                        muts.append(Mutation(op, idx, line, new))
        harmful, benign = [], []
        tried = 0
        for mu in muts:
            if tried >= 10 or (harmful and benign):
                break
            if (plan.has_bug and harmful) or (not plan.has_bug and benign and harmful):
                break
            tried += 1
            mutated = lines[:mu.line] + [mu.new] + lines[mu.line + 1:]
            outs = _outputs("\n".join(mutated), inputs)
            if outs is None:
                continue
            (benign if outs == expected else harmful).append((mu, outs))
        pick = harmful if plan.has_bug else benign
        if not pick:
            continue
        mu, outs = pick[0]
        bounds = excerpt_bounds(lines, mu.line)
        if bounds is None:
            continue
        start, end = bounds
        orig_seg, cut = _dedent(lines[start:end + 1])
        mut_lines = lines[:mu.line] + [mu.new] + lines[mu.line + 1:]
        mut_seg, _ = _dedent(mut_lines[start:end + 1])
        template = "\n".join(lines[:start] + [" " * cut + PLACEHOLDER] + lines[end + 1:])
        rel = mu.line - start + 1
        caption = f"你在「{info.title}」里的代码，第 {start + 1} 到 {end + 1} 行"
        if plan.has_bug:
            bad = next(i for i, (a, b) in enumerate(zip(outs, expected)) if a != b)
            got = outs[bad] if not outs[bad].startswith("<") else "（运行出错）"
            example = f"比如输入 {checks[bad]['show']} 时，改动后输出 {_squash(got)[:30]}，原来是 {_squash(expected[bad])[:30]}。"
            explanation = (f"第 {mu.line + 1} 行原来是 {mu.old.strip()}。" + REASON_BUG.get(mu.op, "") + example)[:160]
        else:
            explanation = REASON_OK.get(mu.op, "还对。改动前后，对各种输入的结果都一样。")
        common = dict(
            quiz_type=plan.quiz_type, error_code=(plan.errors[0] if plan.errors else ""), caption=caption,
            template=template, checks=checks, explanation=explanation, scene=info.title, source="own_code",
            source_problem_id=problem_id, source_server_id=server_id, start_line=start + 1,
            origin={"kind": "own_code", "op": mu.op, "submission": submission_id, "mark_line": rel,
                    "changed_from": mu.old.strip(), "changed_to": mu.new.strip(), "plan": plan.notes})
        if plan.quiz_type == "JUDGE":
            stem = info.brief if info.brief and len(info.brief) <= 40 else f"这段代码能完成「{info.title[:25]}」的要求吗？"
            item = _save(s, plan, stem=stem, shown_code="\n".join(mut_seg), has_bug=plan.has_bug,
                         bug_line=rel if plan.has_bug else None, bug_lines=[rel] if plan.has_bug else [],
                         standard_fix="\n".join(orig_seg) if plan.has_bug else "", **common)
        else:
            choices = []
            if plan.has_bug:
                choices = _choices(lines, start, end, cut, mu, inputs, expected)
            item = _save(s, plan, stem="这段代码被改动了一处，现在还对吗？", shown_code="\n".join(mut_seg),
                         has_bug=plan.has_bug, bug_line=rel if plan.has_bug else None,
                         bug_lines=[rel] if plan.has_bug else [], standard_fix="\n".join(orig_seg) if plan.has_bug else "",
                         choices=choices, **common)
        if item is not None:
            return item
    return None


def _choices(lines: list[str], start: int, end: int, cut: int, mu: Mutation, inputs: list[str],
             expected: list[str]) -> list[dict]:
    """手机上的选择题：第一个是正确改法（把那一行改回去），另外两三个是验证过会错的改法。"""
    rel = mu.line - start + 1
    out = [{"label": mu.old.strip(), "edits": {str(rel): mu.old[cut:]}}]
    mutated = lines[:mu.line] + [mu.new] + lines[mu.line + 1:]
    seen = {mu.old.strip(), mu.new.strip()}
    for op, (fn, _sk, _er) in OPS.items():
        if len(out) >= 4:
            break
        for idx in range(start, end + 1):
            if len(out) >= 4:
                break
            for alt in fn(mutated[idx]):
                if alt.strip() in seen or (idx == mu.line and alt == mu.old):
                    continue
                trial = mutated[:idx] + [alt] + mutated[idx + 1:]
                outs = _outputs("\n".join(trial), inputs)
                if outs is None or outs == expected:
                    continue
                seen.add(alt.strip())
                out.append({"label": alt.strip(), "edits": {str(idx - start + 1): alt[cut:]}})
                break
    return out if len(out) >= 3 else []


# ============================================================ 大模型出题
_SYSTEM = """你在给一名学 C 语言课程的大学生出一道很短的检验小题。只输出 JSON：
{"stem":"题干，不超过 40 字，只问一件事","caption":"代码块标题，如“这段代码想求最大值”",
 "code":"显示给学生的代码，3 到 10 行，不含头文件和读入输出","correct_code":"改对以后的代码；code 本来就对时和 code 一样",
 "template":"完整可编译的 C 程序：头文件、变量声明、读入、输出；显示的那段代码所在的位置写一行 /*<<CODE>>*/",
 "inputs":["4 到 6 组输入，每组是程序从标准输入读到的完整内容，要覆盖容易出错的情况"],
 "explanation":"不超过 60 字，说清楚为什么",
 "choices":["正确改法后的完整 code","另一种看起来像但不对的 code","再一种不对的 code"]}
要求：code 里用给出的变量名和场景；只考给出的那个技能；不要输出期望结果（程序会自己运行算）；
不用 system、文件、网络；不出现题号；不用 emoji。"""


def _own_mistake(s: Session, user_id: str, skill: str) -> str:
    """他以前在这个技能上写错的地方：最近一次有行号的标注，摘出前后两行（去掉学号姓名）。出题时参考易错点，不照抄。"""
    row = s.execute(
        select(m.SubmissionLabel.server_id, m.SubmissionLabel.submission_id, m.SubmissionLabel.line)
        .join(m.Submission, (m.Submission.server_id == m.SubmissionLabel.server_id)
              & (m.Submission.submission_id == m.SubmissionLabel.submission_id))
        .where(m.Submission.user_id == user_id, m.SubmissionLabel.skill_code == skill,
               m.SubmissionLabel.is_current.is_(True), m.SubmissionLabel.line.is_not(None))
        .order_by(m.Submission.submission_id.desc()).limit(1)).first()
    if row is None:
        return ""
    lines = (source_text(s, row.server_id, row.submission_id) or "").replace("\r\n", "\n").split("\n")
    if not 1 <= row.line <= len(lines):
        return ""
    a, b = max(0, row.line - 3), min(len(lines), row.line + 2)
    student = s.get(m.Student, user_id)
    return llm.strip_identity("\n".join(f"{i + 1:>3}| {lines[i]}" for i in range(a, b)), user_id,
                              student.name if student else "")


def from_llm(s: Session, plan: Plan, rng: random.Random) -> m.QuizItem | None:
    if not llm.available():
        return None
    names = _student_names(s, plan)
    scene = ""
    if plan.reason == "review":
        scene = "用一个新的简短场景，针对给定的技能和易错点，不复用原作业题意。"
    elif plan.origin:
        info = catalog.problem_info(s, *plan.origin)
        scene = info.brief if info else ""

    def validate(d: dict) -> dict:
        for k in ("stem", "code", "correct_code", "template", "inputs"):
            if not d.get(k):
                raise ValueError(f"缺 {k}")
        if len(str(d["stem"])) > 40 or len(str(d["code"]).split("\n")) > MAX_SHOWN_LINES:
            raise ValueError("题干或代码太长")
        if str(d["template"]).count(PLACEHOLDER) != 1:
            raise ValueError("模板里要正好有一个占位符")
        if not isinstance(d["inputs"], list) or not 3 <= len(d["inputs"]) <= 8:
            raise ValueError("inputs 要 3 到 8 组")
        if check_reference_solution(quiz.build_program(d["template"], d["correct_code"])[0]) is not None:
            raise ValueError("模板里有不允许的内容")
        if check_editable_code(str(d["code"]), config.QUIZ["max_code_chars"]) is not None:
            raise ValueError("代码里有不允许的内容")
        return d

    mistake = _own_mistake(s, plan.user_id, plan.skill)
    errors = [n for n in dict.fromkeys(error_name(c) for c in plan.errors) if n]
    user = (f"【技能】{skill_name(plan.skill)}\n【他在这个技能上现在是】{plan.level}\n"
            f"【他犯过的错误】{'、'.join(errors) or '没有记录'}\n"
            + (f"【他以前在这里写错的地方（只参考易错点，不照抄、不复用原题）】\n{mistake}\n" if mistake else "")
            + ("【他的作答习惯】常常很有把握却答错\n" if plan.overconfident else "") +
            f"【这道题的代码要{'有一处错' if plan.has_bug else '是对的，但带一个容易被误认为错的写法'}】\n"
            f"【题型】{'判断对错' if plan.quiz_type == 'JUDGE' else '判断对错，有错的话自己改'}\n"
            f"【他代码里的变量名】{json.dumps(names, ensure_ascii=False)}\n【场景】{scene or '自选一个和这个技能有关的日常场景'}")
    try:
        d = llm.call_json("quizgen", course_prompt(_SYSTEM, course_of(plan.skill or "C")), user, validate,
                          retries=1, max_tokens=1200)
    except llm.LLMUnavailable:
        return None
    correct = str(d["correct_code"]).strip("\n")
    code = str(d["code"]).strip("\n")
    inputs = [str(x) if str(x).endswith("\n") else str(x) + "\n" for x in d["inputs"]][:6]
    outs = _outputs(quiz.build_program(d["template"], correct)[0], inputs)
    if outs is None or any(o.startswith("<") for o in outs):
        return None
    checks = [{"input": i, "expected": o, "show": i.strip().replace("\n", "，")} for i, o in zip(inputs, outs)]
    is_bug = code != correct
    if is_bug != plan.has_bug:
        return None
    changed, _ = quiz.changed_lines(code, correct)
    choices = []
    for k, alt in enumerate(d.get("choices") or []):
        alt = str(alt).strip("\n")
        if len(alt.split("\n")) == len(code.split("\n")):
            diff = {str(i + 1): b for i, (a, b) in enumerate(zip(code.split("\n"), alt.split("\n"))) if a != b}
            if diff:
                choices.append({"label": " / ".join(v.strip() for v in diff.values())[:60], "edits": diff})
    if choices and choices[0]["edits"] != {str(i + 1): b for i, (a, b) in
                                           enumerate(zip(code.split("\n"), correct.split("\n"))) if a != b}:
        choices = []
    llm.flush_records(s)
    return _save(s, plan, quiz_type=plan.quiz_type, error_code=(plan.errors[0] if plan.errors else ""),
                 stem=str(d["stem"]), caption=str(d.get("caption") or "")[:60], shown_code=code,
                 template=str(d["template"]), has_bug=is_bug, bug_line=changed[0] if changed else None,
                 bug_lines=changed, standard_fix=correct if is_bug else "", checks=checks,
                 choices=choices if plan.quiz_type == "FIX" and is_bug else [],
                 explanation=str(d.get("explanation") or "")[:160], scene=scene[:24], source="llm",
                 origin={"kind": "llm", "plan": plan.notes})


# ============================================================ 题库的题换成他的变量名
def _student_names(s: Session, plan: Plan) -> dict:
    srcs = _own_sources(s, plan)
    if not srcs:
        return {}
    text = srcs[0][3]
    out = {}
    arrays = re.findall(r"\b(?:int|double|float|long long|char)\s+([A-Za-z_]\w*)\s*\[", text)
    if arrays:
        out["a"] = arrays[0]
    mn = re.search(r'scanf\s*\(\s*"\s*%l{0,2}d\s*"\s*,\s*&\s*([A-Za-z_]\w*)\s*\)', text)
    if mn:
        out["n"] = mn.group(1)
    mi = re.search(r"for\s*\(\s*(?:int\s+)?([A-Za-z_]\w*)\s*=", text)
    if mi:
        out["i"] = mi.group(1)
    for key in ("max", "min"):
        mv = re.search(rf"\b([A-Za-z_]\w*)\s*=\s*{re.escape(out.get('a', 'a'))}\[0\]", text)
        if mv:
            out[key] = mv.group(1)
    return {k: v for k, v in out.items() if v != k}


def from_template(s: Session, plan: Plan, rng: random.Random) -> m.QuizItem | None:
    names = _student_names(s, plan)
    if not names:
        return None
    bank = s.scalars(select(m.QuizItem).where(
        m.QuizItem.skill_code == plan.skill, m.QuizItem.source == "bank", m.QuizItem.verified.is_(True))).all()
    pool = [b for b in bank if b.has_bug == plan.has_bug]
    rng.shuffle(pool)
    for base in pool:
        used = set(re.findall(r"[A-Za-z_]\w*", base.template + base.shown_code))
        mapping = {k: v for k, v in names.items() if k in used and v not in used}
        if not mapping:
            continue

        def ren(text: str) -> str:
            for k, v in mapping.items():
                text = re.sub(rf"(?<![\w%]){re.escape(k)}(?!\w)", v, text)
            return text

        choices = [{"label": ren(c["label"]), "edits": {ln: ren(t) for ln, t in c["edits"].items()}}
                   for c in (base.choices or [])]
        item = _save(s, plan, quiz_type=base.quiz_type, error_code=base.error_code, stem=ren(base.stem),
                     caption=base.caption, shown_code=ren(base.shown_code), template=ren(base.template),
                     has_bug=base.has_bug, bug_line=base.bug_line, bug_lines=list(base.bug_lines or []),
                     judged_code=ren(base.judged_code or ""), standard_fix=ren(base.standard_fix or ""),
                     checks=base.checks, choices=choices, explanation=ren(base.explanation), scene=base.scene,
                     source="template", origin={"kind": "template", "base": base.code, "names": mapping,
                                                "plan": plan.notes})
        if item is not None:
            return item
    return None


def from_bank(s: Session, plan: Plan, rng: random.Random) -> m.QuizItem | None:
    return quiz.choose_item(s, plan.user_id, plan.skill, rng, has_bug=plan.has_bug)


# ============================================================ 入口
def generate(s: Session, user_id: str, skill: str, reason: str, origin: tuple[int, int] | None = None,
             review_item: m.ReviewItem | None = None, rng: random.Random | None = None,
             trace: dict | None = None) -> m.QuizItem | None:
    """给这个学生出一道检验 skill 的小题。出不了返回 None。
    trace：传一个 dict 进来，填上这次的出题计划和试了哪些出题器（决策记录用）。"""
    rng = rng or quiz._rng
    plan = make_plan(s, user_id, skill, reason, origin, review_item, rng)
    if trace is not None:
        trace.update(plan=plan, tried=[])
    order: list[Callable] = []
    if plan.prefer_own:
        order.append(from_own_code)
    if llm.available():
        order.append(from_llm)
    order += [from_template, from_bank]
    for gen in order:
        try:
            item = gen(s, plan, rng)
        except Exception:  # 出题失败不能影响对话和复习
            import logging

            logging.getLogger("quizgen").exception("出题器 %s 出错", gen.__name__)
            item = None
        if trace is not None:
            trace["tried"].append(gen.__name__)
        if item is not None:
            if trace is not None:
                trace["madeBy"] = gen.__name__
            return item
    return None
