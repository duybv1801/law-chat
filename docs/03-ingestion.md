# 03 — Ingestion: parse, chunk, embed, index

## Parser (`app/ingest/parsers/`)

Đầu vào: file hoặc HTML. Đầu ra: `ParsedDocument(text: str, detected: dict)`.

| Định dạng | Thư viện | Ghi chú |
|---|---|---|
| PDF | `pymupdf` | Nếu PDF là bản scan (không có text) → `ingest_status=failed`, lỗi "PDF scan chưa hỗ trợ". OCR để phase sau. |
| DOCX | `python-docx` | Đọc paragraph theo thứ tự, giữ xuống dòng |
| TXT | built-in | Thử UTF-8, fallback `cp1258` / `utf-16` |
| HTML (vbpl) | `selectolax` | Parser riêng trong `crawler/vbpl_parser.py`, xuất ra cùng `ParsedDocument` |

Chuẩn hoá text (`normalize.py`):
- Unicode **NFC** (rất quan trọng với tiếng Việt: dấu tổ hợp và dấu dựng sẵn phải về một dạng).
- Gộp khoảng trắng thừa, bỏ header/footer lặp lại của PDF (dòng giống nhau xuất hiện ở > 50% số trang).
- Giữ nguyên xuống dòng giữa các đoạn.

`detected`: thử đoán `so_hieu`, `doc_type`, `issued_date` từ phần đầu văn bản (regex). Kết quả chỉ để gợi ý cho form upload, user xác nhận lại.

## Chunker (`app/ingest/chunker.py`)

Văn bản luật Việt Nam có cấu trúc rất đều. Regex nhận diện tiêu đề (áp dụng ở đầu dòng, sau khi đã NFC):

```python
PHAN   = r"^Phần\s+(thứ\s+\w+|[IVXLC]+)\b.*$"
CHUONG = r"^Chương\s+[IVXLC\d]+\b.*$"
MUC    = r"^Mục\s+\d+\b.*$"
DIEU   = r"^Điều\s+(\d+[a-z]?)\.\s*(.*)$"
KHOAN  = r"^(\d+)\.\s+"          # chỉ dùng bên trong một Điều
```

Thuật toán:
1. Duyệt từng dòng, theo dõi Phần/Chương/Mục hiện tại.
2. Mỗi lần gặp `Điều N.` thì mở chunk mới. Chunk bao gồm mọi dòng cho đến tiêu đề Điều/Chương/Mục/Phần kế tiếp.
3. Phần text trước Điều 1 (căn cứ ban hành, quốc hiệu) → một chunk `dieu=None`, `dieu_title="Phần mở đầu"`.
4. Điều dài hơn `MAX_CHUNK_TOKENS` (mặc định 800, đếm bằng tokenizer của bge-m3) → tách theo khoản, gom các khoản liền nhau cho đến khi đầy. Mỗi phần lặp lại dòng tiêu đề Điều ở đầu. Ghi `khoan="Khoản 1-3"`.
5. Văn bản không có cấu trúc Điều (ví dụ công văn, file user tải lên lộn xộn) → fallback tách theo đoạn văn, cửa sổ ~500 token, chồng lấn 50 token.
6. Phụ lục, biểu mẫu sau chữ ký → một chunk `dieu_title="Phụ lục"` (hoặc nhiều chunk theo fallback).

`embed_text`:
```
{title} ({so_hieu})
{chuong} > {muc}
{dieu}. {dieu_title}
{text}
```
(bỏ các dòng rỗng nếu thiếu thông tin)

**Test bắt buộc** (`tests/test_chunker.py`): dùng file fixture tự viết nhỏ, có Chương/Mục/Điều, một Điều rất dài, phần mở đầu, phụ lục. Kiểm tra số chunk, breadcrumb, Điều không bị cắt giữa chừng.

## Embedder (`app/ingest/embedder.py`)

