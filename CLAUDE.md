# CLAUDE.md — Chatbot Luật Việt Nam

File này dành cho Claude Code. Đọc file này trước, sau đó đọc tài liệu trong `docs/` theo task đang làm.

## Dự án là gì

Web app có 2 tab chính:
1. **Văn bản**: user tải văn bản luật lên (PDF/DOCX/TXT), và xem danh sách văn bản trong hệ thống.
2. **Hỏi đáp**: chat với chatbot. Chatbot **chỉ trả lời dựa trên dữ liệu trong DB**. Mỗi câu trả lời phải kèm trích dẫn: link văn bản, ngày ban hành, ngày hiệu lực, tình trạng hiệu lực.

Ngoài ra còn có:
- Đăng ký / đăng nhập.
- Trang **Cài đặt**: user nhập API key **một lần** cho từng provider (OpenAI, Anthropic, Gemini). Key được mã hoá khi lưu DB. User chọn provider và model mặc định.
- **Crawler** lấy văn bản từ `vbpl.vn` (Cơ sở dữ liệu quốc gia về pháp luật, Bộ Tư pháp), có **job chạy hằng ngày** để cập nhật văn bản mới và tình trạng hiệu lực.
- Trang **Admin**: xem các lần crawl, chạy crawl thủ công.

Kiến trúc là **RAG pipeline cố định**, không phải agent. Xem lý do trong `docs/01-architecture.md`.

## Bản đồ tài liệu

| File | Đọc khi |
|---|---|
| `docs/01-architecture.md` | Luôn đọc ở task đầu tiên của mỗi phase |
| `docs/02-data-model.md` | Làm DB, Qdrant, indexer |
| `docs/03-ingestion.md` | Làm parser, chunker, embedding, upload |
| `docs/04-crawler.md` | Làm crawler, scheduler |
| `docs/05-rag-chat.md` | Làm retrieval, prompt, citation, chat |
| `docs/06-api.md` | Làm bất kỳ endpoint nào |
| `docs/07-frontend.md` | Làm UI |
| `docs/08-dev-machines.md` | Cài môi trường, torch, embedding, chuyển dữ liệu giữa 2 máy |
| `docs/TASKS.md` | **Danh sách task. Làm theo thứ tự, mỗi lần một task.** |

## Stack (đã chốt, không tự đổi)

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, Pydantic v2, quản lý package bằng `uv`
- **DB quan hệ:** PostgreSQL 16
- **Vector DB:** Qdrant, dùng dense + sparse vector (hybrid search)
- **Embedding:** `BAAI/bge-m3` qua `FlagEmbedding`, chạy local trên server. **Không dùng API key của user để embed.**
- **LLM:** LiteLLM, dùng API key của user
- **Scheduler:** APScheduler (AsyncIOScheduler) chạy trong process backend
- **Crawler:** `httpx` + `selectolax`. Chỉ dùng Playwright nếu trang bắt buộc render JS (xác định ở task T12).
- **Mã hoá key:** `cryptography` (Fernet / MultiFernet)
- **Auth:** JWT trong cookie httpOnly, hash mật khẩu bằng argon2 (`pwdlib`)
- **Frontend:** React + Vite + TypeScript, TanStack Query, Tailwind CSS

## Môi trường dev: 2 máy

Owner code luân phiên trên **2 máy**. Code phải chạy được trên cả hai mà không cần sửa gì, chỉ khác file `.env`.

| | Máy Mac | Máy Linux |
|---|---|---|
| Phần cứng | Apple Silicon, RAM 32 GB | không có GPU |
| Device embedding | `mps` | `cpu` |
| PyTorch | wheel mặc định trên PyPI (có MPS) | **wheel CPU-only** (không kéo CUDA nặng vài GB) |
| Vai trò | máy chính để **index khối lượng lớn** (backfill) | code, test, UI; chỉ index ít (`--limit` nhỏ) |

