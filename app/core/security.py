"""Password hashing and access tokens.

* Passwords: scrypt (standard library) with a random salt per password, compared in constant time.
* Tokens: signed JWT (HS256) with an expiry. The signing key comes from SECRET_KEY, or from a random
  key that is created once and stored in backend/.secret_key (git-ignored).
"""
import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.core.config import settings

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD_LENGTH = 10
ALGORITHM = "HS256"
ROLES = ("admin", "engineer", "viewer")

_KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".secret_key")
_key_cache: Optional[str] = None


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def validate_new_password(password: str) -> Optional[str]:
    """Returns an error message, or None when the password is acceptable."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if password.strip() != password:
        return "Password must not start or end with a space."
    return None


def _secret_key() -> str:
    global _key_cache
    if settings.SECRET_KEY:
        return settings.SECRET_KEY
    if _key_cache:
        return _key_cache
    if os.path.exists(_KEY_FILE):
        _key_cache = open(_KEY_FILE, encoding="utf-8").read().strip()
    if not _key_cache:
        _key_cache = secrets.token_urlsafe(48)
        with open(_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(_key_cache)
    return _key_cache


def create_access_token(username: str, role: str, minutes: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=minutes if minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": exp}, _secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Payload of a valid, unexpired token; None otherwise."""
    try:
        return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
