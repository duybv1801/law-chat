# 08 — Dev trên 2 máy (Mac + Linux không GPU)

## Tóm tắt

- Code giống hệt nhau trên hai máy. Mọi khác biệt nằm ở `.env` và lựa chọn torch tự động của `uv`.
- **Mac** là máy index nặng (backfill). **Linux** chỉ index ít; muốn có dữ liệu đầy đủ thì import từ Mac.
- Scheduler (crawl hằng ngày) chỉ bật trên **một** máy.

## 1. PyTorch theo hệ điều hành

Trên Linux, `pip install torch` mặc định kéo bản kèm CUDA (vài GB) dù máy không có GPU. Cấu hình `uv` để Linux lấy wheel CPU-only, macOS lấy wheel mặc định trên PyPI (đã có MPS):

```toml
# backend/pyproject.toml
[project]
dependencies = [
  "torch>=2.4",
  "FlagEmbedding>=1.3",
  # ...
]

[tool.uv.sources]
torch = [
  { index = "pytorch-cpu", marker = "sys_platform == 'linux'" },
]

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
```

- `uv.lock` là lock đa nền tảng, commit bình thường. Hai máy dùng chung một file lock.
- Kiểm tra sau `uv sync` trên Linux: `uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` → version có đuôi `+cpu`, kết quả `False`.
- Khi deploy lên VPS có GPU sau này, đổi marker hoặc thêm index CUDA riêng (không làm bây giờ).

## 2. Embedding trên từng máy

| | Mac (MPS) | Linux (CPU) |
|---|---|---|
| `EMBEDDING_DEVICE` | `auto` → `mps` | `auto` → `cpu` |
| `EMBEDDING_BATCH_SIZE` | 16–32 | 4–8 |
| `EMBEDDING_THREADS` | bỏ trống | số core vật lý (ví dụ 8) |
| Độ chính xác | fp32 | fp32 |
| Biến môi trường thêm | `PYTORCH_ENABLE_MPS_FALLBACK=1` | — |
| RAM model | ~3 GB | ~3 GB |

- `EMBEDDING_THREADS` → `torch.set_num_threads(n)` lúc khởi tạo Embedder. Không đặt thì để torch tự chọn.
- `PYTORCH_ENABLE_MPS_FALLBACK=1` cho phép op chưa hỗ trợ MPS tự rơi về CPU thay vì crash. Đặt trong `.env` và nạp **trước khi import torch** (đầu `app/main.py` và các CLI).
- Model tải về cache HuggingFace (khoảng 2,3 GB). Có thể đổi chỗ lưu bằng `HF_HOME`.
- Khi khởi động, log một dòng: hệ điều hành, device, số thread, batch size, thời gian load model.

### Embedder giả cho test

`EMBEDDING_BACKEND=bge | fake` (mặc định `bge`).
- `fake`: sinh vector dense 1024 chiều + sparse từ hash của text, có tính tất định, không load torch. Dùng cho unit test và khi chỉ làm UI trên máy yếu.
- `fake` ghi tên model là `fake` vào metadata của collection. Check khớp model khi khởi động (02-data-model) sẽ chặn việc trộn vector giả với vector thật.
- Khi dùng `fake`, đặt `QDRANT_COLLECTION=legal_chunks_fake` để tách hẳn.
- Test mặc định (`uv run pytest`) dùng `fake`. Test model thật đánh dấu `@pytest.mark.slow`, chạy bằng `uv run pytest -m slow` (chạy được trên cả CPU, chỉ chậm hơn).

## 3. Docker

- `docker-compose.yml` dùng **named volume** (`pgdata`, `qdrantdata`), không bind mount vào `./storage`. Tránh lỗi quyền sở hữu file (UID) trên Linux.
- Port giống nhau trên hai máy: Postgres `5432`, Qdrant `6333` (HTTP) / `6334` (gRPC).
- Linux: user phải nằm trong group `docker` (ghi trong README, không viết script đổi quyền).

