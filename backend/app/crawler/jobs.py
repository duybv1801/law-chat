"""Crawl jobs: `backfill`, `daily_new`, `status_refresh` (docs/04-crawler.md, "Jobs").

Every job:
- takes a Postgres advisory lock for its `job_type` (session-level, on its own connection).
  If another process holds it, the job is skipped: no `crawl_runs` row, `skipped=True`.
  The lock connection is checked every `LOCK_CHECK_INTERVAL_S`; if it is lost (Postgres
  then releases the lock), the job stops so two runs never crawl at the same time;
- records one `crawl_runs` row (`running` -> `success` | `partial` | `failed`) and saves
  `stats` after every document, so the admin page shows progress;
- handles every document in its own try/except: a failure counts in `stats.failed` and the
  job goes on, unless `MAX_CONSECUTIVE_FAILURES` in a row (site down or blocking us);
- upserts by (`source='vbpl'`, `source_id`) through the indexer, which only updates the
  metadata when the text is unchanged.

Deviations approved in `docs/vbpl-findings.md`: listing uses sitemaps; document relations
are not available, so `daily_new` does not reset `status_checked_at` of replaced documents
(`status_refresh` checks overdue `con_hieu_luc` documents first instead); documents without
HTML full text are not ingested (`skipped_no_content`).
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import anyio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import Date, case, cast, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.crawler.vbpl_client import ListFilters, ListPage, PageOutOfRange, VbplNotFound
from app.crawler.vbpl_parser import DocRef, VbplDocument, VbplStatus
from app.db.models import CRAWL_JOB_TYPES, CrawlRun, LegalDocument
from app.db.session import SessionFactory
from app.ingest.embedder import Embedder, get_embedder
from app.ingest.indexer import content_hash, index_document, update_document_metadata
from app.ingest.schemas import DocumentMetadata, LegalDocumentIn, Scope, Status

logger = logging.getLogger(__name__)

JobType = Literal["backfill", "daily_new", "status_refresh"]
RunStatus = Literal["success", "partial", "failed", "skipped"]

# Backfill order (docs/04-crawler.md). Types are estimated from the URL slug, which cannot
# tell a Nghị quyết of the National Assembly or a Quyết định of the Prime Minister from
# others: they are grouped by type only. Every other type comes last (`OTHER_TYPES`).
BACKFILL_DOC_TYPE_PRIORITY: tuple[str, ...] = (
    "Hiến pháp",
    "Bộ luật",
    "Luật",
    "Nghị quyết",
    "Pháp lệnh",
    "Nghị định",
    "Quyết định",
    "Thông tư",
)
OTHER_TYPES = "*"  # checkpoint name of the group "every type not in the priority list"

# Backfill order: `priority` = by doc type group (above), newest `lastmod` first inside a
# group; `newest` = sitemap order only (newest `lastmod` first, every type mixed), for
# small manual runs that should bring the latest documents first. The sitemap has no
# issue date, so "newest" means last updated on vbpl.vn.
BackfillOrder = Literal["priority", "newest"]
NEWEST_GROUP = "newest"  # checkpoint name of the single group of the `newest` order

# "Còn hiệu lực" for ingesting new documents: in force, partly in force, or not yet in force.
IN_FORCE_STATUSES: frozenset[Status] = frozenset(
    {"con_hieu_luc", "het_hieu_luc_mot_phan", "chua_co_hieu_luc"}
)
DEFAULT_FILTERS = ListFilters(scope="trung_uong", statuses=IN_FORCE_STATUSES)

# Columns compared to tell `updated` from `unchanged`; timestamps change on every crawl.
COMPARED_COLUMNS = tuple(
    c for c in DocumentMetadata.model_fields if c not in ("crawled_at", "status_checked_at")
)
VALIDITY_COLUMNS = ("status", "status_note", "effective_date", "expiry_date")

MAX_CONSECUTIVE_FAILURES = 20
MAX_FAILURES_KEPT = 50  # failed documents kept in `params.failures` (source id + error)
_LOCK_CLASS = 0x4C43  # first key of the advisory lock; second key = index of the job type
LOCK_CHECK_INTERVAL_S = 60.0  # also keeps the lock connection from idling out

INTERRUPTED_ERROR = "Bị gián đoạn: tiến trình dừng khi job đang chạy."
LOCK_LOST_ERROR = "Mất kết nối giữ khoá job với Postgres: dừng job để tránh chạy trùng."


class CrawlerClient(Protocol):
    """What the jobs use of `VbplClient` (tests pass a fake)."""

    async def list_documents(self, *, page: int, filters: ListFilters) -> ListPage: ...

    def list_recently_updated(self, since: date, *, scope: Any = ...) -> AsyncIterator[DocRef]: ...

    async def get_document(self, source_id: str, url: str | None = None) -> VbplDocument: ...

    async def get_status(self, source_id: str, url: str | None = None) -> VbplStatus: ...


@dataclass
class JobContext:
    session_factory: SessionFactory
    # Advisory locks are taken on a connection of their own, held for the whole job: use
    # an engine with NullPool so a long job does not hold a connection of the app's pool.
    lock_engine: AsyncEngine
    qdrant: AsyncQdrantClient
    client: CrawlerClient
    embedder: Embedder | None = None  # None: the process-wide embedder

    @property
    def model_name(self) -> str:
        return (self.embedder or get_embedder()).model_name


@dataclass
class CrawlStats:
    fetched: int = 0  # detail pages fetched
    created: int = 0
    updated: int = 0  # text re-indexed or metadata changed
    unchanged: int = 0
    status_changed: int = 0  # validity (status, dates) changed, also counted above
    failed: int = 0
    skipped_no_content: int = 0  # no HTML full text (PDF only): not ingested
    not_found: int = 0  # 404 on vbpl.vn (e.g. `--vbpqta_*` sitemap URLs)
    filtered: int = 0  # new document whose status is outside the filters

    @property
    def stored(self) -> int:
        """Documents in the DB after being handled by this run."""
        return self.created + self.updated + self.unchanged

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class CrawlRunResult:
    job_type: JobType
    status: RunStatus
    run_id: uuid.UUID | None = None  # None when skipped
    stats: CrawlStats = field(default_factory=CrawlStats)
    error: str | None = None

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


class _TooManyFailures(Exception):
    pass


class LockLost(Exception):
    pass


class _Run:
    """State of one running job: its `crawl_runs` row, stats and params."""

    def __init__(self, ctx: JobContext, job_type: JobType, params: dict[str, Any]) -> None:
        self.ctx = ctx
        self.job_type = job_type
        self.params = params
        self.stats = CrawlStats()
        self.id: uuid.UUID | None = None
        self.consecutive_failures = 0

    async def save(self, **values: Any) -> None:
        async with self.ctx.session_factory() as session:
            await session.execute(
                update(CrawlRun)
                .where(CrawlRun.id == self.id)
                .values(
                    stats=self.stats.to_dict(),
                    params=self.params,
                    updated_at=func.clock_timestamp(),  # Core UPDATE skips the ORM onupdate
                    **values,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    async def guarded(self, source_id: str, step: Callable[[], Awaitable[None]]) -> None:
        """Run one document's work; a failure is counted, logged and swallowed."""
        try:
            await step()
        except Exception as exc:
            self.stats.failed += 1
            self.consecutive_failures += 1
            failures = self.params.setdefault("failures", [])
            if len(failures) < MAX_FAILURES_KEPT:
                failures.append({"source_id": source_id, "error": _describe(exc)})
            logger.warning("%s: document %s failed: %s", self.job_type, source_id, _describe(exc))
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise _TooManyFailures(
                    f"{MAX_CONSECUTIVE_FAILURES} văn bản lỗi liên tiếp, dừng job "
                    f"(lỗi cuối: {_describe(exc)})"
                ) from exc
        else:
            self.consecutive_failures = 0
        await self.save()


