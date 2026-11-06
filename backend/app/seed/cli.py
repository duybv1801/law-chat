"""Seed bundle CLI (`uv run python -m app.seed.cli --help`):

export --out DIR [--database NAME] [--via docker|local] [--allow-private]
restore --from DIR [--database NAME] [--collection NAME] [--replace] [--via docker|local]
verify DIR
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from app.core.config import settings
from app.seed.bundle import (
    DEFAULT_COMPOSE_FILE,
    BundleError,
    PgTarget,
    PgVia,
    export_bundle,
    read_manifest,
    restore_bundle,
    verify_files,
)

cli = typer.Typer(add_completion=False, no_args_is_help=True)

VIA_HELP = "Run pg_dump / pg_restore inside the compose service (docker) or from PATH (local)"


@cli.callback()
def main() -> None:
    """Export / restore the pre-crawled vbpl data (Postgres dump + Qdrant snapshot)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _target(database: str | None) -> PgTarget:
    target = PgTarget.from_url(settings.database_url.get_secret_value())
    return target.with_database(database) if database else target


def _fail(exc: BundleError) -> None:
    typer.echo(f"Lỗi: {exc}", err=True)
    raise typer.Exit(1)


@cli.command()
def export(
    out: Annotated[Path, typer.Option("--out", help="Directory to write the bundle into")],
    database: Annotated[
        str | None, typer.Option(help="Database name (default: DATABASE_URL)")
    ] = None,
    via: Annotated[PgVia, typer.Option(help=VIA_HELP)] = "docker",
    pg_bin: Annotated[Path | None, typer.Option(help="Directory of pg_dump (--via local)")] = None,
    compose_file: Annotated[Path, typer.Option(help="docker-compose.yml")] = DEFAULT_COMPOSE_FILE,
    allow_private: Annotated[
        bool,
        typer.Option("--allow-private", help="Allow users / uploads in the dump (never ship it)"),
    ] = False,
) -> None:
    """Dump the database + the Qdrant collection (QDRANT_COLLECTION) into a bundle."""
    try:
        manifest = asyncio.run(
            export_bundle(
                out,
                target=_target(database),
                via=via,
                pg_bin=pg_bin,
                compose_file=compose_file,
                allow_private=allow_private,
            )
        )
    except BundleError as exc:
        _fail(exc)
    summary = {k: manifest[k] for k in ("documents", "chunks", "points", "embedding_model")}
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@cli.command()
def restore(
    src: Annotated[Path, typer.Option("--from", help="Bundle directory")],
    database: Annotated[
        str | None, typer.Option(help="Database name (default: DATABASE_URL)")
    ] = None,
    collection: Annotated[
        str | None, typer.Option(help="Collection (default: QDRANT_COLLECTION)")
    ] = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Drop the database / collection if they have data")
    ] = False,
    via: Annotated[PgVia, typer.Option(help=VIA_HELP)] = "docker",
    pg_bin: Annotated[
        Path | None, typer.Option(help="Directory of pg_restore (--via local)")
    ] = None,
    compose_file: Annotated[Path, typer.Option(help="docker-compose.yml")] = DEFAULT_COMPOSE_FILE,
    skip_checksum: Annotated[
        bool, typer.Option("--skip-checksum", help="Skip the sha256 check")
    ] = False,
) -> None:
    """Restore a bundle into Postgres + Qdrant, migrate, and check counts."""
    if replace and not typer.confirm(
        "--replace xoá database / collection đích (kể cả user, hội thoại). Tiếp tục?"
    ):
        raise typer.Exit(1)
    try:
        result = asyncio.run(
            restore_bundle(
                src,
                target=_target(database),
                via=via,
                qdrant_url=settings.qdrant_url,
                collection=collection or settings.qdrant_collection,
                pg_bin=pg_bin,
                compose_file=compose_file,
                replace_existing=replace,
                verify_checksums=not skip_checksum,
                expected_model=settings.embedding_model,
            )
        )
    except BundleError as exc:
        _fail(exc)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command()
def verify(src: Annotated[Path, typer.Argument(help="Bundle directory")]) -> None:
    """Check the bundle files against the manifest (size + sha256)."""
    try:
        manifest = read_manifest(src)
        verify_files(src, manifest)
    except BundleError as exc:
        _fail(exc)
    typer.echo(f"OK: {manifest['documents']} documents, {manifest['points']} points.")


if __name__ == "__main__":
    cli()
