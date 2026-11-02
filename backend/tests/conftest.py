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
