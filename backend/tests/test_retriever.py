import uuid

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import update

from app.core.config import settings
from app.db.models import LegalChunk, LegalDocument, User
from app.ingest.embedder import FakeEmbedder
from app.ingest.indexer import ensure_collection, index_document
from app.ingest.schemas import LegalDocumentIn
from app.rag.retriever import (
    RetrievalFilters,
    RetrievedChunk,
    build_filter,
    find_so_hieu,
    group_blocks,
    retrieve,
)

# High enough for an unrelated question to fail with the fake embedder, low enough for any
# question sharing a few words with a chunk to pass.
MIN_SCORE = 0.3

LAO_DONG = """LUẬT LAO ĐỘNG

Chương I
NHỮNG QUY ĐỊNH CHUNG

Điều 1. Phạm vi điều chỉnh
Luật này quy định tiêu chuẩn lao động; quyền, nghĩa vụ, trách nhiệm
của người lao động và người sử dụng lao động.

Điều 5. Quyền và nghĩa vụ của người lao động
1. Người lao động có quyền làm việc, tự do lựa chọn việc làm, nơi làm việc, nghề nghiệp.
2. Người lao động có nghĩa vụ thực hiện hợp đồng lao động, thỏa ước lao động tập thể.

Chương II
HỢP ĐỒNG LAO ĐỘNG

Điều 13. Hợp đồng lao động
Hợp đồng lao động là sự thỏa thuận giữa người lao động và người sử dụng lao động về việc làm
có trả công, tiền lương, điều kiện lao động.
"""

DAT_DAI = """LUẬT ĐẤT ĐAI

Điều 1. Phạm vi điều chỉnh
Luật này quy định về chế độ sở hữu đất đai, quyền hạn và trách nhiệm
của Nhà nước đại diện chủ sở hữu toàn dân về đất đai.

Điều 3. Giải thích từ ngữ
Thửa đất là phần diện tích đất được giới hạn bởi ranh giới xác định
trên thực địa hoặc được mô tả trên hồ sơ địa chính.
"""

THUE = """NGHỊ ĐỊNH QUY ĐỊNH VỀ HÓA ĐƠN

Điều 4. Nguyên tắc lập hóa đơn
Khi bán hàng hóa, cung cấp dịch vụ, người bán phải lập hóa đơn để giao cho người mua
và người lao động của doanh nghiệp không được lập thay.
"""

NOI_QUY = """NỘI QUY CÔNG TY

Điều 2. Thời giờ làm việc của người lao động
Người lao động làm việc tám giờ mỗi ngày,
quyền và nghĩa vụ của người lao động theo hợp đồng lao động đã ký.
"""


# --- Pure logic ---


def test_find_so_hieu() -> None:
    query = "Điều 5 Luật 45/2019/QH14 và nghị định 100/2019/nđ-cp, lại hỏi 45/2019/QH14"
    assert find_so_hieu(query) == ["45/2019/QH14", "100/2019/NĐ-CP"]
    assert find_so_hieu("Thông tư 24/2026/TT-BYT") == ["24/2026/TT-BYT"]
    assert find_so_hieu("người lao động có quyền gì?") == []
    assert find_so_hieu("năm 2019/2020") == []


def test_build_filter_always_has_permission() -> None:
    user_id = uuid.uuid4()
    qfilter = build_filter(RetrievalFilters(user_id=user_id))
    assert qfilter.must is not None
    permission = qfilter.must[0]
    assert isinstance(permission, qm.Filter)
    assert {c.key: c.match.value for c in permission.should} == {  # type: ignore[union-attr]
        "source": "vbpl",
        "owner_id": str(user_id),
    }
    assert qfilter.must_not == [
        qm.FieldCondition(key="status", match=qm.MatchValue(value="het_hieu_luc_toan_bo"))
    ]

    full = build_filter(
        RetrievalFilters(user_id=user_id, include_expired=True, doc_types=["Luật"]),
        ["45/2019/QH14"],
    )
    assert full.must_not is None
    assert full.must is not None and len(full.must) == 3
    assert full.must[0] == permission


