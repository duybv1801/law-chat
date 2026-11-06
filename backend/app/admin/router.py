"""Admin API routes: stats, crawl runs, manual jobs, re-index (docs/06-api.md, "Admin").

Every route requires an admin (`require_admin`: 401 if not logged in, 403 for a user).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from pydantic import ValidationError

from app.admin import service
from app.admin.schemas import (
    AdminStats,
    CrawlRequest,
    CrawlRunList,
    CrawlStarted,
    ReindexStarted,
)
from app.core.deps import QdrantDep, SessionDep, SessionFactoryDep, require_admin
from app.core.errors import AppError
from app.crawler.jobs import JobType, is_job_running
from app.scheduler import JobRunner, parse_params

PAGE_SIZE_MAX = 100

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def get_job_runner(request: Request) -> JobRunner:
    """The runner created in the app lifespan."""
    return request.app.state.job_runner


JobRunnerDep = Annotated[JobRunner, Depends(get_job_runner)]


@router.get("/stats")
async def stats(session: SessionDep, qdrant: QdrantDep) -> AdminStats:
    return await service.stats(session, qdrant)


@router.get("/crawl-runs")
async def list_crawl_runs(
    session: SessionDep,
    job_type: JobType | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 20,
) -> CrawlRunList:
    return await service.list_crawl_runs(session, job_type=job_type, page=page, page_size=page_size)


@router.post("/crawl/{job_type}", status_code=status.HTTP_202_ACCEPTED)
async def start_crawl(
    job_type: JobType,
    session: SessionDep,
    runner: JobRunnerDep,
    body: CrawlRequest | None = None,
) -> CrawlStarted:
    try:
        params = parse_params(job_type, body.params if body else None)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()} - {""})
        raise AppError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "Tham số không hợp lệ" + (f": {', '.join(fields)}" if fields else ""),
        ) from None
    if runner.is_running(job_type) or await is_job_running(session, job_type):
        raise AppError(
            status.HTTP_409_CONFLICT, "job_running", "Job này đang chạy, vui lòng chờ chạy xong."
        )
    return CrawlStarted(crawl_run_id=runner.start(job_type, params))


@router.post("/documents/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: uuid.UUID,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    qdrant: QdrantDep,
    background: BackgroundTasks,
) -> ReindexStarted:
    doc = await service.check_reindex(session, document_id)
    if doc.source == "user_upload":
        assert doc.owner_id is not None  # checked by `check_reindex`
        background.add_task(service.reindex_upload, session_factory, qdrant, doc.id, doc.owner_id)
    else:
        background.add_task(service.reindex_vbpl, session_factory, qdrant, doc.id)
    return ReindexStarted(document_id=doc.id)
