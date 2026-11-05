import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.crawler import jobs
from app.crawler.jobs import (
    IN_FORCE_STATUSES,
    OTHER_TYPES,
    JobContext,
    advisory_lock,
    backfill_groups,
    daily_since,
    run_backfill,
    run_daily_new,
    run_status_refresh,
    status_refresh_batch,
)
from app.crawler.vbpl_client import ListFilters, ListPage, PageOutOfRange, VbplNotFound
from app.crawler.vbpl_parser import DocRef, VbplDocument, VbplStatus, map_status
from app.db.models import CrawlRun, LegalChunk, LegalDocument
from app.ingest.embedder import FakeEmbedder
from app.ingest.indexer import ensure_collection
from app.ingest.schemas import Status

LUAT_MAU = (Path(__file__).parent / "fixtures" / "chunker" / "luat_mau.txt").read_text("utf-8")
BASE = "https://vbpl.vn/van-ban/chi-tiet/"
STATUS_CODES = {
    "con_hieu_luc": "CHL",
    "het_hieu_luc_toan_bo": "HHL",
    "het_hieu_luc_mot_phan": "HHL1P",
    "chua_co_hieu_luc": "CCHL",
}


def ref(slug: str, lastmod: str = "2026-11-01") -> DocRef:
    return DocRef(
        url=BASE + slug, source_id=slug.rsplit("--", 1)[1], lastmod=date.fromisoformat(lastmod)
    )


def validity(status: Status = "con_hieu_luc", expiry: date | None = None) -> VbplStatus:
    code = STATUS_CODES.get(status)
    _, note = map_status(
        code, {"CHL": "Còn hiệu lực", "HHL": "Hết hiệu lực toàn bộ"}.get(code or "")
    )
    return VbplStatus(
        status=status,
        status_code=code,
        status_note=note,
        effective_date=date(2020, 1, 1),
        expiry_date=expiry,
        updated_date=None,
    )


def document(
    source_id: str,
    doc_type: str = "Luật",
    status: Status = "con_hieu_luc",
    text: str | None = LUAT_MAU,
    expiry: date | None = None,
) -> VbplDocument:
    return VbplDocument(
        source_id=source_id,
        source_url=f"{BASE}van-ban--{source_id}",
        title=f"{doc_type} mẫu {source_id}",
        so_hieu=f"{source_id}/2020/QH14",
        doc_type=doc_type,
        issuing_body="Quốc hội",
        issued_date=date(2019, 11, 20),
        status=validity(status, expiry),
        is_consolidated=False,
        scope="trung_uong",
        has_content=text is not None,
        text=text,
    )


class FakeVbpl:
    """In-memory vbpl.vn: sitemap pages of `DocRef`, documents by id (missing -> 404)."""

    def __init__(self, pages: list[list[DocRef]], docs: dict[str, VbplDocument]) -> None:
        self.pages = pages
        self.docs = docs
        self.fetched: list[str] = []
        self.listed: list[int] = []
        self.status_calls: list[str] = []
        self.failing: set[str] = set()
        self.gate: asyncio.Event | None = None  # get_document waits on it when set
        self.waiting = asyncio.Event()

    async def list_documents(self, *, page: int, filters: ListFilters) -> ListPage:
        self.listed.append(page)
        if not 1 <= page <= len(self.pages):
            raise PageOutOfRange(f"page {page}")
        return ListPage(page=page, total_pages=len(self.pages), items=list(self.pages[page - 1]))

    async def list_recently_updated(
        self, since: date, *, scope: str = "trung_uong"
    ) -> AsyncIterator[DocRef]:
        for items in self.pages:
            for item in items:
                if item.lastmod is not None and item.lastmod < since:
                    return
                yield item

    async def get_document(self, source_id: str, url: str | None = None) -> VbplDocument:
        if self.gate is not None:
            self.waiting.set()
            await self.gate.wait()
        self.fetched.append(source_id)
        if source_id in self.failing:
            raise RuntimeError("parse failed")
        if source_id not in self.docs:
            raise VbplNotFound(url or source_id)
        return self.docs[source_id]

    async def get_status(self, source_id: str, url: str | None = None) -> VbplStatus:
        self.status_calls.append(source_id)
        if source_id in self.failing:
            raise RuntimeError("timeout")
        if source_id not in self.docs:
            raise VbplNotFound(url or source_id)
        return self.docs[source_id].status


