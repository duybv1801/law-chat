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
| T02 | docker-compose Postgres + Qdrant, `.env.example`, config | T01 | ✅ |
| T03 | DB models + Alembic migration đầu tiên | T02 | ✅ |
| **P1 — Auth & Cài đặt** ||||
| T04 | Auth (register/login/refresh/logout/me) + CLI tạo admin | T03 | ✅ |
| T05 | Crypto + API key CRUD + kiểm tra key | T04 | ✅ |
| T06 | Model registry + `/models` + `/settings` | T05 | ✅ |
| **P2 — Ingestion** ||||
| T07 | Parser PDF/DOCX/TXT + chuẩn hoá text | T01 | ✅ |
| T08 | Chunker theo Điều | T07 | ✅ |
| T09 | Embedder bge-m3 (dense + sparse) | T01 | ✅ |
| T10 | Qdrant collection + indexer | T03, T08, T09 | ✅ |
| T10b | Script đồng bộ dữ liệu Mac → Linux (`data_sync.py`) | T10 | ⬜ |
| T11 | API upload / list / detail / delete văn bản | T04, T10 | ✅ |
| **P3 — Crawler** ||||
| T12 | Khảo sát vbpl.vn → `docs/vbpl-findings.md` | — | ✅ |
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
- T02: `.env` đặt ở **gốc repo** (cạnh `.env.example`); `config.py` đọc theo đường dẫn tuyệt đối nên chạy từ thư mục nào cũng được. Đường dẫn tương đối (`UPLOAD_DIR`, `HF_HOME`) được neo vào `backend/`. Biến để trống (`EMBEDDING_THREADS=`) coi như không đặt (`env_ignore_empty`).
- T02: Extension `citext`, `unaccent` và DB test `law_test` được tạo bằng script init `docker/postgres/init/01-extensions.sql` (bind mount read-only vào `docker-entrypoint-initdb.d`; dữ liệu vẫn ở named volume). Script chỉ chạy khi volume rỗng; migration T03 vẫn tự `CREATE EXTENSION IF NOT EXISTS`. Qdrant pin `qdrant/qdrant:v1.20.0` (khớp `qdrant-client` 1.20).
- T02: `APP_ENCRYPTION_KEYS` và `JWT_SECRET` là bắt buộc (không có default), kiểu `SecretStr` để không lộ qua `repr`/log. `CRAWLER_RATE_LIMIT_RPS` bị chặn `<= 1` ngay ở config (quy tắc 6). `SCHEDULER_ENABLED` mặc định `false` nếu không đặt.
- T02: `/api/health` trả `{"status","postgres","qdrant"}`; một service lỗi → HTTP 503 `status=degraded` (timeout 3s mỗi check, chỉ log tên exception). `app/db/session.py` mới có `engine`; `get_session` để T03. `AsyncQdrantClient` tạo trong lifespan (`app.state.qdrant`), tắt `check_compatibility` để không gọi mạng lúc khởi động.
- T02 (review): `DATABASE_URL` cũng là `SecretStr` (chứa mật khẩu DB; gọi `.get_secret_value()` khi tạo engine). `JWT_SECRET` tối thiểu 32 ký tự; mỗi phần tử `APP_ENCRYPTION_KEYS` phải là Fernet key hợp lệ. `hide_input_in_errors=True` để lỗi validate không in giá trị secret. `HF_HOME` được ghi đè bằng đường dẫn đã resolve (không `setdefault`). Test mặc định dùng DB `law_test` (`conftest.py`). Qdrant có healthcheck gọi `/readyz` qua `/dev/tcp` của bash (image không có curl/wget).
- T03: `legal_documents.content_hash` cho phép NULL (docs không ghi NULL) vì upload tạo document `pending` trước khi parse (03-ingestion mục Upload); PDF scan không bao giờ có hash. `owner_id` FK **không** `ON DELETE CASCADE` (xoá văn bản phải qua indexer để dọn Qdrant trước); `user_settings`, `user_api_keys`, `conversations` → `CASCADE`; `messages`, `legal_chunks` cascade theo cha. Default: `role=user`, `status=khong_xac_dinh`, `scope=trung_uong`, `ingest_status=pending`, `crawl_runs.status=running`, mảng rỗng / `{}` / `[]` cho array, jsonb. `crawl_runs` cũng có `created_at/updated_at` (quy tắc "mọi bảng").
- T03: Thêm ràng buộc ngoài docs: CHECK `source <> 'user_upload' OR owner_id IS NOT NULL`, UNIQUE (`document_id`, `ordinal`) cho `legal_chunks`, index (`user_id`, `updated_at`) cho `conversations`, (`conversation_id`, `created_at`) cho `messages`, (`job_type`, `started_at`) cho `crawl_runs`. Tên constraint theo naming convention trong `Base.metadata`. Giá trị hợp lệ cho các CHECK là hằng số tuple trong `app/db/models.py` (dùng lại cho schema Pydantic sau này).
- T03: Alembic async (`alembic/env.py`) lấy URL từ `settings.database_url`, không ghi URL trong `alembic.ini`. Revision đặt id thủ công dạng `0001`, `0002`, ... (`--rev-id`). Migration đầu `CREATE EXTENSION IF NOT EXISTS citext, unaccent`; downgrade **giữ** extension (init SQL cũng tạo, object khác có thể phụ thuộc).
- T03 (review): `created_at`/`updated_at` dùng `clock_timestamp()` thay `now()` để các message chèn trong cùng transaction vẫn đúng thứ tự. Trigger `messages_touch_conversation` (AFTER INSERT ON messages) đặt `conversations.updated_at = NEW.created_at` để danh sách hội thoại sắp theo hoạt động gần nhất. `updated_at` các bảng khác vẫn chỉ cập nhật qua ORM `onupdate` (UPDATE bằng Core phải tự set).
- T03: Test DB: fixture `migrated_db` (session) `DROP SCHEMA public CASCADE` → `upgrade head` trên `law_test` (không dùng `downgrade base`, để chạy được cả khi DB đang ở revision của nhánh khác); **từ chối chạy nếu tên DB không kết thúc bằng `_test`**; skip có lý do nếu Postgres không chạy hoặc chưa có DB `law_test` (bắt cả `DBAPIError`). `db_session` bọc mỗi test trong transaction ngoài rồi rollback (`join_transaction_mode="create_savepoint"`), engine `NullPool` (mỗi test một event loop).
- T04: Refresh token là JWT **stateless** (claim `type=access|refresh`, `jti` ngẫu nhiên), không có bảng lưu/thu hồi. `/auth/refresh` cấp lại **cả hai** cookie (xoay vòng). `/auth/logout` chỉ xoá cookie, không cần đăng nhập; token cũ hết hạn tự nhiên. User bị `is_active=false` → access/refresh đều bị từ chối (kiểm tra DB mỗi request).
- T04: Lỗi thống nhất qua `app/core/errors.py` (`AppError` + handler cho `HTTPException` và lỗi validate). Lỗi validate (`422 validation_error`) chỉ nêu tên trường, **không** echo giá trị (có thể là mật khẩu / API key). Mã lỗi auth: `not_authenticated` (401), `invalid_credentials` (401, giống nhau cho email không tồn tại và sai mật khẩu, có verify hash giả để không lộ qua thời gian), `account_disabled` (403), `email_taken` (409), `forbidden` (403), `too_many_requests` (429 + `Retry-After`), `csrf_failed` (403).
- T04: CSRF là middleware ASGI thuần `app/core/csrf.py` (không buffer response, an toàn cho SSE): mọi method khác GET/HEAD/OPTIONS phải có đúng `X-Requested-With: fetch`, áp cho toàn app.
- T04: Rate limit login tự viết (`app/auth/rate_limit.py`, sliding window trong bộ nhớ) theo `request.client.host`, 10 lần **sai** / 15 phút (đăng nhập đúng không bị tính, để nhiều user chung IP/NAT không khoá lẫn nhau; khi đã chạm ngưỡng thì cả mật khẩu đúng cũng bị 429). Khi deploy sau reverse proxy cần bật `--proxy-headers` của uvicorn để lấy IP thật.
- T04 (review): `/auth/register` cũng bị giới hạn 5 lần / giờ / IP. Limiter xoá key khi hết lượt và quét key cũ mỗi cửa sổ (bộ nhớ không tăng mãi). Hash/verify argon2 chạy qua `asyncio.to_thread` để không chặn event loop. Lỗi auth tạo mới mỗi lần raise (factory `invalid_credentials()`, `email_taken()`, `not_authenticated()`), không dùng instance dùng chung. `create_user` chỉ đổi `IntegrityError` của constraint `uq_users_email` thành `email_taken`, lỗi khác raise nguyên. Logic "token → user active" gom vào `deps.user_from_token` (dùng cho cả `current_user` và `/auth/refresh`).
- T04 (review, **để sau**): (1) Logout không thu hồi refresh token: token bị lộ trước khi logout vẫn xoay vòng được tối đa 14 ngày/lần. Muốn sửa cần cột `users.token_version` (tăng khi logout/đổi mật khẩu, nhúng vào JWT) — làm cùng task hardening. (2) `/auth/register` trả 409 `email_taken` nên vẫn dò được email đã đăng ký (verify hash giả ở login chỉ che thời gian); chấp nhận vì register đã bị rate limit, sửa triệt để cần xác minh email.
- T04: Email kiểm tra bằng regex đơn giản (không thêm `email-validator`), chuẩn hoá `strip().lower()`. Mật khẩu 8–128 ký tự (chặn trên để giới hạn chi phí argon2). Hash dùng `PasswordHash.recommended()` (argon2id), tự rehash khi tham số cũ (`verify_and_update`).
- T04: CLI `create-admin` báo lỗi (exit 1) nếu email đã tồn tại, không tự nâng quyền user có sẵn.
- T04: Test API dùng `httpx.AsyncClient` + `ASGITransport` (fixture `client` trong `conftest.py`, override `get_session` bằng `db_session`), không dùng `TestClient` vì asyncpg connection phải ở cùng event loop.
- T05: `crypto.py` cache một `MultiFernet` từ `APP_ENCRYPTION_KEYS`; các hàm nhận thêm tham số `fernet=` (chỉ để test / script rotate). `build_fernet(keys)` dùng chung.
- T05: `validate_key` tạm thời (đến T17) chỉ từ chối key rỗng hoặc có khoảng trắng, **chưa gọi provider** → `validated_at` lúc này chỉ có nghĩa "đã qua kiểm tra hình thức". Router gọi qua module `app.llm.client` để test monkeypatch được. `LLMError(code, user_message)` + câu thông báo tiếng Việt cho 4 mã đã có trong `app/llm/errors.py`; phần map exception LiteLLM để T17.
- T05: `PUT /settings/api-keys/{provider}`: key được `strip()`, dài 8–512 ký tự (`SecretStr`, không lộ qua repr); provider ngoài `openai|anthropic|gemini` → 422. `LLMError invalid_key` → `400 invalid_key`; mã khác → 503/502 (xem mục review bên dưới); cả hai trường hợp **không lưu** và giữ nguyên key cũ. Lưu bằng upsert `ON CONFLICT (user_id, provider)` (giữ nguyên `id`), đặt lại `last_used_at = NULL` khi thay key. `DELETE` idempotent (không có key vẫn `204`). Log chỉ ghi `user_id` + `provider`.
- T05 (review): `validate_key` trả `bool` — `True` chỉ khi provider đã xác nhận key; bản tạm trả `False` nên `validated_at = NULL` (không ghi "đã kiểm tra" cho key chưa gọi provider; thay key cũng reset về `NULL`). Map lỗi kiểm tra key: `invalid_key` → 400, `rate_limited` → **503** (provider đang chặn tạm, không phải lỗi của client), mã khác → 502; exception lạ (timeout, lỗi LiteLLM chưa map) → `502 provider_error`, log **chỉ tên class** (message có thể chứa request kèm key). `DELETE` key xoá luôn `default_provider/default_model_id` nếu đang trỏ vào provider đó. `PUT` key giới hạn 20 lần / giờ / user (`api_key_save_limiter`; `enforce`/`too_many_requests` chuyển sang `app/auth/rate_limit.py` dùng chung). `Provider` Literal định nghĩa một chỗ trong `db/models.py`, `PROVIDERS = get_args(Provider)`. Kiểm tra Fernet key gom vào `crypto.build_fernet` (config gọi lại; `crypto` import `settings` lười để tránh vòng import).
- T05: `GET /settings` làm luôn ở T05 (cần cho điều kiện "chỉ thấy `key_last4`"); `default_provider/default_model_id` đọc từ `user_settings` (chưa có dòng → `null`). `PUT /settings` và kiểm tra model để T06. Thêm file `app/settings/schemas.py`.
- T06: `app/llm/registry.py` kiểm tra `models.yaml` bằng Pydantic `extra="forbid"` (bắt lỗi gõ sai tên trường): `id` dạng `[a-z0-9][a-z0-9._-]*`, `litellm_model` và `validation_model.<provider>` **phải bắt đầu bằng `<provider>/`** (key của provider đó mới là key được gửi đi), id không trùng, ít nhất 1 model `enabled`, `validation_model` đủ cả 3 provider. Lỗi → `RegistryError` ghi đường dẫn file + từng trường sai; `lifespan` gọi `get_registry()` nên backend **dừng ngay khi khởi động** nếu file hỏng. Placeholder `<...>` chỉ log warning (không chặn khởi động) để dev được khi chưa điền model thật. Đường dẫn cố định `backend/config/models.yaml` (không thêm biến env). `get_registry()` cache một lần / process; dependency FastAPI là `registry_dep` (async, test override bằng `dependency_overrides`).
- T06: `GET /models` cần đăng nhập (docs không ghi), trả các model `enabled` theo thứ tự trong file; router `models_router` đặt trong `app/settings/router.py`. `PUT /settings` body `{default_model_id: str | null}` (bắt buộc có trường; `null` = xoá mặc định). Model không có / bị tắt → `400 unknown_model`; provider chưa có key → `400 no_api_key` (cùng mã với event chat ở T19); lỗi thì giữ nguyên mặc định cũ. Lưu bằng upsert `user_settings`. `GET /settings` trả nguyên giá trị đã lưu kể cả khi model sau đó bị tắt/xoá khỏi yaml — chat service (T19) phải tự kiểm tra lại model khi dùng.
- T06 (review): `GET /settings` (và response của `PUT /settings`) trả `default_model_id/default_provider = null` nếu model đã lưu bị tắt/xoá khỏi yaml; dòng trong DB **giữ nguyên** (bật lại model thì mặc định quay lại). `set_default_model` khoá dòng key bằng `SELECT ... FOR UPDATE` để `DELETE` key chạy đồng thời không để lại mặc định trỏ vào provider không còn key. `load_registry` đổi cả `OSError`/`UnicodeDecodeError` thành `RegistryError`. `PROVIDER_LABELS` chuyển sang `db/models.py` cạnh `Provider` (test kiểm tra đủ provider). `MODEL_ID_MAX` dùng chung cho `ModelSpec.id` và `SettingsIn`. Comment trong `models.yaml` viết tiếng Anh (quy tắc 8). Có test `lifespan` dừng khi yaml hỏng.
- T12: Liệt kê văn bản vbpl.vn bằng **sitemap** (robots.txt cấm `/api/`; trang danh sách chỉ tải kết quả qua API). Đọc trang chi tiết qua payload RSC (`initialDoc`), không dùng Playwright. Map tình trạng theo `effStatus.code`. Ba điểm lệch so với 04-crawler (list bằng sitemap, bỏ cập nhật theo quan hệ văn bản, bỏ qua văn bản không có toàn văn HTML) ghi ở cuối `docs/vbpl-findings.md` — **owner đã duyệt 2026-11-02** (kèm chiến lược cập nhật hiệu lực qua `lastmod` + `status_refresh` ưu tiên văn bản quá `effective_until`).
- T12: `.gitattributes` thêm `backend/tests/fixtures/** -text` để fixture HTML giữ nguyên byte (row text của RSC tính độ dài theo byte UTF-8, git đổi xuống dòng sẽ làm hỏng parser test).
- T07: API parser: `parse_bytes(data, filename)` / `parse_file(path)` trong `app/ingest/parsers/__init__.py` chọn parser theo phần mở rộng → `normalize_text` → `detect_metadata`; `build_parsed(raw_text)` dùng lại cho vbpl parser (T13). Lỗi là `ParseError(code, user_message)` (file `parsers/base.py`, giống `LLMError`): `unsupported_format`, `corrupt_file`, `encrypted_pdf`, `scanned_pdf` ("PDF scan chưa hỗ trợ."), `empty_document`; `user_message` là giá trị ghi vào `ingest_error`. Kiểm tra magic bytes để T11.
- T07: PDF có mật khẩu → `encrypted_pdf`. Text lấy bằng `get_text("text", sort=True)`, giữ nguyên xuống dòng của layout (không nối dòng bị ngắt).
- T07: Bỏ header/footer: chỉ xét 3 dòng không rỗng đầu và cuối mỗi trang, cần ≥ 3 trang, xuất hiện ở **> 50%** số trang. Chữ số chỉ được che (để khớp "Trang 3/10") với dòng ≤ 2 từ; dòng dài phải khớp nguyên văn. Dòng bắt đầu bằng Phần/Chương/Mục/Điều hoặc `1.`/`a)` không bao giờ bị bỏ.
- T07: Chuẩn hoá: NFC, bỏ ký tự vô hình (zero-width, BOM, soft hyphen) và ký tự điều khiển, gộp khoảng trắng (cả tab, NBSP) trong dòng, tối đa 1 dòng trống giữa các đoạn. DOCX đọc cả **bảng** theo thứ tự trong body (header văn bản luật thường là bảng chứa cơ quan / số hiệu / ngày), ô gộp chỉ lấy một lần. TXT: BOM (UTF-8/UTF-16) → UTF-16 không BOM (đoán qua byte NUL) → UTF-8 → `cp1258` (`errors="replace"`).
- T07: `detected` luôn có đủ 4 khoá `so_hieu, doc_type, issued_date (date), title` (không tìm thấy → `None`), chỉ tìm trong phần trước "Điều 1" (giới hạn xem mục review bên dưới). `doc_type` từ dòng tiêu đề in hoa ("NGHỊ ĐỊNH", bỏ qua dòng kết thúc bằng `:` như "QUYẾT ĐỊNH:"), không có thì đoán từ mã số hiệu (`NĐ`, `TT`, `TTLT`, `QĐ`, `CT`, ...; `QH` không đoán vì có thể là Luật hoặc Nghị quyết). `title` = loại + dòng trích yếu, phần in hoa chuyển chữ thường (mất viết hoa danh từ riêng, user tự sửa).
- T07 (review): Kiểm tra **scan theo từng trang, sau khi bỏ header/footer**: trang < 50 ký tự (không tính khoảng trắng) **và có ảnh** là trang scan; trang ít chữ không có ảnh (trang trắng, trang chữ ký) không tính. Tỉ lệ trang chữ / (trang chữ + trang scan) < 10% → `scanned_pdf`; < 90% → nhận nhưng có warning `{"code": "partially_scanned", "scanned_pages": [...]}` (số trang từ 1, sau này làm đầu vào OCR). Ngưỡng 10% (không phải 20%) để PDF 10 trang chữ + 90 trang scan vẫn được nhận kèm warning.
- T07 (review): `ParsedDocument.warnings: list[dict]` (JSON được). Mỗi extractor trả `(text, warnings)`. **Chưa lưu DB**: cần cột `legal_documents.ingest_warnings` (jsonb) + migration, để T11 làm cùng upload và trả trong preview.
- T07 (review): Lỗi đọc file: PDF/DOCX bắt **mọi** exception khi mở và trích text → `corrupt_file`. PDF `page_count == 0`, hoặc file MuPDF phải sửa (`is_repaired`) mà không còn chữ → `corrupt_file` (không phải `empty_document`).
- T07 (review): Phần đầu văn bản dừng ở "Điều 1" **không phân biệt hoa thường** ("ĐIỀU 1."), tối đa 40 dòng / 3000 ký tự. Dòng "Căn cứ ..." bị bỏ khi dò số hiệu và ngày (là của văn bản khác). `issued_date` theo thứ tự ưu tiên: (1) "thông qua ngày …" trong toàn văn — chỉ với Hiến pháp/Bộ luật/Luật/Pháp lệnh/Nghị quyết(/liên tịch) hoặc khi chưa rõ loại; (2) dòng "<địa danh>, ngày …" (không neo đầu dòng vì cột header PDF có thể chung dòng với "Số: …"); (3) ngày đầu tiên còn lại trong phần đầu. Ngày không tồn tại hoặc ở tương lai bị bỏ. `detected` thêm khoá `issued_date_source` (`passed` | `place_date` | `fallback` | `None`) để UI nhắc kiểm tra khi là `fallback` (lệch 06-api: 5 khoá).
- T08: API `chunk_document(text, *, title, so_hieu, count_tokens=None, max_tokens=800) -> list[Chunk]` (`Chunk` = các cột của `legal_chunks` trừ id/document_id). Đếm token mặc định bằng `default_token_counter()`: với `EMBEDDING_BACKEND=bge` dùng `bge_token_counter()`, chỉ tải `tokenizer.json` của `EMBEDDING_MODEL` vào cache HuggingFace qua `huggingface_hub` + `tokenizers` (khai báo trực tiếp trong `pyproject.toml`, không import torch); với `fake` đếm theo từ, không tải gì. Lifespan nạp sẵn bằng `asyncio.to_thread` (lỗi mạng chỉ log warning, không chặn khởi động). Unit test truyền bộ đếm theo khoảng trắng.
- T08: Tiêu đề Phần/Chương/Mục khớp **không phân biệt hoa thường** ("PHẦN THỨ NHẤT", "CHƯƠNG I") và phần còn lại của dòng phải rỗng, bắt đầu bằng dấu câu hoặc in hoa (để "Mục 2 Chương này quy định..." trong thân Điều không bị coi là tiêu đề). Tên nằm ở các dòng in hoa kế tiếp (có thể nhiều dòng do PDF xuống dòng) được nối bằng dấu cách: `Chương I. NHỮNG QUY ĐỊNH CHUNG`. Dòng tiêu đề chỉ vào breadcrumb, không nằm trong text chunk nào. Breadcrumb trong `embed_text` gồm cả Phần: `{phan} > {chuong} > {muc}`.
- T08: `Điều` nhận cả `Điều 5:` và `ĐIỀU 5.` (văn bản cũ). Số Điều phải tăng: nhận số kế tiếp (`n+1`) hoặc hậu tố chữ cùng số (`Điều 5a` sau `Điều 5`); nhảy số chỉ nhận khi dòng có tiêu đề; số lùi không bao giờ nhận. Vì vậy dòng `Điều 5.` do PDF xuống dòng giữa câu vẫn là thân Điều. Hệ quả: nếu trích xuất mất một Điều và Điều sau không có tiêu đề trên cùng dòng thì nó bị gộp vào Điều trước. Phần mở đầu = text trước tiêu đề đầu tiên (Phần/Chương/Mục/Điều). Text nằm giữa tiêu đề Chương và Điều đầu tiên (hiếm) thành chunk riêng `dieu=None, dieu_title=None`, không bị bỏ. Phần chữ ký / "thông qua ngày" sau Điều cuối nằm trong chunk Điều cuối.
- T08: Khoản phải đánh số liên tiếp 1, 2, 3...; phần dẫn trước khoản 1 đi cùng khoản 1. Một khoản dài hơn ngân sách → cắt theo cửa sổ, mọi phần đều ghi `Khoản n`; Điều dài không có khoản → cửa sổ, `khoan=None`. Dòng tiêu đề Điều dài hơn nửa giới hạn (cả Điều nằm trên một dòng) → cắt cửa sổ cả block, `dieu_title=None`. `token_count` đo trên `text`, giới hạn 800 cũng đo trên `text` (gộp khoản theo tổng token từng khoản, xấp xỉ).
- T08: Phụ lục chỉ nhận sau Điều đầu tiên: dòng in hoa bắt đầu bằng "PHỤ LỤC/MẪU SỐ/BIỂU MẪU", hoặc dòng chỉ gồm nhãn ("Phụ lục II", "Mẫu số 01/TK") **mà dòng kế tiếp in hoa hoặc là "(Kèm theo ...)"** (nhãn đứng một mình trong danh sách mẫu của một Điều không tính); "Phụ lục kèm theo Luật này..." không tính. Mỗi tiêu đề phụ lục mở một block mới, breadcrumb để trống; trong phụ lục không nhận Chương, còn Điều chỉ nhận nếu đúng số tăng và có tiêu đề (khi đó quay lại thân văn bản; điều khoản "Điều 1." của hợp đồng mẫu không tính).
- T08: Fallback cửa sổ `min(500, max_tokens)` token / chồng lấn 50: cắt giữa các đoạn văn; chỉ đoạn dài hơn cửa sổ mới cắt theo từ (nhóm ≤ cửa sổ − 50, tìm nhị phân nên ít lần gọi tokenizer). Phần chồng lấn là các đoạn cuối, hoặc các từ cuối của đoạn trước đó; nếu không đủ chỗ cho đoạn kế tiếp thì bỏ chồng lấn để không vượt giới hạn. Block không phải Điều (mở đầu, phụ lục, văn bản không có Điều) ≤ 800 token giữ nguyên một chunk. `embed_text` bỏ dòng `{dieu}. {dieu_title}` nếu text chunk bắt đầu bằng dòng tiêu đề gốc của Điều (kể cả dạng `ĐIỀU 1:`).
- T09: `Embedder` là lớp cơ sở (ABC) có `model_name`, `dim` (1024), `embed_documents`/`embed_query` (sync) và `aembed_documents`/`aembed_query` (`asyncio.to_thread`). Không encode song song: một `threading.Lock` giữ quanh mỗi lần gọi model (áp dụng cho cả caller sync, nhiều event loop, và coroutine bị huỷ mà thread vẫn đang chạy). `embed_documents` encode và giữ lock theo từng lát `batch_size` văn bản, nên một văn bản lớn đang index không chặn câu hỏi chat cả chục phút. `BgeM3Embedder(model_name, device, batch_size, threads)` thêm tham số `threads` (docs không có) để gọi `torch.set_num_threads` trước khi load. `get_embedder()` là singleton có lock (gọi đồng thời chỉ load model một lần); lifespan load tokenizer và embedder song song bằng `asyncio.gather` + `asyncio.to_thread`, embedder lỗi thì **dừng khởi động** (vẫn dispose engine) (khác tokenizer: không có embedder thì không chat / index được).
- T09: Sparse: nếu mọi weight < 0.01 (câu hỏi rất ngắn) thì giữ token có weight lớn nhất, không trả sparse rỗng. `embedder.py` import `app.core.config` ngay trước khi import torch để biến môi trường MPS fallback / `HF_HOME` luôn được đặt.
- T09: FlagEmbedding 1.4.2: `BGEM3FlagModel(..., devices="<device>")` truyền **một** device (không tạo multi-process pool), gọi `encode(..., max_length=...)`. `passage_max_length` mặc định của thư viện là 512 → đặt `PASSAGE_MAX_TOKENS=1024` (chunk ≤ 800 token + tiêu đề/breadcrumb), query 512. Không dùng instruction cho query (bge-m3 không cần). Device chỉ định rõ mà máy không có (vd. `cuda` trên Mac) → `RuntimeError` ngay khi khởi tạo, không âm thầm rơi về cpu.
- T09: `FakeEmbedder` là hashed bag-of-words (mỗi từ → 8 chiều ±1, blake2b; sparse id = hash % 250002, weight = tần suất / tần suất lớn nhất), không chỉ hash cả text, để câu có chung từ có cosine cao hơn → test retriever (T16) dùng được. Tên model `fake`.
- T09: `scripts/bench_embed.py` nằm ở `backend/scripts/` (cạnh `gen_keys.py`), tự thêm `backend/` vào `sys.path` để chạy được bằng `uv run python scripts/bench_embed.py`. Đoạn mẫu ghép các chunk liên tiếp của fixture chunker tới ~`--tokens` token (mặc định 400; chunk của fixture chỉ ~80 token, quá ngắn so với thực tế); có 1 batch khởi động không tính giờ.
- T10: Các hàm indexer nhận thêm `session: AsyncSession` và `qdrant: AsyncQdrantClient` ở đầu (docs không có), cùng tham số tuỳ chọn `embedder=` (mặc định `get_embedder()`); hàm tự `commit` session (session phải có `expire_on_commit=False`). `ensure_collection(qdrant, embedder)` gọi trong lifespan sau khi tạo client: collection khác model / khác kích thước vector → **dừng khởi động**; Qdrant chưa chạy → chỉ log cảnh báo (auth, settings vẫn chạy, `/api/health` báo 503), indexer tự gọi `ensure_collection` ở lần index đầu. Hai process cùng tạo collection (uvicorn + CLI) không lỗi. Collection cũ không có metadata model: rỗng → nhận làm của model hiện tại; có point → từ chối. Input là `LegalDocumentIn` (file mới `app/ingest/schemas.py`, `extra="forbid"`): gồm `source`, `source_id` + `DocumentMetadata` (các cột metadata được phép sửa; không có `owner_id`, `so_hieu_norm`, trạng thái ingest). `normalize_so_hieu` cũng ở đây.
- T10: Tên model lưu trong **metadata của collection** (`{"embedding_model": ...}`, Qdrant ≥ 1.16), không thêm bảng `app_meta`. Khi khởi động kiểm tra cả model lẫn kích thước vector `dense`; payload index chỉ tạo cái còn thiếu. `qdrant-client>=1.16` (tham số `metadata`). Ngày trong payload dạng `YYYY-MM-DD` (index datetime của Qdrant lọc `range` được, có test).
- T10: Payload **bỏ hẳn các trường NULL** thay vì ghi `null` (lệch ví dụ `"owner_id": null` trong 02-data-model): `set_payload` của Qdrant xoá key nếu giá trị là null, nên giữ một cách biểu diễn cho cả point mới lẫn point được cập nhật. `update_document_metadata` gọi `delete_payload` cho trường bị đặt về NULL. Retriever (T16) lọc "không có giá trị" bằng `IsEmpty`; filter phân quyền dùng `source = vbpl` nên không cần `owner_id` null.
- T10: `index_document`: chỉ bỏ qua (chỉ cập nhật metadata) khi `ingest_status=done` **và** cùng hash **và** cùng `embedding_model`, nên văn bản lỗi lần trước hoặc index bằng model khác sẽ được index lại. Văn bản upload (`source_id=None`) luôn tạo mới. Thêm `reindex_document` cũng bỏ qua theo cùng điều kiện. Đổi `title` / `so_hieu` (nằm trong `embed_text`) cũng index lại dù cùng hash. Thứ tự (lệch 03-ingestion: không xoá point cũ trước): commit `processing` → chunk (thread) → embed (không giữ lock) → **khoá dòng** `legal_documents` (`FOR UPDATE`, trong SAVEPOINT), đọc lại metadata → xoá point mồ côi của lần chạy chết trước → upsert point mới (batch 64, payload từ dòng vừa đọc) → thay `legal_chunks` + `content_hash` + `done` → commit → xoá point cũ theo id. Văn bản bị xoá trong lúc embed → `LookupError`, không ghi gì. Lỗi (kể cả bị huỷ; dọn dẹp chạy trong `anyio` shield, tối đa 30s) → xoá point mới, `failed` + `ingest_error` (tiếng Việt: `IndexingError.user_message`, lỗi lạ dùng câu chung), rồi raise lại; **bản cũ (chunk, point, `content_hash`, `embedding_model`) được giữ để vẫn tra cứu được** → retriever (T16) không lọc theo `ingest_status`. `content_hash` chỉ ghi khi thành công. Text rỗng → `IndexingError("empty_document")`.
- T10: `update_document_metadata` chỉ nhận trường của `DocumentMetadata` (khác → `ValueError`); đổi `title` / `so_hieu` → `ValueError` (phải index lại bằng `index_document`). **Outbox**: khoá dòng, sửa metadata và đặt cột mới `payload_dirty=true` (migration 0002, partial index) trong cùng transaction, commit Postgres **trước**, rồi `sync_document_payload` ghi **toàn bộ** payload từ dòng đang khoá sang Qdrant trong một request `batch_update_points` (set + delete key NULL; không dùng `overwrite_payload` vì sẽ xoá `dieu`/`ordinal`/`document_id` của từng point) và xoá cờ. Qdrant lỗi → không raise, cờ giữ nguyên; `payload_sync_loop` trong lifespan (mỗi 60s, chạy trên mọi máy, không phụ thuộc `SCHEDULER_ENABLED`) gọi `sync_pending_payloads` (cũ nhất trước, dừng ở lỗi đầu tiên). Swap khi index cũng xoá cờ. Dùng một cờ bool thay cho cặp `payload_version`/`payload_synced_version`: sync giữ khoá dòng nên không thể ghi đè trạng thái mới bằng trạng thái cũ. `/api/health` thêm `payload_pending` (số văn bản chờ đồng bộ, `null` nếu Postgres lỗi; không làm `status` thành degraded). Hệ quả: khi Qdrant chết, payload có thể cũ → retriever (T16) chỉ dùng payload để lọc, còn link/ngày/tình trạng trong câu trả lời lấy từ Postgres (đúng quy tắc 2). `delete_document` khoá dòng, xoá point rồi xoá dòng (chunk cascade), idempotent; xoá file upload để T11. Các hàm làm việc trong SAVEPOINT nên lỗi chỉ expire văn bản liên quan; chỉ khi COMMIT lỗi mới rollback cả session. Embed bị huỷ: không cần sửa, `aembed_documents` (T09) đã chạy mỗi lát 16 chunk trong một thread riêng nên chỉ chạy nốt lát hiện tại.
- T11: Quyền xem giống retriever cho **mọi** user, kể cả admin: `source = vbpl` HOẶC `owner_id = current_user` (list, detail). Admin không thấy văn bản upload của người khác trong danh sách / chi tiết (quy tắc 5), nhưng `DELETE` theo id được mọi văn bản. Văn bản upload của người khác → `404 not_found` (không lộ sự tồn tại); văn bản vbpl mà user thường xoá → `403 forbidden`. Qdrant lỗi khi xoá → `503 service_unavailable`, không xoá gì. File gốc bị xoá sau khi DB commit (lỗi xoá file chỉ log).
- T11: Upload: form field rỗng (`""`) coi như không nhập (`status` rỗng → `khong_xac_dinh`); khoảng trắng trong text được gộp. Lỗi form trả `422 validation_error` như các endpoint khác. Kiểm tra file theo thứ tự: phần mở rộng (`400 unsupported_format`) → kích thước (`413 file_too_large`, đọc tối đa 20 MB + 1 byte) → rỗng (`400 empty_file`) → magic bytes (`400 invalid_file`): PDF có `%PDF-` trong 1024 byte đầu; DOCX là zip có `word/document.xml`; TXT không bắt đầu bằng chữ ký nhị phân (PDF/zip/OLE/PNG/JPEG) và không có byte NUL trừ khi là UTF-16 (`txt.looks_like_text`). Parse chạy trong background task (sau `202`); `ParseError` → `failed` + `ingest_error` = câu tiếng Việt của parser. Preview trả thêm `warnings` (vd. PDF scan một phần), lỗi parse → `400` với mã của `ParseError`.
- T11: File gốc ghi vào `.part` rồi rename, **trước** khi commit dòng `pending` (commit lỗi → xoá file). Background task mở session riêng qua dependency `get_session_factory` (`app/db/session.py`) và lấy Qdrant qua `get_qdrant` (`app/core/deps.py`, đọc `app.state.qdrant`); test override cả hai. Khi khởi động, lifespan chạy `resume_interrupted_uploads`: văn bản upload còn `pending` / `processing` (process trước bị dừng giữa chừng, background task không sống qua restart) được ingest lại tuần tự; mất file gốc → `failed`. Giả định chỉ có **một** process backend (đúng với `BackgroundTasks` trong 03-ingestion); chạy nhiều worker thì mỗi worker sẽ ingest lại một lần (an toàn nhờ swap của indexer, chỉ tốn thời gian).
- T11: `GET /documents`: `q` tìm `unaccent(title|so_hieu) ILIKE unaccent('%q%')` (escape `%`, `_`, `\`; tối đa 200 ký tự); `page_size` mặc định 20; sắp xếp `issued_date DESC NULLS LAST, created_at DESC`. Query param `status` (alias) để khớp docs. `DocumentDetail` = `DocumentSummary` + `issuing_body, expiry_date, status_note, replaced_by, amended_by, is_consolidated, file_url, ingest_error, updated_at, chunk_count, dieu_list: [{dieu, dieu_title}], can_delete` (Điều bị tách nhiều chunk chỉ liệt kê một lần, lấy tiêu đề đầu tiên khác NULL; chunk phụ lục / mở đầu không có trong `dieu_list`). `STATUS_LABELS` đặt ở `app/ingest/schemas.py` để T18 (citations) dùng lại.

## Nhật ký
(mỗi task một dòng: `T0x — ngày — tóm tắt — lệch so với docs (nếu có)`)

- T01 — 2026-11-02 — Scaffold `backend/` (uv, Python 3.12, cây thư mục `app/` với file docstring, `/api/health`, lifespan, ruff + pytest-asyncio, 1 test health) và `frontend/` (Vite 8 + React 19 + TS + Tailwind 4 + React Router + TanStack Query, proxy `/api` → 8000). Thêm `.python-version`, `.gitignore`, `.gitattributes`, `.editorconfig`, `frontend/.nvmrc`. Đã kiểm tra trên Linux: `uv sync` OK, torch `2.15.0+cpu`, `cuda=False`, không có gói `nvidia-*`; `uvicorn` + `/api/health` OK (cả qua proxy Vite); `npm run build` OK; `pytest` 1 passed; `ruff` sạch. — Lệch: thư mục chưa phải git repo nên `uv.lock` chưa được commit; **chưa chạy `uv sync` trên Mac** (lock đã có wheel `macosx_14_0_arm64` từ PyPI, owner cần kiểm tra). `alembic/` và `config/` chỉ có `.gitkeep` (T03, T06 sẽ điền).
- T02 — 2026-11-02 — `docker-compose.yml` (postgres:16 + qdrant v1.20.0, named volume `pgdata`/`qdrantdata`, healthcheck postgres), init SQL tạo `citext`/`unaccent` + DB `law_test`; `.env.example` có khối Mac/Linux; `app/core/config.py` (pydantic-settings, đủ biến, export `PYTORCH_ENABLE_MPS_FALLBACK`/`HF_HOME` vào `os.environ`, `main.py` import config đầu tiên); `scripts/gen_keys.py`; `/api/health` kiểm tra Postgres + Qdrant. Đã kiểm tra trên Linux: `docker compose up -d` lên cả hai service, `\dx` thấy 2 extension trong `law_test`; uvicorn đọc `.env` (Linux block) → health 200; dừng qdrant → 503 `qdrant=error`; bật lại → 200. `pytest` 15 passed; `ruff` sạch. — Lệch: thêm thư mục `docker/postgres/init/` (bind mount file init, không phải dữ liệu). Chưa kiểm tra trên Mac.
- T03 — 2026-11-02 — `app/db/models.py` (8 bảng theo 02-data-model, CHECK, index, partial unique `(source, source_id)`, citext email), `app/db/session.py` (`SessionLocal`, `get_session`), Alembic async + migration `0001_initial_schema`. Đã kiểm tra trên Linux: `alembic upgrade head` → `alembic check` không drift → `downgrade base` (chỉ còn `alembic_version`, extension bị gỡ) → `upgrade head` trên DB `law`. Test `tests/test_db.py` (DB `law_test`): roundtrip migration, models khớp migration, tạo user + document + chunk, citext unique, các CHECK, partial unique, cascade xoá user/document. `pytest` 32 passed; `ruff` sạch. — Lệch: `content_hash` nullable (xem Quyết định phát sinh). Chưa kiểm tra trên Mac.
- T04 — 2026-11-02 — `app/core/security.py` (argon2 qua pwdlib, JWT HS256 access/refresh), `app/core/deps.py` (`current_user`, `require_admin`, `SessionDep`), `app/core/errors.py` (format lỗi chung), `app/core/csrf.py` (middleware `X-Requested-With`), `app/auth/` (schemas, service, router `/api/auth/{register,login,refresh,logout,me}`, rate limit login, CLI `create-admin`). Test `tests/test_auth.py`: hash/JWT/limiter thuần, register → me → logout → me 401, refresh (cả token sai loại / user không tồn tại), login sai 10 lần → lần 11 bị 429, CSRF, require_admin, CLI. Đã chạy thật trên Linux: `create-admin` → login → `/me` trả `role=admin`, POST thiếu header → 403. `pytest` 49 passed; `ruff` sạch. — Lệch: thêm file `app/core/errors.py`, `app/core/csrf.py`, `app/auth/rate_limit.py`, `app/auth/cli.py` (cli có trong docs nhưng không có trong cây thư mục). Chưa kiểm tra trên Mac.
- T05 — 2026-11-02 — `app/core/crypto.py` (`encrypt_secret`/`decrypt_secret`/`rotate`, MultiFernet), `app/llm/errors.py` (`LLMError`), `app/llm/client.py` (interface `validate_key` tạm + `stream_chat` stub), `app/settings/` (schemas, service, router: `GET /settings`, `PUT/DELETE /settings/api-keys/{provider}`). Test `tests/test_crypto.py` (roundtrip, sai key, token bị sửa, rotate) và `tests/test_settings.py` (CRUD, chỉ trả `key_last4`, key sai → 400 không lưu, key cũ giữ nguyên, phân tách theo user, xoá user → xoá key, `caplog` DEBUG không chứa key). `pytest` 79 passed; `ruff` sạch. Sau review: `pytest` 83 passed. — Lệch: `validate_key` chưa gọi provider thật (T17); thêm `app/settings/schemas.py`. Chưa kiểm tra trên Mac.
- T06 — 2026-11-02 — `backend/config/models.yaml` (placeholder `<model-id>` như docs, owner tự điền), `app/llm/registry.py` (`load_registry`/`get_registry`, `ModelRegistry.enabled_models/get_enabled/placeholders`), `GET /api/models`, `PUT /api/settings` (`set_default_model` trong `app/settings/service.py`, schema `SettingsIn`/`ModelOut`), lifespan load registry khi khởi động. Test `tests/test_registry.py` (yaml hợp lệ, file thiếu, YAML hỏng, không phải mapping, 13 trường hợp sai schema đều báo rõ trường, placeholder chỉ warning, file thật của repo load được) và thêm vào `tests/test_settings.py` (`/models` chỉ model enabled, không lộ `litellm_model`; PUT settings đặt/đổi/xoá mặc định, suy ra provider, chặn model chưa có key / key của user khác / model tắt hoặc không tồn tại, 422). Chạy thật uvicorn: khởi động OK, `/api/models` chưa đăng nhập → 401. `pytest` 120 passed; `ruff` sạch. Sau review: `pytest` 125 passed. — Lệch: `/models` yêu cầu đăng nhập. Chưa kiểm tra trên Mac.
- T12 — 2026-11-02 — Khảo sát vbpl.vn bằng `httpx` (34 request, cách nhau ≥ 1,5s): robots.txt cho phép trang văn bản, cấm `/api/`; trang chi tiết render SSR (Next.js), metadata + toàn văn HTML có cấu trúc `prov-*` nằm trong payload RSC; liệt kê bằng sitemap (trung ương: file 1–12, `lastmod` giảm dần); ~56.000 URL trung ương, ước lượng 6.000–14.000 còn hiệu lực. Viết `docs/vbpl-findings.md` (trả lời 9 câu + bảng map tình trạng), lưu 5 trang chi tiết + 2 sitemap vào `backend/tests/fixtures/vbpl/`. Không viết code production. `pytest` 125 passed. — Lệch: list/daily dùng sitemap; không lấy được quan hệ văn bản và PDF gốc (chỉ qua API bị cấm); selectolax phải dùng backend `lexbor`. Owner đã duyệt findings (2026-11-02).
- T07 — 2026-11-02 — `app/ingest/parsers/`: `base.py` (`ParsedDocument`, `ParseError`), `normalize.py` (NFC, khoảng trắng, bỏ header/footer lặp), `pdf.py` (pymupdf, phát hiện scan / mật khẩu / file hỏng), `docx.py` (paragraph + bảng theo thứ tự), `txt.py` (UTF-8/UTF-16/cp1258), `detect.py` (số hiệu, loại, ngày ban hành, tiêu đề), dispatch `parse_bytes`/`parse_file` trong `__init__.py`. Fixture `make_pdf` / `make_docx` sinh bằng code trong `tests/conftest.py` (PDF tiếng Việt qua `insert_htmlbox`, PDF scan = trang chỉ có ảnh). Test `tests/test_parsers.py` (38): NFC hai dạng → một dạng qua normalize/TXT/DOCX/cp1258, khoảng trắng, header/footer, các encoding TXT, DOCX bảng + ô gộp, PDF text / scan / hỏng / mật khẩu, detect số hiệu / loại / ngày / tiêu đề. `pytest` 163 passed; `ruff` sạch. — Lệch: thêm `parsers/base.py`; `detected` có thêm `title` (theo 06-api). Chưa kiểm tra trên Mac.
- T08 — 2026-11-02 — `app/ingest/chunker.py`: `chunk_document` (Phần/Chương/Mục/Điều, phần mở đầu, tách Điều dài theo khoản, phụ lục, fallback cửa sổ 500/50), `embed_text` theo breadcrumb, `bge_token_counter` (tokenizer bge-m3, không torch). Fixture `tests/fixtures/chunker/luat_mau.txt`; `tests/test_chunker.py` 16 test + 1 test `slow` dùng tokenizer thật (đã chạy pass trên Linux). Sau review: sửa mất nội dung khi cả Điều nằm trên một dòng, phụ lục bắt nhầm, `Điều N.` xuống dòng giữa câu, tiêu đề Chương nhiều dòng, lặp tiêu đề trong `embed_text`, cửa sổ theo `max_tokens` và cắt giữa đoạn, đếm token ít lần hơn, tokenizer nạp lúc khởi động; 28 test + 1 `slow`, `pytest` 204 passed. Lệch docs: breadcrumb có thêm Phần, nhận `Điều N:`, tiêu đề không phân biệt hoa thường (xem Quyết định phát sinh).
- T09 — 2026-11-02 — `app/ingest/embedder.py`: `Embedder` (ABC + wrapper async, `threading.Lock` quanh model, encode theo lát `batch_size`), `BgeM3Embedder` (FlagEmbedding, chọn device cuda → mps → cpu, fp16 chỉ trên cuda, `EMBEDDING_THREADS`, sparse key → int và bỏ weight < 0.01, log os/device/threads/batch/thời gian load), `FakeEmbedder` (không import torch), `create_embedder`/`get_embedder` theo `EMBEDDING_BACKEND`; lifespan load embedder. `scripts/bench_embed.py`. Test `tests/test_embedder.py`: 8 test với fake (tất định, normalize, có chung từ → cosine cao hơn, chuyển sparse, factory, subprocess kiểm tra import `app.main` với `fake` không kéo torch/FlagEmbedding/transformers, wrapper async không encode song song và chạy ngoài thread event loop) + 1 test `slow` model thật (1024 chiều, normalize, sparse int ≥ 0.01, câu đồng nghĩa cosine cao hơn câu không liên quan, giữ thứ tự, text dài bị cắt không lỗi). `pytest` 212 passed; `pytest -m slow tests/test_embedder.py` pass trên Linux (cpu). Bench Linux (x86_64, torch 2.15.0+cpu, 8 thread, batch 8, 100 đoạn ~578 token): load 6,2s, encode 205,1s = **0,49 đoạn/giây**. — Lệch: thêm tham số `threads`, async wrapper, `PASSAGE_MAX_TOKENS` (xem Quyết định phát sinh). **Chưa chạy trên Mac**: owner cần chạy `uv run pytest -m slow tests/test_embedder.py` và `uv run python scripts/bench_embed.py` trên Mac rồi ghi kết quả vào đây. Sau review: lock chuyển từ asyncio semaphore sang `threading.Lock` (đúng cả khi huỷ request), encode theo lát, `get_embedder` an toàn đa luồng, sparse không rỗng, lifespan load song song và dispose engine khi lỗi; thêm 5 test, `pytest` 217 passed, `-m slow` pass trên Linux.
- T10 — 2026-11-03 — `app/ingest/indexer.py` (`ensure_collection`, `index_document`, `reindex_document`, `update_document_metadata`, `delete_document`, `IndexingError`, `CollectionMismatchError`), `app/ingest/schemas.py` (`LegalDocumentIn`, `DocumentMetadata`, `normalize_so_hieu`); lifespan gọi `ensure_collection`. Test `tests/test_indexer.py` (17; Postgres `law_test` + Qdrant với collection tạm `test_indexer_*`, xoá sau mỗi test): đúng số point = số dòng `legal_chunks` = số chunk, payload đúng; index lại cùng hash không embed lại, giữ nguyên id; cùng hash khác metadata chỉ đổi payload; đổi text → chunk/point mới; delete sạch cả hai nơi (văn bản khác không bị ảnh hưởng); `update_document_metadata` đổi payload, Qdrant lỗi thì Postgres rollback; embed lỗi → `failed`, không còn point/chunk, index lại được; text rỗng; upload + `reindex_document` văn bản `pending`; `ensure_collection` idempotent, chặn khác model / khác kích thước; lọc range theo ngày. Chạy thật uvicorn (`fake`): tạo collection có metadata + 9 payload index, health 200; đổi model → `CollectionMismatchError`. `pytest` 234 passed (2 slow skipped); `ruff` sạch. Sau review: giữ bản cũ khi index lại lỗi, khoá dòng chống mất cập nhật metadata / point mồ côi khi xoá giữa chừng, đổi tiêu đề/số hiệu thì embed lại, khôi phục payload khi commit lỗi, dọn dẹp khi bị huỷ, khởi động không phụ thuộc Qdrant, chống race tạo collection, `qdrant-client>=1.16`; đồng bộ payload qua outbox (`payload_dirty`, migration 0002, worker 60s, `payload_pending` trong health); 27 test indexer, `pytest` 245 passed. — Lệch: hàm nhận `session`/`qdrant`; payload bỏ trường NULL; tên model trong metadata collection (xem Quyết định phát sinh). Chưa kiểm tra trên Mac.
- T11 — 2026-11-04 — `app/documents/` (`schemas.py`, `service.py`, `router.py`): `POST /api/documents/upload/preview`, `POST /api/documents/upload` (`202`, lưu file `UPLOAD_DIR/{owner_id}/{document_id}{ext}`, ingest bằng `BackgroundTasks` qua `reindex_document`), `GET /api/documents` (scope/q/doc_type/status/phân trang), `GET /api/documents/{id}`, `DELETE /api/documents/{id}` (Qdrant + Postgres + file gốc); `resume_interrupted_uploads` trong lifespan; `STATUS_LABELS`; `txt.looks_like_text`; dependency `get_session_factory`, `get_qdrant`. Test `tests/test_documents.py` (14): upload TXT → ingest xong → hiện trong `scope=mine` (đúng số chunk = số point, payload có `owner_id`), detail có `dieu_list`; user B không thấy văn bản của A (list mọi scope, detail 404), B xoá văn bản của A → 404, xoá văn bản vbpl → 403; owner xoá → sạch DB, Qdrant, file; admin xoá được mọi văn bản; file sai định dạng / magic bytes / quá lớn / rỗng / form sai; PDF scan → `failed` kèm thông báo; DOCX; preview không lưu gì; lọc unaccent, `%` literal, phân trang; resume upload bị gián đoạn. Đã chạy thật uvicorn trên Linux (DB `law_test`, embedder fake): register → upload → `done` → list/detail → delete 204, file bị xoá. `pytest` 259 passed, 2 skipped; `ruff` sạch. — Lệch: preview trả thêm `warnings`; detail có thêm các trường ở mục Quyết định phát sinh; thêm resume khi khởi động (docs không có). Chưa kiểm tra trên Mac.
