import pytest
from fastapi.testclient import TestClient

from app import main
from app.ingest.indexer import CollectionMismatchError


async def _ok(*_args: object) -> None:
    return None


async def _fail(*_args: object) -> None:
    raise ConnectionError("down")


@pytest.mark.parametrize(
    ("pg", "qd", "code", "body"),
    [
        (_ok, _ok, 200, {"status": "ok", "postgres": "ok", "qdrant": "ok", "payload_pending": 2}),
        (
            _fail,
            _ok,
            503,
            {"status": "degraded", "postgres": "error", "qdrant": "ok", "payload_pending": None},
        ),
        (
            _ok,
            _fail,
            503,
            {"status": "degraded", "postgres": "ok", "qdrant": "error", "payload_pending": 2},
        ),
    ],
)
def test_health(monkeypatch: pytest.MonkeyPatch, pg, qd, code: int, body: dict) -> None:
    monkeypatch.setattr(main, "check_postgres", pg)
    monkeypatch.setattr(main, "check_qdrant", qd)

    async def pending() -> int:
        await pg()
        return 2

    monkeypatch.setattr(main, "count_payload_pending", pending)
    # Qdrant down at startup: the app still starts and /api/health reports it.
    monkeypatch.setattr(main, "ensure_collection", qd)
    with TestClient(main.app) as client:
        response = client.get("/api/health")
    assert response.status_code == code
    assert response.json() == body


def test_startup_fails_on_collection_of_another_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mismatch(*_args: object) -> None:
        raise CollectionMismatchError("other model")

    monkeypatch.setattr(main, "ensure_collection", mismatch)
    with pytest.raises(CollectionMismatchError), TestClient(main.app):
        pass