def _chunk(document: LegalDocument, ordinal: int, dieu: str | None, text: str) -> RetrievedChunk:
    chunk = LegalChunk(
        id=uuid.uuid4(),
        document_id=document.id,
        ordinal=ordinal,
        dieu=dieu,
        dieu_title=f"Tiêu đề {dieu}" if dieu else None,
        text=text,
        embed_text=text,
        token_count=1,
    )
    chunk.document = document
    return RetrievedChunk(chunk, 1.0 / (ordinal + 1))


def test_group_blocks() -> None:
    a = LegalDocument(id=uuid.uuid4(), source="vbpl", title="A")
    b = LegalDocument(id=uuid.uuid4(), source="vbpl", title="B")
    ranked = [
        _chunk(a, 3, "Điều 5", "khoản 2"),
        _chunk(b, 0, "Điều 5", "B điều 5"),
        _chunk(a, 2, "Điều 5", "khoản 1"),
        _chunk(a, 0, None, "mở đầu"),
        _chunk(a, 6, "Điều 5", "khoản 5"),
        _chunk(a, 9, None, "phụ lục"),
    ]
    blocks = group_blocks(ranked)
    assert [(blk.document.title, blk.dieu, [c.ordinal for c in blk.chunks]) for blk in blocks] == [
        ("A", "Điều 5", [2, 3, 6]),
        ("B", "Điều 5", [0]),
        ("A", None, [0]),
        ("A", None, [9]),
    ]
    assert blocks[0].text == "khoản 1\nkhoản 2\n[...]\nkhoản 5"
    assert blocks[0].dieu_title == "Tiêu đề Điều 5"
    assert group_blocks([]) == []


def test_block_text_drops_repeated_heading_and_overlap() -> None:
    doc = LegalDocument(id=uuid.uuid4(), source="vbpl", title="A")
    heading = "Điều 5. Quyền của người lao động"
    overlap = "được trả lương phù hợp với trình độ kỹ năng nghề"
    ranked = [
        _chunk(doc, 7, "Điều 5", f"{heading}\n1. Làm việc.\n2. Người lao động {overlap}"),
        _chunk(doc, 8, "Điều 5", f"{heading}\n{overlap} trên cơ sở thỏa thuận.\n3. Đình công."),
        _chunk(doc, 11, "Điều 5", f"{heading}\n6. Đơn phương chấm dứt hợp đồng."),
    ]
    (block,) = group_blocks(ranked)
    assert block.text == (
        f"{heading}\n1. Làm việc.\n2. Người lao động {overlap}"
        "\ntrên cơ sở thỏa thuận.\n3. Đình công."
        "\n[...]\n6. Đơn phương chấm dứt hợp đồng."
    )


def test_block_text_keeps_short_coincidental_match() -> None:
    doc = LegalDocument(id=uuid.uuid4(), source="vbpl", title="A")
    ranked = [
        _chunk(doc, 1, None, "Phụ lục kèm theo Nghị định này."),
        _chunk(doc, 2, "Điều 1", "Điều 1. Phạm vi\nNghị định này quy định"),
        _chunk(doc, 3, "Điều 1", "Nghị định này áp dụng cho mọi tổ chức."),
    ]
    blocks = group_blocks(ranked)
    assert (
        blocks[1].text
        == "Điều 1. Phạm vi\nNghị định này quy định\nNghị định này áp dụng cho mọi tổ chức."
    )


# --- Integration: Postgres (law_test) + Qdrant (temporary collection) ---


@pytest.fixture
async def qdrant(monkeypatch: pytest.MonkeyPatch):
    client = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        await client.get_collections()
    except Exception as exc:
        await client.close()
        pytest.skip(f"Qdrant not reachable ({type(exc).__name__}): run `docker compose up -d`")
    name = f"test_retriever_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "qdrant_collection", name)
    try:
        await ensure_collection(client, FakeEmbedder())
        yield client
    finally:
        await client.delete_collection(name)
        await client.close()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