def _describe(exc: BaseException) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message[:300]}" if message else type(exc).__name__


def _today() -> date:
    return datetime.now(ZoneInfo(settings.tz_app)).date()


# --- Lock and run bookkeeping -----------------------------------------------------------


@dataclass
class HeldLock:
    conn: AsyncConnection

    async def check(self) -> None:
        """Raise `LockLost` if the lock connection is gone. A session-level lock lives as
        long as its connection, so a live connection means the lock is still held."""
        try:
            await self.conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise LockLost(LOCK_LOST_ERROR) from exc


@asynccontextmanager
async def advisory_lock(engine: AsyncEngine, job_type: JobType) -> AsyncIterator[HeldLock | None]:
    """Try the session-level advisory lock of `job_type`; yield it, or None if another
    process holds it.

    Held on a dedicated AUTOCOMMIT connection (no transaction left open for hours) and
    released when the block exits, or by Postgres if the process or connection dies.
    """
    keys = {"cls": _LOCK_CLASS, "job": CRAWL_JOB_TYPES.index(job_type)}
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        acquired = bool(await conn.scalar(text("SELECT pg_try_advisory_lock(:cls, :job)"), keys))
        if not acquired:
            yield None
            return
        try:
            yield HeldLock(conn)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await conn.execute(text("SELECT pg_advisory_unlock(:cls, :job)"), keys)
                except Exception as exc:  # connection lost: Postgres already released it
                    logger.warning("Could not release the %s lock: %s", job_type, _describe(exc))


