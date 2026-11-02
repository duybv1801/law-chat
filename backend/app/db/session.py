"""Async SQLAlchemy engine and session factory (session factory added in T03)."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url.get_secret_value(), pool_pre_ping=True
)
