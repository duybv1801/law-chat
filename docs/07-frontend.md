# 07 — Frontend

React + Vite + TypeScript, TanStack Query, React Router, Tailwind CSS. Không dùng UI kit nặng. Có thể dùng `lucide-react` cho icon.

## Cấu trúc

```
src/
  api/client.ts        # fetch wrapper: credentials 'include', header X-Requested-With, tự refresh khi gặp 401 rồi gọi lại 1 lần
  api/*.ts             # hàm gọi từng nhóm endpoint + type khớp docs/06-api.md
  hooks/useChatStream.ts
  pages/LoginPage.tsx, RegisterPage.tsx
  pages/DocumentsPage.tsx      # tab Văn bản
  pages/ChatPage.tsx           # tab Hỏi đáp
  pages/SettingsPage.tsx
  pages/AdminPage.tsx
  components/...
```

Vite dev server proxy `/api` → `http://localhost:8000`.

## Layout

- Thanh trên cùng: tên app, 2 tab chính **Văn bản** | **Hỏi đáp**, bên phải là menu user (Cài đặt, Admin nếu là admin, Đăng xuất).
- Chưa đăng nhập → chuyển về `/login`.
- Chưa có API key nào → banner ở tab Hỏi đáp: "Bạn cần thêm API key trong Cài đặt để bắt đầu hỏi đáp" + nút đi tới Cài đặt.
- Responsive: dùng được trên điện thoại (sidebar hội thoại thu vào drawer).

## Tab Văn bản (`/documents`)

- Khu vực upload: kéo thả hoặc chọn file → gọi `/upload/preview` → hiện form với các trường điền sẵn (Tên văn bản*, Số hiệu, Loại, Ngày ban hành, Ngày hiệu lực, Tình trạng). Nút "Tải lên".
- Danh sách văn bản: bộ lọc `Tất cả | Của tôi | Chính thống (vbpl)`, ô tìm kiếm, lọc loại văn bản và tình trạng. Bảng có cột: Số hiệu, Tên, Loại, Ban hành, Hiệu lực, Tình trạng (badge màu), Trạng thái xử lý.
- Văn bản đang `pending` / `processing` → poll mỗi 3 giây đến khi xong.
- Bấm vào một dòng → drawer chi tiết: metadata, link nguồn, danh sách Điều. Nút xoá cho văn bản của mình.

Màu badge tình trạng: còn hiệu lực = xanh lá, hết hiệu lực một phần = vàng, hết hiệu lực toàn bộ = đỏ, chưa có hiệu lực = xanh dương, không xác định = xám.

## Tab Hỏi đáp (`/chat`, `/chat/:conversationId`)

- Sidebar trái: danh sách hội thoại, nút "Hội thoại mới", đổi tên / xoá.
- Khung chat:
  - Dropdown chọn model ở trên khung nhập (mặc định theo Cài đặt). Chỉ hiện model của provider đã có key.
  - Ô nhập nhiều dòng: Enter gửi, Shift+Enter xuống dòng. Nút dừng khi đang stream (dùng `AbortController`).
  - Câu trả lời render markdown (`react-markdown`). Marker `[n]` render thành chip nhỏ bấm được → cuộn tới và highlight thẻ nguồn tương ứng.
  - Dưới mỗi câu trả lời là **danh sách thẻ nguồn**, mỗi thẻ gồm:
    - `[n] Tên văn bản (Số hiệu) — Điều X. Tiêu đề`
    - `Ban hành: dd/mm/yyyy · Hiệu lực: dd/mm/yyyy · [badge tình trạng]`
    - Link "Xem văn bản gốc ↗" (mở `source_url` trong tab mới), hoặc nhãn "Tài liệu bạn tải lên — chưa xác minh"
    - Có thể mở rộng để xem `excerpt`
  - Footer nhỏ: "Thông tin chỉ để tham khảo, không thay thế tư vấn pháp lý."
  - Lỗi `invalid_key` / `no_api_key` → thông báo kèm nút đi tới Cài đặt.

`useChatStream(conversationId)`: gửi POST, đọc `response.body` bằng `TextDecoder`, tách event theo dòng trống `\n\n`, cập nhật state `{text, citations, status, error}`. Khi `done` thì invalidate query danh sách message.

## Cài đặt (`/settings`)

- Mục **API key**: 3 dòng OpenAI / Anthropic / Gemini. Mỗi dòng:
  - Chưa có key: ô nhập kiểu password + nút "Lưu và kiểm tra".
  - Đã có key: hiện `••••abcd`, thời điểm kiểm tra, nút "Thay key" và "Xoá".
  - Đang kiểm tra: spinner. Key sai: báo lỗi ngay dưới ô nhập.
  - Ghi chú: "Key được mã hoá khi lưu và không bao giờ hiển thị lại."
- Mục **Model mặc định**: dropdown, chỉ cho chọn model của provider đã có key.

## Admin (`/admin`)

- Thẻ thống kê từ `/admin/stats`.
- Bảng `crawl_runs`: loại job, trạng thái, bắt đầu, thời lượng, các số liệu trong stats, lỗi (mở rộng để xem).
- Nút "Chạy ngay" cho `daily_new`, `status_refresh`, `backfill` (có ô nhập `limit`).
