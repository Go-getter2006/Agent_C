"""DeepSeek 调用（OpenAI 兼容接口）。没有配置 API Key 时自动进入模拟模式。

DeepSeek 的 JSON 模式：response_format={"type": "json_object"}，提示词里必须出现 “json” 字样；
偶尔会返回空内容，所以这里做了重试，失败时退回到规则版（模拟）结果，保证系统不中断。
"""
import json
import logging
import time

import requests

from . import config

log = logging.getLogger("ctutor.llm")


class LLMError(RuntimeError):
    pass


def is_mock() -> bool:
    return config.MOCK_LLM


def chat(messages, temperature=0.3, max_tokens=900, json_mode=False, timeout=60) -> str:
    if is_mock():
        raise LLMError("mock mode")
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(f"{config.DEEPSEEK_BASE_URL}/chat/completions", headers=headers,
                         json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise LLMError(f"DeepSeek 返回 {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise LLMError(f"无法解析返回结果: {str(data)[:300]}") from exc


def chat_json(system: str, user: str, fallback, retries: int = 2, max_tokens: int = 700) -> dict:
    """要求模型输出 json；失败时调用 fallback() 得到规则版结果，并标记 _source。"""
    if is_mock():
        out = fallback()
        out["_source"] = "mock"
        return out
    if "json" not in system.lower():
        system = system + "\n请只输出 json。"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err = None
    for attempt in range(retries + 1):
        try:
            text = chat(messages, temperature=0.0, max_tokens=max_tokens, json_mode=True)
            if text.strip():
                out = json.loads(text)
                if isinstance(out, dict):
                    out["_source"] = "llm"
                    return out
            last_err = "empty"
        except (LLMError, ValueError, requests.RequestException) as exc:
            last_err = str(exc)
        time.sleep(0.5 * (attempt + 1))
    log.warning("chat_json 失败，改用规则版结果：%s", last_err)
    out = fallback()
    out["_source"] = "fallback"
    out["_error"] = str(last_err)[:200]
    return out


def chat_text(messages, fallback, temperature=0.4, max_tokens=900) -> tuple:
    """返回 (文本, 来源)。"""
    if is_mock():
        return fallback(), "mock"
    try:
        text = chat(messages, temperature=temperature, max_tokens=max_tokens)
        if text.strip():
            return text, "llm"
    except (LLMError, requests.RequestException) as exc:
        log.warning("chat_text 失败：%s", exc)
    return fallback(), "fallback"