# --- Pure logic -------------------------------------------------------------------------


def test_backfill_groups_follow_priority() -> None:
    assert backfill_groups(ListFilters()) == [
        "Hiến pháp",
        "Bộ luật",
        "Luật",
        "Nghị quyết",
        "Pháp lệnh",
        "Nghị định",
        "Quyết định",
        "Thông tư",
        OTHER_TYPES,
    ]
    only = ListFilters(doc_types=frozenset({"Thông tư", "Chỉ thị", "Luật"}))
    assert backfill_groups(only) == ["Luật", "Thông tư", "Chỉ thị"]


def test_other_group_takes_unlisted_types() -> None:
    every = backfill_groups(ListFilters())
    assert jobs._group_of(ref("chi-thi-a--1"), every) == OTHER_TYPES
    assert jobs._group_of(ref("khong-ro--2"), every) == OTHER_TYPES
    assert jobs._group_of(ref("luat-a--3"), every) == "Luật"
    assert jobs._group_of(ref("bo-luat-a--4"), every) == "Bộ luật"
    only = backfill_groups(ListFilters(doc_types=frozenset({"Luật", "Chỉ thị"})))
    assert jobs._group_of(ref("chi-thi-a--1"), only) == "Chỉ thị"
    assert jobs._group_of(ref("thong-tu-a--5"), only) is None


def test_resume_index_follows_source_id() -> None:
    refs = [ref("luat-a--1"), ref("luat-b--2"), ref("luat-c--3")]
    checkpoint = jobs._checkpoint("Luật", 1, refs, 1)
    assert checkpoint == {"group": "Luật", "page": 1, "position": 1, "source_id": "2"}
    moved = [ref("luat-new--9"), *refs]  # new entry on top of the sitemap file
    assert jobs._resume_index(moved, checkpoint) == 2
    assert jobs._resume_index([ref("luat-x--7")], checkpoint) == 1  # gone: position
    assert jobs._resume_index([], checkpoint) == 0


async def test_lock_loss_cancels_the_job(monkeypatch) -> None:
    class DeadLock:
        async def check(self) -> None:
            raise jobs.LockLost(jobs.LOCK_LOST_ERROR)

    monkeypatch.setattr(jobs, "LOCK_CHECK_INTERVAL_S", 0.01)
    cancelled = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(jobs.LockLost):
        await jobs._while_held(DeadLock(), work())  # type: ignore[arg-type]
    assert cancelled.is_set()


def test_filters_accept_document() -> None:
    in_force = ListFilters(statuses=IN_FORCE_STATUSES)
    assert in_force.accepts_document(document("1"))
    assert in_force.accepts_document(document("1", status="chua_co_hieu_luc"))
    assert not in_force.accepts_document(document("1", status="het_hieu_luc_toan_bo"))
    assert not in_force.accepts_document(document("1", status="khong_xac_dinh"))
    luat = ListFilters(doc_types=frozenset({"Luật"}))
    assert luat.accepts_document(document("1", status="het_hieu_luc_toan_bo"))
    # Doc types select refs by slug at listing time; the real type is not re-checked.
    assert luat.accepts_document(document("1", doc_type="Nghị định"))


# --- Integration: Postgres (law_test) + Qdrant (temporary collection) -------------------


