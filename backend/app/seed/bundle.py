"""Seed bundle: export / restore of the pre-crawled vbpl data (built by
`notebooks/seed_colab.ipynb`, restored with `python -m app.seed.cli restore`).

A bundle is a directory holding the native formats of the system's own databases, so it
restores into the docker-compose services as is:
- `law.dump`: `pg_dump -Fc --no-owner --no-acl` of the whole database (PostgreSQL 16,
  schema + data + `alembic_version` + `crawl_runs`, which hold the backfill checkpoint and
  the `daily_new` anchor);
- `legal_chunks.snapshot`: snapshot of the Qdrant collection (same Qdrant version as
  `docker-compose.yml`; a newer Qdrant can read it, an older one may not);
- `manifest.json`: counts, versions, embedding model, size + sha256 of both files. Written
  last: a directory without it is incomplete;
- `IMPORT.md`: how to restore it.

The dump holds every table, so bundles must come from a database only the crawler used:
`export_bundle` refuses one with users or uploaded documents (they would be shipped).

Postgres tools run either `local` (binaries on PATH or `pg_bin`, as on Colab) or `docker`
(inside the compose service, as on the dev machines where Postgres only runs in Docker).
Both stream through stdin / stdout, so the file paths are always host paths.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import anyio
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import URL, func, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import BACKEND_DIR, REPO_ROOT, settings

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
DUMP_FILE = "law.dump"
SNAPSHOT_FILE = "legal_chunks.snapshot"
MANIFEST_FILE = "manifest.json"
IMPORT_FILE = "IMPORT.md"
LATEST_DIR = "latest"
PREVIOUS_DIR = "previous"
STAGING_PREFIX = ".staging-"
DEFAULT_COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

PgVia = Literal["local", "docker"]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SCROLL_BATCH = 10_000
_DELETE_BATCH = 1_000
_COPY_CHUNK = 8 * 1024 * 1024


class BundleError(Exception):
    pass


# --- Postgres ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PgTarget:
    host: str
    port: int
    user: str
    password: str | None
    database: str

    @classmethod
    def from_url(cls, url: str) -> "PgTarget":
        parsed = make_url(url)
        return cls(
            host=parsed.host or "localhost",
            port=parsed.port or 5432,
            user=parsed.username or "postgres",
            password=parsed.password,
            database=parsed.database or "postgres",
        )

    def with_database(self, database: str) -> "PgTarget":
        return replace(self, database=database)

    def sqlalchemy_url(self) -> str:
        url = URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )
        return url.render_as_string(hide_password=False)


def check_identifier(name: str) -> str:
    """Database / collection names are interpolated into SQL and URLs: plain names only."""
    if not _IDENTIFIER.match(name):
        raise BundleError(f"Invalid name {name!r}: letters, digits and _ only")
    return name


def pg_command(
    tool: str,
    target: PgTarget,
    args: list[str],
    via: PgVia,
    *,
    pg_bin: Path | None = None,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    service: str = "postgres",
) -> tuple[list[str], dict[str, str]]:
    """argv and extra environment for a Postgres client tool.

    `docker` runs the tool inside the compose service over its local socket (trusted by
    the postgres image), so host / port / password of the URL are not used there."""
    if via == "local":
        exe = str(pg_bin / tool) if pg_bin else tool
        argv = [exe, "-h", target.host, "-p", str(target.port), "-U", target.user, *args]
        env = {"PGPASSWORD": target.password} if target.password else {}
        return argv, env
    argv = ["docker", "compose", "-f", str(compose_file), "exec", "-T", service, tool]
    return [*argv, "-U", target.user, *args], {}


def _run_pg(
    tool: str,
    target: PgTarget,
    args: list[str],
    via: PgVia,
    *,
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
    pg_bin: Path | None = None,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
) -> None:
    argv, extra_env = pg_command(tool, target, args, via, pg_bin=pg_bin, compose_file=compose_file)
    stdin = stdin_path.open("rb") if stdin_path else subprocess.DEVNULL
    stdout = stdout_path.open("wb") if stdout_path else subprocess.DEVNULL
    try:
        proc = subprocess.run(
            argv,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            env=os.environ | extra_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BundleError(
            f"{argv[0]} not found: install the PostgreSQL 16 client, or use via='docker'"
        ) from exc
    finally:
        for handle in (stdin, stdout):
            if not isinstance(handle, int):
                handle.close()
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()[-2000:]
        raise BundleError(f"{tool} failed (exit {proc.returncode}): {stderr}")


def dump_postgres(target: PgTarget, out: Path, via: PgVia, **kwargs: Any) -> None:
    args = ["-d", target.database, "-Fc", "--no-owner", "--no-acl"]
    _run_pg("pg_dump", target, args, via, stdout_path=out, **kwargs)


def restore_postgres(target: PgTarget, dump: Path, via: PgVia, **kwargs: Any) -> None:
    args = [
        "-d", target.database,
        "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction",
    ]  # fmt: skip
    _run_pg("pg_restore", target, args, via, stdin_path=dump, **kwargs)


@asynccontextmanager
async def _engine(target: PgTarget, autocommit: bool = False) -> AsyncIterator[Any]:
    kwargs: dict[str, Any] = {"poolclass": NullPool}
    if autocommit:
        kwargs["isolation_level"] = "AUTOCOMMIT"
    engine = create_async_engine(target.sqlalchemy_url(), **kwargs)
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def open_session(target: PgTarget) -> AsyncIterator[AsyncSession]:
    async with (
        _engine(target) as engine,
        async_sessionmaker(engine, expire_on_commit=False)() as session,
    ):
        yield session


DbState = Literal["missing", "empty", "has_tables"]


async def database_state(target: PgTarget) -> DbState:
    """`empty` = exists without tables in `public` (extensions alone are fine)."""
    async with (
        _engine(target.with_database("postgres"), autocommit=True) as engine,
        engine.connect() as conn,
    ):
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target.database},
        )
    if not exists:
        return "missing"
    async with _engine(target) as engine, engine.connect() as conn:
        tables = await conn.scalar(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        )
    return "has_tables" if tables else "empty"


async def create_database(target: PgTarget, *, drop_existing: bool = False) -> None:
    name = check_identifier(target.database)
    async with (
        _engine(target.with_database("postgres"), autocommit=True) as engine,
        engine.connect() as conn,
    ):
        if drop_existing:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))


async def drop_database(target: PgTarget) -> None:
    name = check_identifier(target.database)
    async with (
        _engine(target.with_database("postgres"), autocommit=True) as engine,
        engine.connect() as conn,
    ):
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def run_migrations(target: PgTarget) -> None:
    """`alembic upgrade head` on `target`, in a subprocess (Alembic runs its own loop)."""
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=os.environ | {"DATABASE_URL": target.sqlalchemy_url()},
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise BundleError(f"alembic upgrade head failed: {proc.stderr.strip()[-2000:]}")


# --- Qdrant -----------------------------------------------------------------------------


def _qdrant(url: str) -> httpx.Client:
    return httpx.Client(base_url=url.rstrip("/"), timeout=httpx.Timeout(60.0, read=None))


def qdrant_version(url: str) -> str:
    with _qdrant(url) as client:
        response = client.get("/")
        response.raise_for_status()
        return str(response.json().get("version"))


def collection_info(url: str, collection: str) -> dict[str, Any] | None:
    with _qdrant(url) as client:
        response = client.get(f"/collections/{check_identifier(collection)}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["result"]


def count_points(url: str, collection: str) -> int:
    with _qdrant(url) as client:
        response = client.post(
            f"/collections/{check_identifier(collection)}/points/count", json={"exact": True}
        )
        response.raise_for_status()
        return int(response.json()["result"]["count"])


def snapshot_collection(url: str, collection: str, out: Path) -> None:
    """Create a snapshot on the server, download it to `out`, delete it on the server."""
    base = f"/collections/{check_identifier(collection)}/snapshots"
    with _qdrant(url) as client:
        response = client.post(base, params={"wait": "true"})
        response.raise_for_status()
        name = response.json()["result"]["name"]
        try:
            with client.stream("GET", f"{base}/{name}") as stream, out.open("wb") as f:
                stream.raise_for_status()
                for chunk in stream.iter_bytes(_COPY_CHUNK):
                    f.write(chunk)
        finally:
            client.delete(f"{base}/{name}")


def restore_collection(url: str, collection: str, snapshot: Path) -> None:
    """Upload a snapshot as `collection`, replacing its content (`priority=snapshot`)."""
    with _qdrant(url) as client, snapshot.open("rb") as f:
        response = client.post(
            f"/collections/{check_identifier(collection)}/snapshots/upload",
            params={"priority": "snapshot", "wait": "true"},
            files={"snapshot": (snapshot.name, f, "application/octet-stream")},
        )
        if response.is_error:
            raise BundleError(f"Qdrant snapshot upload failed: {response.text[:2000]}")


def delete_collection(url: str, collection: str) -> None:
    with _qdrant(url) as client:
        client.delete(f"/collections/{check_identifier(collection)}").raise_for_status()


# --- Consistency ------------------------------------------------------------------------


def diff_ids(point_ids: Iterable[str], chunk_ids: set[str]) -> tuple[list[str], set[str]]:
    """(points without a chunk row, chunk rows without a point)."""
    missing = set(chunk_ids)
    orphans = []
    for point_id in point_ids:
        if point_id in missing:
            missing.discard(point_id)
        else:
            orphans.append(point_id)
    return orphans, missing


async def reconcile_points(
    session: AsyncSession, qdrant: AsyncQdrantClient, collection: str
) -> dict[str, Any]:
    """Delete points that have no chunk row (left by an indexing run cancelled between the
    Qdrant upsert and the Postgres commit, or old points not yet cleaned up) and report
    chunks without a point (their documents need a re-index). Point id = chunk id."""
    from app.db.models import LegalChunk

    chunks = await session.scalar(select(func.count()).select_from(LegalChunk)) or 0
    points = (await qdrant.count(collection, exact=True)).count
    result: dict[str, Any] = {"chunks": chunks, "points": points, "orphans_deleted": 0}
    result["missing_documents"] = []
    if chunks == points:
        return result

    chunk_ids = {str(i) for i in await session.scalars(select(LegalChunk.id))}
    point_ids: list[str] = []
    offset = None
    while True:
        records, offset = await qdrant.scroll(
            collection, limit=_SCROLL_BATCH, offset=offset, with_payload=False, with_vectors=False
        )
        point_ids.extend(str(r.id) for r in records)
        if offset is None:
            break
    orphans, missing = diff_ids(point_ids, chunk_ids)
    for start in range(0, len(orphans), _DELETE_BATCH):
        await qdrant.delete(
            collection,
            points_selector=qm.PointIdsList(points=orphans[start : start + _DELETE_BATCH]),
            wait=True,
        )
    if missing:
        rows = await session.scalars(
            select(LegalChunk.document_id).where(LegalChunk.id.in_(list(missing))).distinct()
        )
        result["missing_documents"] = [str(r) for r in rows]
    result["orphans_deleted"] = len(orphans)
    result["points"] = points - len(orphans)
    if orphans:
        logger.warning("Deleted %d orphan Qdrant points", len(orphans))
    return result


# --- Manifest ---------------------------------------------------------------------------


def backfill_summary(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Last backfill run; `complete` = it walked the whole scope (a run that stopped on
    `limit`, failed or was interrupted keeps a checkpoint)."""
    if run is None:
        return None
    params = run.get("params") or {}
    checkpoint = params.get("checkpoint")
    return {
        "status": run["status"],
        "started_at": run["started_at"],
        "finished_at": run.get("finished_at"),
        "stats": run.get("stats") or {},
        "order": params.get("order", "priority"),
        "filters": params.get("filters"),
        "checkpoint": checkpoint,
        "complete": run["status"] in ("success", "partial") and checkpoint is None,
    }


