"""FastAPI application entry point."""

# Must be imported first: exports PYTORCH_ENABLE_MPS_FALLBACK / HF_HOME before torch loads.
from app.core.config import settings  # isort: skip

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response, status
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.auth.router import router as auth_router
from app.core.csrf import CsrfHeaderMiddleware
from app.core.errors import register_error_handlers
from app.db.session import SessionLocal, engine
from app.documents.router import router as documents_router
from app.documents.service import resume_interrupted_uploads
from app.ingest.chunker import default_token_counter
from app.ingest.embedder import get_embedder
from app.ingest.indexer import (
    CollectionMismatchError,
    count_pending_payloads,
    ensure_collection,
    sync_pending_payloads,
)
from app.llm.registry import get_registry
from app.settings.router import models_router
from app.settings.router import router as settings_router

HEALTH_CHECK_TIMEOUT_S = 3.0
PAYLOAD_SYNC_INTERVAL_S = 60

logger = logging.getLogger(__name__)


async def payload_sync_loop(qdrant: AsyncQdrantClient) -> None:
    """Outbox worker: copy metadata to the Qdrant payload when Qdrant missed it (it was down).

    Runs on every machine (each has its own Postgres + Qdrant), unlike the crawl scheduler.
    """
    while True:
        await asyncio.sleep(PAYLOAD_SYNC_INTERVAL_S)
        try:
            async with SessionLocal() as session:
                await sync_pending_payloads(session, qdrant)
        except Exception as exc:
            logger.warning("Payload sync run failed: %s", type(exc).__name__)


async def resume_uploads(qdrant: AsyncQdrantClient) -> None:
    try:
        await resume_interrupted_uploads(SessionLocal, qdrant)
    except Exception as exc:
        logger.warning("Could not resume interrupted uploads: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_registry()  # fail fast on a broken config/models.yaml (raises RegistryError)
    qdrant: AsyncQdrantClient | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        # Both may download once; load them in parallel, off the event loop.
        tokenizer, embedder = await asyncio.gather(
            asyncio.to_thread(default_token_counter),
            asyncio.to_thread(get_embedder),
            return_exceptions=True,
        )
        if isinstance(tokenizer, Exception):
            # Chat and the rest of the app work without it; chunking retries on first use.
            logger.warning("Could not load the chunker tokenizer", exc_info=tokenizer)
        elif isinstance(tokenizer, BaseException):
            raise tokenizer
        if isinstance(embedder, BaseException):
            raise embedder  # fail fast: chat and indexing need the embedding model
        qdrant = app.state.qdrant = AsyncQdrantClient(
            # Version check would hit the network at startup; /api/health covers connectivity.
            url=settings.qdrant_url,
            check_compatibility=False,
        )
        try:
            await ensure_collection(qdrant, embedder)
        except CollectionMismatchError:
            raise  # fail fast: the collection belongs to another embedding model
        except Exception as exc:
            # Qdrant not up yet: auth and settings still work, /api/health reports it, and
            # indexing checks the collection on first use.
            logger.warning("Could not check the Qdrant collection: %s", type(exc).__name__)
        tasks.append(asyncio.create_task(payload_sync_loop(qdrant)))
        tasks.append(asyncio.create_task(resume_uploads(qdrant)))
        yield
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if qdrant is not None:
            await qdrant.close()
        await engine.dispose()


app = FastAPI(title="Law Chatbot API", lifespan=lifespan)
app.add_middleware(CsrfHeaderMiddleware)
register_error_handlers(app)
app.include_router(auth_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(documents_router, prefix="/api")


async def check_postgres() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_qdrant(client: AsyncQdrantClient) -> None:
    await client.get_collections()


async def count_payload_pending() -> int:
    async with SessionLocal() as session:
        return await count_pending_payloads(session)


async def _pending() -> int | None:
    try:
        return await asyncio.wait_for(count_payload_pending(), timeout=HEALTH_CHECK_TIMEOUT_S)
    except Exception:
        return None  # Postgres down: already reported by its probe


async def _probe(name: str, check: Awaitable[None]) -> str:
    try:
        await asyncio.wait_for(check, timeout=HEALTH_CHECK_TIMEOUT_S)
    except Exception as exc:
        # Only the exception type: messages may echo connection details.
        logger.warning("health check %s failed: %s", name, type(exc).__name__)
        return "error"
    return "ok"


@app.get("/api/health")
async def health(request: Request, response: Response) -> dict[str, str | int | None]:
    postgres, qdrant, payload_pending = await asyncio.gather(
        _probe("postgres", check_postgres()),
        _probe("qdrant", check_qdrant(request.app.state.qdrant)),
        _pending(),
    )
    healthy = postgres == "ok" and qdrant == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "degraded",
        "postgres": postgres,
        "qdrant": qdrant,
        # Documents whose metadata Qdrant has not received yet (synced every minute).
        "payload_pending": payload_pending,
    }
