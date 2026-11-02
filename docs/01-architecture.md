# 01 — Kiến trúc

## Sơ đồ tổng thể

```
                ┌──────────────────────── Frontend (React) ────────────────────────┐
                │  Đăng nhập │ Tab Văn bản │ Tab Hỏi đáp │ Cài đặt │ Admin          │
                └───────────────────────────────┬──────────────────────────────────┘
                                                │ /api (cookie JWT)
┌───────────────────────────────── Backend (FastAPI) ─────────────────────────────────┐
│ auth   settings(api keys)   documents(upload)   chat(SSE)   admin                     │
│                                                                                       │
│ ingest: parse → chunk theo Điều → embed (bge-m3, local) → upsert Qdrant + Postgres    │
│ rag:    embed câu hỏi → hybrid search (filter quyền + hiệu lực) → ngưỡng →            │
│         prompt → LiteLLM (key của user) → parse marker [n] → citation từ metadata     │
│ crawler + APScheduler: backfill / daily_new / status_refresh → ingest                 │
└───────────────┬───────────────────────────────┬───────────────────────────┬───────────┘
                │                               │                           │
          PostgreSQL                         Qdrant                 LLM providers
   (users, keys mã hoá, documents,   (vector dense+sparse,        (OpenAI / Anthropic /
    chunks, conversations, crawl)     payload để lọc)               Gemini qua LiteLLM)
```

## Vì sao dùng RAG pipeline cố định, không dùng agent

- User tự chọn provider và model, nên chất lượng gọi tool không đồng đều giữa các model. Một pipeline cố định cho kết quả ổn định với mọi model.
- Yêu cầu "chỉ dùng kiến thức trong DB" dễ kiểm soát hơn khi chính backend quyết định context đưa vào LLM.
- Rẻ hơn, nhanh hơn, dễ debug và dễ đánh giá chất lượng.
- **Phase sau (không làm bây giờ):** có thể thêm tool calling nhẹ (`get_article`, `get_amendments`), giới hạn 2–3 vòng, cho câu hỏi đa bước về sửa đổi/thay thế văn bản.

## Luồng 1 — Ingest (dùng chung cho crawler và upload)

1. Có văn bản gồm text thô + metadata (từ vbpl hoặc do user nhập khi upload).
2. `chunker` tách văn bản theo cấu trúc Phần/Chương/Mục/**Điều**. Điều dài thì tách tiếp theo Khoản.
3. Mỗi chunk tạo ra một `embed_text` = breadcrumb + nội dung. Breadcrumb dạng: `"{title} ({so_hieu}) > Chương II > Điều 5. Tiêu đề"`.
4. `embedder` chạy bge-m3 theo batch, trả về dense vector (1024 chiều) + sparse vector (lexical weights).
5. `indexer`: ghi `legal_documents` + `legal_chunks` vào Postgres trong một transaction, rồi upsert point vào Qdrant (point id = chunk id).
6. Khi re-index một văn bản: xoá toàn bộ chunk và point cũ theo `document_id` rồi ghi lại từ đầu. Không làm diff từng chunk.

## Luồng 2 — Chat

1. User gửi câu hỏi kèm `conversation_id`.
2. Backend lấy provider/model (trong request, hoặc mặc định của user) và giải mã API key của provider đó.
3. Nếu câu hỏi có nhắc số hiệu văn bản (regex), ưu tiên lọc theo `so_hieu`.
4. Hybrid search trên Qdrant, kèm filter phân quyền và filter hiệu lực. Lấy top-k.
5. Điểm cao nhất dưới ngưỡng → trả câu từ chối cố định, **không gọi LLM**.
6. Dựng prompt: system prompt + các chunk được đánh số `[1]..[k]` + vài lượt chat gần nhất + câu hỏi.
7. Gọi LiteLLM ở chế độ stream, đẩy token về frontend qua SSE.
8. Stream xong: parse các marker `[n]` → map sang chunk → tạo danh sách citation từ metadata trong DB → gửi event `citations` → lưu message.

## Luồng 3 — Crawl theo lịch

- `daily_new` (02:00 hằng ngày, giờ Asia/Ho_Chi_Minh): lấy văn bản mới ban hành hoặc mới cập nhật trong 7 ngày gần nhất (chồng lấn để không sót) → ingest văn bản mới hoặc có `content_hash` thay đổi.
- `status_refresh` (03:00 hằng ngày): kiểm tra lại tình trạng hiệu lực của N văn bản có `status_checked_at` cũ nhất (N mặc định 500) → cập nhật Postgres + Qdrant payload.
- `backfill`: chạy tay qua CLI hoặc trang admin, crawl dần theo thứ tự ưu tiên.
- Mỗi lần chạy ghi một dòng vào `crawl_runs`. Không cho hai job cùng loại chạy song song (dùng lock trong Postgres).

## Phạm vi dữ liệu ban đầu

Ưu tiên văn bản **trung ương, còn hiệu lực**: Hiến pháp, Bộ luật, Luật, Nghị quyết của Quốc hội, Pháp lệnh, Nghị định, Quyết định của Thủ tướng, Thông tư. Văn bản địa phương và văn bản hết hiệu lực để sau (cấu hình được).

## Cấu hình (`.env`)

```
DATABASE_URL=postgresql+asyncpg://law:law@localhost:5432/law
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=legal_chunks
APP_ENCRYPTION_KEYS=<fernet key 1>,<fernet key cũ nếu có>   # MultiFernet, key đầu tiên dùng để mã hoá
JWT_SECRET=<random>
JWT_ACCESS_TTL_MIN=30
JWT_REFRESH_TTL_DAYS=14
COOKIE_SECURE=false
EMBEDDING_BACKEND=bge        # bge | fake (fake chỉ cho test / làm UI, xem 08-dev-machines.md)
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=auto        # auto | cuda | mps | cpu
EMBEDDING_BATCH_SIZE=16      # Linux CPU: 4-8
EMBEDDING_THREADS=           # Linux CPU: số core vật lý; để trống = torch tự chọn
PYTORCH_ENABLE_MPS_FALLBACK=1   # chỉ có tác dụng trên Mac
HF_HOME=                     # tuỳ chọn: chỗ lưu cache model
UPLOAD_DIR=storage/uploads
RAG_TOP_K=8
RAG_MIN_SCORE=0.0          # tinh chỉnh sau khi có bộ eval (T25)
CRAWLER_RATE_LIMIT_RPS=1
CRAWLER_USER_AGENT=LawChatbot/0.1 (+contact email)
SCHEDULER_ENABLED=true
TZ_APP=Asia/Ho_Chi_Minh
```
