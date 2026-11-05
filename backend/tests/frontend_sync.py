"""Helpers for tests that check backend constants against their frontend mirrors."""

import re
from pathlib import Path

import pytest

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def read_ts_source(relative: str) -> str:
    """Read a frontend source file, skipping the test when the frontend is not checked out."""
    ts_file = FRONTEND_SRC / relative
    if not ts_file.exists():
        pytest.skip("frontend sources not present")
    return ts_file.read_text(encoding="utf-8")


def ts_int_const(source: str, name: str) -> int:
    """Value of `export const NAME = <int>` in a TS source."""
    match = re.search(rf"export const {name} = (\d+)", source)
    assert match, f"{name} missing in frontend source"
    return int(match.group(1))


def ts_str_list(source: str, name: str) -> list[str]:
    """Items of `export const NAME = ['a', 'b', ...]` in a TS source."""
    match = re.search(rf"export const {name} = \[([^\]]*)\]", source)
    assert match, f"{name} missing in frontend source"
    return re.findall(r"'([^']*)'", match.group(1))


def ts_str_record(source: str, name: str) -> dict[str, str]:
    """Entries of `export const NAME: Record<...> = { key: 'value', ... }` in a TS source."""
    match = re.search(rf"export const {name}[^=]*= \{{([^}}]*)\}}", source)
    assert match, f"{name} missing in frontend source"
    return dict(re.findall(r"(\w+): '([^']*)'", match.group(1)))
