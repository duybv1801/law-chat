# TASKS

## Cách dùng file này

- Làm **một task một lần**, theo thứ tự ID. Task chỉ được bắt đầu khi các task trong "Phụ thuộc" đã ✅.
- Trước khi code: đọc `CLAUDE.md` + các file trong mục "Đọc".
- Xong task: chạy test, đánh dấu ✅ ở bảng tổng quan, ghi ngắn gọn vào "Nhật ký" ở cuối file (đã làm gì, lệch gì so với docs).
- Không làm việc của task sau "tiện tay" trong task hiện tại.

Câu lệnh mẫu cho Claude Code:
> Đọc CLAUDE.md và docs/TASKS.md. Làm task T05. Xong thì chạy test và cập nhật TASKS.md.

## Tổng quan

| ID | Task | Phụ thuộc | Trạng thái |
|---|---|---|---|
| **P0 — Nền tảng** ||||
| T01 | Scaffold monorepo backend + frontend | — | ✅ |
| T02 | docker-compose Postgres + Qdrant, `.env.example`, config | T01 | ⬜ |
| T03 | DB models + Alembic migration đầu tiên | T02 | ⬜ |
| **P1 — Auth & Cài đặt** ||||
| T04 | Auth (register/login/refresh/logout/me) + CLI tạo admin | T03 | ⬜ |
| T05 | Crypto + API key CRUD + kiểm tra key | T04 | ⬜ |
| T06 | Model registry + `/models` + `/settings` | T05 | ⬜ |
| **P2 — Ingestion** ||||
| T07 | Parser PDF/DOCX/TXT + chuẩn hoá text | T01 | ⬜ |
| T08 | Chunker theo Điều | T07 | ⬜ |
| T09 | Embedder bge-m3 (dense + sparse) | T01 | ⬜ |
| T10 | Qdrant collection + indexer | T03, T08, T09 | ⬜ |
| T10b | Script đồng bộ dữ liệu Mac → Linux (`data_sync.py`) | T10 | ⬜ |
| T11 | API upload / list / detail / delete văn bản | T04, T10 | ⬜ |
| **P3 — Crawler** ||||
| T12 | Khảo sát vbpl.vn → `docs/vbpl-findings.md` | — | ⬜ |
| T13 | VbplClient + vbpl_parser + CLI `fetch-one` | T10, T12 | ⬜ |
| T14 | Jobs backfill / daily_new / status_refresh + CLI | T13 | ⬜ |
| T15 | Scheduler + API admin | T14 | ⬜ |
| **P4 — RAG chat** ||||
| T16 | Retriever hybrid + ngưỡng + phân quyền | T10 | ⬜ |
| T17 | LLM client (stream, map lỗi) | T06 | ⬜ |
| T18 | Prompt + citations | T16 | ⬜ |
| T19 | Chat service + API conversations + SSE | T17, T18 | ⬜ |
| **P5 — Frontend** ||||
| T20 | Khung app + auth pages + api client | T04 | ⬜ |
| T21 | Trang Cài đặt | T06, T20 | ⬜ |
| T22 | Tab Văn bản | T11, T20 | ⬜ |
| T23 | Tab Hỏi đáp | T19, T20 | ⬜ |
| T24 | Trang Admin | T15, T20 | ⬜ |
| **P6 — Chất lượng** ||||
| T25 | Bộ eval 30 câu + script đo + tinh chỉnh ngưỡng | T19, T14 | ⬜ |
| T26 | Rà soát bảo mật & log | T23 | ⬜ |

T07, T09 và T12 không phụ thuộc auth. Có thể làm song song với P1 nếu muốn.

---

## P0 — Nền tảng

### T01 — Scaffold monorepo
**Đọc:** CLAUDE.md, 08-dev-machines (mục 1, 6)
- `backend/` dùng `uv init`, Python 3.12, cài dependency theo stack trong CLAUDE.md. Tạo đúng cây thư mục (file rỗng có docstring mô tả là được).
- Cấu hình `[tool.uv.sources]` để torch dùng wheel CPU-only trên Linux (theo 08-dev-machines mục 1). Lock một lần, commit `uv.lock`.
- Thêm `.python-version`, `frontend/.nvmrc`, `.gitattributes`, `.editorconfig`.
- `app/main.py`: FastAPI có lifespan, `GET /api/health` trả `{"status":"ok"}`.
- Cấu hình `ruff` + `pytest` + `pytest-asyncio`.
- `frontend/`: Vite React TS + Tailwind + React Router + TanStack Query, proxy `/api`.
- `.gitignore` (bỏ qua `storage/`, `.env`, cache model).

