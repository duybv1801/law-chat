import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import get_args

import anyio
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import DOCUMENT_SCOPES, DOCUMENT_STATUSES, LegalChunk, LegalDocument, User
from app.ingest import indexer
from app.ingest.chunker import chunk_document
from app.ingest.embedder import EmbeddingResult, FakeEmbedder
from app.ingest.indexer import (
    CollectionMismatchError,
    IndexingError,
    content_hash,
    count_pending_payloads,
    delete_document,
    ensure_collection,
    index_document,
    reindex_document,
    sync_pending_payloads,
    update_document_metadata,
)
from app.ingest.schemas import LegalDocumentIn, Scope, Status, normalize_so_hieu

LUAT_MAU = (Path(__file__).parent / "fixtures" / "chunker" / "luat_mau.txt").read_text("utf-8")


class CountingEmbedder(FakeEmbedder):
    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.fail = fail

    def _embed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model crashed")
        return super()._embed_documents(texts)


class HookEmbedder(CountingEmbedder):
    """Runs `hook` while embedding, like another request arriving during a long embed."""

    def __init__(self, hook: Callable[[], Awaitable[None]]) -> None:
        super().__init__()
        self.hook = hook

    async def aembed_documents(self, texts: list[str]) -> list[EmbeddingResult]:
        await self.hook()
        return await super().aembed_documents(texts)


# --- Pure logic ---


def test_normalize_so_hieu() -> None:
    assert normalize_so_hieu(" 45/2019/qh 14 ") == "45/2019/QH14"
    assert normalize_so_hieu("12/2024/nđ-cp") == "12/2024/NĐ-CP"
    assert normalize_so_hieu("  ") is None
    assert normalize_so_hieu(None) is None


def test_content_hash_is_sha256_of_utf8() -> None:
    assert content_hash("Điều 1") == content_hash("Điều 1")
    assert content_hash("Điều 1") != content_hash("Điều 2")
    assert len(content_hash("")) == 64


def test_schema_literals_match_db_checks() -> None:
    assert get_args(Status) == DOCUMENT_STATUSES
    assert get_args(Scope) == DOCUMENT_SCOPES


def test_metadata_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        LegalDocumentIn(source="vbpl", title="x", owner_id=uuid.uuid4())


# --- Integration: Postgres (law_test) + Qdrant (temporary collection) ---


@pytest.fixture
async def qdrant(monkeypatch: pytest.MonkeyPatch):
    client = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        await client.get_collections()
    except Exception as exc:
        await client.close()
        pytest.skip(f"Qdrant not reachable ({type(exc).__name__}): run `docker compose up -d`")
    name = f"test_indexer_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "qdrant_collection", name)
    try:
        await ensure_collection(client, FakeEmbedder())
        yield client
    finally:
        await client.delete_collection(name)
        await client.close()


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


def _vbpl(**overrides: object) -> LegalDocumentIn:
    values: dict[str, object] = {
        "source": "vbpl",
        "source_id": "139264",
        "title": "Luật Mẫu",
        "so_hieu": "45/2019/QH14",
        "doc_type": "Luật",
        "issued_date": "2019-11-20",
        "effective_date": "2021-01-01",
        "status": "con_hieu_luc",
    }
    values.update(overrides)
    return LegalDocumentIn.model_validate(values)


async def _points(qdrant: AsyncQdrantClient, document_id: uuid.UUID) -> list[qm.Record]:
    records, _ = await qdrant.scroll(
        settings.qdrant_collection,
        scroll_filter=indexer._document_filter(document_id),
        limit=1000,
        with_payload=True,
        with_vectors=True,
    )
    return records


async def _chunks(session: AsyncSession, document_id: uuid.UUID) -> list[LegalChunk]:
    rows = await session.scalars(
        select(LegalChunk).where(LegalChunk.document_id == document_id).order_by(LegalChunk.ordinal)
    )
    return list(rows)


