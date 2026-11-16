"""大模型网关（DeepSeek，OpenAI 兼容接口）。

统一做这几件事：结构化输出、程序校验、重试、提示词带版本号、记录耗时和费用。
发给大模型的内容不带学号和姓名；源码里的姓名学号先去掉（strip_identity）。
没有配置 key 时 available() 为假，调用方走规则或固定模板。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, TypeVar

import httpx

from . import config, db
from . import models as m

log = logging.getLogger("llm")
T = TypeVar("T")

# 测试时可以换成假的后端：fn(messages, json_mode, max_tokens, temperature) -> (text, tokens_in, tokens_out)
_backend: Callable | None = None


class LLMUnavailable(RuntimeError):
    """没有配置大模型，或者调用、校验反复失败。调用方要有不依赖大模型的退路。"""


def set_backend(fn: Callable | None) -> None:
    global _backend
    _backend = fn


def available() -> bool:
    return _backend is not None or bool(config.DEEPSEEK_API_KEY)


def _http_backend(messages, json_mode: bool, max_tokens: int, temperature: float):
    payload = {"model": config.DEEPSEEK_MODEL, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = httpx.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions", json=payload, timeout=60,
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"})
    if resp.status_code != 200:
        raise RuntimeError(f"大模型接口返回 {resp.status_code}")
    data = resp.json()
    usage = data.get("usage") or {}
    return (data["choices"][0]["message"]["content"] or "",
            int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))


def _record(prompt_id: str, ok: bool, attempts: int, started: float, tin: int, tout: int, error: str = "") -> None:
    cost = (tin * config.LLM_PRICE_IN + tout * config.LLM_PRICE_OUT) / 1_000_000
    try:
        with db.session_scope() as s:
            s.add(m.LlmCall(prompt_id=prompt_id, prompt_version=config.PROMPT_VERSION, model=config.DEEPSEEK_MODEL,
                            ok=ok, attempts=attempts, duration_ms=int((time.monotonic() - started) * 1000),
                            tokens_in=tin, tokens_out=tout, cost_yuan=round(cost, 6), error=error[:250]))
    except Exception:  # 记账失败不影响主流程
        log.exception("记录大模型调用失败")


def call_json(prompt_id: str, system: str, user: str, validate: Callable[[dict], T],
              retries: int = 1, max_tokens: int = 800) -> T:
    """要求输出 JSON，并用 validate 校验（不合格就抛 ValueError）。不合格时带着原因重试。"""
    if not available():
        raise LLMUnavailable("没有配置大模型")
    backend = _backend or _http_backend
    if "json" not in system.lower():
        system += "\n只输出一个 JSON 对象，不要输出别的内容。"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    started, tin, tout, last = time.monotonic(), 0, 0, ""
    for attempt in range(1, retries + 2):
        try:
            text, a, b = backend(messages, True, max_tokens, 0.0)
            tin, tout = tin + a, tout + b
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("输出不是 JSON 对象")
            result = validate(data)
            _record(prompt_id, True, attempt, started, tin, tout)
            return result
        except ValueError as exc:
            last = f"校验不通过：{exc}"
            messages = messages[:2] + [{"role": "user", "content": f"上一次的输出不合格（{exc}）。请严格按要求重新输出。"}]
        except Exception as exc:  # 网络、接口错误
            last = f"{type(exc).__name__}"
            time.sleep(0.5 * attempt)
    _record(prompt_id, False, retries + 1, started, tin, tout, last)
    raise LLMUnavailable(last)


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