@pytest.fixture
async def qdrant(monkeypatch: pytest.MonkeyPatch):
    client = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        await client.get_collections()
    except Exception as exc:
        await client.close()
        pytest.skip(f"Qdrant not reachable ({type(exc).__name__}): run `docker compose up -d`")
    name = f"test_jobs_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "qdrant_collection", name)
    try:
        await ensure_collection(client, FakeEmbedder())
        yield client
    finally:
        await client.delete_collection(name)
        await client.close()


@pytest.fixture
async def lock_engine(migrated_db):
    engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def make_ctx(db_session: AsyncSession, qdrant: AsyncQdrantClient, lock_engine):
    @asynccontextmanager
    async def shared():
        yield db_session  # jobs share the rolled-back test transaction

    def build(client: FakeVbpl) -> JobContext:
        return JobContext(
            session_factory=shared,
            lock_engine=lock_engine,
            qdrant=qdrant,
            client=client,
            embedder=FakeEmbedder(),
        )

    return build


def sample_site() -> FakeVbpl:
    pages = [
        [
            ref("luat-a--1"),
            ref("nghi-dinh-b--2"),  # no longer in force: filtered
            ref("thong-tu-c--3"),  # PDF only: skipped
            ref("luat-d--vbpqta_4"),  # 404
        ],
        [ref("hien-phap-e--5"), ref("chi-thi-f--6")],
    ]
    docs = {
        "1": document("1"),
        "2": document("2", doc_type="Nghị định", status="het_hieu_luc_toan_bo"),
        "3": document("3", doc_type="Thông tư", text=None),
        "5": document("5", doc_type="Hiến pháp"),
        "6": document("6", doc_type="Chỉ thị", status="het_hieu_luc_mot_phan"),
    }
    return FakeVbpl(pages, docs)


async def runs(session: AsyncSession, job_type: str) -> list[CrawlRun]:
    rows = await session.scalars(
        select(CrawlRun).where(CrawlRun.job_type == job_type).order_by(CrawlRun.started_at)
    )
    return list(rows)


async def vbpl_count(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(LegalDocument).where(LegalDocument.source == "vbpl")
        )
        or 0
    )


async def payloads(qdrant: AsyncQdrantClient, source_id: str, session: AsyncSession) -> list[dict]:
    doc = await session.scalar(select(LegalDocument).where(LegalDocument.source_id == source_id))
    assert doc is not None
    records, _ = await qdrant.scroll(
        settings.qdrant_collection,
        scroll_filter=qm.Filter(
            must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(doc.id)))]
        ),
        limit=1000,
        with_payload=True,
    )
    return [r.payload or {} for r in records]


async def test_backfill_ingests_in_priority_order_then_unchanged(make_ctx, db_session) -> None:
    site = sample_site()
    first = await run_backfill(ctx=make_ctx(site))

    assert first.status == "success"
    # Hiến pháp first, then Luật, Nghị định, Thông tư, then the other types.
    assert site.fetched == ["5", "1", "vbpqta_4", "2", "3", "6"]
    stats = first.stats
    assert (stats.created, stats.updated, stats.unchanged) == (3, 0, 0)
    assert (stats.filtered, stats.skipped_no_content, stats.not_found, stats.failed) == (1, 1, 1, 0)
    assert stats.fetched == 5
    assert await vbpl_count(db_session) == 3
    chunk_count = await db_session.scalar(select(func.count()).select_from(LegalChunk))

    second = await run_backfill(ctx=make_ctx(sample_site()))
    assert second.status == "success"
    assert (second.stats.created, second.stats.updated, second.stats.unchanged) == (0, 0, 3)
    assert await vbpl_count(db_session) == 3
    assert await db_session.scalar(select(func.count()).select_from(LegalChunk)) == chunk_count

    recorded = await runs(db_session, "backfill")
    assert [r.status for r in recorded] == ["success", "success"]
    assert recorded[1].stats["unchanged"] == 3
    assert recorded[1].finished_at is not None
    assert recorded[1].params["checkpoint"] is None  # whole scope done


