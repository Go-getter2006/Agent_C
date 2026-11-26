"""已知的 XDOJ 服务器。地址不是秘密，直接写在这里。

C 语言和数据结构用的是同一套 XDOJ 系统，部署在两台不同的服务器上，地址不同（《总说明》3.1）。
两台服务器的题号、提交编号、班级编号各自独立，系统里所有来自 XDOJ 的主键都带服务器标识。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m


@dataclass(frozen=True)
class KnownServer:
    code: str
    name: str
    course: str          # C：C 语言程序设计；D：数据结构
    base_url: str | None  # None：从课程环境配置读取地址
    basis: str           # 地址的出处


C_SERVER = KnownServer(
    code="c", name="C 语言程序设计", course="C",
    base_url="http://47.92.139.74/xdoj-ssm",
    basis="《给 ClaudeCode 的总说明》3.1：“C 语言：http://47.92.139.74/xdoj-ssm/”；"
          "《XDOJ 同步程序开发说明》第 4 节的接口路径也相对于这个地址",
)
DS_SERVER = KnownServer(
    code="ds", name="数据结构", course="D", base_url=None,
    basis="用户于 2026-11-26 提供数据结构地址 http://39.100.78.5/xdoj-ssm/；可用 XDOJ_DS_URL 覆盖",
)
KNOWN = {s.code: s for s in (C_SERVER, DS_SERVER)}


def normalize_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


class WrongServer(ValueError):
    """服务器地址和课程对不上。"""


def check_course_url(course: str, base_url: str | None, code: str = "") -> None:
    """数据结构的服务器不能用 C 语言那台的地址，反过来也一样；已知服务器的地址不能被改成别的。"""
    url = normalize_url(base_url)
    if not url:
        return
    if course != "C" and url == normalize_url(C_SERVER.base_url):
        raise WrongServer(f"{C_SERVER.base_url} 是 C 语言的 XDOJ，不能登记成{'数据结构' if course == 'D' else course}的服务器")
    from . import config

    if course != "D" and config.XDOJ_DS_URL and url == normalize_url(config.XDOJ_DS_URL):
        raise WrongServer(f"{config.XDOJ_DS_URL} 是数据结构的 XDOJ，不能登记成其他课程的服务器")
    known = KNOWN.get(code)
    if known is not None and known.base_url and url != normalize_url(known.base_url):
        raise WrongServer(f"服务器 {code} 的地址应当是 {known.base_url}")
    if known is not None and known.course != course:
        raise WrongServer(f"服务器 {code} 是{known.name}的，课程对不上")


def ensure_known(s: Session) -> dict[str, m.Server]:
    """把已知服务器写进库。数据结构使用配置中的地址，升级后补上旧库的空地址。"""
    from . import config

    out = {}
    for known in KNOWN.values():
        url = known.base_url or (config.XDOJ_DS_URL if known.code == "ds" else "")
        if url:
            check_course_url(known.course, url, known.code)
        row = s.scalar(select(m.Server).where(m.Server.code == known.code))
        if row is None:
            row = m.Server(code=known.code, name=known.name, course=known.course, base_url=url or "")
            s.add(row)
            s.flush()
        else:
            row.name, row.course = known.name, known.course
            if url:
                row.base_url = url
        out[known.code] = row
    return out
