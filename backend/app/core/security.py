"""Password hashing (argon2 via pwdlib) and JWT helpers for the auth cookies."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Literal

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

TokenKind = Literal["access", "refresh"]
JWT_ALGORITHM = "HS256"

_password_hash = PasswordHash.recommended()  # argon2id


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    """Return (valid, new_hash). `new_hash` is set when the stored hash's parameters are
    outdated and should be replaced."""
    return _password_hash.verify_and_update(password, password_hash)


@cache
def _dummy_hash() -> str:
    return _password_hash.hash("dummy-password-for-timing")


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check when the user does not exist, so login timing
    does not reveal which emails are registered."""
    _password_hash.verify(password, _dummy_hash())


def token_ttl(kind: TokenKind) -> timedelta:
    if kind == "access":
        return timedelta(minutes=settings.jwt_access_ttl_min)
    return timedelta(days=settings.jwt_refresh_ttl_days)


def create_token(user_id: uuid.UUID, kind: TokenKind, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "type": kind,
        "iat": issued,
        "exp": issued + token_ttl(kind),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


def decode_token(token: str, kind: TokenKind) -> uuid.UUID:
    """Return the user id of a valid token of the given kind.

    Raises `jwt.InvalidTokenError` for bad signature, expiry, wrong kind or malformed subject.
    """
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "type", "exp"]},
    )
    if claims["type"] != kind:
        raise jwt.InvalidTokenError("wrong token type")
    try:
        return uuid.UUID(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("malformed subject") from exc
