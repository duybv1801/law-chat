"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup/shutdown hooks (DB, Qdrant, scheduler) are added in later tasks.
    yield


app = FastAPI(title="Law Chatbot API", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