async def corpus(db_session, qdrant, embedder):
    """Two users; vbpl documents (one fully expired) and an upload of each user."""
    alice = User(email="alice@example.com", password_hash="x")
    bob = User(email="bob@example.com", password_hash="x")
    db_session.add_all([alice, bob])
    await db_session.flush()

    async def add(text: str, owner: User | None = None, **values: object) -> uuid.UUID:
        source = "user_upload" if owner else "vbpl"
        doc = LegalDocumentIn.model_validate(
            {"source": source, "source_id": None if owner else uuid.uuid4().hex} | values
        )
        return await index_document(
            db_session,
            qdrant,
            doc,
            text,
            owner_id=owner.id if owner else None,
            embedder=embedder,
        )

    ids = {
        "lao_dong": await add(
            LAO_DONG,
            title="Luật Lao động",
            so_hieu="45/2019/QH14",
            doc_type="Luật",
            status="con_hieu_luc",
        ),
        "lao_dong_cu": await add(
            LAO_DONG.replace("tiền lương", "tiền công"),
            title="Bộ luật Lao động",
            so_hieu="10/2012/QH13",
            doc_type="Luật",
            status="het_hieu_luc_toan_bo",
        ),
        "dat_dai": await add(
            DAT_DAI,
            title="Luật Đất đai",
            so_hieu="31/2024/QH15",
            doc_type="Luật",
            status="con_hieu_luc",
        ),
        "thue": await add(
            THUE,
            title="Nghị định quy định về hóa đơn",
            so_hieu="123/2020/NĐ-CP",
            doc_type="Nghị định",
            status="het_hieu_luc_mot_phan",
        ),
        "noi_quy_alice": await add(NOI_QUY, owner=alice, title="Nội quy công ty Alice"),
        "noi_quy_bob": await add(NOI_QUY, owner=bob, title="Nội quy công ty Bob"),
    }
    return alice, bob, ids


async def _retrieve(db_session, qdrant, embedder, query: str, filters: RetrievalFilters, **kw):
    return await retrieve(
        db_session, qdrant, query, filters, kw.pop("top_k", 20),
        min_score=kw.pop("min_score", MIN_SCORE), embedder=embedder, **kw,
    )  # fmt: skip


def _documents(result) -> set[uuid.UUID]:
    return {item.chunk.document_id for item in result.chunks}


async def test_so_hieu_in_question_returns_that_document(
    db_session, qdrant, embedder, corpus
) -> None:
    alice, _, ids = corpus
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "Theo Luật 31/2024/qh15, phạm vi điều chỉnh là gì?",
        RetrievalFilters(user_id=alice.id),
    )
    assert result.so_hieu_norm == ["31/2024/QH15"]
    assert result.found
    assert _documents(result) == {ids["dat_dai"]}
    assert result.chunks[0].chunk.dieu == "Điều 1"
    assert result.chunks[0].chunk.document.so_hieu == "31/2024/QH15"


async def test_unknown_or_hidden_so_hieu_is_not_a_filter(
    db_session, qdrant, embedder, corpus
) -> None:
    alice, _, ids = corpus
    question = "Quyền và nghĩa vụ của người lao động theo 99/2099/QH99 và 10/2012/QH13"
    result = await _retrieve(
        db_session, qdrant, embedder, question, RetrievalFilters(user_id=alice.id)
    )
    # 10/2012/QH13 is fully expired: not searchable by default, so not used as a filter.
    assert result.so_hieu_norm is None
    assert ids["lao_dong"] in _documents(result)

    with_expired = await _retrieve(
        db_session,
        qdrant,
        embedder,
        question,
        RetrievalFilters(user_id=alice.id, include_expired=True),
    )
    assert with_expired.so_hieu_norm == ["10/2012/QH13"]
    assert _documents(with_expired) == {ids["lao_dong_cu"]}


