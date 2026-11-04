import codecs
import io
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_qdrant
from app.db.models import DOCUMENT_STATUSES, LegalChunk, LegalDocument, User
from app.db.session import get_session_factory
from app.documents import service
from app.documents.schemas import UploadMetadata
from app.ingest import indexer
from app.ingest.embedder import FakeEmbedder
from app.ingest.schemas import STATUS_LABELS, LegalDocumentIn
from app.main import app

PASSWORD = "mat-khau-123"
LUAT_MAU = (Path(__file__).parent / "fixtures" / "chunker" / "luat_mau.txt").read_text("utf-8")


# --- Pure logic ---------------------------------------------------------------------------


def test_status_labels_cover_every_status() -> None:
    assert set(STATUS_LABELS) == set(DOCUMENT_STATUSES)


def test_upload_metadata_blank_fields_are_unset() -> None:
    meta = UploadMetadata.model_validate(
        {"title": "  Luật   Mẫu \n", "so_hieu": " ", "issued_date": ""}
    )
    assert meta.title == "Luật Mẫu"
    assert meta.so_hieu is None
    assert meta.issued_date is None
    assert meta.status == "khong_xac_dinh"
    with pytest.raises(ValueError):
        UploadMetadata.model_validate({"title": "   "})


def test_matches_format(make_pdf, make_docx) -> None:
    pdf = make_pdf(["Điều 1. Phạm vi"])
    docx = make_docx(["Điều 1. Phạm vi"])
    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as archive:
        archive.writestr("a.txt", "x")

    assert service.matches_format(pdf, ".pdf")
    assert service.matches_format(docx, ".docx")
    assert service.matches_format("Điều 1".encode(), ".txt")
    assert service.matches_format(codecs.BOM_UTF16_LE + "Điều 1".encode("utf-16-le"), ".txt")
    assert service.matches_format("Dieu 1 pham vi".encode("utf-16-le"), ".txt")  # no BOM

    assert not service.matches_format(b"Dieu 1", ".pdf")
    assert not service.matches_format(pdf, ".docx")
    assert not service.matches_format(plain_zip.getvalue(), ".docx")  # zip, but not Word
    assert not service.matches_format(b"PK\x03\x04broken", ".docx")
    assert not service.matches_format(pdf, ".txt")
    assert not service.matches_format(docx, ".txt")
    assert not service.matches_format(b"\x7fELF" + bytes(range(256)) * 8, ".txt")
    assert not service.matches_format(b"data", ".doc")


# --- API: Postgres (law_test) + Qdrant (temporary collection) ------------------------------


@pytest.fixture
async def qdrant(monkeypatch: pytest.MonkeyPatch):
    client = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False)
    try:
        await client.get_collections()
    except Exception as exc:
        await client.close()
        pytest.skip(f"Qdrant not reachable ({type(exc).__name__}): run `docker compose up -d`")
    name = f"test_documents_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "qdrant_collection", name)
    try:
        await indexer.ensure_collection(client, FakeEmbedder())
        yield client
    finally:
        await client.delete_collection(name)
        await client.close()


@pytest.fixture
def session_factory(db_session: AsyncSession):
    @asynccontextmanager
    async def shared():
        yield db_session  # background ingest shares the rolled-back test transaction

    return shared


@pytest.fixture
async def api(client, qdrant, session_factory, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """HTTP client with Qdrant, the background session and the upload dir wired for tests.

    httpx's ASGITransport returns only after background tasks finished, so an upload is
    already ingested when the response arrives."""
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    app.dependency_overrides[get_qdrant] = lambda: qdrant
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    yield client
    app.dependency_overrides.pop(get_qdrant, None)
    app.dependency_overrides.pop(get_session_factory, None)


async def _register(client: httpx.AsyncClient, email: str) -> uuid.UUID:
    response = await client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


async def _login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


async def _upload(
    client: httpx.AsyncClient,
    content: bytes = LUAT_MAU.encode(),
    filename: str = "luat.txt",
    **form: str,
) -> httpx.Response:
    data = {"title": "Luật Thử nghiệm", "so_hieu": "99/2025/QH15", "doc_type": "Luật"} | form
    return await client.post(
        "/api/documents/upload", data=data, files={"file": (filename, content)}
    )


async def _vbpl(
    session: AsyncSession, qdrant: AsyncQdrantClient, source_id: str = "1", **fields: object
) -> uuid.UUID:
    values: dict[str, object] = {
        "source": "vbpl",
        "source_id": source_id,
        "title": "Luật Lao động",
        "so_hieu": "45/2019/QH14",
        "doc_type": "Luật",
        "issued_date": "2019-11-20",
        "status": "con_hieu_luc",
        "source_url": f"https://vbpl.vn/van-ban/{source_id}",
    } | fields
    return await indexer.index_document(
        session, qdrant, LegalDocumentIn.model_validate(values), LUAT_MAU
    )


async def _point_count(qdrant: AsyncQdrantClient, document_id: uuid.UUID) -> int:
    result = await qdrant.count(
        settings.qdrant_collection,
        count_filter=qm.Filter(
            must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(document_id)))]
        ),
        exact=True,
    )
    return result.count


