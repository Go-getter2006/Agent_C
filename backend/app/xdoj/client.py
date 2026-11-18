"""XDOJ 客户端：会话、登录、限速、重试、接口白名单。

硬性规则：
1. 只读。白名单外的路径一律拒绝，不发请求。
2. 全局串行限速：同一台服务器的所有数据源共用一个限速器，同一时刻只有一个请求。
3. 日志只记接口、状态码、耗时；不记参数、密码、源码、输出。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .. import config

log = logging.getLogger("xdoj.client")

# 允许访问的接口。除了这里列出的，任何路径都不会被请求。
WHITELIST: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/"),  # 只为拿到会话 Cookie
    ("POST", "/user/login.do"),
    ("GET", "/admin/dashboard.do"),  # 只读教师姓名（页面右上角）
    ("POST", "/class/list.do"),
    ("POST", "/user/listStudents.do"),
    ("POST", "/contest/list.do"),
    ("GET", "/contest/problems.do"),
    ("POST", "/problem/list.do"),
    ("GET", "/problem/viewTextFile.do"),
    ("GET", "/testcase/list.do"),
    ("GET", "/testcase/downloadInputFile.do"),
    ("GET", "/testcase/downloadOutputFile.do"),
    ("POST", "/submission/list.do"),
    ("GET", "/submission/listJudge.do"),
    ("POST", "/submission/listAllJudgeRun.do"),
    ("POST", "/submission/getRunOutput.do"),
    ("GET", "/submission/downloadSource.do"),
})


class XdojError(RuntimeError):
    """请求失败的基类。"""


class NotAllowed(XdojError):
    """白名单外的接口。"""


class SessionError(XdojError):
    """登录本身失败（账号或密码不对、账号停用、登录接口不对劲）。应当停下报错。

    注意：某一个接口拒绝了请求（400、返回的不是 JSON、重新登录后仍被转到登录页）不是这个错，
    是 ResponseError：只让这一个任务失败、过一会儿重试，不能让整个后台同步停下来。
    """


class ClientError(XdojError):
    """4xx：不重试。"""

    def __init__(self, status: int, endpoint: str):
        super().__init__(f"{endpoint} 返回 {status}")
        self.status = status


class RetryExhausted(XdojError):
    """网络错误、超时、5xx、429 重试用尽。"""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class ResponseError(XdojError):
    """接口回的东西不对：该是 JSON 却不是、重新登录后这个接口仍然转到登录页。只记状态码和类型，不记内容。"""

    def __init__(self, endpoint: str, status: int, what: str):
        super().__init__(f"{endpoint} 返回 {status}：{what}")
        self.status = status


class RateLimiter:
    """全局串行：一次只放行一个请求，相邻两次请求的开始时间至少相隔 min_interval 秒。"""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = max(config.XDOJ_MIN_INTERVAL_FLOOR, float(min_interval))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_start: float | None = None

    def __enter__(self):
        self._lock.acquire()
        if self._last_start is not None:
            wait = self.min_interval - (self._clock() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._clock()
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


_limiters: dict[str, RateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(base_url: str, min_interval: float | None = None) -> RateLimiter:
    """同一台服务器（按地址）只有一个限速器。"""
    key = httpx.URL(base_url).host or base_url
    with _limiters_lock:
        if key not in _limiters:
            _limiters[key] = RateLimiter(min_interval or config.XDOJ_MIN_INTERVAL)
        return _limiters[key]


@dataclass
class RequestRecord:
    endpoint: str
    status_code: int
    duration_ms: int
    ok: bool
    started_at: float


class XdojClient:
    def __init__(
        self,
        base_url: str,
        user_id: str,
        password: str,
        *,
        limiter: RateLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        on_request: Callable[[RequestRecord], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._user_id = user_id
        self._password = password
        self.limiter = limiter or limiter_for(self.base_url)
        self._on_request = on_request
        self._sleep = sleep
        self.max_retries = max_retries if max_retries is not None else config.SYNC["max_retries"]
        self._http = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(config.XDOJ_READ_TIMEOUT, connect=config.XDOJ_CONNECT_TIMEOUT),
            headers={"User-Agent": "xdoj-sync/1.0 (read-only)"},
        )
        self.logged_in = False
        self.user_type = None
        self.grid_styles: dict[str, int] = {}  # 子表接口试通的参数写法（见 endpoints.grid_json）
        self.request_count = 0
        self.consecutive_failures = 0

    def __repr__(self) -> str:  # 不把账号密码带进任何输出
        return f"XdojClient({self.base_url!r})"

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ 底层
    def _send_once(self, method: str, path: str, params, data, ajax: bool = False) -> httpx.Response:
        key = (method.upper(), path)
        if key not in WHITELIST:
            raise NotAllowed(f"接口不在白名单里，已拒绝：{method.upper()} {path}")
        url = self.base_url + (path if path != "/" else "/")
        with self.limiter:
            started = time.time()
            t0 = time.monotonic()
            status, ok = 0, False
            try:
                # 和网页一样：页面里用 jQuery 取的 JSON 接口都带这个头；下载文件是直接打开链接，不带
                headers = {"X-Requested-With": "XMLHttpRequest"} if ajax else None
                resp = self._http.request(method.upper(), url, params=params, data=data, headers=headers)
                status = resp.status_code
                ok = 200 <= status < 300
                return resp
            finally:
                self.request_count += 1
                self.consecutive_failures = 0 if ok else self.consecutive_failures + 1
                rec = RequestRecord(path, status, int((time.monotonic() - t0) * 1000), ok, started)
                log.info("%s %s -> %s (%d ms)", method.upper(), path, status, rec.duration_ms)
                if self._on_request:
                    try:
                        self._on_request(rec)
                    except Exception:  # 记日志失败不能影响同步
                        log.exception("写请求日志失败")

    def _send_with_retry(self, method: str, path: str, params, data, ajax: bool = False,
                         retries: int | None = None) -> httpx.Response:
        delay = 1.0
        last: Exception | None = None
        last_status = 0
        retries = self.max_retries if retries is None else retries
        for attempt in range(retries + 1):
            try:
                resp = self._send_once(method, path, params, data, ajax)
            except NotAllowed:
                raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = XdojError(f"{path} 返回 {resp.status_code}")
                    last_status = resp.status_code
                    retry_after = resp.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = max(delay, float(retry_after))
                else:
                    return resp
            if attempt < retries:
                self._sleep(delay)
                delay = min(delay * 2, 60.0)
        what = f"状态码 {last_status}" if last_status else type(last).__name__
        raise RetryExhausted(f"{path} 重试 {retries} 次后仍失败：{what}", last_status)

    # ------------------------------------------------------------ 登录
    def login(self) -> None:
        """先 GET / 拿到会话，再 POST /user/login.do。error 不为 0 就报错。"""
        self._send_with_retry("GET", "/", None, None)
        resp = self._send_with_retry(
            "POST", "/user/login.do", None,
            {"userId": self._user_id, "password": self._password, "preferLanguage": "zh_CN"}, ajax=True,
        )
        try:
            body = resp.json()
        except ValueError as exc:
            raise SessionError("登录接口返回的不是 JSON") from exc
        if not isinstance(body, dict) or body.get("error") != 0:
            raise SessionError("XDOJ 登录失败：账号或密码不对，或账号被停用")
        self.user_type = body.get("userType")  # 1 = 教师
        self.logged_in = True

    def teacher_name(self) -> str | None:
        """从教师首页右上角读姓名：.user-info 里第一个 <small> 是姓名，第二个是账号。对不上就返回 None。"""
        try:
            resp = self._send_with_retry("GET", "/admin/dashboard.do", None, None)
        except XdojError:
            return None
        if resp.status_code != 200:
            return None
        import html
        import re

        block = re.search(r'class="[^"]*user-info[^"]*"(.*?)</(?:span|div|a)>', resp.text, re.S)
        if not block:
            return None
        smalls = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
                  for x in re.findall(r"<small[^>]*>(.*?)</small>", block.group(1), re.S)]
        if len(smalls) >= 2 and smalls[1] == self._user_id and smalls[0]:
            return smalls[0][:64]
        return None

    @staticmethod
    def _sent_to_login(resp: httpx.Response) -> bool:
        """会话失效：被转到登录页（302，或 200 但内容就是登录页）。

        只有这种情况才重新登录。400、500 这类错误页、返回的不是 JSON，都不是会话的问题：以前把它们也当成
        “掉线了”，重新登录后照样不对就报“会话无效”，整个后台同步停下来、过 30 秒重启、又卡在同一个任务上。
        """
        if resp.status_code in (301, 302, 303, 307, 308, 401):
            return True
        if resp.status_code == 200 and "html" in resp.headers.get("content-type", "").lower():
            head = resp.text[:20000]
            return "loginform" in head or "/user/login.do" in head
        return False

    # ------------------------------------------------------------ 对外
    def request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None,
                expect: str = "json", retry: bool = True) -> httpx.Response:
        """发一个白名单内的请求。被转到登录页时重新登录一次再试。

        - 登录本身失败：SessionError（停下报错）；
        - 这个接口拒绝了请求（4xx）：ClientError；5xx、网络问题重试用尽：RetryExhausted；
        - 该是 JSON 却不是、重新登录后仍被转到登录页：ResponseError。
        后三种只让当前任务失败，过一会儿重试。retry=False 时 5xx 不重试（试参数写法时用）。
        """
        if (method.upper(), path) not in WHITELIST:
            raise NotAllowed(f"接口不在白名单里，已拒绝：{method.upper()} {path}")
        if not self.logged_in:
            self.login()
        ajax = expect == "json"
        retries = None if retry else 0
        resp = self._send_with_retry(method, path, params, data, ajax, retries)
        if self._sent_to_login(resp):
            self.logged_in = False
            self.login()
            resp = self._send_with_retry(method, path, params, data, ajax, retries)
            if self._sent_to_login(resp):
                raise ResponseError(path, resp.status_code, "重新登录后这个接口仍然转到登录页")
        if 400 <= resp.status_code < 500:
            raise ClientError(resp.status_code, path)
        if expect == "json":
            try:
                resp.json()
            except ValueError:
                ctype = resp.headers.get("content-type", "").split(";")[0] or "没有类型"
                raise ResponseError(path, resp.status_code, f"不是 JSON（{ctype}）") from None
        return resp

    def get_json(self, path: str, params: dict | None = None, retry: bool = True):
        return self.request("GET", path, params=params, retry=retry).json()

    def post_json(self, path: str, data: dict | None = None, retry: bool = True):
        return self.request("POST", path, data=data, retry=retry).json()

    def get_bytes(self, path: str, params: dict | None = None) -> tuple[bytes, str]:
        resp = self.request("GET", path, params=params, expect="bytes")
        return resp.content, resp.headers.get("content-type", "")