async def is_job_running(session: AsyncSession, job_type: JobType) -> bool:
    """True if some process holds the advisory lock of `job_type` (read from `pg_locks`,
    without taking the lock). A two-key advisory lock shows as classid/objid, objsubid=2."""
    held = await session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted"
            " AND classid = :cls AND objid = :job AND objsubid = 2"
            " AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))"
        ),
        {"cls": _LOCK_CLASS, "job": CRAWL_JOB_TYPES.index(job_type)},
    )
    return bool(held)


async def _while_held(lock: HeldLock, work: Awaitable[None]) -> None:
    """Run `work`, checking the lock every `LOCK_CHECK_INTERVAL_S`; if the lock is lost,
    cancel `work` and raise `LockLost`."""
    task = asyncio.ensure_future(work)
    try:
        while True:
            await asyncio.wait({task}, timeout=LOCK_CHECK_INTERVAL_S)
            if task.done():
                return task.result()
            await lock.check()
    finally:
        if not task.done():
            task.cancel()
            # Let the job finish its cleanup before the caller records the run.
            with anyio.CancelScope(shield=True), suppress(BaseException):
                await asyncio.wait({task})
        # The task holds the job's exception, whose traceback holds this frame: break the
        # cycle so the job's objects are freed right away.
        del task


async def _close_interrupted_runs(session: AsyncSession, job_type: JobType) -> None:
    """Rows still `running` while we hold the lock belong to a process that died."""
    await session.execute(
        update(CrawlRun)
        .where(CrawlRun.job_type == job_type, CrawlRun.status == "running")
        .values(
            status="failed",
            error=INTERRUPTED_ERROR,
            finished_at=func.clock_timestamp(),
            updated_at=func.clock_timestamp(),
        )
        .execution_options(synchronize_session=False)
    )


async def _run_job(
    ctx: JobContext,
    job_type: JobType,
    params: dict[str, Any],
    body: Callable[[_Run], Awaitable[None]],
    prepare: Callable[[AsyncSession, _Run], Awaitable[None]] | None = None,
    run_id: uuid.UUID | None = None,
) -> CrawlRunResult:
    async with advisory_lock(ctx.lock_engine, job_type) as lock:
        if lock is None:
            logger.info("Job %s is already running elsewhere: skipped", job_type)
            return CrawlRunResult(job_type=job_type, status="skipped")

        run = _Run(ctx, job_type, params)
        async with ctx.session_factory() as session:
            await _close_interrupted_runs(session, job_type)
            if prepare is not None:
                await prepare(session, run)
            row = CrawlRun(
                id=run_id or uuid.uuid4(), job_type=job_type, status="running", params=run.params
            )
            session.add(row)
            await session.commit()
            run.id = row.id
        logger.info("Job %s started (run %s, params %s)", job_type, run.id, run.params)

        error: str | None = None
        try:
            await _while_held(lock, body(run))
        except (Exception, asyncio.CancelledError, KeyboardInterrupt) as exc:
            interrupted = not isinstance(exc, Exception)
            if interrupted:
                error = INTERRUPTED_ERROR
            else:
                known = isinstance(exc, (_TooManyFailures, LockLost))
                error = str(exc) if known else _describe(exc)
            logger.error("Job %s (run %s) failed: %s", job_type, run.id, error)
            # Shielded: after a cancellation every await would raise again.
            with anyio.move_on_after(30, shield=True):
                await run.save(status="failed", error=error, finished_at=func.clock_timestamp())
            if interrupted:
                raise  # recorded; the checkpoint lets the next backfill resume
            return CrawlRunResult(job_type, "failed", run.id, run.stats, error)

        status: RunStatus = "partial" if run.stats.failed else "success"
        if run.stats.failed:
            error = f"{run.stats.failed} văn bản lỗi:\n" + "\n".join(
                f"{f['source_id']}: {f['error']}" for f in run.params["failures"]
            )
        await run.save(status=status, error=error, finished_at=func.clock_timestamp())
        logger.info("Job %s (run %s) %s: %s", job_type, run.id, status, run.stats.to_dict())
        return CrawlRunResult(job_type, status, run.id, run.stats, error)


