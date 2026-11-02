import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Slow tests (real embedding model) run only when selected explicitly, e.g. `-m slow`.
    if config.option.markexpr:
        return
    skip_slow = pytest.mark.skip(reason="slow test: run with `uv run pytest -m slow`")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
