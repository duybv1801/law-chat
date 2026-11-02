# 05 — RAG và chat

## Model registry (`backend/config/models.yaml`)

```yaml
# Điền model string chính xác theo docs của từng provider và LiteLLM tại thời điểm deploy.
# Không hardcode tên model trong code.
models:
  - id: openai-main
    provider: openai
    litellm_model: "openai/<model-id>"
    label: "ChatGPT (GPT-5.5)"
    max_context_tokens: 128000
    enabled: true
  - id: anthropic-main
    provider: anthropic
    litellm_model: "anthropic/<model-id>"
    label: "Claude"
    max_context_tokens: 200000
    enabled: true
  - id: gemini-main
    provider: gemini
    litellm_model: "gemini/<model-id>"
    label: "Gemini"
    max_context_tokens: 128000
    enabled: true
validation_model:          # model rẻ dùng để kiểm tra key khi user lưu
  openai: "openai/<cheap-model-id>"
  anthropic: "anthropic/<cheap-model-id>"
  gemini: "gemini/<cheap-model-id>"
```

`app/llm/registry.py` load file khi khởi động và kiểm tra bằng Pydantic. `GET /api/models` trả về các model `enabled` (không trả `litellm_model`).

## LLM client (`app/llm/client.py`)

```python
async def stream_chat(*, model_id: str, api_key: str, messages: list[dict]) -> AsyncIterator[str]
async def validate_key(*, provider: str, api_key: str) -> None     # raise LLMError
```

- Gọi `litellm.acompletion(model=..., api_key=api_key, messages=..., stream=True, temperature=0.1)`.
- Truyền `api_key` trực tiếp vào từng lệnh gọi. **Không** đặt key vào biến môi trường (nhiều user dùng chung một process).
- Tắt log message / request body của LiteLLM, không bật verbose. Kiểm tra cấu hình tương ứng trong docs LiteLLM của phiên bản đang dùng.
- Map exception sang `LLMError(code, user_message)` trong `errors.py`:
  - lỗi xác thực → `invalid_key`: "API key không hợp lệ hoặc đã bị thu hồi. Vào Cài đặt để cập nhật."
  - rate limit → `rate_limited`
  - vượt context window → `context_too_long`
  - còn lại → `provider_error`
- `validate_key`: gọi `validation_model` với `max_tokens=1`, thành công thì cập nhật `validated_at`.

## Mã hoá key (`app/core/crypto.py`)

```python
def encrypt_secret(plain: str) -> bytes        # MultiFernet(APP_ENCRYPTION_KEYS).encrypt
def decrypt_secret(token: bytes) -> str
def rotate(token: bytes) -> bytes              # MultiFernet.rotate, dùng khi đổi master key
```
Test: mã hoá rồi giải mã ra đúng giá trị; giải mã bằng key sai thì raise; rotate được sang key mới.

## Retriever (`app/rag/retriever.py`)

```python
@dataclass
class RetrievalFilters:
    user_id: UUID
    include_expired: bool = False
    so_hieu_norm: list[str] | None = None
    doc_types: list[str] | None = None

async def retrieve(query: str, filters: RetrievalFilters, top_k: int) -> list[RetrievedChunk]
```

1. `embedder.embed_query(query)`.
2. Tìm số hiệu trong câu hỏi bằng regex `\b\d{1,4}/\d{4}/[A-ZĐ\-]+\d*\b` (ví dụ `100/2019/NĐ-CP`, `45/2019/QH14`). Nếu có và tồn tại trong DB → thêm filter `so_hieu_norm`.
3. Filter của Qdrant (luôn có):
   - phân quyền: `should: [source == "vbpl", owner_id == user_id]` (min_should = 1)
   - `include_expired=False` → `must_not: status == "het_hieu_luc_toan_bo"`
4. Query hybrid dùng Query API của Qdrant:
   ```python
   client.query_points(
       collection_name, 
       prefetch=[
           Prefetch(query=dense, using="dense", limit=top_k * 4, filter=f),
           Prefetch(query=SparseVector(...), using="sparse", limit=top_k * 4, filter=f),
       ],
       query=FusionQuery(fusion=Fusion.RRF),
       limit=top_k,
   )
   ```
5. **Ngưỡng:** RRF score không phản ánh độ liên quan tuyệt đối. Vì vậy ngưỡng tính trên **dense cosine score** của chunk tốt nhất (lấy từ kết quả prefetch dense). Chunk tốt nhất có cosine < `RAG_MIN_SCORE` → coi như không tìm thấy. Giá trị ngưỡng tinh chỉnh ở T25.
6. Lấy chunk + document từ Postgres theo id, giữ đúng thứ tự xếp hạng.
7. Gom chunk liền nhau của cùng một Điều (cùng `document_id`, cùng `dieu`) thành một block để LLM đọc liền mạch.

## Prompt (`app/rag/prompt.py`)

System prompt (tiếng Việt):

