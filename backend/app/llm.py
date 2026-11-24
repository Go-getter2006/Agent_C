"""大模型网关（DeepSeek，OpenAI 兼容接口）。

统一做这几件事：结构化输出、程序校验、重试、提示词带版本号、记录耗时和费用。
发给大模型的内容不带学号和姓名；源码里的姓名学号先去掉（strip_identity）。
没有配置 key 时 available() 为假，调用方走规则或固定模板。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from contextvars import ContextVar
from typing import Callable, TypeVar

import httpx

from . import config, db, pdftext
from . import models as m

log = logging.getLogger("llm")
T = TypeVar("T")

# 测试时可以换成假的后端：fn(messages, json_mode, max_tokens, temperature) -> (text, tokens_in, tokens_out)
_backend: Callable | None = None
last_model: ContextVar[str] = ContextVar("last_successful_llm_model", default="")
# 调大模型之前先做的事（后台任务用 db.releasing 设：先把已经写的提交，不拿着 SQLite 的写锁等大模型）
before_call: ContextVar[Callable[[], None] | None] = ContextVar("llm_before_call", default=None)


class LLMUnavailable(RuntimeError):
    """没有配置大模型，或者调用、校验反复失败。调用方要有不依赖大模型的退路。"""

    invalid = False   # True：连上了、但输出反复不合格（换个时间再问多半还是不合格）；False：没配置、没连上
    status: int | None = None

    def student_message(self) -> str:
        if self.status == 402:
            return "答疑服务额度不足，请联系管理员。"
        if self.status in (400, 401, 403, 404, 422) or not available():
            return "答疑服务配置异常，请联系管理员。"
        if self.status == 429:
            return "答疑服务请求过多，请稍后再试。"
        if self.invalid:
            return "答疑服务未能生成有效回复，请重新发送。"
        return "暂时未能取得答疑服务的回复，请稍后再试。"


def set_backend(fn: Callable | None) -> None:
    global _backend
    _backend = fn


def available() -> bool:
    return _backend is not None or bool(config.DEEPSEEK_API_KEY)


def _http_backend(messages, json_mode: bool, max_tokens: int, temperature: float, *, model: str | None = None,
                  non_thinking: bool = False):
    payload = {"model": model or config.DEEPSEEK_MODEL, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if non_thinking and config.OFFICIAL_DEEPSEEK and payload["model"] == "deepseek-flash":
        payload["thinking"] = {"type": "disabled"}
    resp = httpx.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions", json=payload, timeout=60,
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"})
    if resp.status_code != 200:
        err = LLMUnavailable(f"大模型接口返回 {resp.status_code}")
        err.status = resp.status_code
        raise err
    data = resp.json()
    usage = data.get("usage") or {}
    return (data["choices"][0]["message"]["content"] or "",
            int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))


_pending: list[dict] = []
_pending_lock = threading.Lock()


def _record(prompt_id: str, ok: bool, attempts: int, started: float, tin: int, tout: int, error: str = "",
            model: str | None = None) -> None:
    """先记在内存里，由调用方在自己的事务里写库（flush_records），避免另开连接和正在进行的事务互相等待。"""
    cost = (tin * config.LLM_PRICE_IN + tout * config.LLM_PRICE_OUT) / 1_000_000
    with _pending_lock:
        _pending.append(dict(
            ts=db.now(), prompt_id=prompt_id, prompt_version=config.PROMPT_VERSION, model=model or config.DEEPSEEK_MODEL,
            ok=ok, attempts=attempts, duration_ms=int((time.monotonic() - started) * 1000),
            tokens_in=tin, tokens_out=tout, cost_yuan=round(cost, 6), error=error[:250]))


def flush_records(s) -> int:
    """把还没落库的调用记录写进当前事务。"""
    with _pending_lock:
        rows, _pending[:] = list(_pending), []
    for row in rows:
        s.add(m.LlmCall(**row))
    return len(rows)


def call_json(prompt_id: str, system: str, user: str, validate: Callable[[dict], T],
              retries: int = 1, max_tokens: int = 800, *, pages: list[tuple[int, bytes]] | None = None,
              text_fallback: bool = True, images: list[tuple[str, bytes]] | None = None,
              document: str = "", non_thinking: bool = False) -> T:
    """要求输出 JSON，并用 validate 校验（不合格就抛 ValueError）。不合格时带着原因重试。"""
    if not available():
        raise LLMUnavailable("没有配置大模型")
    if "json" not in system.lower():
        system += "\n只输出一个 JSON 对象，不要输出别的内容。"
    attachments = images or [(f"公开题面第 {n} 页（图文按原位置，精确输出以评测记录为准）", png) for n, png in pages or []]
    if attachments and config.DEEPSEEK_VISION_MODEL.lower() not in ("", "off"):
        parts = [{"type": "text", "text": document or user}]
        for label, png in attachments:
            data = base64.b64encode(png).decode("ascii")
            parts.extend([{"type": "text", "text": label},
                          {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}", "detail": "high"}}])
        if document:
            parts.append({"type": "text", "text": user})
        try:
            return _json_request(prompt_id, system, parts, validate, retries, max_tokens, config.DEEPSEEK_VISION_MODEL,
                                 non_thinking)
        except LLMUnavailable as exc:
            if not text_fallback or exc.status in (401, 402, 403, 429):
                raise
    if attachments:
        system += "\n本次没有附上可用的题面图片。只根据文字和已有评测证据回答，不能推测图案。"
    if document:
        user = document + "\n\n" + user
    return _json_request(prompt_id, system, user, validate, retries, max_tokens, config.DEEPSEEK_MODEL, non_thinking)


def _json_request(prompt_id, system, user, validate, retries, max_tokens, model, non_thinking=False):
    hook = before_call.get()
    if hook is not None:
        hook()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    started, tin, tout, last, invalid = time.monotonic(), 0, 0, "", False
    for attempt in range(1, retries + 2):
        try:
            if _backend:
                text, a, b = _backend(messages, True, max_tokens, 0.0)
            else:
                text, a, b = _http_backend(messages, True, max_tokens, 0.0, model=model, non_thinking=non_thinking)
            tin, tout = tin + a, tout + b
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("输出不是 JSON 对象")
            result = validate(data)
            _record(prompt_id, True, attempt, started, tin, tout, model=model)
            last_model.set(model)
            return result
        except ValueError as exc:
            last, invalid = f"校验不通过：{exc}", True
            messages = messages[:2] + [{"role": "user", "content": f"上一次的输出不合格（{exc}）。请严格按要求重新输出。"}]
        except LLMUnavailable as exc:
            if exc.status is None or exc.status < 500 or attempt > retries or isinstance(user, list):
                _record(prompt_id, False, attempt, started, tin, tout, str(exc), model=model)
                raise
            time.sleep(0.5 * attempt)
        except Exception as exc:  # 网络、接口错误
            last, invalid = f"{type(exc).__name__}", False
            if isinstance(user, list):
                break  # 视觉接口不可用，本次仍可用普通模型分析代码与评测证据
            time.sleep(0.5 * attempt)
    _record(prompt_id, False, attempt, started, tin, tout, last, model=model)
    err = LLMUnavailable(last)
    err.invalid = invalid
    raise err


def evidence_for_model(evidence: dict, user_id: str = "", name: str = "") -> dict:
    """保留诊断所需的题面和评测证据，姓名、学号等身份字段不进入模型上下文。源码由调用方单独编号。"""
    fields = ("testcaseId", "runId", "result", "resultCode", "passed", "runTimeMs", "memoryKb",
              "standardInput", "standardOutput", "programOutput", "outputTruncated", "firstDiffLine", "base64")
    return {
        "problemText": pdftext.for_llm(strip_identity(evidence.get("problemText") or "", user_id, name)),
        "judgement": evidence.get("judgement"),
        "judgementCode": evidence.get("judgementCode"),
        "compileOutput": strip_identity(evidence.get("compileOutput") or "", user_id, name),
        "testCases": [{**{key: row[key] for key in fields if key in row},
                       "standardInputAvailable": row.get("standardInput") is not None,
                       "standardOutputAvailable": row.get("standardOutput") is not None,
                       "programOutputAvailable": row.get("programOutput") is not None}
                      for row in evidence.get("testCases", [])],
    }


def failed_cases(evidence: dict, user_id: str = "", name: str = "", limit: int = 3, chars: int = 400) -> list[dict]:
    """没通过的测试点（只给大模型分析用，回复里不能出现）：结果、输入、期望输出、他的输出；过长的截断。"""
    def cut(v):
        return v if not isinstance(v, str) or len(v) <= chars else v[:chars] + f"…（共 {len(v)} 字）"

    rows = [t for t in evidence_for_model(evidence, user_id, name)["testCases"] if t.get("passed") is False][:limit]
    keys = ("result", "standardInput", "standardOutput", "programOutput", "firstDiffLine", "outputTruncated")
    return [{k: cut(row[k]) for k in keys if k in row} for row in rows]


def numbered(source: str, user_id: str = "", name: str = "", limit: int = 80) -> str:
    """带行号的源码（去掉学号姓名），最多 limit 行。"""
    lines = strip_identity(source or "", user_id, name).replace("\r\n", "\n").split("\n")
    out = "\n".join(f"{i + 1:>3}| {x}" for i, x in enumerate(lines[:limit]))
    return out + (f"\n（后面还有 {len(lines) - limit} 行）" if len(lines) > limit else "")


# ------------------------------------------------------------ 去掉身份信息
_COMMENT_RE = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/")
_IDENTITY_HINT = re.compile(r"\d{6,}|姓名|学号|班级|作者|name|author|student|@", re.I)


def strip_identity(text: str, user_id: str = "", name: str = "") -> str:
    """发给大模型之前：去掉学号、姓名；注释里像是身份信息的整条去掉。"""
    if not text:
        return text

    def clean_comment(match: re.Match) -> str:
        body = match.group(0)
        if _IDENTITY_HINT.search(body) or (name and name in body) or (user_id and user_id in body):
            return "/* */" if body.startswith("/*") else "//"
        return body

    out = _COMMENT_RE.sub(clean_comment, text)
    for secret in (user_id, name):
        if secret and len(secret) >= 2:
            out = out.replace(secret, "某同学")
    return out
