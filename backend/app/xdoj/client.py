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
    """登录失败，或重新登录后会话仍然无效。应当停下报错。"""


class ClientError(XdojError):
    """4xx：不重试。"""

    def __init__(self, status: int, endpoint: str):
        super().__init__(f"{endpoint} 返回 {status}")
        self.status = status


class RetryExhausted(XdojError):
    """网络错误、超时、5xx、429 重试用尽。"""


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
        self.request_count = 0
        self.consecutive_failures = 0

    def __repr__(self) -> str:  # 不把账号密码带进任何输出
        return f"XdojClient({self.base_url!r})"

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ 底层
    def _send_once(self, method: str, path: str, params, data) -> httpx.Response:
        key = (method.upper(), path)
        if key not in WHITELIST:
            raise NotAllowed(f"接口不在白名单里，已拒绝：{method.upper()} {path}")
        url = self.base_url + (path if path != "/" else "/")
        with self.limiter:
            started = time.time()
            t0 = time.monotonic()
            status, ok = 0, False
            try:
                resp = self._http.request(method.upper(), url, params=params, data=data)
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

    def _send_with_retry(self, method: str, path: str, params, data) -> httpx.Response:
        delay = 1.0
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._send_once(method, path, params, data)
            except NotAllowed:
                raise
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = XdojError(f"{path} 返回 {resp.status_code}")
                    retry_after = resp.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        delay = max(delay, float(retry_after))
                else:
                    return resp
            if attempt < self.max_retries:
                self._sleep(delay)
                delay = min(delay * 2, 60.0)
        raise RetryExhausted(f"{path} 重试 {self.max_retries} 次后仍失败：{type(last).__name__}")

    # ------------------------------------------------------------ 登录
    def login(self) -> None:
        """先 GET / 拿到会话，再 POST /user/login.do。error 不为 0 就报错。"""
        self._send_with_retry("GET", "/", None, None)
        resp = self._send_with_retry(
            "POST", "/user/login.do", None,
            {"userId": self._user_id, "password": self._password, "preferLanguage": "zh_CN"},
        )
        try:
            body = resp.json()
        except ValueError as exc:
            raise SessionError("登录接口返回的不是 JSON") from exc
        if not isinstance(body, dict) or body.get("error") != 0:
            raise SessionError("XDOJ 登录失败：账号或密码不对，或账号被停用")
        self.logged_in = True

    @staticmethod
    def _looks_logged_out(resp: httpx.Response, expect: str) -> bool:
        if resp.status_code in (301, 302, 303, 307, 401):
            return True
        if expect == "json":
            try:
                resp.json()
            except ValueError:
                return True
        return False

    # ------------------------------------------------------------ 对外
    def request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None,
                expect: str = "json") -> httpx.Response:
        """发一个白名单内的请求。会话失效时重新登录一次再试；仍失效就抛 SessionError。"""
        if (method.upper(), path) not in WHITELIST:
            raise NotAllowed(f"接口不在白名单里，已拒绝：{method.upper()} {path}")
        if not self.logged_in:
            self.login()
        resp = self._send_with_retry(method, path, params, data)
        if self._looks_logged_out(resp, expect):
            self.logged_in = False
            self.login()
            resp = self._send_with_retry(method, path, params, data)
            if self._looks_logged_out(resp, expect):
                raise SessionError(f"{path}：重新登录后会话仍然无效")
        if 400 <= resp.status_code < 500:
            raise ClientError(resp.status_code, path)
        return resp

    def get_json(self, path: str, params: dict | None = None):
        return self.request("GET", path, params=params).json()

    def post_json(self, path: str, data: dict | None = None):
        return self.request("POST", path, data=data).json()

    def get_bytes(self, path: str, params: dict | None = None) -> tuple[bytes, str]:
        resp = self.request("GET", path, params=params, expect="bytes")
        return resp.content, resp.headers.get("content-type", "")