```
Bạn là trợ lý tra cứu pháp luật Việt Nam.
QUY TẮC:
1. Chỉ trả lời dựa trên các trích đoạn văn bản được cung cấp trong phần NGUỒN. Không dùng kiến thức bên ngoài.
2. Mỗi ý trong câu trả lời phải kèm số nguồn dạng [1], [2] ngay sau ý đó.
3. Nếu các nguồn không đủ để trả lời, nói rõ: "Không tìm thấy quy định liên quan trong cơ sở dữ liệu." và không suy đoán.
4. Không tự viết link, ngày ban hành, ngày hiệu lực. Hệ thống sẽ tự hiển thị các thông tin này theo số nguồn.
5. Nếu nguồn có ghi chú tình trạng khác "còn hiệu lực", hãy nhắc người dùng lưu ý.
6. Nếu nguồn là "tài liệu người dùng tải lên", nói rõ đây không phải nguồn chính thống.
7. Trả lời bằng tiếng Việt, ngắn gọn, đi thẳng vào câu hỏi. Trích nguyên văn quy định khi cần độ chính xác.
```

Phần NGUỒN, mỗi block:
```
[1] {title} ({so_hieu}) — {dieu}. {dieu_title}
Tình trạng: {status_label} | Nguồn: {vbpl.vn | tài liệu người dùng tải lên}
---
{text}
```

Lịch sử chat: đưa tối đa 6 message gần nhất (chỉ nội dung, không kèm nguồn cũ). Tổng token vượt 70% `max_context_tokens` thì cắt bớt lịch sử trước, sau đó mới bớt nguồn cuối danh sách.

**Phase sau:** dùng LLM viết lại câu hỏi phụ thuộc ngữ cảnh ("thế còn trường hợp kia?") thành câu hỏi độc lập trước khi retrieve. Giai đoạn đầu: ghép câu hỏi trước đó của user vào query embedding nếu câu hỏi hiện tại ngắn hơn 8 từ.

## Citations (`app/rag/citations.py`)

```python
def extract_markers(answer: str) -> list[int]                      # [1], [2,3], [1][2] → [1,2,3], bỏ trùng, giữ thứ tự xuất hiện
def build_citations(markers: list[int], blocks: list[SourceBlock]) -> list[Citation]
```

Citation (lưu vào `messages.citations` và gửi cho frontend):
```json
{
  "n": 1,
  "document_id": "uuid",
  "title": "Luật Lao động",
  "so_hieu": "45/2019/QH14",
  "doc_type": "Luật",
  "dieu": "Điều 5",
  "dieu_title": "Quyền và nghĩa vụ của người lao động",
  "issued_date": "2019-11-20",
  "effective_date": "2021-01-01",
  "status": "con_hieu_luc",
  "status_label": "Còn hiệu lực",
  "source": "vbpl",
  "source_url": "https://vbpl.vn/...",
  "excerpt": "200 ký tự đầu của chunk"
}
```

Quy tắc:
- Marker trỏ tới số không tồn tại → bỏ qua và log warning.
- Câu trả lời không có marker nào và không phải câu từ chối → vẫn gửi citations của top 3 nguồn, có cờ `"uncited": true`. Frontend hiện dòng "Nguồn tham khảo (mô hình không trích dẫn cụ thể)".
- Văn bản `user_upload` không có `source_url` → frontend hiện nhãn "Tài liệu bạn tải lên — chưa xác minh".
- **Test bắt buộc** cho `extract_markers` và `build_citations`.

## Chat service (`app/rag/service.py`)

```python
async def answer(user: User, conversation_id: UUID, question: str, model_id: str | None) -> AsyncIterator[ChatEvent]
```
Thứ tự xử lý:
1. Xác định model (tham số request → `user_settings.default_model_id`). Không có → event lỗi `no_model`.
2. Lấy key của provider tương ứng. Không có → event lỗi `no_api_key`.
3. Lưu message của user.
4. `retrieve`. Dưới ngưỡng → yield `token` chứa câu từ chối cố định, lưu message assistant với `refused=true`, yield `done`. **Không gọi LLM.**
5. Dựng prompt → `stream_chat` → yield từng `token`.
6. Xong → `extract_markers` → `build_citations` → yield `citations` → lưu message assistant → cập nhật `user_api_keys.last_used_at` → yield `done`.
7. Lỗi LLM giữa chừng → yield `error` (code + user_message), lưu phần đã sinh kèm cờ lỗi.

Câu từ chối cố định:
> Không tìm thấy quy định liên quan trong cơ sở dữ liệu hiện có. Bạn thử diễn đạt lại câu hỏi, nêu rõ tên hoặc số hiệu văn bản, hoặc tải văn bản liên quan lên ở tab Văn bản.

Mọi câu trả lời (kể cả từ chối) kèm footer cố định ở frontend: "Thông tin chỉ để tham khảo, không thay thế tư vấn pháp lý."
