"""文本小工具：识别贴的代码、取变量名、选引用的代码行、数字数。"""
from __future__ import annotations

import re

C_WORDS = {
    "int", "long", "short", "char", "float", "double", "unsigned", "signed", "void", "const", "static",
    "if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "return",
    "struct", "typedef", "sizeof", "enum", "union", "main", "printf", "scanf", "getchar", "putchar",
    "gets", "puts", "fgets", "strlen", "strcmp", "strcpy", "strcat", "sqrt", "pow", "fabs", "abs",
    "malloc", "free", "NULL", "EOF", "include", "define", "stdio", "stdlib", "string", "math", "h",
    "true", "false", "bool", "max", "min", "i", "j", "k", "n", "m",
}

_CODE_LINE = re.compile(
    r"(;\s*$|^\s*#\s*include|\{\s*$|^\s*\}|^\s*(int|long|double|float|char|void)\s+\w+|"
    r"\b(printf|scanf)\s*\(|^\s*(for|while|if)\s*\()")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DECL = re.compile(
    r"\b(?:unsigned\s+|signed\s+|const\s+|static\s+)*(?:long\s+long|long|int|short|double|float|char)\b([^;(){}]*);")
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]")


def split_message(text: str) -> tuple[str, str]:
    """把消息分成“说的话”和“贴的代码”。三个反引号围起来的、或连续几行像 C 代码的，算代码。"""
    text = (text or "").replace("\r\n", "\n").strip()
    fenced = re.findall(r"```(?:c|C|cpp)?\n?([\s\S]*?)```", text)
    if fenced:
        prose = re.sub(r"```(?:c|C|cpp)?\n?[\s\S]*?```", "\n", text).strip()
        return prose, "\n".join(part.strip("\n") for part in fenced).strip("\n")
    lines = text.split("\n")
    flags = [bool(_CODE_LINE.search(line)) for line in lines]
    if sum(flags) < 2:
        return text, ""
    first = flags.index(True)
    last = len(flags) - 1 - flags[::-1].index(True)
    code = "\n".join(lines[first:last + 1]).strip("\n")
    prose = "\n".join(lines[:first] + lines[last + 1:]).strip()
    return prose, code


def identifiers(source: str) -> set[str]:
    return set(_IDENT.findall(re.sub(r'"(\\.|[^"\\])*"', '""', source or "")))


def declared_vars(source: str) -> list[dict]:
    """声明的变量：名字、是不是数组、所在行。只做粗略的识别，够给模板取变量名用。"""
    out, seen = [], set()
    for line_no, line in enumerate((source or "").split("\n"), start=1):
        for match in _DECL.finditer(line):
            for part in match.group(1).split(","):
                part = re.sub(r"=.*$", "", part).strip()
                m = re.match(r"\**\s*([A-Za-z_]\w*)\s*(\[)?", part)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    out.append({"name": m.group(1), "array": bool(m.group(2)), "line": line_no})
    return out


def line_assignment(line: str) -> tuple[str, str] | None:
    """这一行给哪个变量赋了什么初值，如 `int max = 0;` → ("max", "0")。"""
    m = re.search(r"([A-Za-z_]\w*)\s*=\s*([^;,=][^;,]*)", line or "")
    return (m.group(1), m.group(2).strip()) if m else None


def excerpt_range(n_lines: int, line: int | None, lines: list[str], lo: int = 2, hi: int = 8) -> tuple[int, int] | None:
    """围绕出问题的那一行选 2~8 行引用：从这一行开始，往下带上紧接着的语句。"""
    if not n_lines or not line or not (1 <= line <= n_lines):
        return None
    start, end = line, line
    while end < n_lines and end - start + 1 < 3:
        end += 1
    while end < n_lines and end - start + 1 < hi and lines[end].startswith((" ", "\t")) \
            and len(lines[end]) - len(lines[end].lstrip()) > len(lines[line - 1]) - len(lines[line - 1].lstrip()):
        end += 1  # 把缩进更深的后续行（循环体）带上
    while end - start + 1 < lo and start > 1:
        start -= 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return start, end


def dedent(lines: list[str]) -> list[str]:
    """去掉这几行共同的缩进，展开制表符。"""
    lines = [line.expandtabs(4).rstrip() for line in lines]
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents) if indents else 0
    return [line[cut:] if line.strip() else "" for line in lines]


def visible_chars(text: str) -> int:
    """算回复长度：不计空白和反引号。"""
    return len(re.sub(r"[\s`]", "", text or ""))


def code_line_count(code: str | None) -> int:
    return len([line for line in (code or "").split("\n") if line.strip()])


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