async def test_documents_require_login(api) -> None:
    assert (await api.get("/api/documents")).status_code == 401
    assert (await _upload(api)).status_code == 401


async def test_upload_txt_is_ingested_and_listed_as_mine(api, db_session, qdrant) -> None:
    user_id = await _register(api, "an@example.com")

    response = await _upload(api, issued_date="2025-06-01", effective_date="", status="")
    assert response.status_code == 202
    body = response.json()
    assert body["ingest_status"] == "pending"
    doc_id = uuid.UUID(body["id"])

    stored = settings.upload_dir / str(user_id) / f"{doc_id}.txt"
    assert stored.read_bytes() == LUAT_MAU.encode()

    listing = (await api.get("/api/documents", params={"scope": "mine"})).json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["id"] == str(doc_id)
    assert item["source"] == "user_upload"
    assert item["ingest_status"] == "done"
    assert item["so_hieu"] == "99/2025/QH15"
    assert item["issued_date"] == "2025-06-01"
    assert item["effective_date"] is None
    assert item["status"] == "khong_xac_dinh"
    assert item["status_label"] == "Không xác định"
    assert item["source_url"] is None

    chunk_count = await db_session.scalar(
        select(func.count()).select_from(LegalChunk).where(LegalChunk.document_id == doc_id)
    )
    assert chunk_count and await _point_count(qdrant, doc_id) == chunk_count
    records, _ = await qdrant.scroll(settings.qdrant_collection, limit=1, with_payload=True)
    assert records[0].payload is not None
    assert records[0].payload["owner_id"] == str(user_id)
    assert records[0].payload["source"] == "user_upload"

    detail = (await api.get(f"/api/documents/{doc_id}")).json()
    assert detail["chunk_count"] == chunk_count
    assert detail["can_delete"] is True
    assert detail["ingest_error"] is None
    dieu = [d["dieu"] for d in detail["dieu_list"]]
    assert dieu[:3] == ["Điều 1", "Điều 2", "Điều 3"]
    assert len(dieu) == len(set(dieu))
    assert detail["dieu_list"][0]["dieu_title"] == "Phạm vi điều chỉnh"


