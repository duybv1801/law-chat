import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select

from app.core.config import settings
from app.seed import bundle
from app.seed.bundle import (
    DUMP_FILE,
    MANIFEST_FILE,
    SNAPSHOT_FILE,
    BundleError,
    PgTarget,
    backfill_summary,
    diff_ids,
    find_bundle,
    import_instructions,
    new_staging,
    pg_command,
    publish,
    read_manifest,
    verify_files,
)

TARGET = PgTarget("db.local", 5433, "law", "secret", "law_seed")


# --- Pure logic -------------------------------------------------------------------------


def test_pg_target_from_url_roundtrip() -> None:
    target = PgTarget.from_url("postgresql+asyncpg://law:p%40ss@db:5433/law")
    assert target == PgTarget("db", 5433, "law", "p@ss", "law")
    assert PgTarget.from_url(target.sqlalchemy_url()) == target
    assert target.with_database("law_seed").database == "law_seed"


def test_pg_command_local_passes_password_in_env_only() -> None:
    argv, env = pg_command("pg_dump", TARGET, ["-Fc"], "local", pg_bin=Path("/opt/pg/bin"))
    assert argv == [
        str(Path("/opt/pg/bin/pg_dump")), "-h", "db.local", "-p", "5433", "-U", "law", "-Fc"
    ]  # fmt: skip
    assert env == {"PGPASSWORD": "secret"}
    assert "secret" not in " ".join(argv)


def test_pg_command_docker_runs_in_compose_service() -> None:
    argv, env = pg_command("pg_restore", TARGET, ["-d", "x"], "docker", compose_file=Path("dc.yml"))
    assert argv[:7] == ["docker", "compose", "-f", "dc.yml", "exec", "-T", "postgres"]
    assert argv[7:] == ["pg_restore", "-U", "law", "-d", "x"]
    assert env == {}


@pytest.mark.parametrize("name", ["law", "law_seed", "_x1"])
def test_check_identifier_accepts_plain_names(name: str) -> None:
    assert bundle.check_identifier(name) == name


@pytest.mark.parametrize("name", ["", "1law", 'law"; DROP', "a-b", "x" * 64, "../x"])
def test_check_identifier_rejects_others(name: str) -> None:
    with pytest.raises(BundleError):
        bundle.check_identifier(name)


def test_diff_ids() -> None:
    orphans, missing = diff_ids(["a", "b", "x"], {"a", "b", "c"})
    assert orphans == ["x"]
    assert missing == {"c"}
    assert diff_ids([], set()) == ([], set())


def test_backfill_summary_complete_only_without_checkpoint() -> None:
    base = {"status": "success", "started_at": "t0", "stats": {"created": 3}}
    done = backfill_summary(base | {"params": {"checkpoint": None, "order": "newest"}})
    assert done is not None and done["complete"] and done["order"] == "newest"
    limited = backfill_summary(base | {"params": {"checkpoint": {"group": "Luật"}}})
    assert limited is not None and not limited["complete"]
    failed = backfill_summary(base | {"status": "failed", "params": {"checkpoint": None}})
    assert failed is not None and not failed["complete"]
    assert backfill_summary(None) is None


