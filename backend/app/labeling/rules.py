"""规则先行：识别信号明确的错误由程序直接给出编码，不交给大模型猜。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..sandbox.precheck import first_compile_error


@dataclass
class LabelDraft:
    error_code: str
    skill_code: str = ""
    role: str = "primary"
    confidence: float = 0.0
    line: int | None = None
    evidence: str = ""
    testcase_ids: list[int] = field(default_factory=list)
    source: str = "rule"


_CE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("CE05", ("stray",)),
    ("CE08", ("undefined reference to `main'", "undefined reference to 'main'", "undefined reference to main")),
    ("CE01", ("expected ';'", "expected ')'", "expected '}'", "expected '('", "expected ‘;’", "expected ‘)’",
              "expected ‘}’", "expected declaration or statement at end of input", "expected ',' or ';'",
              "expected '=', ',', ';'", "missing terminating")),
    ("CE02", ("undeclared",)),
    ("CE04", ("implicit declaration", "no such file or directory")),
    ("CE07", ("redefinition", "redeclaration", "conflicting types")),
    ("CE03", ("incompatible", "too few arguments", "too many arguments", "makes pointer from integer",
              "makes integer from pointer", "invalid operands")),
]


def label_compile_error(compile_output: str, source: str | None) -> LabelDraft:
    if source is not None and not source.strip():
        return LabelDraft("OT02", confidence=0.95, evidence="源码是空的")
    line, msg = first_compile_error(compile_output or "")
    low = (compile_output or "").lower()
    first = msg.lower()
    code = "CE06"
    for candidate, words in _CE_RULES:  # 先看第一条报错，再看全部输出
        if any(w in first for w in words):
            code = candidate
            break
    else:
        for candidate, words in _CE_RULES:
            if any(w in low for w in words):
                code = candidate
                break
    if source is not None and "main" not in source:
        code = "CE08"
    return LabelDraft(code, confidence=0.95, line=line, evidence=msg[:200])


_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _tokens(text: str) -> list[str]:
    return (text or "").split()


def _is_num(tok: str) -> bool:
    return bool(_NUM_RE.match(tok))


def _decimals(tok: str) -> int:
    return len(tok.split(".", 1)[1]) if "." in tok else 0


def label_wrong_answer(failed: list[dict]) -> LabelDraft | None:
    """failed：没通过的测试点，每项有 testcaseId、standardOutput、programOutput。

    只在所有失败测试点都符合同一个信号时才下结论。
    """
    cases = [c for c in failed if c.get("standardOutput") is not None and c.get("programOutput") is not None]
    if not cases or len(cases) != len(failed):
        return None
    ids = [c["testcaseId"] for c in cases]

    # IO04 输出格式不符：去掉空白后和标准输出一致
    if all(_squash(c["programOutput"]) == _squash(c["standardOutput"]) and _squash(c["standardOutput"])
           for c in cases):
        return LabelDraft("IO04", confidence=0.95, evidence="去掉空白后和标准输出一致", testcase_ids=ids)

    # IO05 输出了多余内容：输出里包含完整的标准输出，多出来的是文字（提示语、调试输出）
    def has_extra(c: dict) -> bool:
        want, got = _squash(c["standardOutput"]), _squash(c["programOutput"])
        if not want or len(got) <= len(want):
            return False
        at = got.rfind(want)
        if at < 0:
            return False
        before, after = got[:at], got[at + len(want):]
        # 标准输出不能只是某个更大的数的一部分（期望 5、输出 15 不算）
        if (before[-1:].isdigit() and want[:1].isdigit()) or (after[:1].isdigit() and want[-1:].isdigit()):
            return False
        return any(ch.isalpha() for ch in before + after)

    if all(has_extra(c) for c in cases):
        return LabelDraft("IO05", confidence=0.8, evidence="输出里包含标准输出，另有多余文字", testcase_ids=ids)

    # 下面两条按“数值”逐项比较
    def numeric_pairs(c: dict) -> list[tuple[str, str]] | None:
        want, got = _tokens(c["standardOutput"]), _tokens(c["programOutput"])
        if len(want) != len(got) or not want:
            return None
        return list(zip(want, got))

    pairs_all = [numeric_pairs(c) for c in cases]
    if all(p is not None for p in pairs_all):
        # IO06 小数位数或精度不符：数值相近，位数不同
        def precision_only(pairs) -> bool:
            differs = False
            for w, g in pairs:
                if w == g:
                    continue
                if not (_is_num(w) and _is_num(g)):
                    return False
                if _decimals(w) == _decimals(g):
                    return False
                d = min(_decimals(w), _decimals(g))
                if abs(float(w) - float(g)) > 0.5 * 10 ** (-d) + 1e-12:
                    return False
                differs = True
            return differs

        # TY01 整数除法：输出等于正确值截掉小数
        def truncated(pairs) -> bool:
            hit = False
            for w, g in pairs:
                if w == g:
                    continue
                if not (_is_num(w) and _is_num(g)):
                    return False
                wf, gf = float(w), float(g)
                if wf == math.trunc(wf) or gf != math.trunc(wf):
                    return False
                hit = True
            return hit

        if all(truncated(p) for p in pairs_all):
            return LabelDraft("TY01", confidence=0.85, evidence="输出等于正确值截掉小数", testcase_ids=ids)
        if all(precision_only(p) for p in pairs_all):
            return LabelDraft("IO06", confidence=0.85, evidence="数值相近，小数位数不同", testcase_ids=ids)
    return None


def label_by_rules(evidence: dict) -> LabelDraft | None:
    """evidence 是 assemble_submission 的结果。规则判不了返回 None。"""
    source = evidence.get("sourceCode")
    if evidence.get("judgementCode") == "CE" or (evidence.get("compileOutput") and not evidence.get("testCases")):
        return label_compile_error(evidence.get("compileOutput") or "", source)
    if source is not None and not source.strip():
        return LabelDraft("OT02", confidence=0.95, evidence="源码是空的")
    failed = [t for t in evidence.get("testCases", []) if t.get("passed") is False]
    if not failed:
        return None
    return label_wrong_answer(failed)
