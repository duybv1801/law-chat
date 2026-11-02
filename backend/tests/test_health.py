import pytest
from fastapi.testclient import TestClient

from app import main


async def _ok(*_args: object) -> None:
    return None


async def _fail(*_args: object) -> None:
    raise ConnectionError("down")


@pytest.mark.parametrize(
    ("pg", "qd", "code", "body"),
    [
        (_ok, _ok, 200, {"status": "ok", "postgres": "ok", "qdrant": "ok"}),
        (_fail, _ok, 503, {"status": "degraded", "postgres": "error", "qdrant": "ok"}),
        (_ok, _fail, 503, {"status": "degraded", "postgres": "ok", "qdrant": "error"}),
    ],
)
def test_health(monkeypatch: pytest.MonkeyPatch, pg, qd, code: int, body: dict) -> None:
    monkeypatch.setattr(main, "check_postgres", pg)
    monkeypatch.setattr(main, "check_qdrant", qd)
    with TestClient(main.app) as client:
        response = client.get("/api/health")
    assert response.status_code == code
    assert response.json() == body
