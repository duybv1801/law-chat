"""FastAPI application entry point."""

# Must be imported first: exports PYTORCH_ENABLE_MPS_FALLBACK / HF_HOME before torch loads.
from app.core.config import settings  # isort: skip

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.auth.router import router as auth_router
from app.core.csrf import CsrfHeaderMiddleware
from app.core.errors import register_error_handlers
from app.db.session import engine
from app.ingest.chunker import default_token_counter
from app.ingest.embedder import get_embedder
from app.llm.registry import get_registry
from app.settings.router import models_router
from app.settings.router import router as settings_router

HEALTH_CHECK_TIMEOUT_S = 3.0

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_registry()  # fail fast on a broken config/models.yaml (raises RegistryError)
    qdrant: AsyncQdrantClient | None = None
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
        yield
    finally:
        if qdrant is not None:
            await qdrant.close()
        await engine.dispose()


app = FastAPI(title="Law Chatbot API", lifespan=lifespan)
app.add_middleware(CsrfHeaderMiddleware)
register_error_handlers(app)
app.include_router(auth_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(models_router, prefix="/api")


async def check_postgres() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_qdrant(client: AsyncQdrantClient) -> None:
    await client.get_collections()


async def _probe(name: str, check: Awaitable[None]) -> str:
    try:
        await asyncio.wait_for(check, timeout=HEALTH_CHECK_TIMEOUT_S)
    except Exception as exc:
        # Only the exception type: messages may echo connection details.
        logger.warning("health check %s failed: %s", name, type(exc).__name__)
        return "error"
    return "ok"


@app.get("/api/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    postgres, qdrant = await asyncio.gather(
        _probe("postgres", check_postgres()),
        _probe("qdrant", check_qdrant(request.app.state.qdrant)),
    )
    healthy = postgres == "ok" and qdrant == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if healthy else "degraded", "postgres": postgres, "qdrant": qdrant}