async def collect_stats(session: AsyncSession) -> dict[str, Any]:
    from app.db.models import CrawlRun, LegalChunk, LegalDocument, User

    async def grouped(column: Any, *where: Any) -> dict[str, int]:
        rows = await session.execute(
            select(column, func.count()).where(*where).group_by(column).order_by(column)
        )
        return {str(k): v for k, v in rows.all()}

    run = (
        await session.execute(
            select(CrawlRun)
            .where(CrawlRun.job_type == "backfill")
            .order_by(CrawlRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    run_dict = None
    if run is not None:
        run_dict = {
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stats": run.stats,
            "params": run.params,
        }
    return {
        "alembic_revision": await session.scalar(text("SELECT version_num FROM alembic_version")),
        "postgres_version": await session.scalar(text("SHOW server_version")),
        "users": await session.scalar(select(func.count()).select_from(User)) or 0,
        "documents_by_source": await grouped(LegalDocument.source),
        "documents_by_status": await grouped(LegalDocument.status, LegalDocument.source == "vbpl"),
        "documents_by_doc_type": await grouped(
            LegalDocument.doc_type, LegalDocument.source == "vbpl"
        ),
        "documents_by_ingest_status": await grouped(LegalDocument.ingest_status),
        "chunks": await session.scalar(select(func.count()).select_from(LegalChunk)) or 0,
        "backfill": backfill_summary(run_dict),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path) -> dict[str, Any]:
    return {"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}


def build_manifest(
    stats: dict[str, Any],
    qdrant: dict[str, Any],
    files: list[Path],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    documents = sum(stats["documents_by_source"].values())
    return {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "documents": documents,
        "chunks": stats["chunks"],
        "points": qdrant["points"],
        "embedding_model": qdrant["embedding_model"],
        "vector_size": qdrant["vector_size"],
        "qdrant_version": qdrant["version"],
        "postgres_version": stats["postgres_version"],
        "alembic_revision": stats["alembic_revision"],
        "files": {f.name: file_entry(f) for f in files},
        "stats": stats,
        **(extra or {}),
    }


def read_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / MANIFEST_FILE
    if not path.is_file():
        raise BundleError(f"{path} not found: not a complete bundle")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BundleError(f"Unsupported bundle format {manifest.get('format_version')!r}")
    return manifest


def verify_files(bundle: Path, manifest: dict[str, Any]) -> None:
    for name, entry in manifest["files"].items():
        path = bundle / name
        if not path.is_file():
            raise BundleError(f"{name} is missing from {bundle}")
        if path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
            raise BundleError(f"{name} does not match the manifest (corrupt or partial copy)")


def import_instructions(manifest: dict[str, Any]) -> str:
    return f"""# Bộ dữ liệu luật dựng sẵn

Tạo lúc {manifest["created_at"]} — {manifest["documents"]} văn bản, {manifest["chunks"]} chunk,
{manifest["points"]} point. Embedding `{manifest["embedding_model"]}`
({manifest["vector_size"]} chiều). PostgreSQL {manifest["postgres_version"]},
Qdrant {manifest["qdrant_version"]}, migration `{manifest["alembic_revision"]}`.

| File | Nội dung |
|---|---|
| `{DUMP_FILE}` | `pg_dump -Fc --no-owner --no-acl` của cả database |
| `{SNAPSHOT_FILE}` | Snapshot Qdrant của collection chunk |
| `{MANIFEST_FILE}` | Số lượng, phiên bản, sha256 từng file |

## Cách 1: lệnh của dự án (khuyên dùng)

Chạy `docker compose up -d` ở gốc repo, rồi trong `backend/`:

```bash
uv run python -m app.seed.cli restore --from /đường/dẫn/tới/bundle
# vào database khác:    --database law_seed
# vào collection khác:  --collection legal_chunks_seed
# ghi đè dữ liệu đang có (XOÁ cả user, hội thoại trong database đó): --replace
```

Lệnh kiểm tra sha256, khôi phục Postgres và Qdrant, chạy `alembic upgrade head`, rồi so số lượng
với manifest. Database đích (mặc định lấy từ `DATABASE_URL`) phải chưa có hoặc còn trống.

## Cách 2: lệnh tay

```bash
docker compose exec -T postgres createdb -U law law
docker compose exec -T postgres pg_restore -U law -d law --no-owner --no-acl \\
    --exit-on-error --single-transaction < {DUMP_FILE}
curl -X POST "http://localhost:6333/collections/legal_chunks/snapshots/upload?priority=snapshot" \\
    -F "snapshot=@{SNAPSHOT_FILE}"
cd backend && uv run alembic upgrade head
```

`EMBEDDING_MODEL` của máy phải trùng `{manifest["embedding_model"]}`. Qdrant phải cùng phiên bản
hoặc mới hơn {manifest["qdrant_version"]}.
"""


# --- Bundle directories (Drive) ---------------------------------------------------------


def new_staging(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = root / f"{STAGING_PREFIX}{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def publish(staging: Path, root: Path, *, keep_previous: bool = True) -> Path:
    """Make `staging` the `latest` bundle; the old `latest` becomes `previous` (or is
    deleted). A crash midway leaves `previous` or `latest` usable (see `find_bundle`)."""
    read_manifest(staging)  # never publish an incomplete bundle
    latest, previous = root / LATEST_DIR, root / PREVIOUS_DIR
    if latest.exists():
        if keep_previous:
            if previous.exists():
                shutil.rmtree(previous)
            latest.rename(previous)
        else:
            shutil.rmtree(latest)
    staging.rename(latest)
    for stale in root.glob(f"{STAGING_PREFIX}*"):
        shutil.rmtree(stale, ignore_errors=True)
    return latest


def find_bundle(root: Path) -> Path | None:
    for name in (LATEST_DIR, PREVIOUS_DIR):
        if (root / name / MANIFEST_FILE).is_file():
            return root / name
    return None


def copy_bundle(src: Path, dst: Path) -> None:
    """Copy the bundle files (manifest last, so a partial copy is never complete)."""
    dst.mkdir(parents=True, exist_ok=True)
    names = [n for n in (DUMP_FILE, SNAPSHOT_FILE, IMPORT_FILE) if (src / n).exists()]
    for name in [*names, MANIFEST_FILE]:
        shutil.copyfile(src / name, dst / name)


# --- Export / restore -------------------------------------------------------------------


async def export_bundle(
    out: Path,
    *,
    target: PgTarget,
    via: PgVia,
    pg_bin: Path | None = None,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    extra: dict[str, Any] | None = None,
    allow_private: bool = False,
) -> dict[str, Any]:
    """Write a complete bundle of `target` + `settings.qdrant_collection` into `out`.
    No crawl / indexing job may run meanwhile (the two dumps would disagree)."""
    from app.db.models import LegalDocument
    from app.ingest.indexer import count_pending_payloads, sync_pending_payloads

    collection = settings.qdrant_collection
    await anyio.Path(out).mkdir(parents=True, exist_ok=True)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, timeout=600, check_compatibility=False)
    try:
        async with open_session(target) as session:
            stats = await collect_stats(session)
            private = stats["users"] + await session.scalar(
                select(func.count())
                .select_from(LegalDocument)
                .where(LegalDocument.source != "vbpl")
            )
            if private and not allow_private:
                raise BundleError(
                    f"Database {target.database} has {stats['users']} users / uploaded "
                    "documents: a bundle must come from a crawler-only database."
                )
            while await count_pending_payloads(session):
                if not await sync_pending_payloads(session, qdrant):
                    raise BundleError("Qdrant payload sync is stuck (is Qdrant up?)")
            reconcile = await reconcile_points(session, qdrant, collection)
            stats = await collect_stats(session)
    finally:
        await qdrant.close()

    info = collection_info(settings.qdrant_url, collection)
    if info is None:
        raise BundleError(f"Qdrant collection {collection} does not exist")
    vectors = info["config"]["params"]["vectors"]
    qdrant_meta = {
        "version": qdrant_version(settings.qdrant_url),
        "points": count_points(settings.qdrant_url, collection),
        "embedding_model": (info["config"].get("metadata") or {}).get("embedding_model"),
        "vector_size": vectors["dense"]["size"],
    }
    dump = out / DUMP_FILE
    snapshot = out / SNAPSHOT_FILE
    logger.info("pg_dump %s -> %s", target.database, dump)
    dump_postgres(target, dump, via, pg_bin=pg_bin, compose_file=compose_file)
    logger.info("Qdrant snapshot %s -> %s", collection, snapshot)
    snapshot_collection(settings.qdrant_url, collection, snapshot)

    manifest = build_manifest(
        stats, qdrant_meta, [dump, snapshot], (extra or {}) | {"reconcile": reconcile}
    )
    (out / IMPORT_FILE).write_text(import_instructions(manifest), encoding="utf-8")
    tmp = out / f"{MANIFEST_FILE}.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out / MANIFEST_FILE)
    return manifest


async def restore_bundle(
    bundle: Path,
    *,
    target: PgTarget,
    via: PgVia,
    qdrant_url: str,
    collection: str,
    pg_bin: Path | None = None,
    compose_file: Path = DEFAULT_COMPOSE_FILE,
    replace_existing: bool = False,
    verify_checksums: bool = True,
    expected_model: str | None = None,
    migrate: bool = True,
) -> dict[str, Any]:
    """Restore `bundle` into database `target` and Qdrant `collection`, then compare the
    counts with the manifest. Refuses non-empty targets unless `replace_existing`."""
    check_identifier(target.database)
    manifest = read_manifest(bundle)
    if expected_model and manifest["embedding_model"] != expected_model:
        raise BundleError(
            f"Bundle embedded with {manifest['embedding_model']!r}, "
            f"this machine uses EMBEDDING_MODEL={expected_model!r}"
        )
    if verify_checksums:
        verify_files(bundle, manifest)

    state = await database_state(target)
    info = collection_info(qdrant_url, collection)
    has_points = info is not None and count_points(qdrant_url, collection) > 0
    if not replace_existing and (state == "has_tables" or has_points):
        raise BundleError(
            f"Database {target.database} ({state}) or collection {collection} "
            f"({'has points' if has_points else 'empty'}) is not empty: use --replace"
        )

    if state == "missing" or (state == "has_tables" and replace_existing):
        await create_database(target, drop_existing=state != "missing")
    logger.info("pg_restore -> %s", target.database)
    restore_postgres(target, bundle / DUMP_FILE, via, pg_bin=pg_bin, compose_file=compose_file)
    logger.info("Qdrant snapshot -> %s", collection)
    restore_collection(qdrant_url, collection, bundle / SNAPSHOT_FILE)
    if migrate:
        run_migrations(target)

    async with open_session(target) as session:
        stats = await collect_stats(session)
    result = {
        "documents": sum(stats["documents_by_source"].values()),
        "chunks": stats["chunks"],
        "points": count_points(qdrant_url, collection),
        "alembic_revision": stats["alembic_revision"],
    }
    expected = {k: manifest[k] for k in ("documents", "chunks", "points")}
    wrong = {k: (result[k], v) for k, v in expected.items() if result[k] != v}
    if wrong:
        raise BundleError(f"Counts differ from the manifest (got, expected): {wrong}")
    return result