def _fake_bundle(path: Path, content: bytes = b"dump") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / DUMP_FILE).write_bytes(content)
    (path / SNAPSHOT_FILE).write_bytes(b"snapshot")
    stats = {
        "documents_by_source": {"vbpl": 2},
        "chunks": 5,
        "postgres_version": "16.4",
        "alembic_revision": "0003",
    }
    qdrant = {"points": 5, "embedding_model": "BAAI/bge-m3", "vector_size": 1024, "version": "1"}
    manifest = bundle.build_manifest(
        stats, qdrant, [path / DUMP_FILE, path / SNAPSHOT_FILE], {"git_commit": "abc"}
    )
    (path / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_manifest_records_files_and_counts(tmp_path: Path) -> None:
    manifest = read_manifest(_fake_bundle(tmp_path / "b"))
    assert manifest["documents"] == 2 and manifest["points"] == 5
    assert manifest["git_commit"] == "abc"
    assert manifest["files"][DUMP_FILE]["size"] == 4
    verify_files(tmp_path / "b", manifest)
    text = import_instructions(manifest)
    assert "BAAI/bge-m3" in text and DUMP_FILE in text and "app.seed.cli restore" in text


def test_verify_files_detects_corruption(tmp_path: Path) -> None:
    path = _fake_bundle(tmp_path / "b")
    manifest = read_manifest(path)
    (path / DUMP_FILE).write_bytes(b"dumq")  # same size, other content
    with pytest.raises(BundleError, match=DUMP_FILE):
        verify_files(path, manifest)
    (path / DUMP_FILE).write_bytes(b"dump")
    (path / SNAPSHOT_FILE).unlink()
    with pytest.raises(BundleError, match="missing"):
        verify_files(path, manifest)


def test_read_manifest_rejects_incomplete_or_unknown(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="not a complete bundle"):
        read_manifest(tmp_path)
    (tmp_path / MANIFEST_FILE).write_text(json.dumps({"format_version": 99}))
    with pytest.raises(BundleError, match="Unsupported"):
        read_manifest(tmp_path)


def test_publish_rotates_latest_and_previous(tmp_path: Path) -> None:
    root = tmp_path / "drive"
    assert find_bundle(root) is None

    first = publish(_fake_bundle(new_staging(root), b"v1"), root)
    assert first == root / "latest" and (first / DUMP_FILE).read_bytes() == b"v1"

    (root / ".staging-stale").mkdir()  # left by a crash
    staging = _fake_bundle(root / ".staging-new", b"v2")
    publish(staging, root)
    assert (root / "latest" / DUMP_FILE).read_bytes() == b"v2"
    assert (root / "previous" / DUMP_FILE).read_bytes() == b"v1"
    assert not list(root.glob(".staging-*"))

    publish(_fake_bundle(root / ".staging-3", b"v3"), root, keep_previous=False)
    assert (root / "latest" / DUMP_FILE).read_bytes() == b"v3"
    assert (root / "previous" / DUMP_FILE).read_bytes() == b"v1"


def test_publish_refuses_incomplete_staging(tmp_path: Path) -> None:
    staging = tmp_path / ".staging-x"
    staging.mkdir()
    with pytest.raises(BundleError):
        publish(staging, tmp_path)
    assert not (tmp_path / "latest").exists()


def test_find_bundle_falls_back_to_previous(tmp_path: Path) -> None:
    # Crash between "latest -> previous" and "staging -> latest".
    _fake_bundle(tmp_path / "previous")
    assert find_bundle(tmp_path) == tmp_path / "previous"
    _fake_bundle(tmp_path / "latest")
    assert find_bundle(tmp_path) == tmp_path / "latest"


def test_copy_bundle_copies_manifest_last(tmp_path: Path) -> None:
    src = _fake_bundle(tmp_path / "src")
    bundle.copy_bundle(src, tmp_path / "dst")
    verify_files(tmp_path / "dst", read_manifest(tmp_path / "dst"))


# --- Integration: export -> restore through the docker compose services ---------------


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(
        ["docker", "compose", "-f", str(bundle.DEFAULT_COMPOSE_FILE), "exec", "-T",
         "postgres", "pg_isready", "-q"],
        capture_output=True,
        check=False,
    )  # fmt: skip
    return proc.returncode == 0


@pytest.fixture
async def seed_env(monkeypatch: pytest.MonkeyPatch):
    """Two throwaway databases (`*_test`) and two Qdrant collections, dropped afterwards."""
    if not _docker_ready():
        pytest.skip("docker compose postgres service not running")
    base = PgTarget.from_url(settings.database_url.get_secret_value())
    suffix = uuid.uuid4().hex[:8]
    src = base.with_database(f"seed_src_{suffix}_test")
    dst = base.with_database(f"seed_dst_{suffix}_test")
    collections = (f"test_seed_src_{suffix}", f"test_seed_dst_{suffix}")
    monkeypatch.setattr(settings, "qdrant_collection", collections[0])
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        await bundle.create_database(src)
        bundle.run_migrations(src)
        yield src, dst, collections, qdrant
    finally:
        for target in (src, dst):
            await bundle.drop_database(target)
        for name in collections:
            if await qdrant.collection_exists(name):
                await qdrant.delete_collection(name)
        await qdrant.close()


async def test_export_then_restore_roundtrip(seed_env, tmp_path: Path) -> None:
    from app.db.models import LegalChunk, LegalDocument, User
    from app.ingest.embedder import FakeEmbedder
    from app.ingest.indexer import ensure_collection, index_document
    from app.ingest.schemas import LegalDocumentIn

    src, dst, (src_collection, dst_collection), qdrant = seed_env
    embedder = FakeEmbedder()
    await ensure_collection(qdrant, embedder)
    text = (Path(__file__).parent / "fixtures" / "chunker" / "luat_mau.txt").read_text("utf-8")
    async with bundle.open_session(src) as session:
        for source_id in ("1001", "1002"):
            doc = LegalDocumentIn.model_validate(
                {
                    "source": "vbpl",
                    "source_id": source_id,
                    "title": f"Luật Mẫu {source_id}",
                    "so_hieu": f"{source_id}/2019/QH14",
                    "doc_type": "Luật",
                    "status": "con_hieu_luc",
                }
            )
            await index_document(session, qdrant, doc, text, embedder=embedder)
        chunks = await session.scalar(select(func.count()).select_from(LegalChunk))
    # An orphan point (indexing cancelled after the Qdrant upsert): removed by the export.
    from qdrant_client import models as qm

    orphan = str(uuid.uuid4())
    vector = embedder.embed_documents(["x"])[0]
    await qdrant.upsert(
        src_collection,
        points=[
            qm.PointStruct(
                id=orphan,
                vector={
                    "dense": vector.dense,
                    "sparse": qm.SparseVector(
                        indices=list(vector.sparse), values=list(vector.sparse.values())
                    ),
                },
                payload={"source": "vbpl"},
            )
        ],
        wait=True,
    )

    out = tmp_path / "bundle"
    manifest = await bundle.export_bundle(out, target=src, via="docker", extra={"k": "v"})
    assert manifest["documents"] == 2
    assert manifest["chunks"] == chunks == manifest["points"]
    assert manifest["reconcile"]["orphans_deleted"] == 1
    assert manifest["embedding_model"] == embedder.model_name
    assert manifest["k"] == "v"
    assert (out / bundle.IMPORT_FILE).is_file()
    verify_files(out, read_manifest(out))

    result = await bundle.restore_bundle(
        out,
        target=dst,
        via="docker",
        qdrant_url=settings.qdrant_url,
        collection=dst_collection,
        expected_model=embedder.model_name,
    )
    assert result["documents"] == 2 and result["chunks"] == chunks == result["points"]
    info = bundle.collection_info(settings.qdrant_url, dst_collection)
    assert info is not None
    assert info["config"]["metadata"]["embedding_model"] == embedder.model_name
    async with bundle.open_session(dst) as session:
        titles = set(await session.scalars(select(LegalDocument.title)))
        assert titles == {"Luật Mẫu 1001", "Luật Mẫu 1002"}

    # A second restore into the now non-empty targets is refused without replace.
    with pytest.raises(BundleError, match="not empty"):
        await bundle.restore_bundle(
            out, target=dst, via="docker", qdrant_url=settings.qdrant_url,
            collection=dst_collection,
        )  # fmt: skip
    with pytest.raises(BundleError, match="EMBEDDING_MODEL"):
        await bundle.restore_bundle(
            out, target=dst, via="docker", qdrant_url=settings.qdrant_url,
            collection=dst_collection, expected_model="other/model",
        )  # fmt: skip

    # Export refuses a database with users (they would be shipped).
    async with bundle.open_session(src) as session:
        session.add(User(email="u@example.test", password_hash="x"))
        await session.commit()
    with pytest.raises(BundleError, match="crawler-only"):
        await bundle.export_bundle(tmp_path / "private", target=src, via="docker")