```python
class Embedder:
    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 16): ...
    def embed_documents(self, texts: list[str]) -> list[EmbeddingResult]: ...
    def embed_query(self, text: str) -> EmbeddingResult: ...

@dataclass
class EmbeddingResult:
    dense: list[float]                 # 1024, đã normalize
    sparse: dict[int, float]           # token_id -> weight
```

- Dùng `FlagEmbedding.BGEM3FlagModel(model_name, use_fp16=..., devices=...)`, gọi `encode(..., return_dense=True, return_sparse=True, return_colbert_vecs=False)`. Kiểm tra lại tên tham số theo phiên bản FlagEmbedding đang dùng.
- Chọn device: `auto` → `cuda` nếu có, rồi `mps`, rồi `cpu`. `use_fp16=True` chỉ khi chạy cuda; mps và cpu dùng fp32.
- `EMBEDDING_THREADS` có giá trị → `torch.set_num_threads(n)` trước khi load model.
- Có hai implementation cùng interface, chọn bằng `EMBEDDING_BACKEND`: `BgeM3Embedder` (thật) và `FakeEmbedder` (vector tất định từ hash, không import torch). Chi tiết ở `docs/08-dev-machines.md`. **Import torch / FlagEmbedding bên trong `BgeM3Embedder`**, không import ở đầu module, để `fake` chạy được mà không cần load torch.
- Kết quả sparse của FlagEmbedding có key là token id dạng string → chuyển sang int. Bỏ token có weight < 0.01 để giảm kích thước.
- **Singleton**: chỉ load model một lần cho mỗi process (khởi tạo trong lifespan của FastAPI, CLI tự khởi tạo riêng).
- Hàm encode là CPU/GPU-bound → khi gọi từ async code phải dùng `await asyncio.to_thread(...)`. Có một `asyncio.Semaphore(1)` để không encode song song.
- Test: unit test dùng `FakeEmbedder`. Có một test `@pytest.mark.slow` chạy model thật, kiểm tra hai câu đồng nghĩa có cosine cao hơn câu không liên quan. Test này phải pass trên cả Mac (mps) và Linux (cpu).

## Indexer (`app/ingest/indexer.py`)

```python
async def index_document(doc: LegalDocumentIn, text: str, *, owner_id: UUID | None) -> UUID
async def reindex_document(document_id: UUID, text: str) -> None
async def update_document_metadata(document_id: UUID, **fields) -> None   # Postgres + set_payload
async def delete_document(document_id: UUID) -> None
async def ensure_collection() -> None                                       # tạo collection + payload index nếu chưa có
```

`index_document`:
1. Tính `content_hash`. Nếu đã có văn bản cùng (`source`, `source_id`) và cùng hash → chỉ cập nhật metadata, return.
2. Đặt `ingest_status=processing`.
3. chunk → embed theo batch → xoá point cũ theo `document_id` → upsert point mới (batch 64) → ghi `legal_chunks` (xoá chunk cũ trước).
4. `ingest_status=done`, `embedding_model=...`. Có lỗi thì `failed` + `ingest_error`, không để dữ liệu nửa vời trong Qdrant (xoá point của document đó).

## Upload (`app/documents/router.py`)

- `POST /api/documents/upload` dạng multipart: `file` + các field `title` (bắt buộc), `so_hieu`, `doc_type`, `issued_date`, `effective_date`, `status` (mặc định `khong_xac_dinh`).
- Giới hạn file: 20 MB. Chỉ nhận `.pdf`, `.docx`, `.txt`. Kiểm tra cả magic bytes, không chỉ phần mở rộng.
- Lưu file gốc vào `storage/uploads/{owner_id}/{document_id}{ext}` (thư mục cấu hình bằng `UPLOAD_DIR`).
- Tạo document với `ingest_status=pending`, trả về `202` ngay. Xử lý ingest bằng `BackgroundTasks` (đủ cho giai đoạn đầu chạy một máy).
- `POST /api/documents/upload/preview`: chỉ parse và trả `detected` để điền sẵn form. Không lưu gì.