**Xong khi:** `uv run uvicorn app.main:app` chạy được, `/api/health` ok; `npm run dev` hiện trang trắng có chữ tên app; `uv run pytest` chạy (0 test cũng được). Owner chạy `uv sync` trên **cả hai máy** thành công; trên Linux, `torch.__version__` có đuôi `+cpu`.

### T02 — Docker + config
**Đọc:** 01-architecture (mục Cấu hình)
- `docker-compose.yml`: `postgres:16` (có extension `citext`, `unaccent`), `qdrant/qdrant` (lấy tag ổn định mới nhất). Dùng **named volume** (`pgdata`, `qdrantdata`), không bind mount.
- `app/core/config.py`: `pydantic-settings`, đủ các biến trong `.env.example`.
- `.env.example` có hai khối ghi chú Mac / Linux theo 08-dev-machines mục 7.
- Nạp `PYTORCH_ENABLE_MPS_FALLBACK` vào `os.environ` trước khi bất kỳ module nào import torch.
- Script `scripts/gen_keys.py` sinh `APP_ENCRYPTION_KEYS` và `JWT_SECRET`.

**Xong khi:** `docker compose up -d` lên cả hai service; backend đọc được config; `/api/health` kiểm tra luôn kết nối Postgres + Qdrant.

### T03 — DB models + migration
**Đọc:** 02-data-model (phần Postgres)
- SQLAlchemy 2 async, `app/db/session.py` (engine, `get_session`), `app/db/models.py` đủ các bảng.
- Alembic async; migration đầu tiên tạo extension, bảng, index, CHECK constraint.

**Xong khi:** `alembic upgrade head` rồi `alembic downgrade base` chạy sạch; có test tạo user và document bằng DB test (dùng DB riêng `law_test`).

## P1 — Auth & Cài đặt

### T04 — Auth
**Đọc:** 06-api (Auth)
- Hash bằng `pwdlib` argon2; JWT bằng `pyjwt`; cookie như mô tả; middleware kiểm tra `X-Requested-With`.
- `current_user`, `require_admin`. Giới hạn tần suất login.
- CLI `python -m app.auth.cli create-admin <email>` (nhập mật khẩu qua prompt).

**Xong khi:** test register → me → logout → me trả 401; refresh hoạt động; login sai 11 lần bị chặn.

### T05 — Crypto + API key
**Đọc:** 05-rag-chat (Mã hoá key, LLM client – validate_key), 06-api (Settings)
- `app/core/crypto.py` + test.
- `PUT/DELETE /settings/api-keys/{provider}`. `validate_key` tạm thời có thể mock, T17 sẽ hoàn thiện. Tạo sẵn interface trong `app/llm/client.py`.
- Bảo đảm key không xuất hiện trong log: test dùng `caplog` gửi key giả rồi assert chuỗi key không có trong log.

**Xong khi:** test CRUD key; GET settings chỉ thấy `key_last4`; xoá user thì key bị xoá theo (cascade).

### T06 — Model registry + settings
**Đọc:** 05-rag-chat (Model registry)
- `config/models.yaml` với placeholder `<model-id>`. Owner sẽ tự điền model string.
- `GET /models`, `GET/PUT /settings`. Không cho chọn model của provider chưa có key.

**Xong khi:** test load yaml lỗi thì báo rõ; test PUT settings.

## P2 — Ingestion

### T07 — Parser
**Đọc:** 03-ingestion (Parser)
- `parsers/pdf.py`, `docx.py`, `txt.py`, `normalize.py`, `detect.py` (đoán metadata).
- Fixture nhỏ tự tạo trong `tests/fixtures/` (sinh DOCX/PDF bằng code trong conftest, không commit file lớn).

**Xong khi:** test NFC (cùng một chữ có dấu ở 2 dạng → ra một dạng); test PDF scan báo lỗi đúng; test detect số hiệu.

### T08 — Chunker
**Đọc:** 03-ingestion (Chunker)
**Xong khi:** đủ các test bắt buộc trong docs.

### T09 — Embedder
**Đọc:** 03-ingestion (Embedder), 08-dev-machines (mục 2)
- `BgeM3Embedder` + `FakeEmbedder`, factory chọn theo `EMBEDDING_BACKEND`.
- Model tải về cache của HuggingFace (khoảng 2,3 GB) lần chạy đầu tiên.
- Script `scripts/bench_embed.py`: encode 100 đoạn mẫu, in ra device, số thread, batch size, thời gian.

