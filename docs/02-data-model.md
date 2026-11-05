# 02 — Data model

Postgres là nguồn sự thật. Qdrant chứa vector + bản sao metadata để lọc.

## Postgres

Mọi `id` là UUID (v4, sinh ở app). Mọi bảng có `created_at`, `updated_at` (timestamptz, UTC).

### `users`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| id | uuid PK | |
| email | citext UNIQUE | |
| password_hash | text | argon2 |
| role | text | `user` \| `admin` (CHECK) |
| is_active | bool | default true |

Admin đầu tiên tạo bằng CLI: `uv run python -m app.auth.cli create-admin <email>`.

### `user_settings`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| user_id | uuid PK FK users | |
| default_provider | text NULL | `openai` \| `anthropic` \| `gemini` |
| default_model_id | text NULL | `id` trong `models.yaml` |

### `user_api_keys`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users ON DELETE CASCADE | |
| provider | text | `openai` \| `anthropic` \| `gemini` |
| encrypted_key | bytea | token MultiFernet |
| key_last4 | text | 4 ký tự cuối, để hiển thị |
| validated_at | timestamptz NULL | lần kiểm tra key thành công gần nhất |
| last_used_at | timestamptz NULL | |

UNIQUE (`user_id`, `provider`). Lưu lại key cho cùng provider thì ghi đè dòng cũ.

### `legal_documents`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| id | uuid PK | |
| source | text | `vbpl` \| `user_upload` |
| source_id | text NULL | ID văn bản bên vbpl.vn |
| owner_id | uuid NULL FK users | bắt buộc khi `source = user_upload` |
| so_hieu | text NULL | ví dụ `45/2019/QH14`. Lưu thêm `so_hieu_norm` (bỏ khoảng trắng, upper) để so khớp |
| so_hieu_norm | text NULL | index |
| title | text | trích yếu / tên văn bản |
| doc_type | text NULL | Luật, Nghị định, Thông tư, ... |
| issuing_body | text NULL | cơ quan ban hành |
| issued_date | date NULL | ngày ban hành |
| effective_date | date NULL | ngày có hiệu lực |
| expiry_date | date NULL | ngày hết hiệu lực |
| status | text | `con_hieu_luc` \| `het_hieu_luc_mot_phan` \| `het_hieu_luc_toan_bo` \| `chua_co_hieu_luc` \| `khong_xac_dinh` |
| status_note | text NULL | ghi chú tình trạng nguyên văn từ nguồn |
| replaced_by | text[] | số hiệu các văn bản thay thế |
| amended_by | text[] | số hiệu các văn bản sửa đổi |
| is_consolidated | bool | văn bản hợp nhất |
| scope | text | `trung_uong` \| `dia_phuong` |
| source_url | text NULL | link trang văn bản gốc |
| file_url | text NULL | link file đính kèm nếu có |
| content_hash | text | sha256 của text đã chuẩn hoá |
| ingest_status | text | `pending` \| `processing` \| `done` \| `failed` |
| ingest_error | text NULL | |
| embedding_model | text NULL | model dùng khi index |
| payload_dirty | bool, default false | metadata đã commit nhưng chưa chép sang payload Qdrant (outbox) |
| crawled_at | timestamptz NULL | |
| status_checked_at | timestamptz NULL | |

UNIQUE (`source`, `source_id`) WHERE `source_id IS NOT NULL`. Index trên `so_hieu_norm`, `status`, `owner_id`, `status_checked_at`.

### `legal_chunks`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| id | uuid PK | **trùng với point id trong Qdrant** |
| document_id | uuid FK legal_documents ON DELETE CASCADE | |
| ordinal | int | thứ tự trong văn bản |
| phan, chuong, muc | text NULL | ví dụ `Chương II. QUYỀN VÀ NGHĨA VỤ` |
| dieu | text NULL | ví dụ `Điều 5` |
| dieu_title | text NULL | tiêu đề Điều |
| khoan | text NULL | khi Điều bị tách theo khoản, ví dụ `Khoản 1-3` |
| text | text | nội dung gốc của chunk |
| embed_text | text | breadcrumb + text (cái thực sự được embed) |
| token_count | int | |

### `conversations`
`id`, `user_id` FK, `title` (lấy 60 ký tự đầu của câu hỏi đầu tiên).

### `messages`
| Cột | Kiểu | Ghi chú |
|---|---|---|
| id | uuid PK | |
| conversation_id | uuid FK ON DELETE CASCADE | |
| role | text | `user` \| `assistant` |
| content | text | |
| provider, model_id | text NULL | với assistant |
| citations | jsonb | mảng citation (xem 05-rag-chat.md) |
| retrieved_chunk_ids | uuid[] | để debug và đánh giá |
| refused | bool | true nếu từ chối vì không đủ dữ liệu |
| usage | jsonb NULL | token in/out nếu provider trả về |
| error | text NULL | câu trả lời chưa xong: mã lỗi LLM, `retrieval_failed` hoặc `cancelled`; `content` giữ phần đã sinh |

### `crawl_runs`
`id`, `job_type` (`backfill` \| `daily_new` \| `status_refresh`), `status` (`running` \| `success` \| `failed` \| `partial`), `started_at`, `finished_at`, `stats` jsonb (`fetched`, `created`, `updated`, `unchanged`, `status_changed`, `failed`), `error` text, `params` jsonb.

## Qdrant

Collection `legal_chunks`:

```python
vectors_config = {"dense": VectorParams(size=1024, distance=Distance.COSINE, on_disk=True)}
sparse_vectors_config = {"sparse": SparseVectorParams(index=SparseIndexParams(on_disk=True))}
quantization_config = ScalarQuantization(scalar=ScalarQuantizationConfig(type=ScalarType.INT8, always_ram=True))
```

Point:
- `id` = `legal_chunks.id`
- vector `dense` = dense vector của bge-m3 (đã normalize)
- vector `sparse` = lexical weights của bge-m3 → `SparseVector(indices, values)`. Cần map token id → int, dùng luôn token id của tokenizer.

Payload (bản sao từ Postgres):
```json
{
  "document_id": "uuid",
  "source": "vbpl | user_upload",
  "owner_id": "uuid | null",
  "so_hieu_norm": "45/2019/QH14",
  "doc_type": "Luật",
  "status": "con_hieu_luc",
  "scope": "trung_uong",
  "issued_date": "2019-11-20",
  "effective_date": "2021-01-01",
  "dieu": "Điều 5",
  "ordinal": 12
}
```
Text, title, link không cần nằm trong payload. Sau khi search, lấy chunk từ Postgres theo `id` (một query `WHERE id = ANY(...)` kèm join document).

Payload index: `document_id`, `source`, `owner_id`, `so_hieu_norm`, `doc_type`, `status`, `scope` (keyword); `effective_date`, `issued_date` (datetime).

Lưu tên model embedding vào metadata của collection (hoặc một bảng `app_meta`). Khi khởi động backend, nếu `EMBEDDING_MODEL` khác với giá trị đã lưu thì **báo lỗi và dừng**, không tự re-index.

## Đồng bộ Postgres → Qdrant

- Sửa metadata văn bản (tình trạng, ngày) → commit Postgres kèm `payload_dirty=true`, rồi `set_payload` (toàn bộ payload) với filter `document_id` và xoá cờ. Qdrant lỗi thì worker trong backend thử lại mỗi 60s.
- Xoá văn bản → `delete` theo filter `document_id`, sau đó xoá trong Postgres.
- Viết hết vào `app/ingest/indexer.py`. Không module nào khác được gọi Qdrant client để ghi.
