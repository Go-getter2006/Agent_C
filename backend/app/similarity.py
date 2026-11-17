"""通过的代码之间的雷同。

做法：去掉注释、字符串内容和空白；关键字、库函数名保留；其余标识符按出现顺序换成 v1、v2……；
数字保留。这样只改变量名、改空格换行藏不住，但写法真的不同就不会算一样。

只在三个条件同时满足时才算可疑（suspicious）：
1. 和班里一条更早的、别人的通过代码规范化后完全一样；
2. 这种写法在已经通过的人里少见（不到 copy_rare_share）——人人都这么写的标准写法不算；
3. 两次提交的时间挨得近（copy_window_hours 以内），而且这是他在这道题上的第一次提交。
代码太短（不到 copy_min_tokens 个记号）的题不判。可疑只用来给这次通过的证据打折，并在教师端列出来，学生看不到。
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from . import models as m
from .xdoj.assemble import source_text

_KEEP = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum", "extern",
    "float", "for", "goto", "if", "int", "long", "register", "return", "short", "signed", "sizeof", "static",
    "struct", "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "bool", "true", "false",
    "printf", "scanf", "getchar", "putchar", "gets", "puts", "fgets", "strlen", "strcmp", "strcpy", "strcat",
    "strncpy", "strncmp", "memset", "memcpy", "malloc", "calloc", "free", "sqrt", "pow", "fabs", "abs", "main",
    "NULL", "EOF", "include", "define", "stdio", "stdlib", "string", "math", "ctype", "h", "isalpha", "isdigit",
    "isupper", "islower", "toupper", "tolower", "qsort", "exit",
}
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|&&|\|\||\+\+|--|->|[-+*/%<>=!&|^~?:;,.(){}\[\]#]")


def normalize(source: str) -> list[str]:
    text = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", " ", source or "")
    text = re.sub(r'"(\\.|[^"\\])*"', '"S"', text)
    text = re.sub(r"'(\\.|[^'\\])*'", "'C'", text)
    names: dict[str, str] = {}
    out = []
    for tok in _TOKEN.findall(text):
        if re.match(r"[A-Za-z_]", tok) and tok not in _KEEP:
            tok = names.setdefault(tok, f"v{len(names) + 1}")
        out.append(tok)
    return out


def fingerprint(source: str) -> tuple[str, int]:
    tokens = normalize(source)
    return hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest(), len(tokens)


def check_pass(s: Session, sub: m.Submission) -> m.CodeFingerprint | None:
    """算这条通过的提交的指纹，并判断是不是可疑的雷同。可以重复调用。"""
    row = s.get(m.CodeFingerprint, (sub.server_id, sub.submission_id))
    if row is not None:
        return row
    text = source_text(s, sub.server_id, sub.submission_id)
    if text is None:
        return None
    norm_hash, n_tokens = fingerprint(text)
    row = m.CodeFingerprint(server_id=sub.server_id, submission_id=sub.submission_id, user_id=sub.user_id,
                            problem_id=sub.problem_id, submit_time=sub.submit_time, norm_hash=norm_hash,
                            tokens=n_tokens)
    s.add(row)
    s.flush()
    cfg = config.MASTERY_V2
    if n_tokens < cfg["copy_min_tokens"]:
        return row
    same = s.scalars(select(m.CodeFingerprint).where(
        m.CodeFingerprint.server_id == sub.server_id, m.CodeFingerprint.problem_id == sub.problem_id,
        m.CodeFingerprint.norm_hash == norm_hash, m.CodeFingerprint.user_id != sub.user_id,
        m.CodeFingerprint.submission_id < sub.submission_id).order_by(m.CodeFingerprint.submission_id.desc())).all()
    if not same:
        return row
    passers = s.scalar(select(func.count(func.distinct(m.CodeFingerprint.user_id))).where(
        m.CodeFingerprint.server_id == sub.server_id, m.CodeFingerprint.problem_id == sub.problem_id,
        m.CodeFingerprint.submission_id <= sub.submission_id)) or 1
    writers = len({x.user_id for x in same}) + 1
    share = writers / passers
    nearest = same[0]
    row.matched_submission_id, row.matched_user_id, row.share = nearest.submission_id, nearest.user_id, round(share, 3)
    first_attempt = s.scalar(select(func.count()).select_from(m.Submission).where(
        m.Submission.server_id == sub.server_id, m.Submission.user_id == sub.user_id,
        m.Submission.problem_id == sub.problem_id, m.Submission.submission_id < sub.submission_id)) == 0
    close = (sub.submit_time is not None and nearest.submit_time is not None
             and 0 <= (sub.submit_time - nearest.submit_time).total_seconds() <= cfg["copy_window_hours"] * 3600)
    row.suspicious = bool(share < cfg["copy_rare_share"] and close and first_attempt)
    return row