async def test_backfill_limit_counts_stored_documents(make_ctx, db_session) -> None:
    site = sample_site()
    result = await run_backfill(limit=2, ctx=make_ctx(site))
    assert result.stats.created == 2
    assert site.fetched == ["5", "1"]
    assert (await runs(db_session, "backfill"))[0].params["checkpoint"] == {
        "group": "Luật",
        "page": 1,
        "position": 0,
        "source_id": "1",
    }

    # A run stopped by the limit ended normally: the same command re-checks the same ones.
    again = sample_site()
    result = await run_backfill(limit=2, ctx=make_ctx(again))
    assert again.fetched == ["5", "1"]
    assert result.stats.unchanged == 2


async def test_backfill_resumes_after_failure(make_ctx, db_session, monkeypatch) -> None:
    monkeypatch.setattr(jobs, "MAX_CONSECUTIVE_FAILURES", 1)
    site = sample_site()
    site.failing = {"2"}  # first Nghị định fails -> job aborts
    failed = await run_backfill(ctx=make_ctx(site))
    assert failed.status == "failed"
    assert failed.stats.failed == 1
    row = (await runs(db_session, "backfill"))[0]
    assert row.status == "failed" and row.error and "RuntimeError" in row.error
    assert row.params["checkpoint"] == {
        "group": "Nghị định",
        "page": 1,
        "position": 0,
        "source_id": "2",
    }

    resumed_site = sample_site()
    resumed = await run_backfill(ctx=make_ctx(resumed_site))
    assert resumed.status == "success"
    assert resumed_site.fetched == ["2", "3", "6"]  # retries the failed document
    assert (await runs(db_session, "backfill"))[1].params["resumed_from"]["group"] == "Nghị định"

    fresh_site = sample_site()
    await run_backfill(resume=False, ctx=make_ctx(fresh_site))
    assert fresh_site.fetched[0] == "5"


async def test_failure_streak_checkpoint_points_at_its_first_document(
    make_ctx, db_session, monkeypatch
) -> None:
    monkeypatch.setattr(jobs, "MAX_CONSECUTIVE_FAILURES", 2)
    site = sample_site()
    site.failing = {"2", "3"}  # Nghị định then Thông tư fail in a row -> job aborts
    assert (await run_backfill(ctx=make_ctx(site))).status == "failed"
    checkpoint = (await runs(db_session, "backfill"))[0].params["checkpoint"]
    assert (checkpoint["group"], checkpoint["source_id"]) == ("Nghị định", "2")


async def test_resume_from_missing_sitemap_page_restarts_the_group(make_ctx, db_session) -> None:
    db_session.add(
        CrawlRun(
            job_type="backfill",
            status="failed",
            params={
                "filters": jobs._filters_params(jobs.DEFAULT_FILTERS),
                "checkpoint": {"group": "Thông tư", "page": 9, "position": 0, "source_id": "3"},
            },
        )
    )
    await db_session.commit()
    site = sample_site()
    result = await run_backfill(ctx=make_ctx(site))
    assert result.status == "success"
    assert site.fetched == ["3", "6"]
    assert site.listed == [9, 1, 2]  # each existing file listed once


async def test_failed_write_is_not_counted_as_status_change(
    make_ctx, db_session, monkeypatch
) -> None:
    await run_backfill(ctx=make_ctx(sample_site()))
    site = sample_site()
    site.docs["5"] = document("5", doc_type="Hiến pháp", status="het_hieu_luc_mot_phan")

    async def broken(*args, **kwargs):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(jobs, "index_document", broken)
    result = await run_backfill(ctx=make_ctx(site))
    assert (result.stats.status_changed, result.stats.failed) == (0, 3)


