"""Auth API routes: register, login, refresh, logout, me."""

import math
from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response, status

from app.auth import service
from app.auth.rate_limit import SlidingWindowLimiter, login_limiter, register_limiter
from app.auth.schemas import LoginRequest, RegisterRequest, UserOut
from app.core.config import settings
from app.core.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CurrentUser,
    SessionDep,
    user_from_token,
)
from app.core.errors import AppError
from app.core.security import create_token, token_ttl
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_PATH = "/api/auth"


def _set_auth_cookies(response: Response, user: User) -> None:
    for name, kind, path in (
        (ACCESS_COOKIE, "access", "/"),
        (REFRESH_COOKIE, "refresh", REFRESH_COOKIE_PATH),
    ):
        response.set_cookie(
            name,
            create_token(user.id, kind),
            max_age=int(token_ttl(kind).total_seconds()),
            path=path,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )


def _clear_auth_cookies(response: Response) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_COOKIE_PATH)):
        response.delete_cookie(
            name, path=path, httponly=True, secure=settings.cookie_secure, samesite="lax"
        )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many_requests(retry_after: float) -> AppError:
    return AppError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "too_many_requests",
        "Bạn đã thử quá nhiều lần. Vui lòng thử lại sau.",
        headers={"Retry-After": str(math.ceil(retry_after))},
    )


def _enforce(limiter: SlidingWindowLimiter, key: str, *, record: bool) -> None:
    retry_after = limiter.hit(key) if record else limiter.retry_after(key)
    if retry_after is not None:
        raise _too_many_requests(retry_after)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, session: SessionDep, response: Response
) -> UserOut:
    _enforce(register_limiter, _client_ip(request), record=True)
    user = await service.create_user(session, body.email, body.password)
    _set_auth_cookies(response, user)
    return UserOut.model_validate(user)


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, session: SessionDep, response: Response
) -> UserOut:
    client_ip = _client_ip(request)
    _enforce(login_limiter, client_ip, record=False)
    try:
        user = await service.authenticate(session, body.email, body.password)
    except AppError as exc:
        if exc.code == "invalid_credentials":
            login_limiter.hit(client_ip)
        raise
    _set_auth_cookies(response, user)
    return UserOut.model_validate(user)


@router.post("/refresh", status_code=status.HTTP_204_NO_CONTENT)
async def refresh(
    session: SessionDep,
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> None:
    user = await user_from_token(session, refresh_token, "refresh")
    _set_auth_cookies(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    # Stateless JWTs: logout clears the cookies; tokens expire on their own (see TASKS.md).
    _clear_auth_cookies(response)


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
