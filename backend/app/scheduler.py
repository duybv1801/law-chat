"""Crawl job scheduling (docs/04-crawler.md, "Scheduler").

- `JobRunner` runs crawl jobs in the backend process: scheduled ones, and manual ones
  started from the admin API (`start` returns the `crawl_runs` id right away).
- `create_scheduler` registers the daily cron jobs on an `AsyncIOScheduler`; the app
  lifespan starts it only when `SCHEDULER_ENABLED=true` (one machine only).

Two runs of the same job never overlap: the job's Postgres advisory lock skips the second
one, even across processes (uvicorn `--reload`, several workers, the CLI).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.config import settings
from app.crawler.jobs import (
    IN_FORCE_STATUSES,
    CrawlRunResult,
    JobContext,
    JobType,
    run_backfill,
    run_daily_new,
    run_status_refresh,
)
from app.crawler.vbpl_client import ListFilters

logger = logging.getLogger(__name__)

MISFIRE_GRACE_S = 3600

ContextFactory = Callable[[], AbstractAsyncContextManager[JobContext]]


# --- Job parameters (admin API body `params`, same options as the CLI) -------------------


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackfillParams(_Params):
    limit: int | None = Field(default=None, ge=1)
    doc_types: (
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]] | None
    ) = Field(default=None, max_length=50)
    all_statuses: bool = False
    resume: bool = True


class DailyNewParams(_Params):
    overlap_days: int = Field(default=7, ge=0, le=365)


class StatusRefreshParams(_Params):
    batch_size: int = Field(default=500, ge=1, le=10_000)


JobParams = BackfillParams | DailyNewParams | StatusRefreshParams

PARAM_MODELS: dict[JobType, type[JobParams]] = {
    "backfill": BackfillParams,
    "daily_new": DailyNewParams,
    "status_refresh": StatusRefreshParams,
}


def parse_params(job_type: JobType, raw: dict[str, Any] | None) -> JobParams:
    """Validate `raw` for `job_type` (raises pydantic `ValidationError`)."""
    return PARAM_MODELS[job_type].model_validate(raw or {})


async def run_job(
    job_type: JobType, params: JobParams, *, ctx: JobContext, run_id: uuid.UUID | None = None
) -> CrawlRunResult:
    match params:
        case BackfillParams():
            filters = ListFilters(
                doc_types=frozenset(params.doc_types) if params.doc_types else None,
                statuses=None if params.all_statuses else IN_FORCE_STATUSES,
            )
            return await run_backfill(params.limit, filters, params.resume, ctx=ctx, run_id=run_id)
        case DailyNewParams():
            return await run_daily_new(params.overlap_days, ctx=ctx, run_id=run_id)
        case StatusRefreshParams():
            return await run_status_refresh(params.batch_size, ctx=ctx, run_id=run_id)
    raise ValueError(f"no parameters model for {job_type}")  # pragma: no cover


# --- Runner -----------------------------------------------------------------------------


class JobRunner:
    """Runs crawl jobs with a `JobContext` from `context_factory` (real DB, Qdrant and
    vbpl.vn client in the app; fakes in tests)."""

    def __init__(self, context_factory: ContextFactory) -> None:
        self._context_factory = context_factory
        # Every run in this process (manual and scheduled) -> its job type.
        self._tasks: dict[asyncio.Task[Any], JobType] = {}

    def is_running(self, job_type: JobType) -> bool:
        """True if this process has a run of `job_type` (possibly not holding its lock yet)."""
        return job_type in self._tasks.values()

    async def run(
        self,
        job_type: JobType,
        params: JobParams | None = None,
        run_id: uuid.UUID | None = None,
    ) -> CrawlRunResult | None:
        """Run one job to the end. Never raises (except on cancellation): the job records
        its own failures in `crawl_runs`; errors before that (e.g. Postgres down) are
        logged and give None."""
        params = params or PARAM_MODELS[job_type]()
        # Scheduled runs are tasks created by APScheduler: track them too, so `shutdown`
        # waits for them to record themselves as interrupted.
        task = asyncio.current_task()
        if task is not None:
            self._tasks.setdefault(task, job_type)
        try:
            async with self._context_factory() as ctx:
                result = await run_job(job_type, params, ctx=ctx, run_id=run_id)
        except Exception:
            logger.exception("Job %s could not run", job_type)
            return None
        finally:
            if task is not None:
                self._tasks.pop(task, None)
        if result.skipped:
            logger.warning("Job %s is already running: this run is skipped", job_type)
        return result

    def start(self, job_type: JobType, params: JobParams | None = None) -> uuid.UUID:
        """Run a job in the background; return the id its `crawl_runs` row will have.

        The row appears once the job holds its lock (almost at once). If another process
        took the lock meanwhile, the run is skipped and the row never appears."""
        run_id = uuid.uuid4()
        task = asyncio.create_task(self.run(job_type, params, run_id), name=f"crawl-{job_type}")
        self._tasks[task] = job_type
        task.add_done_callback(lambda t: self._tasks.pop(t, None))
        return run_id

    async def wait(self) -> None:
        """Wait for every background run (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        """Cancel background runs; each one records itself as interrupted."""
        for task in list(self._tasks):
            task.cancel()
        await self.wait()


# --- Scheduler --------------------------------------------------------------------------


def scheduled_jobs() -> list[tuple[JobType, str, JobParams]]:
    """(job type, crontab, params) of the daily jobs, from the settings."""
    return [
        ("daily_new", settings.scheduler_daily_new_cron, DailyNewParams()),
        (
            "status_refresh",
            settings.scheduler_status_refresh_cron,
            StatusRefreshParams(batch_size=settings.scheduler_status_refresh_batch_size),
        ),
    ]


def create_scheduler(runner: JobRunner) -> AsyncIOScheduler:
    """Scheduler (not started) with the daily jobs, in `TZ_APP`."""
    tz = ZoneInfo(settings.tz_app)
    scheduler = AsyncIOScheduler(timezone=tz)
    for job_type, cron, params in scheduled_jobs():
        scheduler.add_job(
            runner.run,
            CronTrigger.from_crontab(cron, timezone=tz),
            args=[job_type, params],
            id=job_type,
            name=f"crawl {job_type}",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=MISFIRE_GRACE_S,
            replace_existing=True,
        )
    return scheduler


def start_scheduler(runner: JobRunner) -> AsyncIOScheduler:
    """Create and start the scheduler (needs a running event loop)."""
    scheduler = create_scheduler(runner)
    scheduler.start()
    for job in scheduler.get_jobs():
        logger.info("Scheduled %s: next run at %s", job.name, job.next_run_time)
    return scheduler