# --- One document -----------------------------------------------------------------------


def _validity(values: Any) -> dict[str, Any]:
    if isinstance(values, dict):
        return {c: values[c] for c in VALIDITY_COLUMNS}
    return {c: getattr(values, c) for c in VALIDITY_COLUMNS}


async def _find(session: AsyncSession, source_id: str) -> LegalDocument | None:
    return await session.scalar(
        select(LegalDocument).where(
            LegalDocument.source == "vbpl", LegalDocument.source_id == source_id
        )
    )


async def _sync_document(run: _Run, ref: DocRef, filters: ListFilters) -> None:
    """Fetch one document and create / update / leave it. Raises on failure."""
    ctx, stats = run.ctx, run.stats
    try:
        doc = await ctx.client.get_document(ref.source_id, ref.url)
    except VbplNotFound:
        stats.not_found += 1
        return
    stats.fetched += 1
    now = datetime.now(UTC)
    new = doc.to_document_in(crawled_at=now, scope=filters.scope)
    new_validity = _validity(new.model_dump())

    async with ctx.session_factory() as session:
        existing = await _find(session, doc.source_id)
        if existing is None:
            if not filters.accepts_document(doc):
                stats.filtered += 1
            elif doc.text is None:
                stats.skipped_no_content += 1
            else:
                await index_document(session, ctx.qdrant, new, doc.text, embedder=ctx.embedder)
                stats.created += 1
            return

        old_validity = _validity(existing)

        def count_validity_change() -> None:  # once the change is saved
            if old_validity != new_validity:
                stats.status_changed += 1
                logger.info(
                    "%s: validity of %s changed %s -> %s",
                    run.job_type,
                    doc.source_id,
                    old_validity,
                    new_validity,
                )

        if doc.text is None:
            # Full text gone from vbpl.vn: keep the indexed version, update validity only.
            await update_document_metadata(
                session,
                ctx.qdrant,
                existing.id,
                **new_validity,
                crawled_at=now,
                status_checked_at=now,
            )
            count_validity_change()
            stats.skipped_no_content += 1
            return

        unchanged = _is_unchanged(existing, new, doc.text, ctx.model_name)
        await index_document(session, ctx.qdrant, new, doc.text, embedder=ctx.embedder)
        count_validity_change()
        if unchanged:
            stats.unchanged += 1
        else:
            stats.updated += 1


def _is_unchanged(existing: LegalDocument, new: LegalDocumentIn, text: str, model: str) -> bool:
    values = new.model_dump()
    return (
        existing.ingest_status == "done"
        and existing.content_hash == content_hash(text)
        and existing.embedding_model == model
        and all(getattr(existing, c) == values[c] for c in COMPARED_COLUMNS)
    )


# --- backfill ---------------------------------------------------------------------------