Quy ước chung:
- **Postgres và Qdrant chạy bằng Docker** trên cả hai máy (`docker compose up -d`), dùng named volume.
- **Backend chạy native trên cả hai máy** (không chạy trong Docker). Lý do: Docker trên macOS không truy cập được GPU Metal (MPS). Trên Linux chạy native để giống hệt Mac.
- Device chọn tự động: `cuda` → `mps` → `cpu`. Có thể override bằng `EMBEDDING_DEVICE`. Không được viết code chỉ chạy trên một hệ điều hành (đường dẫn dùng `pathlib`, không gọi lệnh shell riêng của macOS).
- Mỗi máy có `.env` riêng (không commit). Mẫu cho từng máy ở `.env.example` (có ghi chú khác biệt Mac/Linux).
- **Không index song song trên hai máy rồi trộn dữ liệu.** Dữ liệu chuyển một chiều Mac → Linux bằng `scripts/data_sync.py` (task T10b). Xem `docs/08-dev-machines.md`.
- Sau này deploy lên VPS Linux (có thể không có GPU). Máy Linux dev chính là môi trường giống production nhất.

## Lệnh thường dùng

```bash
docker compose up -d                       # postgres + qdrant
cd backend && uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
uv run pytest
uv run python -m app.crawler.cli backfill --limit 100   # crawl thử
cd frontend && npm i && npm run dev        # port 5173, proxy /api -> 8000

# Chuyển dữ liệu đã index từ Mac sang Linux
uv run python scripts/data_sync.py export --out storage/sync/      # chạy trên Mac
uv run python scripts/data_sync.py import --from storage/sync/     # chạy trên Linux
```
`uv sync` tự chọn đúng bản torch theo hệ điều hành (cấu hình trong `pyproject.toml`, xem `docs/08-dev-machines.md`).

## Quy tắc bắt buộc

1. **Không bao giờ log, trả về hay in ra API key gốc.** Frontend chỉ được nhận `key_last4`. Tắt log message và request body của LiteLLM.
2. **Link, ngày tháng và tình trạng hiệu lực trong câu trả lời phải lấy từ metadata trong DB.** Không bao giờ dùng giá trị do LLM tự sinh ra. LLM chỉ trả về marker `[n]`. Xem `docs/05-rag-chat.md`.
3. **Retrieval không đủ ngưỡng thì từ chối trả lời và không gọi LLM.**
4. **Postgres là nguồn sự thật.** Payload trong Qdrant chỉ là bản sao để lọc và hiển thị nhanh. Mỗi lần sửa metadata văn bản phải đồng bộ sang Qdrant.
5. **Văn bản do user tải lên là riêng tư của người đó.** Mọi truy vấn Qdrant phải có filter phân quyền (`source = vbpl` HOẶC `owner_id = current_user`).
6. **Crawler phải lịch sự với vbpl.vn:** tối đa 1 request/giây, có User-Agent rõ ràng, retry có backoff, tôn trọng robots.txt.
7. **Tên model không hardcode trong code.** Lấy từ `backend/config/models.yaml`.
8. Code, tên biến và comment viết bằng tiếng Anh. Text hiển thị cho người dùng và system prompt viết bằng tiếng Việt.
9. Mỗi task phải có test cho logic thuần (parser, chunker, citation, crypto). Chạy `uv run pytest` xanh rồi mới đánh dấu task xong.
10. Gặp quyết định chưa có trong docs: chọn phương án đơn giản nhất, ghi vào mục "Quyết định phát sinh" ở cuối `docs/TASKS.md`.

## Cấu trúc thư mục

```
law-chatbot/
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
├── docs/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── config/models.yaml
│   ├── app/
│   │   ├── main.py
│   │   ├── core/        # config.py, crypto.py, security.py, deps.py, logging.py
│   │   ├── db/          # session.py, models.py
│   │   ├── auth/        # router.py, service.py, schemas.py
│   │   ├── settings/    # router.py (API keys, default model), service.py
│   │   ├── llm/         # registry.py, client.py, errors.py
│   │   ├── ingest/      # parsers/, chunker.py, embedder.py, indexer.py, service.py
│   │   ├── documents/   # router.py (upload, list, detail, delete)
│   │   ├── crawler/     # vbpl_client.py, vbpl_parser.py, jobs.py, cli.py
│   │   ├── rag/         # retriever.py, prompt.py, citations.py, service.py
│   │   ├── chat/        # router.py (conversations, messages SSE)
│   │   ├── admin/       # router.py
│   │   └── scheduler.py
│   └── tests/
└── frontend/
    └── src/
        ├── api/  ├── pages/  ├── components/  └── hooks/
```
