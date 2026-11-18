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


def sign_session(user_id: int, course: str = "C", hours: int | None = None) -> str:
    """登录凭证：账号 + 这次登录的课程（C 语言、数据结构分开登录）+ 到期时间，带签名。"""
    exp = int(time.time()) + 3600 * (hours or config.SESSION_HOURS)
    body = f"{user_id}.{course}.{exp}"
    sig = hmac.new(_derive("session"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_session(token: str) -> tuple[int, str] | None:
    """返回 (账号, 课程)。签名不对、过期、旧格式的凭证都返回 None（重新登录一次就好）。"""
    try:
        uid, course, exp, sig = token.split(".")
        body = f"{uid}.{course}.{exp}"
        good = hmac.new(_derive("session"), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good) or int(exp) < time.time() or course not in ("C", "D"):
            return None
        return int(uid), course
    except (ValueError, AttributeError):
        return None