async def test_one_failed_document_makes_run_partial(make_ctx, db_session) -> None:
    site = sample_site()
    site.failing = {"1"}
    result = await run_backfill(ctx=make_ctx(site))
    assert result.status == "partial"
    assert (result.stats.failed, result.stats.created) == (1, 2)
    row = (await runs(db_session, "backfill"))[0]
    assert row.status == "partial"
    assert row.params["failures"] == [{"source_id": "1", "error": "RuntimeError: parse failed"}]
    assert row.error == "1 văn bản lỗi:\n1: RuntimeError: parse failed"


async def test_changed_text_and_status_are_updated(make_ctx, db_session, qdrant) -> None:
    await run_backfill(ctx=make_ctx(sample_site()))
    site = sample_site()
    site.docs["1"] = document("1", text=LUAT_MAU.replace("Điều 1.", "Điều 1. Sửa đổi.", 1))
    site.docs["5"] = document("5", doc_type="Hiến pháp", status="het_hieu_luc_mot_phan")
    result = await run_backfill(ctx=make_ctx(site))

    assert (result.stats.updated, result.stats.unchanged, result.stats.status_changed) == (2, 1, 1)
    assert {p["status"] for p in await payloads(qdrant, "5", db_session)} == {
        "het_hieu_luc_mot_phan"
    }
    doc = await db_session.scalar(select(LegalDocument).where(LegalDocument.source_id == "1"))
    assert doc is not None
    assert any(
        "Sửa đổi." in c.text
        for c in await db_session.scalars(
            select(LegalChunk).where(LegalChunk.document_id == doc.id)
        )
    )


async def test_second_job_of_same_type_is_skipped(make_ctx, db_session) -> None:
    site = sample_site()
    site.gate = asyncio.Event()
    first = asyncio.create_task(run_backfill(ctx=make_ctx(site)))
    await asyncio.wait_for(site.waiting.wait(), 5)  # first job holds the lock

    second = await run_backfill(ctx=make_ctx(sample_site()))
    assert second.skipped and second.run_id is None
    # Another job type is not blocked.
    other = await run_status_refresh(batch_size=10, ctx=make_ctx(sample_site()))
    assert other.status == "success"

    site.gate.set()
    assert (await asyncio.wait_for(first, 30)).status == "success"
    assert len(await runs(db_session, "backfill")) == 1


async def test_advisory_lock_is_released(lock_engine) -> None:
    async with advisory_lock(lock_engine, "daily_new") as acquired:
        assert acquired
        async with advisory_lock(lock_engine, "daily_new") as again:
            assert not again
    async with advisory_lock(lock_engine, "daily_new") as acquired:
        assert acquired


async def test_interrupted_run_is_closed_by_the_next_one(make_ctx, db_session) -> None:
    stale = CrawlRun(job_type="status_refresh", status="running")
    db_session.add(stale)
    await db_session.commit()
    await run_status_refresh(ctx=make_ctx(sample_site()))
    await db_session.refresh(stale)
    assert stale.status == "failed"
    assert stale.error == jobs.INTERRUPTED_ERROR


async def test_status_refresh_updates_postgres_and_qdrant(make_ctx, db_session, qdrant) -> None:
    await run_backfill(ctx=make_ctx(sample_site()))
    site = sample_site()
    site.docs["1"] = document("1", status="het_hieu_luc_toan_bo", expiry=date(2026, 3, 1))
    before = datetime.now(UTC)

    result = await run_status_refresh(batch_size=10, ctx=make_ctx(site))

    assert result.status == "success"
    assert sorted(site.status_calls) == ["1", "5", "6"]
    assert (result.stats.status_changed, result.stats.unchanged) == (1, 2)
    doc = await db_session.scalar(select(LegalDocument).where(LegalDocument.source_id == "1"))
    assert doc is not None
    assert (doc.status, doc.expiry_date) == ("het_hieu_luc_toan_bo", date(2026, 3, 1))
    assert doc.status_note == "Hết hiệu lực toàn bộ"
    assert doc.status_checked_at is not None and doc.status_checked_at >= before
    assert not doc.payload_dirty
    points = await payloads(qdrant, "1", db_session)
    assert points and {p["status"] for p in points} == {"het_hieu_luc_toan_bo"}
    assert {p["status"] for p in await payloads(qdrant, "5", db_session)} == {"con_hieu_luc"}


