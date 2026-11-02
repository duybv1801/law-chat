"""Async SQLAlchemy engine, session factory and the `get_session` FastAPI dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url.get_secret_value(), pool_pre_ping=True
)

# expire_on_commit=False: objects stay readable after commit (no implicit async lazy-load).
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one session per request. Callers commit explicitly; errors roll back."""
    async with SessionLocal() as session:
        yield session