## 4. Đồng bộ dữ liệu Mac → Linux (`scripts/data_sync.py`, task T10b)

Chỉ đồng bộ **dữ liệu văn bản nguồn `vbpl`**. Không đồng bộ users, API key, hội thoại hay văn bản user tự upload (các bảng này gắn với user của từng máy, và key được mã hoá bằng master key riêng của máy đó).

**Export (trên Mac):**
```bash
uv run python scripts/data_sync.py export --out storage/sync/
```
Tạo ra:
- `manifest.json`: `embedding_model`, kích thước vector, số văn bản, số chunk, thời điểm export, git commit hash.
- `documents.jsonl.gz`: các dòng `legal_documents` có `source='vbpl'`.
- `chunks.jsonl.gz`: `legal_chunks` của các văn bản đó.
- `points.jsonl.gz`: point trong Qdrant (`id`, `dense`, `sparse`, `payload`) lấy bằng `scroll` với filter `source == vbpl`, theo batch 512, kèm vector.

**Import (trên Linux):**
```bash
uv run python scripts/data_sync.py import --from storage/sync/ [--dry-run]
```
1. Kiểm tra `manifest.embedding_model` trùng với `EMBEDDING_MODEL` của máy. Khác thì dừng.
2. Kiểm tra migration Alembic ở đầu (head) trên máy đích.
3. Trong một transaction: xoá toàn bộ document `source='vbpl'` (chunk xoá theo cascade) → insert lại từ file.
4. Qdrant: xoá point có `source == vbpl` → upsert point từ file theo batch.
5. In ra số lượng và so với manifest.

Chuyển file giữa hai máy bằng cách nào cũng được (rsync, scp, ổ USB). Script không tự động copy qua mạng.

**Không làm** chiều ngược lại hoặc trộn hai chiều. Nếu Linux có crawl thử, lần import tiếp theo sẽ ghi đè phần `vbpl`. Chấp nhận được.

## 4b. Bộ dữ liệu dựng sẵn (seed bundle, Colab)

Backfill khối lượng lớn để đóng gói cho người dùng chạy trên Google Colab: `backend/notebooks/seed_colab.ipynb`
(hướng dẫn trong notebook). Kết quả trên Drive (`MyDrive/law-chatbot-seed/latest/`) là `law.dump` (pg_dump) +
`legal_chunks.snapshot` (Qdrant) + `manifest.json`. Import vào máy dev / máy người dùng:

```bash
docker compose up -d
cd backend
uv run python -m app.seed.cli restore --from /path/to/latest            # DB + collection của .env, phải trống
uv run python -m app.seed.cli restore --from ... --database law_seed --collection legal_chunks_seed
```

## 5. Scheduler và crawl

- Chỉ máy Mac đặt `SCHEDULER_ENABLED=true`. Linux đặt `false`.
- Trên Linux chạy crawl thủ công với `--limit` nhỏ để thử parser, không chạy backfill lớn.

## 6. Đồng nhất phiên bản công cụ

Commit các file sau ở gốc repo:
- `.python-version` → `3.12`
- `frontend/.nvmrc` → phiên bản Node LTS dùng chung
- `.gitattributes` → `* text=auto eol=lf` (tránh lỗi xuống dòng khi sửa file trên hai máy)
- `.editorconfig` cơ bản

## 7. Mẫu `.env` theo máy

`.env.example` chứa đầy đủ biến (xem 01-architecture) và ghi chú hai khối:

```bash
# --- Mac ---
EMBEDDING_DEVICE=auto
EMBEDDING_BATCH_SIZE=16
PYTORCH_ENABLE_MPS_FALLBACK=1
SCHEDULER_ENABLED=true

# --- Linux (không GPU) ---
# EMBEDDING_DEVICE=cpu
# EMBEDDING_BATCH_SIZE=8
# EMBEDDING_THREADS=8
# SCHEDULER_ENABLED=false
```

`APP_ENCRYPTION_KEYS` và `JWT_SECRET` mỗi máy sinh riêng bằng `scripts/gen_keys.py`.