async def test_index_document_writes_chunks_and_points(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    expected = chunk_document(LUAT_MAU, title="Luật Mẫu", so_hieu="45/2019/QH14")
    chunks = await _chunks(db_session, doc_id)
    points = await _points(qdrant, doc_id)
    assert len(expected) > 5
    assert len(chunks) == len(points) == len(expected)
    assert {str(c.id) for c in chunks} == {str(p.id) for p in points}
    assert [c.text for c in chunks] == [c.text for c in expected]

    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None
    assert doc.ingest_status == "done"
    assert doc.ingest_error is None
    assert doc.embedding_model == "fake"
    assert doc.content_hash == content_hash(LUAT_MAU)
    assert doc.so_hieu_norm == "45/2019/QH14"

    by_id = {str(c.id): c for c in chunks}
    for point in points:
        chunk = by_id[str(point.id)]
        expected_payload = {
            "document_id": str(doc_id),
            "source": "vbpl",
            "so_hieu_norm": "45/2019/QH14",
            "doc_type": "Luật",
            "status": "con_hieu_luc",
            "scope": "trung_uong",
            "issued_date": "2019-11-20",
            "effective_date": "2021-01-01",
            "ordinal": chunk.ordinal,
        }
        if chunk.dieu is not None:  # NULL columns (owner_id, preamble's dieu) are left out
            expected_payload["dieu"] = chunk.dieu
        assert point.payload == expected_payload
        assert isinstance(point.vector, dict)
        assert len(point.vector["dense"]) == 1024
        sparse = point.vector["sparse"]
        assert isinstance(sparse, qm.SparseVector) and sparse.indices


async def test_payload_date_index_supports_range_filter(db_session, qdrant, embedder) -> None:
    await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    async def count(**range_args: str) -> int:
        result = await qdrant.count(
            settings.qdrant_collection,
            count_filter=qm.Filter(
                must=[qm.FieldCondition(key="effective_date", range=qm.DatetimeRange(**range_args))]
            ),
            exact=True,
        )
        return result.count

    assert await count(lte="2021-01-01") > 0
    assert await count(gt="2021-01-01") == 0


async def test_same_hash_changes_nothing(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    chunk_ids = [c.id for c in await _chunks(db_session, doc_id)]
    point_ids = {p.id for p in await _points(qdrant, doc_id)}
    calls = embedder.calls

    again = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    assert again == doc_id
    assert embedder.calls == calls  # not re-embedded
    assert [c.id for c in await _chunks(db_session, doc_id)] == chunk_ids
    assert {p.id for p in await _points(qdrant, doc_id)} == point_ids
    total = await db_session.scalar(select(func.count()).select_from(LegalDocument))
    assert total == 1


async def test_same_hash_new_metadata_updates_payload_only(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    chunk_ids = [c.id for c in await _chunks(db_session, doc_id)]
    calls = embedder.calls

    updated = _vbpl(status="het_hieu_luc_toan_bo", expiry_date="2025-07-01")
    await index_document(db_session, qdrant, updated, LUAT_MAU, embedder=embedder)

    assert embedder.calls == calls
    assert [c.id for c in await _chunks(db_session, doc_id)] == chunk_ids
    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None
    assert doc.status == "het_hieu_luc_toan_bo"
    assert str(doc.expiry_date) == "2025-07-01"
    assert {p.payload["status"] for p in await _points(qdrant, doc_id)} == {"het_hieu_luc_toan_bo"}


async def test_changed_text_replaces_chunks(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    old_ids = {c.id for c in await _chunks(db_session, doc_id)}

    new_text = "Điều 1. Phạm vi\nLuật này quy định mới.\n\nĐiều 2. Hiệu lực\nCó hiệu lực ngay."
    again = await index_document(db_session, qdrant, _vbpl(), new_text, embedder=embedder)

    assert again == doc_id
    chunks = await _chunks(db_session, doc_id)
    points = await _points(qdrant, doc_id)
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2"]
    assert {str(c.id) for c in chunks} == {str(p.id) for p in points}
    assert not old_ids & {c.id for c in chunks}
    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None and doc.content_hash == content_hash(new_text)


async def test_delete_document_cleans_both_stores(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    other_id = await index_document(
        db_session, qdrant, _vbpl(source_id="999"), LUAT_MAU, embedder=embedder
    )

    await delete_document(db_session, qdrant, doc_id)

    assert await db_session.get(LegalDocument, doc_id) is None
    assert await _chunks(db_session, doc_id) == []
    assert await _points(qdrant, doc_id) == []
    assert await _points(qdrant, other_id)  # other documents untouched
    await delete_document(db_session, qdrant, doc_id)  # idempotent


async def test_update_document_metadata_syncs_payload(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    await update_document_metadata(
        db_session,
        qdrant,
        doc_id,
        status="het_hieu_luc_mot_phan",
        effective_date=None,
        status_note="Bị sửa đổi bởi Luật 99/2025/QH15",
    )

    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None
    assert doc.status == "het_hieu_luc_mot_phan"
    assert doc.status_note == "Bị sửa đổi bởi Luật 99/2025/QH15"
    for point in await _points(qdrant, doc_id):
        assert point.payload["status"] == "het_hieu_luc_mot_phan"
        assert "effective_date" not in point.payload
        assert point.payload["document_id"] == str(doc_id)


async def test_update_document_metadata_rejects_unknown_field(db_session, qdrant) -> None:
    with pytest.raises(ValueError, match="owner_id"):
        await update_document_metadata(db_session, qdrant, uuid.uuid4(), owner_id=None)
    with pytest.raises(LookupError):
        await update_document_metadata(db_session, qdrant, uuid.uuid4(), status="con_hieu_luc")


async def test_update_metadata_commits_first_and_syncs_later(
    db_session, qdrant, embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None and not doc.payload_dirty
    batch_update_points = qdrant.batch_update_points

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(qdrant, "batch_update_points", broken)
    # Qdrant down: no error, Postgres (source of truth) has the new value, Qdrant lags.
    await update_document_metadata(
        db_session, qdrant, doc_id, status="het_hieu_luc_toan_bo", effective_date=None
    )
    assert doc.status == "het_hieu_luc_toan_bo" and doc.payload_dirty
    assert await count_pending_payloads(db_session) == 1
    assert {p.payload["status"] for p in await _points(qdrant, doc_id)} == {"con_hieu_luc"}
    assert await sync_pending_payloads(db_session, qdrant) == 0  # still down: retried later

    monkeypatch.setattr(qdrant, "batch_update_points", batch_update_points)
    assert await sync_pending_payloads(db_session, qdrant) == 1
    assert not doc.payload_dirty
    assert await count_pending_payloads(db_session) == 0
    for point in await _points(qdrant, doc_id):
        assert point.payload["status"] == "het_hieu_luc_toan_bo"
        assert "effective_date" not in point.payload
        assert point.payload["ordinal"] >= 0  # per-point fields kept


async def test_update_metadata_commit_failure_writes_nothing(
    db_session, qdrant, embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    doc = await db_session.get(LegalDocument, doc_id)
    commit = db_session.commit
    failed = False

    async def broken_commit() -> None:
        nonlocal failed
        if not failed:  # only the update's own commit
            failed = True
            raise ConnectionError("postgres connection lost")
        await commit()

    monkeypatch.setattr(db_session, "commit", broken_commit)
    with pytest.raises(ConnectionError):
        await update_document_metadata(db_session, qdrant, doc_id, status="het_hieu_luc_toan_bo")

    assert doc is not None and doc.status == "con_hieu_luc"  # reloaded, not expired
    assert not doc.payload_dirty
    assert {p.payload["status"] for p in await _points(qdrant, doc_id)} == {"con_hieu_luc"}


async def test_reindex_clears_pending_payload(
    db_session, qdrant, embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    async def broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(qdrant, "batch_update_points", broken)
    await update_document_metadata(db_session, qdrant, doc_id, status="het_hieu_luc_toan_bo")
    await index_document(
        db_session,
        qdrant,
        _vbpl(status="het_hieu_luc_toan_bo"),
        LUAT_MAU + "\nthêm",
        embedder=embedder,
    )

    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None and not doc.payload_dirty
    assert {p.payload["status"] for p in await _points(qdrant, doc_id)} == {"het_hieu_luc_toan_bo"}


async def test_update_metadata_rejects_title_change(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    with pytest.raises(ValueError, match="title"):
        await update_document_metadata(db_session, qdrant, doc_id, title="Luật Khác")
    # Unchanged values are accepted (index_document passes them along).
    await update_document_metadata(
        db_session,
        qdrant,
        doc_id,
        title="Luật Mẫu",
        so_hieu="45/2019/QH14",
        status="khong_xac_dinh",
    )
    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None and doc.title == "Luật Mẫu" and doc.status == "khong_xac_dinh"


async def test_new_title_same_text_reembeds(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    calls = embedder.calls

    renamed = _vbpl(title="Luật Mẫu (sửa)", so_hieu="46/2019/QH14")
    await index_document(db_session, qdrant, renamed, LUAT_MAU, embedder=embedder)

    assert embedder.calls > calls
    chunks = await _chunks(db_session, doc_id)
    assert chunks and all(c.embed_text.startswith("Luật Mẫu (sửa) (46/2019/QH14)") for c in chunks)
    assert {str(c.id) for c in chunks} == {str(p.id) for p in await _points(qdrant, doc_id)}


async def test_metadata_update_while_embedding_is_kept(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    async def update_status() -> None:
        await update_document_metadata(db_session, qdrant, doc_id, status="het_hieu_luc_toan_bo")

    changed = LUAT_MAU + "\nthêm"
    await index_document(db_session, qdrant, _vbpl(), changed, embedder=HookEmbedder(update_status))

    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None and doc.status == "het_hieu_luc_toan_bo"
    points = await _points(qdrant, doc_id)
    assert len(points) == len(await _chunks(db_session, doc_id))
    assert {p.payload["status"] for p in points} == {"het_hieu_luc_toan_bo"}


async def test_delete_while_embedding_writes_nothing(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)

    async def delete() -> None:
        await delete_document(db_session, qdrant, doc_id)

    with pytest.raises(LookupError, match="deleted"):
        await index_document(
            db_session, qdrant, _vbpl(), LUAT_MAU + "\nthêm", embedder=HookEmbedder(delete)
        )
    assert await db_session.get(LegalDocument, doc_id) is None
    assert await _chunks(db_session, doc_id) == []
    assert await _points(qdrant, doc_id) == []


async def test_cancelled_index_is_marked_failed(db_session, qdrant, embedder) -> None:
    async def hang() -> None:
        await asyncio.sleep(3600)

    # Like Starlette cancelling a background task: an anyio cancel scope.
    with anyio.move_on_after(0.2):
        await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=HookEmbedder(hang))

    doc = await db_session.scalar(select(LegalDocument))
    assert doc is not None
    assert doc.ingest_status == "failed"
    assert doc.ingest_error == indexer.INDEX_FAILED_MESSAGE


async def test_failed_reindex_keeps_previous_version(db_session, qdrant, embedder) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    chunk_ids = {str(c.id) for c in await _chunks(db_session, doc_id)}

    with pytest.raises(RuntimeError, match="model crashed"):
        await index_document(
            db_session, qdrant, _vbpl(), LUAT_MAU + "\nthêm", embedder=CountingEmbedder(fail=True)
        )

    doc = await db_session.get(LegalDocument, doc_id)
    assert doc is not None
    assert doc.ingest_status == "failed"
    assert doc.ingest_error == indexer.INDEX_FAILED_MESSAGE
    assert doc.content_hash == content_hash(LUAT_MAU)  # describes the kept chunks
    assert doc.embedding_model == "fake"
    # The last good version stays searchable.
    assert {str(c.id) for c in await _chunks(db_session, doc_id)} == chunk_ids
    assert {str(p.id) for p in await _points(qdrant, doc_id)} == chunk_ids

    # Same text as the failed attempt: indexed again, not skipped.
    await index_document(db_session, qdrant, _vbpl(), LUAT_MAU + "\nthêm", embedder=embedder)
    assert doc.ingest_status == "done"
    new_ids = {str(c.id) for c in await _chunks(db_session, doc_id)}
    assert new_ids == {str(p.id) for p in await _points(qdrant, doc_id)}
    assert not new_ids & chunk_ids


async def test_failed_upsert_removes_new_points(
    db_session, qdrant, embedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id = await index_document(db_session, qdrant, _vbpl(), LUAT_MAU, embedder=embedder)
    chunk_ids = {str(c.id) for c in await _chunks(db_session, doc_id)}
    upsert = qdrant.upsert
    batches = 0

    async def flaky_upsert(*args: object, **kwargs: object):
        nonlocal batches
        batches += 1
        if batches == 2:
            raise ConnectionError("qdrant timeout")
        return await upsert(*args, **kwargs)

    monkeypatch.setattr(indexer, "UPSERT_BATCH_SIZE", 2)
    monkeypatch.setattr(qdrant, "upsert", flaky_upsert)
    with pytest.raises(ConnectionError):
        await index_document(db_session, qdrant, _vbpl(), LUAT_MAU + "\nthêm", embedder=embedder)

    assert {str(c.id) for c in await _chunks(db_session, doc_id)} == chunk_ids
    assert {str(p.id) for p in await _points(qdrant, doc_id)} == chunk_ids


async def test_empty_text_fails_with_user_message(db_session, qdrant, embedder) -> None:
    with pytest.raises(IndexingError) as info:
        await index_document(db_session, qdrant, _vbpl(), "  \n\n", embedder=embedder)
    assert info.value.code == "empty_document"

    doc = await db_session.scalar(select(LegalDocument))
    assert doc is not None
    assert doc.ingest_status == "failed"
    assert doc.ingest_error == indexer.EMPTY_DOCUMENT_MESSAGE


async def test_upload_and_reindex_pending_document(db_session, qdrant, embedder) -> None:
    user = User(email="owner@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    upload = LegalDocumentIn(source="user_upload", title="Quy chế nội bộ")

    first = await index_document(
        db_session, qdrant, upload, LUAT_MAU, owner_id=user.id, embedder=embedder
    )
    second = await index_document(
        db_session, qdrant, upload, LUAT_MAU, owner_id=user.id, embedder=embedder
    )
    assert first != second  # uploads have no source_id: never merged
    points = await _points(qdrant, first)
    assert points and {p.payload["owner_id"] for p in points} == {str(user.id)}
    assert {p.payload["source"] for p in points} == {"user_upload"}

    # Upload flow (T11): the row is created `pending` first, the text is indexed later.
    pending = LegalDocument(source="user_upload", owner_id=user.id, title="Đang xử lý")
    db_session.add(pending)
    await db_session.commit()
    await reindex_document(db_session, qdrant, pending.id, LUAT_MAU, embedder=embedder)
    assert pending.ingest_status == "done"
    assert len(await _chunks(db_session, pending.id)) == len(await _points(qdrant, pending.id)) > 0

    calls = embedder.calls
    await reindex_document(db_session, qdrant, pending.id, LUAT_MAU, embedder=embedder)
    assert embedder.calls == calls
    with pytest.raises(LookupError):
        await reindex_document(db_session, qdrant, uuid.uuid4(), LUAT_MAU, embedder=embedder)


async def test_ensure_collection_is_idempotent_and_checks_model(qdrant) -> None:
    await ensure_collection(qdrant, FakeEmbedder())  # second call: no error
    info = await qdrant.get_collection(settings.qdrant_collection)
    assert info.config.metadata == {"embedding_model": "fake"}
    assert set(info.payload_schema) == set(indexer.KEYWORD_INDEXES) | set(indexer.DATETIME_INDEXES)

    class OtherModel(FakeEmbedder):
        model_name = "BAAI/bge-m3"

    with pytest.raises(CollectionMismatchError, match="fake"):
        await ensure_collection(qdrant, OtherModel())
    with pytest.raises(CollectionMismatchError, match="size 512"):
        await ensure_collection(qdrant, FakeEmbedder(dim=512))


async def test_ensure_collection_concurrent_creation(qdrant) -> None:
    name = settings.qdrant_collection
    await qdrant.delete_collection(name)
    await asyncio.gather(*(ensure_collection(qdrant, FakeEmbedder()) for _ in range(3)))
    info = await qdrant.get_collection(name)
    assert info.config.metadata == {"embedding_model": "fake"}


async def test_ensure_collection_adopts_empty_legacy_collection(qdrant) -> None:
    name = settings.qdrant_collection
    await qdrant.delete_collection(name)
    vectors = {"dense": qm.VectorParams(size=1024, distance=qm.Distance.COSINE)}
    await qdrant.create_collection(name, vectors_config=vectors)  # no metadata (pre-T10)

    await ensure_collection(qdrant, FakeEmbedder())
    assert (await qdrant.get_collection(name)).config.metadata == {"embedding_model": "fake"}

    # Not empty: the model that built it is unknown, so it is refused.
    await qdrant.delete_collection(name)
    await qdrant.create_collection(name, vectors_config=vectors)
    await qdrant.upsert(
        name, points=[qm.PointStruct(id=1, vector={"dense": [1.0] * 1024}, payload={})], wait=True
    )
    with pytest.raises(CollectionMismatchError, match="None"):
        await ensure_collection(qdrant, FakeEmbedder())