**Xong khi:** unit test dùng `FakeEmbedder` chạy được mà không import torch; test `slow` pass trên **cả Mac (mps) và Linux (cpu)**; owner chạy `bench_embed.py` trên hai máy và ghi kết quả vào Nhật ký (dùng để ước lượng thời gian backfill).

### T10 — Qdrant + indexer
**Đọc:** 02-data-model (Qdrant, Đồng bộ), 03-ingestion (Indexer)
- `ensure_collection()` gọi trong lifespan; kiểm tra model embedding khớp với collection.

**Xong khi:** test tích hợp: index một văn bản → đúng số point trong Qdrant và đúng số dòng `legal_chunks`; index lại cùng hash → không đổi gì; đổi text → chunk được thay mới; delete → sạch cả hai nơi; `update_document_metadata` → payload trong Qdrant đổi theo.

### T10b — Đồng bộ dữ liệu Mac → Linux
**Đọc:** 08-dev-machines (mục 4)
- `scripts/data_sync.py` với 2 lệnh `export` / `import` (`typer`), có `--dry-run` cho import.
- Ghi file theo kiểu stream (gzip, theo batch), không load toàn bộ vào RAM.

**Xong khi:** test tích hợp trong cùng một máy: index 3 văn bản vbpl + 1 văn bản upload → export → xoá dữ liệu vbpl → import → số document, chunk, point khớp manifest; văn bản upload không bị ảnh hưởng; manifest khác `embedding_model` thì import từ chối chạy.

### T11 — API văn bản
**Đọc:** 03-ingestion (Upload), 06-api (Documents)
**Xong khi:** test upload TXT → ingest xong → hiện trong `GET /documents?scope=mine`; user B không thấy văn bản của user A; user B xoá văn bản của A → 403/404.

## P3 — Crawler

### T12 — Khảo sát vbpl.vn
**Đọc:** 04-crawler (Bối cảnh, T12)
- Chỉ khảo sát, lưu fixture, viết `docs/vbpl-findings.md`. Không viết code production.
- Gửi ít request (vài chục), mỗi request cách nhau ≥ 1 giây.

**Xong khi:** findings trả lời đủ 9 câu hỏi trong docs; có bảng map tình trạng; **dừng lại cho owner duyệt** trước khi làm T13.

### T13 — Client + parser
**Đọc:** 04-crawler (Client), vbpl-findings.md
**Xong khi:** test parser dùng fixture đúng các trường; `fetch-one --dry-run` in ra metadata + chunk của một văn bản thật.

### T14 — Jobs + CLI
**Đọc:** 04-crawler (Jobs, CLI)
**Xong khi:** `backfill --limit 50` chạy thật, có 50 văn bản trong DB; chạy lại → toàn bộ `unchanged`; test advisory lock (chạy 2 job cùng loại song song → job thứ hai bỏ qua); test `status_refresh` dùng client giả trả tình trạng mới → payload Qdrant đổi.

### T15 — Scheduler + admin API
**Đọc:** 04-crawler (Scheduler), 06-api (Admin)
**Xong khi:** bật scheduler với cron test (mỗi phút) → thấy `crawl_runs` mới; các endpoint admin có test phân quyền (user thường → 403).

## P4 — RAG chat

### T16 — Retriever
**Đọc:** 05-rag-chat (Retriever)
**Xong khi:** test tích hợp với vài văn bản mẫu: câu hỏi có số hiệu → trả đúng văn bản đó; văn bản hết hiệu lực toàn bộ không xuất hiện mặc định; văn bản `user_upload` của người khác không bao giờ xuất hiện; câu hỏi không liên quan → dưới ngưỡng.

### T17 — LLM client
**Đọc:** 05-rag-chat (LLM client)
- Hoàn thiện `validate_key` ở T05.

**Xong khi:** unit test map lỗi (mock LiteLLM); script `scripts/smoke_llm.py --provider X` để owner thử với key thật.

### T18 — Prompt + citations
**Đọc:** 05-rag-chat (Prompt, Citations)
**Xong khi:** test `extract_markers` với các dạng `[1]`, `[1, 2]`, `[1][3]`, marker không tồn tại; test cắt bớt context khi vượt giới hạn.

### T19 — Chat service + API
**Đọc:** 05-rag-chat (Chat service), 06-api (Chat)
**Xong khi:** test với LLM giả (stream cố định có marker): thứ tự event đúng `start → token* → citations → done`; trường hợp dưới ngưỡng không gọi LLM (assert mock không được gọi); chưa có key → event `error no_api_key`; message được lưu kèm citations.

## P5 — Frontend

