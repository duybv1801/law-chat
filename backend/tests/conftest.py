import os

import pytest

# Test defaults, set before `app.core.config` is imported. Real env vars win over `.env`.
os.environ.setdefault("APP_ENCRYPTION_KEYS", "ZmFrZS10ZXN0LWtleS0zMi1ieXRlcy1sb25nLWtleSE=")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-at-least-32-characters-long")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://law:law@localhost:5432/law_test")
os.environ.setdefault("EMBEDDING_BACKEND", "fake")
os.environ.setdefault("QDRANT_COLLECTION", "legal_chunks_fake")
os.environ.setdefault("SCHEDULER_ENABLED", "false")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Slow tests (real embedding model) run only when selected explicitly, e.g. `-m slow`.
    if config.option.markexpr:
        return
    skip_slow = pytest.mark.skip(reason="slow test: run with `uv run pytest -m slow`")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


def _alembic_config():
    from alembic.config import Config

    from app.core.config import BACKEND_DIR

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False  # keep pytest's logging (caplog) intact
    return cfg


def _test_engine():
    """Engine on the test DB. NullPool: pytest-asyncio uses one event loop per test, and
    pooled asyncpg connections must not outlive their loop."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    return create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)


@pytest.fixture(scope="session")
def alembic_config():
    """Alembic config bound to the test DB. Refuses to run on a DB not named `*_test`."""
    import asyncio

    from sqlalchemy import make_url, text
    from sqlalchemy.exc import DBAPIError

    from app.core.config import settings

    database = make_url(settings.database_url.get_secret_value()).database or ""
    if not database.endswith("_test"):
        # Fixtures below drop every table: never point them at a dev / prod database.
        raise RuntimeError(f"DB tests need a database named *_test, got {database!r}")

    async def ping() -> None:
        engine = _test_engine()
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except (OSError, DBAPIError) as exc:
        # DBAPIError covers e.g. a missing `law_test` DB (volume created before the init SQL).
        orig = getattr(exc, "orig", None)
        reason = type(getattr(orig, "__cause__", None) or orig or exc).__name__
        pytest.skip(f"Postgres / {database} not reachable ({reason}): run `docker compose up -d`")
    return _alembic_config()


@pytest.fixture(scope="session")
def migrated_db(alembic_config) -> None:
    """Fresh schema for the test session: drop the `public` schema, then upgrade to head.

    Dropping the schema (not `downgrade base`) also works when the DB holds a revision
    from another branch that this checkout does not know.
    """
    import asyncio

    from sqlalchemy import text

    from alembic import command

    async def reset() -> None:
        engine = _test_engine()
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()

    asyncio.run(reset())
    command.upgrade(alembic_config, "head")


@pytest.fixture
async def db_session(migrated_db):
    """Session inside an outer transaction that is rolled back after each test.

    `session.commit()` inside tests only releases a SAVEPOINT, so nothing persists.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = _test_engine()
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db_session):
    """HTTP client for the app, sharing `db_session` (so everything is rolled back).

    httpx `ASGITransport` runs the app in the test's event loop, which the asyncpg
    connection requires (Starlette's TestClient would use another thread / loop).
    """
    import httpx

    from app.auth.rate_limit import login_limiter, register_limiter
    from app.db.session import get_session
    from app.main import app

    async def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    login_limiter.reset()
    register_limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-Requested-With": "fetch"},
    ) as http:
        yield http
    app.dependency_overrides.pop(get_session, None)
    login_limiter.reset()
    register_limiter.reset()
