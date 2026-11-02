"""Auth business logic: register, authenticate, admin creation."""

import asyncio
import uuid

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import burn_password_check, hash_password, verify_password
from app.db.models import User

EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


# Factories, not module-level instances: re-raising one shared exception object keeps
# appending frames (and their locals, e.g. passwords) to its __traceback__.
def invalid_credentials() -> AppError:
    return AppError(
        status.HTTP_401_UNAUTHORIZED, "invalid_credentials", "Email hoặc mật khẩu không đúng."
    )


def email_taken() -> AppError:
    return AppError(status.HTTP_409_CONFLICT, "email_taken", "Email này đã được đăng ký.")


def _is_email_conflict(exc: IntegrityError) -> bool:
    # asyncpg's UniqueViolationError is the cause of SQLAlchemy's adapted DBAPI error.
    cause = exc.orig.__cause__ if exc.orig is not None else None
    return getattr(cause, "constraint_name", None) == EMAIL_UNIQUE_CONSTRAINT


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email))


async def create_user(session: AsyncSession, email: str, password: str, role: str = "user") -> User:
    """Insert and commit a user. Raises `email_taken` if the email exists (case-insensitive)."""
    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(email=email, password_hash=password_hash, role=role)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _is_email_conflict(exc):
            raise email_taken() from None
        raise
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    user = await get_user_by_email(session, email)
    if user is None:
        await asyncio.to_thread(burn_password_check, password)
        raise invalid_credentials()
    valid, new_hash = await asyncio.to_thread(verify_password, password, user.password_hash)
    if not valid:
        raise invalid_credentials()
    if not user.is_active:
        raise AppError(status.HTTP_403_FORBIDDEN, "account_disabled", "Tài khoản đã bị khoá.")
    if new_hash is not None:
        user.password_hash = new_hash
        await session.commit()
    return user


async def get_active_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    user = await session.get(User, user_id)
    return user if user is not None and user.is_active else None
