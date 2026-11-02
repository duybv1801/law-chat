# 04 — Crawler vbpl.vn và scheduler

## Bối cảnh

- `vbpl.vn` là Cơ sở dữ liệu quốc gia về pháp luật do Bộ Tư pháp quản lý.
- **Phiên bản mới của trang chạy chính thức từ 23/4/2026.** Cấu trúc HTML, URL và API của bản mới **chưa được khảo sát**. Không tin theo các hướng dẫn crawl cũ trên mạng (bản cũ dùng `vbpl.vn/Pages/portal.aspx`, `ws.vbpl.vn`).
- Vì vậy crawler được làm theo 2 bước: **T12 khảo sát** → viết `docs/vbpl-findings.md` → **T13 mới code client**.

## T12 — Khảo sát (không viết code production)

Dùng `httpx` và (nếu cần) Playwright để khảo sát, rồi ghi vào `docs/vbpl-findings.md`:

1. `robots.txt`: những đường dẫn bị cấm, có `Crawl-delay` không.
2. Trang có render phía server không, hay dữ liệu đến từ API JSON (mở DevTools → tab Network)? **Nếu có API JSON công khai thì ưu tiên dùng API**, ổn định hơn parse HTML.
3. Cách liệt kê văn bản: trang danh sách / tìm kiếm, phân trang, lọc theo loại văn bản, ngày ban hành, cơ quan ban hành, phạm vi trung ương/địa phương. Có sắp xếp theo ngày cập nhật không (cần cho `daily_new`).
4. Trang chi tiết một văn bản: ID văn bản nằm ở đâu, URL có ổn định không.
5. Các trường lấy được: số hiệu, trích yếu, loại, cơ quan, người ký, ngày ban hành, ngày hiệu lực, ngày hết hiệu lực, **tình trạng hiệu lực (giá trị nguyên văn)**, văn bản thay thế / sửa đổi / được sửa đổi, văn bản hợp nhất, file đính kèm.
6. Nội dung toàn văn: là HTML có cấu trúc, hay chỉ có file PDF/DOC đính kèm?
7. Có trang hoặc API kiểu "văn bản mới cập nhật" không.
8. Ước lượng tổng số văn bản trung ương còn hiệu lực.
9. Lưu 3–5 trang mẫu (HTML/JSON) vào `backend/tests/fixtures/vbpl/` để làm test parser.

Kết thúc T12: đề xuất bảng ánh xạ "giá trị tình trạng hiệu lực nguyên văn → enum `status`" trong `02-data-model.md`. Không chắc thì map về `khong_xac_dinh` và giữ nguyên văn trong `status_note`.

**Nếu robots.txt cấm hoặc điều khoản sử dụng không cho crawl tự động:** dừng lại, ghi vào findings và báo owner. Không tìm cách lách.

## Client (`app/crawler/vbpl_client.py`)

```python
class VbplClient:
    async def list_documents(self, *, page: int, filters: ListFilters) -> ListPage
    async def list_recently_updated(self, since: date) -> AsyncIterator[DocRef]
    async def get_document(self, source_id: str) -> VbplDocument     # metadata + text (hoặc file_url)
    async def get_status(self, source_id: str) -> VbplStatus          # chỉ phần hiệu lực, nhẹ hơn get_document
```

- `httpx.AsyncClient` với timeout 30s, header `User-Agent` lấy từ config.
- **Rate limit toàn cục** `CRAWLER_RATE_LIMIT_RPS` (mặc định 1 req/s), dùng token bucket đơn giản.
- Retry với backoff mũ (`tenacity`): tối đa 4 lần cho 429/5xx/timeout. Gặp 4xx khác thì không retry.
- Cache HTTP thô theo URL vào `storage/crawl_cache/` (bật bằng `CRAWLER_CACHE=true` khi dev) để khi viết lại parser không phải tải lại.
- Nếu toàn văn chỉ có file đính kèm → tải file rồi dùng parser PDF/DOCX trong `ingest/parsers`.
- Toàn bộ code phụ thuộc cấu trúc HTML phải nằm trong `vbpl_parser.py`, có test dùng fixture.