async def test_uploads_are_private(api, db_session, qdrant) -> None:
    await _register(api, "an@example.com")
    doc_id = uuid.UUID((await _upload(api)).json()["id"])
    vbpl_id = await _vbpl(db_session, qdrant)

    await _register(api, "binh@example.com")
    for scope in ("all", "mine", "vbpl"):
        listing = (await api.get("/api/documents", params={"scope": scope})).json()
        assert str(doc_id) not in {item["id"] for item in listing["items"]}
    assert (await api.get("/api/documents", params={"scope": "all"})).json()["total"] == 1
    assert (await api.get(f"/api/documents/{doc_id}")).status_code == 404
    assert (await api.get(f"/api/documents/{vbpl_id}")).json()["can_delete"] is False

    response = await api.delete(f"/api/documents/{doc_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    response = await api.delete(f"/api/documents/{vbpl_id}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert await db_session.get(LegalDocument, doc_id) is not None
    assert await _point_count(qdrant, doc_id) > 0
    assert await _point_count(qdrant, vbpl_id) > 0


async def test_owner_deletes_upload_everywhere(api, db_session, qdrant) -> None:
    user_id = await _register(api, "an@example.com")
    doc_id = uuid.UUID((await _upload(api)).json()["id"])
    stored = settings.upload_dir / str(user_id) / f"{doc_id}.txt"
    assert stored.exists()

    assert (await api.delete(f"/api/documents/{doc_id}")).status_code == 204

    assert not stored.exists()
    assert await _point_count(qdrant, doc_id) == 0
    db_session.expunge_all()
    assert await db_session.get(LegalDocument, doc_id) is None
    assert (await api.get(f"/api/documents/{doc_id}")).status_code == 404
    assert (await api.delete(f"/api/documents/{doc_id}")).status_code == 404


async def test_admin_deletes_any_document(api, db_session, qdrant) -> None:
    await _register(api, "an@example.com")
    upload_id = uuid.UUID((await _upload(api)).json()["id"])
    vbpl_id = await _vbpl(db_session, qdrant)
    await _register(api, "admin@example.com")
    await db_session.execute(
        update(User).where(User.email == "admin@example.com").values(role="admin")
    )
    await db_session.commit()

    assert (await api.delete(f"/api/documents/{vbpl_id}")).status_code == 204
    assert (await api.delete(f"/api/documents/{upload_id}")).status_code == 204
    assert await _point_count(qdrant, vbpl_id) == 0
    assert await _point_count(qdrant, upload_id) == 0


async def test_upload_rejects_invalid_files(api, db_session, monkeypatch, make_pdf) -> None:
    await _register(api, "an@example.com")

    async def error(response: httpx.Response) -> tuple[int, str]:
        return response.status_code, response.json()["error"]["code"]

    assert await error(await _upload(api, b"MZ\x90\x00", "virus.exe")) == (
        400,
        "unsupported_format",
    )
    assert await error(await _upload(api, LUAT_MAU.encode(), "luat.pdf")) == (400, "invalid_file")
    assert await error(await _upload(api, make_pdf(["Điều 1"]), "luat.txt")) == (
        400,
        "invalid_file",
    )
    assert await error(await _upload(api, b"", "luat.txt")) == (400, "empty_file")
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 100)
    assert await error(await _upload(api, b"x" * 101, "luat.txt")) == (413, "file_too_large")
    monkeypatch.undo()

    missing_title = await api.post(
        "/api/documents/upload", files={"file": ("luat.txt", LUAT_MAU.encode())}
    )
    assert await error(missing_title) == (422, "validation_error")
    assert "title" in missing_title.json()["error"]["message"]
    bad_status = await _upload(api, status="dang_xem_xet")
    assert await error(bad_status) == (422, "validation_error")
    assert "status" in bad_status.json()["error"]["message"]
    assert await error(await _upload(api, issued_date="20/11/2019")) == (422, "validation_error")

    count = await db_session.scalar(select(func.count()).select_from(LegalDocument))
    assert count == 0
    assert not list((settings.upload_dir).rglob("*.*"))


async def test_scanned_pdf_upload_fails_with_message(api, make_pdf) -> None:
    await _register(api, "an@example.com")
    response = await _upload(api, make_pdf(["Điều 1. Phạm vi"], image_only=True), "scan.pdf")
    assert response.status_code == 202

    detail = (await api.get(f"/api/documents/{response.json()['id']}")).json()
    assert detail["ingest_status"] == "failed"
    assert detail["ingest_error"] == "PDF scan chưa hỗ trợ."
    assert detail["chunk_count"] == 0


async def test_upload_docx(api, make_docx) -> None:
    await _register(api, "an@example.com")
    docx = make_docx(LUAT_MAU.split("\n"))
    response = await _upload(api, docx, "luat.docx")
    assert response.status_code == 202
    detail = (await api.get(f"/api/documents/{response.json()['id']}")).json()
    assert detail["ingest_status"] == "done"
    assert detail["chunk_count"] > 0