async def test_status_refresh_marks_failed_documents_checked(make_ctx, db_session) -> None:
    await run_backfill(ctx=make_ctx(sample_site()))
    site = sample_site()
    site.failing = {"5"}
    del site.docs["1"]  # gone from vbpl.vn
    result = await run_status_refresh(batch_size=10, ctx=make_ctx(site))
    assert result.status == "partial"
    assert (result.stats.failed, result.stats.not_found) == (1, 1)
    checked = await db_session.scalars(
        select(LegalDocument.status_checked_at).where(LegalDocument.source_id.in_(["1", "5"]))
    )
    assert all(c is not None and c > datetime.now(UTC) - timedelta(minutes=1) for c in checked)


async def test_status_refresh_order(make_ctx, db_session) -> None:
    await run_backfill(ctx=make_ctx(sample_site()))
    rows = {d.source_id: d for d in await db_session.scalars(select(LegalDocument))}
    now = datetime.now(UTC)
    rows["1"].status_checked_at = now - timedelta(days=1)
    rows["5"].status_checked_at = None
    rows["6"].status_checked_at = now - timedelta(days=30)
    await db_session.commit()
    order = [d.source_id for d in await status_refresh_batch(db_session, 10)]
    assert order == ["5", "6", "1"]

    # Still "còn hiệu lực" although its expiry date passed: checked first.
    rows["1"].expiry_date = date(2020, 1, 1)
    rows["1"].status_checked_at = datetime(2019, 12, 1, tzinfo=UTC)
    await db_session.commit()
    assert [d.source_id for d in await status_refresh_batch(db_session, 1)] == ["1"]

    # Checked since its expiry date, vbpl.vn still says in force: no longer first.
    rows["1"].status_checked_at = now
    await db_session.commit()
    assert [d.source_id for d in await status_refresh_batch(db_session, 1)] == ["5"]


async def test_daily_new(make_ctx, db_session) -> None:
    await run_backfill(limit=1, ctx=make_ctx(sample_site()))  # only "5" in the DB
    today = jobs._today()
    pages = [
        [
            ref("luat-moi--7", today.isoformat()),
            ref("hien-phap-e--5", today.isoformat()),
            ref("luat-moi--7", today.isoformat()),  # duplicate entry: handled once
            ref("nghi-dinh-cu--8", (today - timedelta(days=3)).isoformat()),
            ref("luat-cu--9", (today - timedelta(days=30)).isoformat()),  # before `since`
        ]
    ]
    docs = {
        "5": document("5", doc_type="Hiến pháp", status="het_hieu_luc_mot_phan"),
        "7": document("7"),
        "8": document("8", doc_type="Nghị định", status="het_hieu_luc_toan_bo"),
        "9": document("9"),
    }
    site = FakeVbpl(pages, docs)
    result = await run_daily_new(ctx=make_ctx(site))

    assert result.status == "success"
    assert site.fetched == ["7", "5", "8"]
    stats = result.stats
    assert (stats.created, stats.updated, stats.status_changed, stats.filtered) == (1, 1, 1, 1)
    row = (await runs(db_session, "daily_new"))[0]
    assert row.params["since"] == (today - timedelta(days=7)).isoformat()
    assert row.params["listed"] == 3

    # Next run starts from the last success minus the overlap.
    local_day = row.started_at.astimezone(ZoneInfo(settings.tz_app)).date()
    assert await daily_since(db_session, 2) == local_day - timedelta(days=2)
    # A partial run (some documents failed) also moves the window forward.
    row.status = "partial"
    await db_session.commit()
    assert await daily_since(db_session, 2) == local_day - timedelta(days=2)