async def test_fully_expired_document_hidden_by_default(
    db_session, qdrant, embedder, corpus
) -> None:
    alice, _, ids = corpus
    question = "Hợp đồng lao động là sự thỏa thuận giữa người lao động và người sử dụng lao động"
    default = await _retrieve(
        db_session, qdrant, embedder, question, RetrievalFilters(user_id=alice.id)
    )
    assert ids["lao_dong"] in _documents(default)
    assert ids["lao_dong_cu"] not in _documents(default)
    # Partly expired documents stay searchable.
    partly = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "lập hóa đơn khi bán hàng hóa",
        RetrievalFilters(user_id=alice.id),
    )
    assert ids["thue"] in _documents(partly)

    expired = await _retrieve(
        db_session,
        qdrant,
        embedder,
        question,
        RetrievalFilters(user_id=alice.id, include_expired=True),
    )
    assert ids["lao_dong_cu"] in _documents(expired)


async def test_uploads_of_other_users_never_returned(db_session, qdrant, embedder, corpus) -> None:
    alice, bob, ids = corpus
    question = "Thời giờ làm việc của người lao động tám giờ mỗi ngày theo nội quy công ty"
    for user, own, other in (
        (alice, "noi_quy_alice", "noi_quy_bob"),
        (bob, "noi_quy_bob", "noi_quy_alice"),
    ):
        for include_expired in (False, True):
            result = await _retrieve(
                db_session,
                qdrant,
                embedder,
                question,
                RetrievalFilters(user_id=user.id, include_expired=include_expired),
                top_k=50,
            )
            assert ids[own] in _documents(result)
            assert ids[other] not in _documents(result)
            assert result.chunks[0].chunk.document_id == ids[own]

    stranger = await _retrieve(
        db_session,
        qdrant,
        embedder,
        question,
        RetrievalFilters(user_id=uuid.uuid4(), include_expired=True),
        top_k=50,
    )
    assert not _documents(stranger) & {ids["noi_quy_alice"], ids["noi_quy_bob"]}


async def test_unrelated_question_below_threshold(db_session, qdrant, embedder, corpus) -> None:
    alice, _, _ = corpus
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "Cách nấu phở bò ngon cho bữa sáng",
        RetrievalFilters(user_id=alice.id),
    )
    assert not result.found
    assert result.chunks == [] and result.blocks == []
    assert result.best_dense_score is not None and result.best_dense_score < MIN_SCORE

    # The same question passes with a threshold low enough (the score is not hidden).
    loose = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "Cách nấu phở bò ngon cho bữa sáng",
        RetrievalFilters(user_id=alice.id),
        min_score=-1.0,
    )
    assert loose.found


async def test_empty_collection_finds_nothing(db_session, qdrant, embedder) -> None:
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "người lao động",
        RetrievalFilters(user_id=uuid.uuid4()),
        min_score=-1.0,
    )
    assert not result.found
    assert result.best_dense_score is None


async def test_doc_types_filter(db_session, qdrant, embedder, corpus) -> None:
    alice, _, ids = corpus
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "người lao động",
        RetrievalFilters(user_id=alice.id, doc_types=["Nghị định"]),
    )
    assert _documents(result) == {ids["thue"]}


async def test_postgres_status_wins_over_stale_payload(
    db_session, qdrant, embedder, corpus
) -> None:
    """A document expired in Postgres whose payload is not synced yet is still dropped."""
    alice, _, ids = corpus
    await db_session.execute(
        update(LegalDocument)
        .where(LegalDocument.id == ids["lao_dong"])
        .values(status="het_hieu_luc_toan_bo", payload_dirty=True)
    )
    await db_session.commit()
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "Hợp đồng lao động là sự thỏa thuận giữa người lao động và người sử dụng lao động",
        RetrievalFilters(user_id=alice.id),
    )
    assert ids["lao_dong"] not in _documents(result)