async def test_preview_detects_metadata_without_storing(
    api, db_session, make_docx, make_pdf
) -> None:
    await _register(api, "an@example.com")
    docx = make_docx(
        ["NGHỊ ĐỊNH", "Quy định chi tiết thi hành Luật", "Điều 1. Phạm vi điều chỉnh"],
        header_table=[["CHÍNH PHỦ\nSố: 140/2004/NĐ-CP", "Hà Nội, ngày 25 tháng 6 năm 2004"]],
    )
    response = await api.post("/api/documents/upload/preview", files={"file": ("nd.docx", docx)})
    assert response.status_code == 200
    detected = response.json()["detected"]
    assert detected["so_hieu"] == "140/2004/NĐ-CP"
    assert detected["doc_type"] == "Nghị định"
    assert detected["issued_date"] == "2004-06-25"

    scan = await api.post(
        "/api/documents/upload/preview",
        files={"file": ("scan.pdf", make_pdf(["Điều 1. Phạm vi"], image_only=True))},
    )
    assert scan.status_code == 400
    assert scan.json()["error"]["code"] == "scanned_pdf"
    assert await db_session.scalar(select(func.count()).select_from(LegalDocument)) == 0
    assert not settings.upload_dir.exists()


async def test_list_filters_and_pagination(api, db_session, qdrant) -> None:
    await _register(api, "an@example.com")
    await _vbpl(db_session, qdrant, "1", title="Luật Lao động", issued_date="2019-11-20")
    await _vbpl(
        db_session,
        qdrant,
        "2",
        title="Nghị định hướng dẫn 100% vốn",
        so_hieu="145/2020/NĐ-CP",
        doc_type="Nghị định",
        issued_date="2020-12-14",
        status="het_hieu_luc_mot_phan",
    )
    await _vbpl(db_session, qdrant, "3", title="Luật Đất đai", issued_date=None)
    await _upload(api, title="Ghi chú Lao động riêng")

    async def ids(**params: object) -> tuple[list[str], int]:
        body = (await api.get("/api/documents", params=params)).json()
        return [item["title"] for item in body["items"]], body["total"]

    titles, total = await ids()
    assert total == 4
    # Newest issued first, undated last.
    assert titles[:2] == ["Nghị định hướng dẫn 100% vốn", "Luật Lao động"]

    assert (await ids(scope="vbpl"))[1] == 3
    assert await ids(scope="mine") == (["Ghi chú Lao động riêng"], 1)
    assert sorted((await ids(q="lao dong"))[0]) == ["Ghi chú Lao động riêng", "Luật Lao động"]
    assert (await ids(q="dat dai"))[0] == ["Luật Đất đai"]
    assert (await ids(q="145/2020"))[0] == ["Nghị định hướng dẫn 100% vốn"]
    assert (await ids(q="100%"))[1] == 1  # `%` is literal, not a wildcard
    assert (await ids(q="%"))[1] == 1
    assert (await ids(doc_type="Nghị định"))[1] == 1
    assert (await ids(status="het_hieu_luc_mot_phan"))[0] == ["Nghị định hướng dẫn 100% vốn"]

    page1 = await ids(page=1, page_size=3)
    page2 = await ids(page=2, page_size=3)
    assert page1[1] == page2[1] == 4
    assert len(page1[0]) == 3 and len(page2[0]) == 1
    assert set(page1[0]) | set(page2[0]) == set(titles)

    for params in ({"page_size": 51}, {"page": 0}, {"scope": "others"}, {"status": "x"}):
        response = await api.get("/api/documents", params=params)
        assert response.status_code == 422


async def test_resume_interrupted_uploads(
    db_session, qdrant, session_factory, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    user = User(id=uuid.uuid4(), email="an@example.com", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    pending, _ = await service.create_upload(
        db_session, user, UploadMetadata(title="Luật Thử nghiệm"), LUAT_MAU.encode(), ".txt"
    )
    lost = LegalDocument(
        id=uuid.uuid4(),
        source="user_upload",
        owner_id=user.id,
        title="Mất file",
        ingest_status="processing",
    )
    db_session.add(lost)
    await db_session.commit()

    assert await service.resume_interrupted_uploads(session_factory, qdrant) == 2

    await db_session.refresh(pending)
    await db_session.refresh(lost)
    assert pending.ingest_status == "done"
    assert await _point_count(qdrant, pending.id) > 0
    assert lost.ingest_status == "failed"
    assert lost.ingest_error == service.INGEST_INTERRUPTED_MESSAGE
    assert await service.resume_interrupted_uploads(session_factory, qdrant) == 0