def backfill_groups(filters: ListFilters, order: BackfillOrder = "priority") -> list[str]:
    """Doc type groups in backfill order; `OTHER_TYPES` = every type not listed before.
    The `newest` order has one group (`NEWEST_GROUP`) holding every listed document."""
    if order == "newest":
        return [NEWEST_GROUP]
    if filters.doc_types is None:
        return [*BACKFILL_DOC_TYPE_PRIORITY, OTHER_TYPES]
    first = [t for t in BACKFILL_DOC_TYPE_PRIORITY if t in filters.doc_types]
    return first + sorted(filters.doc_types - set(first))


def _group_of(ref: DocRef, groups: list[str], filters: ListFilters) -> str | None:
    """Backfill group of a ref (by its slug guess), or None if no group takes it."""
    if groups == [NEWEST_GROUP]:
        return NEWEST_GROUP if filters.accepts(ref) else None
    guess = ref.doc_type_guess
    if guess in groups:
        return guess
    if OTHER_TYPES in groups and guess not in BACKFILL_DOC_TYPE_PRIORITY:
        return OTHER_TYPES
    return None


def _checkpoint(group: str, page: int, refs: list[DocRef], index: int) -> dict[str, Any]:
    """Where a resumed backfill starts: the document at `index` of the group's refs."""
    return {"group": group, "page": page, "position": index, "source_id": refs[index].source_id}


def _resume_index(refs: list[DocRef], checkpoint: dict[str, Any]) -> int:
    """Index of the checkpoint document in the re-fetched refs. Found by `source_id`, as
    entries move inside a sitemap file between runs; by position if it is gone."""
    source_id = checkpoint.get("source_id")
    for index, ref in enumerate(refs):
        if ref.source_id == source_id:
            return index
    return min(checkpoint.get("position", 0), len(refs))


def _filters_params(filters: ListFilters) -> dict[str, Any]:
    return {
        "scope": filters.scope,
        "doc_types": sorted(filters.doc_types) if filters.doc_types is not None else None,
        "statuses": sorted(filters.statuses) if filters.statuses is not None else None,
    }


async def _find_checkpoint(
    session: AsyncSession, filters: dict[str, Any], order: BackfillOrder = "priority"
) -> dict[str, Any] | None:
    """Checkpoint of the latest backfill if it was interrupted / failed with the same filters
    and order (runs from before `order` existed used `priority`).

    A run that ended normally (also one stopped by `limit`) is not resumed: running the
    same command again re-checks the same documents.
    """
    last = await session.scalar(
        select(CrawlRun)
        .where(CrawlRun.job_type == "backfill")
        .order_by(CrawlRun.started_at.desc())
        .limit(1)
    )
    if last is None or last.status != "failed":
        return None
    last_params = last.params or {}
    checkpoint = last_params.get("checkpoint")
    if not checkpoint or last_params.get("filters") != filters:
        return None
    if last_params.get("order", "priority") != order:
        return None
    return dict(checkpoint)