## Jobs (`app/crawler/jobs.py`)

```python
async def run_backfill(limit: int | None, filters: ListFilters, resume: bool = True) -> CrawlRunResult
async def run_daily_new(overlap_days: int = 7) -> CrawlRunResult
async def run_status_refresh(batch_size: int = 500) -> CrawlRunResult
```

Chung cho mọi job:
- Tạo dòng `crawl_runs(status=running)`. Lấy **advisory lock** của Postgres theo `job_type`. Không lấy được lock → bỏ qua, log "job đang chạy".
- Mỗi văn bản xử lý trong try/except riêng. Văn bản lỗi → `stats.failed += 1` và log, rồi đi tiếp. Kết thúc với `partial` nếu có lỗi, `success` nếu không.
- **Upsert theo `(source='vbpl', source_id)`**. Gọi `indexer.index_document`; hash không đổi thì chỉ cập nhật metadata.

`run_backfill`:
- Thứ tự ưu tiên loại văn bản (cấu hình): Hiến pháp → Bộ luật → Luật → Nghị quyết QH → Pháp lệnh → Nghị định → Quyết định TTg → Thông tư.
- Mặc định lọc `scope=trung_uong` và còn hiệu lực.
- `resume=True`: lưu checkpoint (loại văn bản + trang) vào `crawl_runs.params` để lần sau chạy tiếp.

`run_daily_new`:
- Lấy danh sách văn bản ban hành hoặc cập nhật từ `last_success_run.started_at - overlap_days`.
- Văn bản mới → ingest. Văn bản đã có → so `content_hash`, khác thì re-index.
- Nếu văn bản mới có trường "thay thế / sửa đổi văn bản X" và X đã có trong DB → đặt `status_checked_at = NULL` cho X, để `status_refresh` kiểm tra lại X sớm.

`run_status_refresh`:
- Chọn `batch_size` văn bản nguồn vbpl, sắp xếp `status_checked_at NULLS FIRST, ASC`.
- Gọi `get_status`. Có thay đổi thì `indexer.update_document_metadata(...)` (cập nhật cả Qdrant payload), `stats.status_changed += 1`.
- Luôn cập nhật `status_checked_at = now()`.

## CLI (`app/crawler/cli.py`, dùng `typer`)

```bash
uv run python -m app.crawler.cli backfill --limit 100 --doc-type "Luật"
uv run python -m app.crawler.cli daily
uv run python -m app.crawler.cli status-refresh --batch-size 50
uv run python -m app.crawler.cli fetch-one <source_id> --dry-run   # in ra metadata + các chunk, không ghi
```

## Scheduler (`app/scheduler.py`)

- `AsyncIOScheduler(timezone=TZ_APP)`, khởi động trong lifespan của FastAPI khi `SCHEDULER_ENABLED=true`.
- `daily_new`: cron 02:00. `status_refresh`: cron 03:00. Đặt `coalesce=True`, `max_instances=1`, `misfire_grace_time=3600`.
- Dev trên 2 máy: chỉ máy Mac bật `SCHEDULER_ENABLED=true`. Máy Linux đặt `false` và nhận dữ liệu qua `scripts/data_sync.py` (xem 08-dev-machines.md).
- Chạy uvicorn với `--reload` hoặc nhiều worker sẽ khởi động scheduler nhiều lần. Advisory lock trong job đã chặn chạy trùng, nhưng khi dev nên tắt bằng `SCHEDULER_ENABLED=false`.
- Admin chạy job thủ công qua `POST /api/admin/crawl/{job_type}`. Job chạy nền và trả `crawl_run_id` ngay.

## Khi lên VPS sau này

Có thể tách crawler + scheduler thành một process riêng (`python -m app.worker`) để không chung RAM với API. Thiết kế module như trên đã cho phép tách mà không phải sửa logic.
