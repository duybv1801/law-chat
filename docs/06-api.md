# 06 — API

Prefix `/api`. JSON, ngày dạng `YYYY-MM-DD`, thời gian ISO 8601 UTC.

Lỗi thống nhất một format:
```json
{ "error": { "code": "invalid_key", "message": "Thông báo tiếng Việt cho người dùng" } }
```

## Auth

- Access token (JWT, 30 phút) nằm trong cookie `access_token`. Refresh token (14 ngày) nằm trong cookie `refresh_token`, `path=/api/auth`.
- Cả hai cookie đều `httpOnly`, `SameSite=Lax`, `Secure` theo `COOKIE_SECURE`.
- Chống CSRF: mọi request thay đổi dữ liệu phải có header `X-Requested-With: fetch`. Backend kiểm tra header này. Kết hợp `SameSite=Lax` là đủ cho giai đoạn này.
- Dependency `current_user` và `require_admin` nằm trong `app/core/deps.py`.

| Method | Path | Body / Query | Trả về |
|---|---|---|---|
| POST | /auth/register | `{email, password}` (mật khẩu ≥ 8 ký tự) | `201` user, set cookie |
| POST | /auth/login | `{email, password}` | user, set cookie |
| POST | /auth/refresh | — | `204`, set cookie mới |
| POST | /auth/logout | — | `204`, xoá cookie |
| GET | /auth/me | — | `{id, email, role}` |

Giới hạn tần suất login: 10 lần / 15 phút / IP (đếm trong bộ nhớ là đủ, dùng `slowapi` hoặc tự viết).

## Models & Settings

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | /models | — | `[{id, provider, label}]` |
| GET | /settings | — | `{default_provider, default_model_id, api_keys: [{provider, key_last4, validated_at, last_used_at}]}` |
| PUT | /settings | `{default_model_id}` | settings (suy ra `default_provider` từ model) |
| PUT | /settings/api-keys/{provider} | `{api_key}` | `{provider, key_last4, validated_at}`. Kiểm tra key trước, key sai → `400 invalid_key` và **không lưu** |
| DELETE | /settings/api-keys/{provider} | — | `204` |

**Không có endpoint nào trả về key gốc.**

## Documents

| Method | Path | Body / Query | Trả về |
|---|---|---|---|
| POST | /documents/upload/preview | multipart `file` | `{detected: {so_hieu, doc_type, issued_date, title}}` |
| POST | /documents/upload | multipart `file` + field metadata | `202 {id, ingest_status}` |
| GET | /documents | `scope=all\|mine\|vbpl`, `q`, `doc_type`, `status`, `page`, `page_size≤50` | `{items: [DocumentSummary], total}` |
| GET | /documents/{id} | — | `DocumentDetail` (metadata + số chunk + danh sách Điều) |
| DELETE | /documents/{id} | — | `204`. Chỉ owner xoá được văn bản `user_upload` của mình. Admin xoá được mọi văn bản |

`q` tìm theo `title` / `so_hieu` (ILIKE, unaccent). Không dùng vector search ở đây. Kết quả luôn áp quyền xem như retriever.

`DocumentSummary`: `id, source, so_hieu, title, doc_type, issued_date, effective_date, status, status_label, ingest_status, source_url, created_at`.

## Chat

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | /chat/conversations | — | `{id, title, created_at}` |
| GET | /chat/conversations | `page` | danh sách, mới nhất trước |
| GET | /chat/conversations/{id}/messages | — | `[Message]` kèm `citations` |
| PATCH | /chat/conversations/{id} | `{title}` | |
| DELETE | /chat/conversations/{id} | — | `204` |
| POST | /chat/conversations/{id}/messages | `{content, model_id?}` | **SSE stream** |

Các event SSE (`text/event-stream`, mỗi event `data:` là JSON):
```
event: start      data: {"message_id": "...", "model_id": "..."}
event: token      data: {"t": "đoạn text"}
event: citations  data: {"citations": [Citation], "uncited": false}
event: error      data: {"code": "invalid_key", "message": "..."}
event: done       data: {"refused": false}
```
Dùng `sse-starlette`. Frontend dùng `fetch` + đọc stream (không dùng `EventSource`, vì cần POST và gửi cookie).

## Admin (`require_admin`)

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | /admin/stats | — | số văn bản theo nguồn / tình trạng, số chunk, dung lượng collection Qdrant |
| GET | /admin/crawl-runs | `job_type`, `page` | danh sách `crawl_runs` |
| POST | /admin/crawl/{job_type} | `{params?}` | `202 {crawl_run_id}` |
| POST | /admin/documents/{id}/reindex | — | `202` |