async def run_backfill(
    limit: int | None = None,
    filters: ListFilters | None = None,
    resume: bool = True,
    order: BackfillOrder = "priority",
    *,
    ctx: JobContext | None = None,
    run_id: uuid.UUID | None = None,
) -> CrawlRunResult:
    """Walk every sitemap file of the scope, one doc type group at a time (priority order),
    and ingest the documents that pass `filters` (default: central, in force). With
    `order="newest"`, walk the sitemaps once in their own order (latest `lastmod` first).

    `limit` caps the documents stored by this run (created, updated or unchanged);
    documents filtered out or missing do not count. With `resume`, a run continues from the
    checkpoint (group, sitemap page, document) of the previous backfill if that one failed
    or was interrupted with the same filters. The checkpoint is the document in progress,
    or the first of the current run of failures, so neither is skipped on resume.
    """
    filters = filters or DEFAULT_FILTERS
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    params: dict[str, Any] = {
        "limit": limit,
        "resume": resume,
        "order": order,
        "filters": _filters_params(filters),
    }

    async def prepare(session: AsyncSession, run: _Run) -> None:
        if resume:
            checkpoint = await _find_checkpoint(session, run.params["filters"], order)
            if checkpoint:
                run.params["resumed_from"] = checkpoint
                run.params["checkpoint"] = checkpoint

    async def body(run: _Run) -> None:
        groups = backfill_groups(filters, order)
        start = run.params.get("checkpoint") or {}
        if start.get("group") not in groups:
            start = {}
        group_index = groups.index(start["group"]) if start else 0
        listing = ListFilters(scope=filters.scope)  # doc types are grouped here
        # Each sitemap file is fetched once per run and split by group; a group's share is
        # dropped once read, so memory shrinks as the groups are done.
        pages: dict[int, tuple[dict[str, list[DocRef]], int | None]] = {}

        async def refs_of(page: int, group: str) -> tuple[list[DocRef], int | None]:
            if page not in pages:
                listed = await run.ctx.client.list_documents(page=page, filters=listing)
                split: dict[str, list[DocRef]] = {g: [] for g in groups}
                for ref in listed.items:
                    ref_group = _group_of(ref, groups, filters)
                    if ref_group is not None:
                        split[ref_group].append(ref)
                pages[page] = (split, listed.next_page)
            split, next_page = pages[page]
            return split.pop(group, []), next_page

        for group in groups[group_index:]:
            resume = start if group == start.get("group") else None
            page = resume.get("page", 1) if resume else 1
            while True:
                try:
                    refs, next_page = await refs_of(page, group)
                except PageOutOfRange:
                    if resume is None or page == 1:
                        raise
                    logger.warning(
                        "backfill: checkpoint page %s no longer exists, %s restarts at page 1",
                        page,
                        group,
                    )
                    page, resume = 1, None
                    continue
                first = _resume_index(refs, resume) if resume else 0
                resume = None
                for index in range(first, len(refs)):
                    if limit is not None and run.stats.stored >= limit:
                        return
                    ref = refs[index]
                    if run.consecutive_failures == 0:  # else keep the start of the failures
                        run.params["checkpoint"] = _checkpoint(group, page, refs, index)
                    await run.guarded(
                        ref.source_id, lambda ref=ref: _sync_document(run, ref, filters)
                    )
                if next_page is None:
                    break
                page = next_page
        run.params["checkpoint"] = None  # whole scope done

    return await _with_context(
        ctx, lambda c: _run_job(c, "backfill", params, body, prepare, run_id)
    )


# --- daily_new --------------------------------------------------------------------------


async def daily_since(session: AsyncSession, overlap_days: int) -> date:
    """`lastmod` lower bound: local date of the last daily_new run that went through the
    listing (`success` or `partial`) minus the overlap; today minus the overlap if there
    was none. Documents that failed in a `partial` run are retried while they stay inside
    the overlap."""
    last = await session.scalar(
        select(func.max(CrawlRun.started_at)).where(
            CrawlRun.job_type == "daily_new", CrawlRun.status.in_(("success", "partial"))
        )
    )
    base = last.astimezone(ZoneInfo(settings.tz_app)).date() if last is not None else _today()
    return base - timedelta(days=overlap_days)


async def run_daily_new(
    overlap_days: int = 7,
    *,
    scope: Scope = "trung_uong",
    ctx: JobContext | None = None,
    run_id: uuid.UUID | None = None,
) -> CrawlRunResult:
    """Documents of the scope whose sitemap `lastmod` is on or after `daily_since`: new ones
    are ingested if in force; known ones are re-checked (text, metadata, validity)."""
    if overlap_days < 0:
        raise ValueError("overlap_days must be >= 0")
    filters = ListFilters(scope=scope, statuses=IN_FORCE_STATUSES)
    params: dict[str, Any] = {"overlap_days": overlap_days, "scope": scope}

    async def prepare(session: AsyncSession, run: _Run) -> None:
        run.params["since"] = (await daily_since(session, overlap_days)).isoformat()

    async def body(run: _Run) -> None:
        seen: set[str] = set()
        since = date.fromisoformat(run.params["since"])
        async for ref in run.ctx.client.list_recently_updated(since, scope=scope):
            if ref.source_id in seen:
                continue
            seen.add(ref.source_id)
            await run.guarded(ref.source_id, lambda ref=ref: _sync_document(run, ref, filters))
        run.params["listed"] = len(seen)

    return await _with_context(
        ctx, lambda c: _run_job(c, "daily_new", params, body, prepare, run_id)
    )


# --- status_refresh ---------------------------------------------------------------------


