"""同步用到的小工具：时间、编码、哈希、去掉敏感字段。题面识别在 app/pdftext.py。"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
from typing import Any

from ..db import BEIJING, UTC

# 名单等接口会带回学生的密码、令牌、登录 IP。凡是键名像这些的字段，在内存里就丢掉。
_SENSITIVE_KEY = re.compile(r"(pass|pwd|token|secret|session|cookie|salt|loginip|lastip|(^|_)ip$|ipaddr)", re.I)


def scrub(value: Any) -> Any:
    """递归去掉敏感字段，返回可以安全保存的副本。"""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not _SENSITIVE_KEY.search(str(k))}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def pick(d: dict, *names: str, default=None):
    """按候选键名取第一个存在且不为 None 的值。XDOJ 各接口的字段名不完全统一。"""
    for name in names:
        if name in d and d[name] is not None:
            return d[name]
    return default


def to_int(value, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def parse_time(value) -> dt.datetime | None:
    """XDOJ 的时间按北京时间解析，存成带时区的时间。也接受毫秒时间戳。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e11 else float(value)
        return dt.datetime.fromtimestamp(seconds, UTC)
    text = str(value).strip()
    if text.isdigit():
        return parse_time(int(text))
    text = re.sub(r"\.\d+$", "", text)
    for fmt in _TIME_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=BEIJING).astimezone(UTC)
        except ValueError:
            continue
    try:
        parsed = dt.datetime.fromisoformat(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=BEIJING)).astimezone(UTC)
    except ValueError:
        return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def detect_encoding(data: bytes) -> str:
    """检测编码，只用来以后解码显示；字节本身原样保存。

    学生的源码不是 UTF-8 就基本是 GBK（Windows 上的编辑器），所以先试 GB18030，再交给通用检测。
    """
    if not data:
        return "utf-8"
    for enc in ("utf-8", "gb18030"):
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best and best.encoding:
            return best.encoding
    except Exception:
        pass
    return "binary"


def decode_bytes(data: bytes | None, encoding: str = "") -> tuple[str | None, bool]:
    """按记录的编码解码。返回 (文字, 是否解码成功)；失败时文字是 base64。"""
    if data is None:
        return None, True
    for enc in (encoding, "utf-8", "gb18030"):
        if not enc or enc == "binary":
            continue
        try:
            return data.decode(enc), True
        except (UnicodeDecodeError, LookupError):
            continue
    return base64.b64encode(data).decode("ascii"), False


def first_diff_line(a: str, b: str) -> int | None:
    """两段文字第一处不同在第几行（1 起）；完全相同返回 None。"""
    la, lb = a.splitlines(keepends=True), b.splitlines(keepends=True)
    for i in range(max(len(la), len(lb))):
        if i >= len(la) or i >= len(lb) or la[i] != lb[i]:
            return i + 1
    return None
