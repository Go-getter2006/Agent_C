"""签名和加密。密钥都从 SECRET_KEY 派生。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _derive(purpose: str) -> bytes:
    return hashlib.sha256(f"{purpose}:{config.secret_key()}".encode("utf-8")).digest()


def encrypt_secret(plain: str) -> str:
    """教师的 XDOJ 密码：后台同步要长期使用，所以加密保存。"""
    return Fernet(base64.urlsafe_b64encode(_derive("datasource"))).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return Fernet(base64.urlsafe_b64encode(_derive("datasource"))).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("密文解不开：SECRET_KEY 可能换过") from exc


def sign_session(user_id: int, hours: int | None = None) -> str:
    exp = int(time.time()) + 3600 * (hours or config.SESSION_HOURS)
    body = f"{user_id}.{exp}"
    sig = hmac.new(_derive("session"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str) -> int | None:
    try:
        uid, exp, sig = token.split(".")
        body = f"{uid}.{exp}"
        good = hmac.new(_derive("session"), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good) or int(exp) < time.time():
            return None
        return int(uid)
    except (ValueError, AttributeError):
        return None