async def status_refresh_batch(session: AsyncSession, batch_size: int) -> list[LegalDocument]:
    """vbpl documents to check: overdue ones first (still `con_hieu_luc` past their expiry
    date, and not checked since that date), then never checked, then least recently
    checked. Once checked on or after its expiry date, a document vbpl.vn still shows as in
    force waits its turn like the others."""
    checked_day = cast(func.timezone(settings.tz_app, LegalDocument.status_checked_at), Date)
    overdue = case(
        (
            (LegalDocument.status == "con_hieu_luc")
            & (LegalDocument.expiry_date <= _today())
            & or_(
                LegalDocument.status_checked_at.is_(None),
                checked_day < LegalDocument.expiry_date,
            ),
            0,
        ),
        else_=1,
    )
    rows = await session.scalars(
        select(LegalDocument)
        .where(LegalDocument.source == "vbpl")
        .order_by(
            overdue,
            LegalDocument.status_checked_at.asc().nulls_first(),
            LegalDocument.id,
        )
        .limit(batch_size)
    )
    return list(rows)


async def _refresh_one(run: _Run, document: dict[str, Any]) -> None:
    ctx, stats = run.ctx, run.stats
    now = datetime.now(UTC)

    async def save(**changes: Any) -> None:
        async with ctx.session_factory() as session:
            await update_document_metadata(
                session, ctx.qdrant, document["id"], **changes, status_checked_at=now
            )

    try:
        status = await ctx.client.get_status(document["source_id"], document["source_url"])
    except VbplNotFound:
        stats.not_found += 1
        await save()
        return
    except Exception:
        # Marked as checked anyway, so a document failing every time does not stay at
        # the head of the queue; the job counts the failure.
        try:
            await save()
        except Exception:
            logger.exception("Could not mark %s as checked", document["source_id"])
        raise

    stats.fetched += 1
    fresh = _validity(status)
    changes = {c: v for c, v in fresh.items() if document[c] != v}
    await save(**changes)
    if changes:
        stats.status_changed += 1
        # Not caught by daily_new (else the DB would already have it): watch this count to
        # check the assumption "validity changes bump the sitemap lastmod".
        logger.info(
            "status_refresh: %s changed %s (missed by daily_new)", document["source_id"], changes
        )
    else:
        stats.unchanged += 1


async def run_status_refresh(
    batch_size: int = 500, *, ctx: JobContext | None = None, run_id: uuid.UUID | None = None
) -> CrawlRunResult:
    """Re-read the validity of `batch_size` vbpl documents and copy changes to Qdrant."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    params: dict[str, Any] = {"batch_size": batch_size}

    async def body(run: _Run) -> None:
        async with run.ctx.session_factory() as session:
            batch = [
                {"id": d.id, "source_id": d.source_id, "source_url": d.source_url} | _validity(d)
                for d in await status_refresh_batch(session, batch_size)
            ]
        for document in batch:
            await run.guarded(document["source_id"], lambda d=document: _refresh_one(run, d))

    return await _with_context(
        ctx, lambda c: _run_job(c, "status_refresh", params, body, run_id=run_id)
    )


# --- Default context --------------------------------------------------------------------


async def _with_context(
    ctx: JobContext | None, job: Callable[[JobContext], Awaitable[CrawlRunResult]]
) -> CrawlRunResult:
    if ctx is not None:
        return await job(ctx)
    async with default_context() as default:
        return await job(default)


@asynccontextmanager
async def default_context(qdrant: AsyncQdrantClient | None = None) -> AsyncIterator[JobContext]:
    """Real DB, Qdrant and vbpl.vn client. Pass the app's `qdrant` to reuse it."""
    from app.crawler.vbpl_client import VbplClient
    from app.db.session import SessionLocal

    own_qdrant = qdrant is None
    qdrant = qdrant or AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    lock_engine = create_async_engine(settings.database_url.get_secret_value(), poolclass=NullPool)
    try:
        async with VbplClient() as client:
            yield JobContext(
                session_factory=SessionLocal,
                lock_engine=lock_engine,
                qdrant=qdrant,
                client=client,
            )
    finally:
        await lock_engine.dispose()
        if own_qdrant:
            await qdrant.close()
