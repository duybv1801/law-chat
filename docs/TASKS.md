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
| T08 | Chunker theo Điều | T07 | ⬜ |
| T09 | Embedder bge-m3 (dense + sparse) | T01 | ⬜ |
| T10 | Qdrant collection + indexer | T03, T08, T09 | ⬜ |
| T10b | Script đồng bộ dữ liệu Mac → Linux (`data_sync.py`) | T10 | ⬜ |
| T11 | API upload / list / detail / delete văn bản | T04, T10 | ⬜ |
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
