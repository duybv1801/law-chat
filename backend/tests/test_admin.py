"""Admin API: permissions, manual crawl runs, crawl-runs list, stats, re-index.

Postgres (law_test) + Qdrant (temporary collection); vbpl.vn is the in-memory `FakeVbpl`.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import service as admin_service
from app.admin.router import get_job_runner
from app.crawler.jobs import JobContext, advisory_lock
from app.db.models import CrawlRun, LegalChunk, LegalDocument, User
from app.ingest.embedder import FakeEmbedder
from app.main import app
from app.scheduler import JobRunner
from tests.test_crawler_jobs import FakeVbpl, document, lock_engine, sample_site
from tests.test_documents import _register, _upload, _vbpl, api, qdrant, session_factory

__all__ = ["api", "lock_engine", "qdrant", "session_factory"]  # fixtures used below

ADMIN_ROUTES = [
    ("GET", "/api/admin/stats"),
    ("GET", "/api/admin/crawl-runs"),
    ("POST", "/api/admin/crawl/daily_new"),
    ("POST", f"/api/admin/documents/{uuid.uuid4()}/reindex"),
]


class TrackingRunner(JobRunner):
    def __init__(self, context_factory) -> None:
        super().__init__(context_factory)
        self.started: list[str] = []

    def start(self, job_type, params=None):  # type: ignore[no-untyped-def]
        self.started.append(job_type)
        return super().start(job_type, params)


@pytest.fixture
def runner(session_factory, qdrant, lock_engine):
    """Runner whose jobs use the test transaction, Qdrant collection and a fake vbpl.vn."""
    site = sample_site()

    @asynccontextmanager
    async def context() -> AsyncIterator[JobContext]:
        yield JobContext(
            session_factory=session_factory,
            lock_engine=lock_engine,
            qdrant=qdrant,
            client=site,
            embedder=FakeEmbedder(),
        )

    job_runner = TrackingRunner(context)
    app.dependency_overrides[get_job_runner] = lambda: job_runner
    yield job_runner
    app.dependency_overrides.pop(get_job_runner, None)


async def _make_admin(api: httpx.AsyncClient, session: AsyncSession) -> None:
    await _register(api, "admin@example.com")
    await session.execute(
        update(User).where(User.email == "admin@example.com").values(role="admin")
    )
    await session.commit()


async def _chunk_ids(session: AsyncSession, document_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.scalars(select(LegalChunk.id).where(LegalChunk.document_id == document_id))
    return set(rows)


async def _call(api: httpx.AsyncClient, method: str, path: str) -> int:
    return (await api.request(method, path)).status_code


# --- Permissions ------------------------------------------------------------------------


async def test_admin_routes_need_login_then_admin(api, runner, db_session) -> None:
    for method, path in ADMIN_ROUTES:
        assert await _call(api, method, path) == 401, path

    await _register(api, "an@example.com")
    for method, path in ADMIN_ROUTES:
        response = await api.request(method, path)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "forbidden"
    assert runner.started == []

    await _make_admin(api, db_session)
    assert await _call(api, "GET", "/api/admin/stats") == 200
    assert await _call(api, "GET", "/api/admin/crawl-runs") == 200


# --- Manual crawl -----------------------------------------------------------------------


async def test_manual_crawl_runs_in_background_and_is_listed(api, runner, db_session) -> None:
    await _make_admin(api, db_session)

    response = await api.post("/api/admin/crawl/backfill", json={"params": {"limit": 10}})
    assert response.status_code == 202
    run_id = uuid.UUID(response.json()["crawl_run_id"])
    await runner.wait()

    run = await db_session.get(CrawlRun, run_id, populate_existing=True)
    assert run is not None
    assert (run.job_type, run.status) == ("backfill", "success")
    assert run.params["limit"] == 10
    assert run.stats["created"] == 3

    listed = (await api.get("/api/admin/crawl-runs", params={"job_type": "backfill"})).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == str(run_id)
    assert listed["items"][0]["stats"]["created"] == 3

    stats = (await api.get("/api/admin/stats")).json()
    assert stats["documents"]["total"] == 3
    assert stats["documents"]["by_source"] == {"vbpl": 3}
    assert stats["documents"]["by_ingest_status"] == {"done": 3}
    assert stats["chunks"] > 0
    assert stats["qdrant"]["points_count"] == stats["chunks"]
    assert stats["last_runs"]["backfill"]["id"] == str(run_id)
    assert stats["last_runs"]["daily_new"] is None


async def test_manual_crawl_without_body_uses_defaults(api, runner, db_session) -> None:
    await _make_admin(api, db_session)
    response = await api.post("/api/admin/crawl/status_refresh")
    assert response.status_code == 202
    await runner.wait()
    run = await db_session.get(CrawlRun, uuid.UUID(response.json()["crawl_run_id"]))
    assert run is not None
    assert (run.status, run.params) == ("success", {"batch_size": 500})


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/admin/crawl/daily_new", {"params": {"overlap_days": -1}}),
        ("/api/admin/crawl/status_refresh", {"params": {"batch_size": 0}}),
        ("/api/admin/crawl/backfill", {"params": {"unknown": 1}}),
        ("/api/admin/crawl/backfill", {"params": {"doc_types": [" "]}}),
        ("/api/admin/crawl/backfill", {"other": 1}),
        ("/api/admin/crawl/nope", None),
    ],
)
async def test_manual_crawl_rejects_bad_input(api, runner, db_session, path, body) -> None:
    await _make_admin(api, db_session)
    response = await api.post(path, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert runner.started == []


async def test_manual_crawl_conflicts_with_running_job(
    api, runner, db_session, lock_engine
) -> None:
    await _make_admin(api, db_session)
    async with advisory_lock(lock_engine, "backfill") as lock:
        assert lock is not None
        response = await api.post("/api/admin/crawl/backfill", json={"params": {"limit": 1}})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "job_running"
        # Another job type is not blocked.
        assert (await api.post("/api/admin/crawl/status_refresh")).status_code == 202
    await runner.wait()
    assert runner.started == ["status_refresh"]


async def test_crawl_runs_filter_and_pagination(api, db_session) -> None:
    await _make_admin(api, db_session)
    start = datetime(2026, 11, 1, tzinfo=UTC)
    for i in range(5):
        db_session.add(
            CrawlRun(
                job_type="status_refresh" if i % 2 else "daily_new",
                status="success",
                started_at=start + timedelta(hours=i),
            )
        )
    await db_session.commit()

    page1 = (await api.get("/api/admin/crawl-runs", params={"page_size": 2})).json()
    assert page1["total"] == 5
    started = [item["started_at"] for item in page1["items"]]
    assert started == sorted(started, reverse=True)  # newest first
    page3 = (await api.get("/api/admin/crawl-runs", params={"page_size": 2, "page": 3})).json()
    assert len(page3["items"]) == 1
    only = (await api.get("/api/admin/crawl-runs", params={"job_type": "daily_new"})).json()
    assert only["total"] == 3
    assert {item["job_type"] for item in only["items"]} == {"daily_new"}
    assert (await api.get("/api/admin/crawl-runs", params={"job_type": "x"})).status_code == 422


# --- Re-index ---------------------------------------------------------------------------


async def test_reindex_vbpl_refetches_and_re_embeds(
    api, db_session, qdrant, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_admin(api, db_session)
    document_id = await _vbpl(db_session, qdrant, source_id="1")
    before = await _chunk_ids(db_session, document_id)
    site = FakeVbpl([], {"1": document("1")})  # same text: only `force` re-embeds it

    @asynccontextmanager
    async def fake_client() -> AsyncIterator[FakeVbpl]:
        yield site

    monkeypatch.setattr(admin_service, "vbpl_client_factory", fake_client)

    response = await api.post(f"/api/admin/documents/{document_id}/reindex")
    assert response.status_code == 202
    assert response.json() == {"document_id": str(document_id)}
    assert site.fetched == ["1"]
    after = await _chunk_ids(db_session, document_id)
    assert after and after.isdisjoint(before)
    doc = await db_session.get(LegalDocument, document_id, populate_existing=True)
    assert doc is not None
    assert (doc.ingest_status, doc.title) == ("done", "Luật mẫu 1")  # fresh metadata


async def test_reindex_upload_reparses_the_file(api, db_session) -> None:
    await _make_admin(api, db_session)
    document_id = uuid.UUID((await _upload(api)).json()["id"])
    before = await _chunk_ids(db_session, document_id)
    assert before

    assert (await api.post(f"/api/admin/documents/{document_id}/reindex")).status_code == 202
    after = await _chunk_ids(db_session, document_id)
    assert after and after.isdisjoint(before)


async def test_reindex_errors(api, db_session, qdrant) -> None:
    await _make_admin(api, db_session)
    missing = await api.post(f"/api/admin/documents/{uuid.uuid4()}/reindex")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")

    document_id = await _vbpl(db_session, qdrant, source_id="1")
    await db_session.execute(
        update(LegalDocument)
        .where(LegalDocument.id == document_id)
        .values(ingest_status="processing")
    )
    await db_session.commit()
    busy = await api.post(f"/api/admin/documents/{document_id}/reindex")
    assert (busy.status_code, busy.json()["error"]["code"]) == (409, "ingest_in_progress")