async def test_ranking_and_blocks(db_session, qdrant, embedder, corpus) -> None:
    alice, _, ids = corpus
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "Luật 45/2019/QH14: quyền và nghĩa vụ của người lao động",
        RetrievalFilters(user_id=alice.id),
        top_k=3,
    )
    assert len(result.chunks) <= 3
    assert _documents(result) == {ids["lao_dong"]}
    scores = [item.score for item in result.chunks]
    assert scores == sorted(scores, reverse=True)
    assert [block.dieu for block in result.blocks] == [item.chunk.dieu for item in result.chunks]
    dieu_5 = next(block for block in result.blocks if block.dieu == "Điều 5")
    assert "Người lao động có quyền làm việc" in dieu_5.text
    assert dieu_5.document.title == "Luật Lao động"


async def test_threshold_ignores_points_dropped_by_postgres(
    db_session, qdrant, embedder, corpus
) -> None:
    """The best dense match is expired in Postgres (payload not synced): it must not let a
    question pass the threshold on its score."""
    alice, _, ids = corpus
    question = "Thời giờ làm việc của người lao động tám giờ mỗi ngày theo nội quy công ty"
    before = await _retrieve(
        db_session, qdrant, embedder, question, RetrievalFilters(user_id=alice.id)
    )
    assert before.chunks[0].chunk.document_id == ids["noi_quy_alice"]
    best_before = before.best_dense_score
    assert best_before is not None

    await db_session.execute(
        update(LegalDocument)
        .where(LegalDocument.id == ids["noi_quy_alice"])
        .values(status="het_hieu_luc_toan_bo", payload_dirty=True)
    )
    await db_session.commit()
    after = await _retrieve(
        db_session,
        qdrant,
        embedder,
        question,
        RetrievalFilters(user_id=alice.id),
        min_score=best_before - 1e-6,
    )
    assert after.best_dense_score is None or after.best_dense_score < best_before
    assert not after.found


async def test_dropped_points_are_replaced_by_next_ones(
    db_session, qdrant, embedder, corpus
) -> None:
    """Expired in Postgres, payload not synced: the top-ranked document's chunks are dropped
    and the next visible chunks fill the result up to `top_k`."""
    alice, _, ids = corpus
    question = "Thời giờ làm việc của người lao động tám giờ mỗi ngày theo nội quy công ty"
    filters = RetrievalFilters(user_id=alice.id)
    before = await _retrieve(db_session, qdrant, embedder, question, filters, top_k=2)
    assert len(before.chunks) == 2
    assert before.chunks[0].chunk.document_id == ids["noi_quy_alice"]

    await db_session.execute(
        update(LegalDocument)
        .where(LegalDocument.id == ids["noi_quy_alice"])
        .values(status="het_hieu_luc_toan_bo", payload_dirty=True)
    )
    await db_session.commit()
    after = await _retrieve(
        db_session, qdrant, embedder, question, filters, top_k=2, min_score=-1.0
    )
    assert len(after.chunks) == 2
    assert ids["noi_quy_alice"] not in _documents(after)


async def test_so_hieu_of_unindexed_document_is_not_a_filter(
    db_session, qdrant, embedder, corpus
) -> None:
    alice, _, ids = corpus
    db_session.add(
        LegalDocument(
            id=uuid.uuid4(),
            source="vbpl",
            source_id="no-text",
            title="Nghị định chưa có toàn văn",
            so_hieu="1/2025/NĐ-CP",
            so_hieu_norm="1/2025/NĐ-CP",
            status="con_hieu_luc",
            ingest_status="failed",
        )
    )
    await db_session.commit()
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "Theo 1/2025/NĐ-CP quyền và nghĩa vụ của người lao động là gì?",
        RetrievalFilters(user_id=alice.id),
    )
    assert result.so_hieu_norm is None
    assert ids["lao_dong"] in _documents(result)


async def test_explicit_so_hieu_filter_is_normalized(db_session, qdrant, embedder, corpus) -> None:
    alice, _, ids = corpus
    result = await _retrieve(
        db_session,
        qdrant,
        embedder,
        "phạm vi điều chỉnh",
        RetrievalFilters(user_id=alice.id, so_hieu_norm=["31/2024/qh 15"]),
    )
    assert result.so_hieu_norm == ["31/2024/QH15"]
    assert _documents(result) == {ids["dat_dai"]}
