"""Crawler CLI: `uv run python -m app.crawler.cli fetch-one <source_id | url> [--dry-run]`."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import typer
from qdrant_client import AsyncQdrantClient

from app.core.config import settings
from app.crawler.vbpl_client import VbplClient, VbplNotFound
from app.crawler.vbpl_parser import VbplDocument, VbplError, source_id_from_url
from app.ingest.chunker import chunk_document

cli = typer.Typer(add_completion=False, no_args_is_help=True)


@cli.callback()
def main() -> None:
    """vbpl.vn crawler commands."""


def _print_document(doc: VbplDocument, chars: int) -> None:
    metadata = doc.to_document_in().model_dump(mode="json", exclude={"crawled_at"})
    metadata |= {
        "status_code": doc.status.status_code,
        "updated_date": doc.status.updated_date,
        "has_content": doc.has_content,
        "signers": doc.signers,
        "fields": doc.fields,
    }
    typer.echo(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))
    if doc.text is None:
        typer.echo("\nNo HTML full text (PDF only): this document is not ingested.")
        return
    chunks = chunk_document(doc.text, title=doc.title, so_hieu=doc.so_hieu)
    typer.echo(f"\n{len(doc.text)} characters, {len(chunks)} chunks")
    for chunk in chunks:
        info = {
            k: v
            for k, v in asdict(chunk).items()
            if k not in ("text", "embed_text") and v is not None
        }
        text = chunk.text if chars <= 0 or len(chunk.text) <= chars else chunk.text[:chars] + "…"
        typer.echo(f"\n--- chunk {json.dumps(info, ensure_ascii=False)}\n{text}")


async def _index(doc: VbplDocument) -> str:
    # Only when writing: --dry-run needs neither Postgres nor Qdrant.
    from app.db.session import SessionLocal, engine
    from app.ingest.indexer import index_document

    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        async with SessionLocal() as session:
            document_id = await index_document(
                session, qdrant, doc.to_document_in(crawled_at=datetime.now(UTC)), doc.text or ""
            )
    finally:
        await qdrant.close()
        await engine.dispose()
    return str(document_id)


async def _fetch_one(ref: str, dry_run: bool, chars: int) -> None:
    url = ref if ref.startswith(("http://", "https://")) else None
    source_id = source_id_from_url(ref) if url else ref
    if not source_id:
        typer.echo(f"Cannot find a document id in {ref!r} (expected '...--<id>').", err=True)
        raise typer.Exit(1)
    async with VbplClient() as client:
        try:
            doc = await client.get_document(source_id, url)
        except VbplNotFound:
            typer.echo(f"Document {source_id} not found on vbpl.vn (404).", err=True)
            raise typer.Exit(1) from None
        except (VbplError, httpx.HTTPError) as exc:  # httpx: network errors after retries
            typer.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
            raise typer.Exit(1) from None
    if dry_run:
        _print_document(doc, chars)
        return
    if doc.text is None:
        typer.echo(f"Document {source_id} has no HTML full text: skipped.")
        return
    typer.echo(f"Indexed {source_id} as document {await _index(doc)}.")


@cli.command("fetch-one")
def fetch_one(
    ref: str = typer.Argument(help="vbpl.vn document id, or the detail page URL"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print metadata + chunks, write nothing"),
    chars: int = typer.Option(300, help="Characters of each chunk to print (0 = all)"),
) -> None:
    """Fetch one document from vbpl.vn and index it (or only print it with --dry-run)."""
    asyncio.run(_fetch_one(ref, dry_run, chars))


if __name__ == "__main__":
    cli()