### T20 — Khung app + auth
**Đọc:** 07-frontend (Cấu trúc, Layout)
**Xong khi:** đăng ký / đăng nhập / đăng xuất chạy được; route được bảo vệ; tự refresh token.

### T21 — Cài đặt
**Xong khi:** thêm / thay / xoá key cho 3 provider; key sai hiện lỗi; chọn model mặc định.

### T22 — Tab Văn bản
**Xong khi:** upload kèm preview metadata; danh sách có lọc và tìm kiếm; polling trạng thái xử lý; drawer chi tiết; xoá văn bản.

### T23 — Tab Hỏi đáp
**Xong khi:** stream mượt, nút dừng hoạt động; chip `[n]` liên kết với thẻ nguồn; thẻ nguồn hiện đủ link / ngày ban hành / ngày hiệu lực / tình trạng; xử lý lỗi key; lịch sử hội thoại.

### T24 — Admin
**Xong khi:** xem thống kê, xem crawl runs, chạy job thủ công.

## P6 — Chất lượng

### T25 — Eval
- `eval/questions.yaml`: 30 câu hỏi, mỗi câu ghi văn bản + Điều kỳ vọng (owner duyệt danh sách). Thêm 5 câu ngoài phạm vi (kỳ vọng từ chối).
- `scripts/eval_retrieval.py`: tính recall@k theo (`so_hieu`, `dieu`), tỉ lệ từ chối đúng. Quét `RAG_MIN_SCORE` từ 0.3 đến 0.7, in bảng kết quả.

**Xong khi:** có báo cáo `eval/report.md` và đề xuất giá trị `RAG_MIN_SCORE`.

### T26 — Rà soát bảo mật
- Grep toàn bộ code tìm chỗ có thể log key / request body.
- Kiểm tra mọi query Qdrant và Postgres của documents / chat đều có filter phân quyền.
- Header bảo mật cơ bản; CORS chỉ cho origin của frontend; giới hạn kích thước request.

**Xong khi:** checklist trong mục Nhật ký được tick đủ.

---

## Quyết định phát sinh
(Claude Code ghi vào đây khi phải tự quyết một điều chưa có trong docs)

- T01: Tailwind v4 qua plugin `@tailwindcss/vite` (không có `tailwind.config.js`, chỉ `@import 'tailwindcss'`). React Router dùng package `react-router` (v8). `frontend/.nvmrc` = `22` (bản Node LTS đang cài trên máy Linux).
- T01: Backend là project `uv` dạng app (không build package); `pytest` dùng `pythonpath = ["."]` để import `app`. Mặc định `pytest` skip test `slow` (hook trong `tests/conftest.py`, hiện rõ là skipped); chạy bằng `uv run pytest -m slow`.
- T01: Frontend dùng TypeScript 7 (bản native) và `oxlint` thay ESLint (mặc định của create-vite hiện tại, nhanh hơn, ít cấu hình).
- T01: `[tool.uv] environments` chỉ lock cho `darwin` và `linux`. Ruff cho phép `fastapi.Depends/Query/...` làm default argument (B008). Proxy Vite trỏ `127.0.0.1:8000` (tránh `localhost` resolve ra `::1` trên macOS).
- T01: Thêm sẵn các dependency đã chốt cho các task sau (`pymupdf`, `python-docx`, `tenacity`, `typer`, `sse-starlette`, `pyyaml`, `apscheduler<4`, ...) để chỉ lock một lần.

## Nhật ký
(mỗi task một dòng: `T0x — ngày — tóm tắt — lệch so với docs (nếu có)`)

- T01 — 2026-11-02 — Scaffold `backend/` (uv, Python 3.12, cây thư mục `app/` với file docstring, `/api/health`, lifespan, ruff + pytest-asyncio, 1 test health) và `frontend/` (Vite 8 + React 19 + TS + Tailwind 4 + React Router + TanStack Query, proxy `/api` → 8000). Thêm `.python-version`, `.gitignore`, `.gitattributes`, `.editorconfig`, `frontend/.nvmrc`. Đã kiểm tra trên Linux: `uv sync` OK, torch `2.15.0+cpu`, `cuda=False`, không có gói `nvidia-*`; `uvicorn` + `/api/health` OK (cả qua proxy Vite); `npm run build` OK; `pytest` 1 passed; `ruff` sạch. — Lệch: thư mục chưa phải git repo nên `uv.lock` chưa được commit; **chưa chạy `uv sync` trên Mac** (lock đã có wheel `macosx_14_0_arm64` từ PyPI, owner cần kiểm tra). `alembic/` và `config/` chỉ có `.gitkeep` (T03, T06 sẽ điền).
