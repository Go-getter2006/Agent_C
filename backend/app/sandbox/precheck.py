"""进沙箱之前的静态检查，以及把 gcc 的报错翻成一句中文。"""
from __future__ import annotations

import re

# 小题里学生能改的那段代码不允许出现的东西：预处理指令、进程、文件、网络、内联汇编。
_FORBIDDEN_WORDS = (
    "system", "popen", "fork", "vfork", "execl", "execlp", "execle", "execv", "execvp", "execve",
    "fopen", "freopen", "fdopen", "open", "openat", "creat", "remove", "rename", "unlink",
    "socket", "connect", "bind", "listen", "accept", "syscall", "signal", "raise", "kill",
    "setjmp", "longjmp", "dlopen", "mmap", "asm", "__asm__", "__asm", "ptrace", "getenv",
)
_FORBIDDEN_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(map(re.escape, _FORBIDDEN_WORDS)) + r")(?![A-Za-z0-9_])")

# 参考解允许包含的头文件
ALLOWED_HEADERS = {
    "stdio.h", "stdlib.h", "string.h", "math.h", "ctype.h", "limits.h", "stdbool.h", "float.h",
    "stdint.h", "stddef.h", "assert.h",
}
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]', re.M)


def check_editable_code(code: str, max_chars: int) -> str | None:
    """检查小题里学生改过的代码。有问题返回给学生看的一句话，没问题返回 None。"""
    if len(code) > max_chars:
        return "代码太长了，只需要改一两处。"
    if "#" in code:
        return "这段代码里不需要用到 # 开头的指令。"
    m = _FORBIDDEN_RE.search(code)
    if m:
        return f"这道小题用不到 {m.group(1)}，去掉它再试。"
    return None


def check_reference_solution(source: str) -> str | None:
    """检查大模型写的参考解：只能包含标准头文件，不能有进程、文件、网络调用。"""
    if len(source) > 20000:
        return "too long"
    for quote, header in _INCLUDE_RE.findall(source):
        if quote == '"' or header.strip() not in ALLOWED_HEADERS:
            return f"header {header}"
    m = _FORBIDDEN_RE.search(source)
    if m:
        return f"forbidden {m.group(1)}"
    return None


_DIAG_RE = re.compile(r"^[^:\n]*:(\d+):(?:\d+:)?\s*(?:fatal )?error:\s*(.+)$", re.M)


def first_compile_error(diag: str) -> tuple[int | None, str]:
    m = _DIAG_RE.search(diag or "")
    if not m:
        return None, (diag or "").strip().splitlines()[0] if (diag or "").strip() else ""
    return int(m.group(1)), m.group(2).strip()


def _name_in(msg: str) -> str:
    m = re.search(r"['‘`]([^'’`]+)['’`]", msg)
    return m.group(1) if m else ""


def compile_error_zh(diag: str, line_offset: int = 0, shown_lines: int | None = None) -> str:
    """把 gcc 的第一条报错翻成一句中文。line_offset：显示的代码在完整程序里从第几行开始（0 起）。"""
    line, msg = first_compile_error(diag)
    where = ""
    if line is not None:
        shown = line - line_offset
        if shown_lines is not None:
            shown = max(1, min(shown, shown_lines))
        if shown >= 1:
            where = f"第 {shown} 行附近"
    low = msg.lower()
    name = _name_in(msg)
    if low.startswith("expected") and ("';'" in low or "‘;’" in low):
        if "before" in low and where:
            where = where.replace("附近", "之前")
            what = "少了分号（看看上一行的结尾）"
        else:
            what = "少了分号"
    elif "expected ')'" in low or "expected ‘)’" in low or "expected '('" in low:
        what = "圆括号没有配对"
    elif "expected '}'" in low or "expected declaration or statement at end of input" in low or "expected ‘}’" in low:
        what = "花括号没有配对"
    elif "undeclared" in low:
        what = f"变量 {name} 没有声明" if name else "用了没有声明的变量"
    elif "implicit declaration" in low:
        what = f"函数 {name} 没有声明，可能是名字拼错了" if name else "用了没有声明的函数"
    elif "stray" in low:
        what = "有中文标点或别的不认识的字符"
    elif "redefinition" in low or "redeclaration" in low:
        what = f"变量 {name} 重复定义了" if name else "有变量重复定义了"
    elif "incompatible" in low or "too few arguments" in low or "too many arguments" in low:
        what = "类型或参数个数对不上"
    elif "lvalue required" in low:
        what = "等号左边不是能赋值的变量"
    elif "expected expression" in low or "expected identifier" in low:
        what = "语句没有写完整"
    else:
        what = "有语法问题"
    return f"{where}{what}，编译没有通过。" if where else f"代码{what}，编译没有通过。"
