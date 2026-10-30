"""Passwords and tokens.

Passwords: scrypt from the standard library (memory-hard, no extra dependency), stored as
`scrypt$<log2 N>$<r>$<p>$<salt>$<hash>` so the cost can be raised later without breaking old hashes.
Access tokens: short-lived signed JWTs (HS256) that carry only the user id. Roles and departments
are read from the database on every request, so a change takes effect immediately.
Refresh tokens: random strings. Only their SHA-256 is stored, so a database leak does not leak them.
"""

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

import jwt

from app.core.config import Settings

_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
_ALGORITHM = "HS256"


class TokenError(Exception):
    """An access token was missing, malformed, expired or not ours. `reason` is a short code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ----------------------------------------------------------------------------- passwords


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _scrypt(password: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    n = 2**log2_n
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_DKLEN,
        maxmem=256 * n * r + 1024 * 1024,
    )


def hash_password(password: str, cost_log2: int = 15) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, cost_log2, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${cost_log2}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """True only for the right password. Never raises on a malformed stored hash."""
    try:
        scheme, log2_n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = _scrypt(password, base64.b64decode(salt), int(log2_n), int(r), int(p))
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def burn_password_check(password: str, cost_log2: int) -> None:
    """Do the same work as a real check, for logins with an unknown email, so the response time
    does not reveal which emails exist."""
    verify_password(password, _dummy_hash(cost_log2))


_DUMMY_HASHES: dict[int, str] = {}


def _dummy_hash(cost_log2: int) -> str:
    if cost_log2 not in _DUMMY_HASHES:
        _DUMMY_HASHES[cost_log2] = hash_password("not-a-real-password", cost_log2)
    return _DUMMY_HASHES[cost_log2]


# ----------------------------------------------------------------------------- access tokens


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_in: int  # seconds


def _secret(settings: Settings) -> str:
    secret = settings.jwt_secret_key.get_secret_value() if settings.jwt_secret_key else ""
    if not secret:
        raise TokenError("auth_not_configured")
    return secret


def create_access_token(user_id: str, settings: Settings) -> AccessToken:
    now = int(time.time())
    lifetime = settings.access_token_minutes * 60
    claims = {
        "sub": user_id,
        "typ": "access",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + lifetime,
        "jti": uuid.uuid4().hex,
    }
    return AccessToken(jwt.encode(claims, _secret(settings), algorithm=_ALGORITHM), lifetime)


def decode_access_token(token: str, settings: Settings) -> str:
    """Return the user id in a valid access token, or raise TokenError."""
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[_ALGORITHM],  # a fixed list, so "alg: none" and key-confusion tricks fail
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid_token") from exc
    if claims.get("typ") != "access" or not isinstance(claims["sub"], str):
        raise TokenError("invalid_token")
    return claims["sub"]


# ----------------------------------------------------------------------------- refresh tokens


def new_refresh_token() -> tuple[str, str]:
    """A fresh random token and its hash. Give the token to the client, store only the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
