import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from typer.testing import CliRunner

from app.auth import cli as auth_cli
from app.auth import service
from app.auth.rate_limit import LOGIN_MAX_ATTEMPTS, REGISTER_MAX_ATTEMPTS, SlidingWindowLimiter
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.db.models import User
from tests.frontend_sync import read_ts_source, ts_int_const

EMAIL = "an@example.com"
PASSWORD = "mat-khau-123"


async def _register(client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD):
    return await client.post("/api/auth/register", json={"email": email, "password": password})


def _error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


# --- pure logic ---------------------------------------------------------------------------


def test_password_hash_roundtrip() -> None:
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert PASSWORD not in hashed
    assert verify_password(PASSWORD, hashed)[0]
    assert not verify_password("wrong-password", hashed)[0]


def test_token_roundtrip_and_kind() -> None:
    user_id = uuid.uuid4()
    access = create_token(user_id, "access")
    assert decode_token(access, "access") == user_id
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(access, "refresh")  # an access token is not a refresh token
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(create_token(user_id, "refresh"), "access")


def test_token_expired_and_tampered() -> None:
    user_id = uuid.uuid4()
    old = create_token(user_id, "access", now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(old, "access")
    forged = jwt.encode(
        {"sub": str(user_id), "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
        "another-secret-that-is-at-least-32-chars",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(forged, "access")


def test_sliding_window_limiter() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(max_hits=3, window_s=10, clock=lambda: now[0])
    assert [limiter.hit("ip") for _ in range(3)] == [None, None, None]
    assert limiter.hit("ip") == pytest.approx(10)
    assert limiter.hit("other-ip") is None  # keys are independent
    now[0] = 9.5
    assert limiter.hit("ip") == pytest.approx(0.5)
    now[0] = 10.0  # the three hits at t=0 leave the window
    assert [limiter.hit("ip") for _ in range(3)] == [None, None, None]
    assert limiter.hit("ip") is not None


def test_limiter_retry_after_does_not_record() -> None:
    limiter = SlidingWindowLimiter(max_hits=1, window_s=10, clock=lambda: 0.0)
    assert [limiter.retry_after("ip") for _ in range(5)] == [None] * 5
    assert limiter.hit("ip") is None
    assert limiter.retry_after("ip") == pytest.approx(10)


def test_limiter_forgets_idle_keys() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(max_hits=3, window_s=10, clock=lambda: now[0])
    for i in range(100):
        limiter.hit(f"ip-{i}")
    assert len(limiter) == 100
    now[0] = 10.0
    limiter.hit("fresh")  # triggers the sweep of keys idle for a whole window
    assert len(limiter) == 1


def test_auth_errors_are_fresh_instances() -> None:
    assert service.invalid_credentials() is not service.invalid_credentials()
    assert service.email_taken() is not service.email_taken()


def test_auth_limits_match_frontend() -> None:
    """The register/login form mirrors these limits; keep frontend/src/api/auth.ts in sync."""
    from app.auth import schemas

    source = read_ts_source("api/auth.ts")
    for name in ("EMAIL_MAX", "PASSWORD_MIN", "PASSWORD_MAX"):
        assert ts_int_const(source, name) == getattr(schemas, name), name


# --- API ----------------------------------------------------------------------------------


async def test_register_me_logout_flow(client: httpx.AsyncClient) -> None:
    response = await _register(client, email="  An@Example.COM ")
    assert response.status_code == 201
    user = response.json()
    assert user["email"] == EMAIL and user["role"] == "user"
    assert set(user) == {"id", "email", "role"}

    cookies = response.headers.get_list("set-cookie")
    access = next(c for c in cookies if c.startswith("access_token="))
    refresh = next(c for c in cookies if c.startswith("refresh_token="))
    for cookie in (access, refresh):
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Path=/;" in access
    assert "Path=/api/auth" in refresh

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == user

    assert (await client.post("/api/auth/logout")).status_code == 204
    me = await client.get("/api/auth/me")
    assert me.status_code == 401
    assert _error_code(me) == "not_authenticated"


async def test_register_validation_and_duplicate(client: httpx.AsyncClient) -> None:
    short = await _register(client, password="1234567")
    assert short.status_code == 422
    assert _error_code(short) == "validation_error"
    assert "1234567" not in short.text  # input values are never echoed
    assert (await _register(client, email="not-an-email")).status_code == 422

    assert (await _register(client)).status_code == 201
    duplicate = await _register(client, email=EMAIL.upper())
    assert duplicate.status_code == 409
    assert _error_code(duplicate) == "email_taken"


async def test_login(client: httpx.AsyncClient) -> None:
    await _register(client)
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401

    wrong = await client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong-pw"})
    assert wrong.status_code == 401
    assert _error_code(wrong) == "invalid_credentials"
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert unknown.status_code == 401
    assert unknown.json() == wrong.json()  # no hint whether the email exists

    ok = await client.post("/api/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["email"] == EMAIL
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_login_inactive_user(client: httpx.AsyncClient, db_session) -> None:
    user_id = (await _register(client)).json()["id"]
    user = await db_session.get(User, uuid.UUID(user_id))
    user.is_active = False
    await db_session.commit()

    response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 403
    assert _error_code(response) == "account_disabled"
    # Existing access cookie stops working too.
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_refresh(client: httpx.AsyncClient) -> None:
    await _register(client)
    refresh_cookie = client.cookies.get("refresh_token", path="/api/auth")
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401

    client.cookies.set("refresh_token", refresh_cookie, path="/api/auth")
    response = await client.post("/api/auth/refresh")
    assert response.status_code == 204
    assert {c.split("=", 1)[0] for c in response.headers.get_list("set-cookie")} == {
        "access_token",
        "refresh_token",
    }
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_refresh_rejects_bad_tokens(client: httpx.AsyncClient) -> None:
    user_id = uuid.UUID((await _register(client)).json()["id"])
    access_cookie = client.cookies.get("access_token")
    client.cookies.clear()

    assert (await client.post("/api/auth/refresh")).status_code == 401  # no cookie
    for token in (access_cookie, "garbage", create_token(uuid.uuid4(), "refresh")):
        client.cookies.set("refresh_token", token, path="/api/auth")
        response = await client.post("/api/auth/refresh")
        assert response.status_code == 401, token
        assert _error_code(response) == "not_authenticated"
        client.cookies.clear()

    # A refresh token is not accepted as an access token.
    client.cookies.set("access_token", create_token(user_id, "refresh"))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_rate_limited_after_10_failures(client: httpx.AsyncClient) -> None:
    await _register(client)
    body = {"email": EMAIL, "password": "wrong-pw"}
    for _ in range(LOGIN_MAX_ATTEMPTS):
        assert (await client.post("/api/auth/login", json=body)).status_code == 401

    blocked = await client.post("/api/auth/login", json=body)
    assert blocked.status_code == 429
    assert _error_code(blocked) == "too_many_requests"
    assert int(blocked.headers["retry-after"]) > 0
    # Even the right password is blocked until the window passes.
    right = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert right.status_code == 429


async def test_successful_logins_do_not_count(client: httpx.AsyncClient) -> None:
    await _register(client)
    body = {"email": EMAIL, "password": PASSWORD}
    for _ in range(LOGIN_MAX_ATTEMPTS + 2):
        assert (await client.post("/api/auth/login", json=body)).status_code == 200


async def test_register_rate_limited(client: httpx.AsyncClient) -> None:
    for i in range(REGISTER_MAX_ATTEMPTS):
        assert (await _register(client, email=f"u{i}@example.com")).status_code == 201
    blocked = await _register(client, email="one-more@example.com")
    assert blocked.status_code == 429
    assert _error_code(blocked) == "too_many_requests"


async def test_create_user_reraises_other_integrity_errors(db_session) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):  # role CHECK constraint, not a duplicate email
        await service.create_user(db_session, "x@example.com", PASSWORD, role="Admin")


async def test_csrf_header_required(client: httpx.AsyncClient) -> None:
    for headers in ({"X-Requested-With": ""}, {"X-Requested-With": "XMLHttpRequest"}):
        response = await client.post(
            "/api/auth/register", json={"email": EMAIL, "password": PASSWORD}, headers=headers
        )
        assert response.status_code == 403
        assert _error_code(response) == "csrf_failed"
    # Safe methods do not need it.
    response = await client.get("/api/auth/me", headers={"X-Requested-With": ""})
    assert response.status_code == 401


async def test_require_admin(db_session) -> None:
    from app.core.deps import require_admin
    from app.core.errors import AppError

    user = await service.create_user(db_session, "u@example.com", PASSWORD)
    admin = await service.create_user(db_session, "admin@example.com", PASSWORD, role="admin")
    assert await require_admin(admin) is admin
    with pytest.raises(AppError) as exc:
        await require_admin(user)
    assert exc.value.status_code == 403


# --- CLI ----------------------------------------------------------------------------------


def test_cli_create_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_create_admin(email: str, password: str) -> None:
        calls.append((email, password))

    monkeypatch.setattr(auth_cli, "_create_admin", fake_create_admin)
    runner = CliRunner()

    result = runner.invoke(
        auth_cli.cli, ["create-admin", "Boss@Example.com"], input=f"{PASSWORD}\n{PASSWORD}\n"
    )
    assert result.exit_code == 0, result.output
    assert calls == [("boss@example.com", PASSWORD)]
    assert PASSWORD not in result.output  # hidden prompt

    result = runner.invoke(
        auth_cli.cli, ["create-admin", "boss@example.com"], input="short\nshort\n"
    )
    assert result.exit_code == 1
    assert len(calls) == 1


def test_cli_create_admin_existing_email(monkeypatch: pytest.MonkeyPatch) -> None:
    async def taken(email: str, password: str) -> None:
        raise service.email_taken()

    monkeypatch.setattr(auth_cli, "_create_admin", taken)
    result = CliRunner().invoke(
        auth_cli.cli, ["create-admin", EMAIL], input=f"{PASSWORD}\n{PASSWORD}\n"
    )
    assert result.exit_code == 1
    assert "đã được đăng ký" in result.output
