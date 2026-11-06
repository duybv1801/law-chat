"""Scheduler wiring: cron jobs from the settings, fired by APScheduler -> `crawl_runs` row."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, settings
from app.crawler.jobs import JobContext
from app.db.models import CrawlRun
from app.ingest.embedder import FakeEmbedder
from app.scheduler import (
    MISFIRE_GRACE_S,
    BackfillParams,
    DailyNewParams,
    JobRunner,
    StatusRefreshParams,
    create_scheduler,
    parse_params,
)
from tests.test_crawler_jobs import lock_engine, qdrant, sample_site

__all__ = ["lock_engine", "qdrant"]  # fixtures used below


def test_bad_cron_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, scheduler_daily_new_cron="61 * * * *")
    assert Settings(_env_file=None, scheduler_daily_new_cron="* * * * *")


def test_parse_params() -> None:
    assert parse_params("daily_new", None) == DailyNewParams(overlap_days=7)
    assert parse_params("status_refresh", {"batch_size": 5}) == StatusRefreshParams(batch_size=5)
    backfill = parse_params("backfill", {"limit": 2, "doc_types": [" Luật "]})
    assert backfill == BackfillParams(limit=2, doc_types=["Luật"])
    with pytest.raises(ValidationError):
        parse_params("daily_new", {"batch_size": 5})


async def test_scheduler_registers_daily_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "scheduler_status_refresh_batch_size", 42)
    scheduler = create_scheduler(JobRunner(lambda: None))  # type: ignore[arg-type, return-value]
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"daily_new", "status_refresh"}
    tz = ZoneInfo(settings.tz_app)
    for job_id, hour in (("daily_new", "2"), ("status_refresh", "3")):
        job = jobs[job_id]
        assert isinstance(job.trigger, CronTrigger)
        assert str(job.trigger.timezone) == str(tz)
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert (fields["hour"], fields["minute"]) == (hour, "0")
        assert job.coalesce is True
        assert job.max_instances == 1
        assert job.misfire_grace_time == MISFIRE_GRACE_S
    assert jobs["status_refresh"].args == ("status_refresh", StatusRefreshParams(batch_size=42))


async def test_scheduled_job_records_a_crawl_run(
    db_session, qdrant, lock_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every-minute cron (as the owner tests it), fired now instead of waiting."""
    monkeypatch.setattr(settings, "scheduler_daily_new_cron", "* * * * *")
    monkeypatch.setattr(settings, "scheduler_status_refresh_cron", "* * * * *")

    @asynccontextmanager
    async def shared():
        yield db_session

    @asynccontextmanager
    async def context() -> AsyncIterator[JobContext]:
        yield JobContext(
            session_factory=shared,
            lock_engine=lock_engine,
            qdrant=qdrant,
            client=sample_site(),
            embedder=FakeEmbedder(),
        )

    done = asyncio.Event()

    class Runner(JobRunner):
        async def run(self, job_type, params=None, run_id=None):  # type: ignore[no-untyped-def]
            try:
                return await super().run(job_type, params, run_id)
            finally:
                done.set()

    scheduler = create_scheduler(Runner(context))
    every_minute = scheduler.get_job("status_refresh").trigger
    fields = {f.name: str(f) for f in every_minute.fields}
    assert (fields["hour"], fields["minute"]) == ("*", "*")

    scheduler.remove_job("daily_new")  # one job at a time on the shared test session
    scheduler.start()
    try:
        scheduler.get_job("status_refresh").modify(
            next_run_time=datetime.now(ZoneInfo(settings.tz_app))
        )
        await asyncio.wait_for(done.wait(), timeout=30)
    finally:
        scheduler.shutdown(wait=False)

    runs = list(
        await db_session.scalars(select(CrawlRun).where(CrawlRun.job_type == "status_refresh"))
    )
    assert [r.status for r in runs] == ["success"]
    assert runs[0].params == {"batch_size": settings.scheduler_status_refresh_batch_size}
