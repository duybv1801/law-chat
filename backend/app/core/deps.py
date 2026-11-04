"""FastAPI dependencies: DB session, current user, admin guard."""

from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Request, status
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.core.errors import AppError
from app.core.security import TokenKind, decode_token
from app.db.models import User
from app.db.session import SessionFactory, get_session, get_session_factory

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]


def get_qdrant(request: Request) -> AsyncQdrantClient:
    """The client created in the app lifespan."""
    return request.app.state.qdrant


QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]


def not_authenticated() -> AppError:
    return AppError(status.HTTP_401_UNAUTHORIZED, "not_authenticated", "Bạn cần đăng nhập.")


async def user_from_token(session: AsyncSession, token: str | None, kind: TokenKind) -> User:
    """Return the active user a token of `kind` belongs to, else raise `not_authenticated`."""
    if not token:
        raise not_authenticated()
    try:
        user_id = decode_token(token, kind)
    except jwt.InvalidTokenError:
        raise not_authenticated() from None
    user = await service.get_active_user(session, user_id)
    if user is None:
        raise not_authenticated()
    return user


async def current_user(
    session: SessionDep,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> User:
    return await user_from_token(session, access_token, "access")


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise AppError(
            status.HTTP_403_FORBIDDEN, "forbidden", "Bạn không có quyền thực hiện thao tác này."
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
