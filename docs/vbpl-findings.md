# vbpl.vn — Kết quả khảo sát (T12)

Khảo sát ngày **2026-11-02** trên phiên bản mới của `vbpl.vn` (chạy từ 23/4/2026).
Tổng cộng **34 request**, cách nhau ≥ 1,5 giây, User-Agent `LawChatbot/0.1 (survey; contact: …)`, dùng `httpx` (không cần Playwright).
Không gọi bất kỳ đường dẫn nào bị robots.txt cấm.

> **Trạng thái: owner đã duyệt (2026-11-02).** 3 điểm lệch so với `04-crawler.md` được chấp nhận — xem mục [Quyết định đã duyệt](#quyết-định-đã-duyệt). T13 làm theo file này khi tới lượt.

## Tóm tắt

- robots.txt **cho phép** crawl trang văn bản, **cấm** `/api/` và `/Pages/`. Không có `Crawl-delay`. Không thấy điều khoản sử dụng nào cấm crawl tự động.
- Trang là **Next.js App Router, render phía server**. Toàn bộ metadata + toàn văn của một văn bản nằm sẵn trong HTML trang chi tiết (payload RSC `self.__next_f.push(...)`, object `initialDoc`). **Không cần Playwright, không cần gọi API.**
- Trang danh sách / tìm kiếm **không** render sẵn kết quả (tải phía client qua API → không dùng). **Liệt kê văn bản bằng sitemap** (`/sitemap.xml`), có sẵn phân chia trung ương / địa phương và `lastmod` sắp giảm dần → dùng được cho cả `backfill` và `daily_new`.
- Toàn văn là **HTML có cấu trúc** với class `prov-chapter` / `prov-section` / `prov-article` / `prov-clause` / `prov-item` → chunker theo Điều rất thuận.
- Tình trạng hiệu lực có **mã ổn định** (`CHL`, `HHL`, `HHL1P`, `CCHL`, …) kèm tên tiếng Việt → map enum theo mã, không theo chữ.
- **Không lấy được** quan hệ văn bản (thay thế / sửa đổi / …) và file PDF gốc mà không gọi API (mà API nằm dưới đường dẫn bị cấm, xem câu 5–6).

## 1. robots.txt

```
User-Agent: *
Allow: /
Disallow: /api/
Disallow: /Pages/

Sitemap: https://vbpl.vn/sitemap.xml
```

- Không có `Crawl-delay` → giữ mặc định của dự án: 1 request/giây (quy tắc 6).
- `/Pages/` là URL của bản cũ (`/Pages/portal.aspx`) — không dùng.
- `/api/` bị cấm → **crawler không được gọi `/api/*`**, kể cả khi DevTools cho thấy JSON tiện hơn. Đây là lý do chính khiến thiết kế dựa vào sitemap + trang chi tiết.
- Mỗi trang có nhúng script `/_fec_sbu/fec_wrapper.js` (lớp chống bot / WAF phía frontend). Trong 34 request với `httpx` và UA trung thực, **không gặp challenge nào**, mọi trang trả 200 kèm đầy đủ dữ liệu SSR. Rủi ro: sau này có thể bị chặn → client T13 phải nhận ra trang không có `initialDoc` và báo lỗi rõ ràng, không retry dồn dập.
- Không tìm thấy trang "điều khoản sử dụng" trên site. Dữ liệu là văn bản quy phạm pháp luật công khai của Bộ Tư pháp.

## 2. Render phía server hay API JSON?

**Render phía server (Next.js App Router, RSC).** Không cần chạy JS.

Trang chi tiết chứa:

1. **JSON-LD** `@type: Legislation` (`legislationIdentifier`, `legislationType`, `legislationDate`, `legislationLegalForce` = `InForce`/…, `legislationPassedBy`). Gọn nhưng thiếu ngày hiệu lực → chỉ dùng để đối chiếu.
2. **Payload RSC**: các thẻ `<script>self.__next_f.push([1,"..."])</script>`. Nối các chuỗi lại thành "flight stream", gồm nhiều row dạng `<hex-id>:<json>\n` hoặc `<hex-id>:T<hex-len>,<text>` (row text, độ dài tính bằng **byte UTF-8**, không có ký tự xuống dòng kết thúc).
   - Trong flight có key `"initialDoc":{...}` → toàn bộ metadata (xem câu 5).
   - `initialDoc.documentContent.content` thường là tham chiếu `"$2b"` (trỏ tới row text `2b`, chứa HTML toàn văn); với văn bản không có toàn văn thì là chuỗi HTML rỗng nhúng thẳng. **Parser phải xử lý cả hai trường hợp.**

Cách bóc tách đã thử (dùng trong T13):

```python
parts = re.findall(r'self\.__next_f\.push\((\[.*?\])\)</script>', html, re.S)
flight = "".join(json.loads(p)[1] for p in parts if len(json.loads(p)) > 1)
i = flight.index('"initialDoc":') + len('"initialDoc":')
doc, _ = json.JSONDecoder().raw_decode(flight, i)
# documentContent.content == "$2b"  → tìm row "2b:T<hexlen>," trong flight.encode("utf-8"),
# lấy đúng <hexlen> byte tiếp theo rồi decode utf-8.
```

Có API JSON (client gọi qua axios, ví dụ `/qtdc/public/doc/minio/buckets/vbpl/<id>/<file>/download`), nhưng không xác định được baseURL từ JS (không có host API nào khác ngoài `vbpl.vn` trong trang → gần như chắc là `/api/...`, bị cấm). **Không dùng.** Owner có thể xác nhận bằng DevTools → Network nếu muốn.

Ghi chú kỹ thuật: `selectolax` bản đang lock **bỏ backend Modest** — `from selectolax.parser import HTMLParser` raise `ImportError`. T13 phải dùng `from selectolax.lexbor import LexborHTMLParser`.

## 3. Liệt kê văn bản

**Trang danh sách** `https://vbpl.vn/van-ban/trung-uong` (và `/dia-phuong`): HTML SSR không chứa danh sách văn bản (0 link chi tiết trong payload); danh sách, phân trang, bộ lọc đều tải phía client qua API → **không dùng được**.

**Sitemap** (cho phép, chính thức):

- `https://vbpl.vn/sitemap.xml` là sitemap index, có comment phân nhóm:
  - `sitemap/0.xml`: trang tĩnh.
  - `sitemap/1.xml` … `sitemap/12.xml`: **Trung ương**.
  - `sitemap/13.xml` … `sitemap/36.xml`: **Địa phương**.
- Mỗi file tối đa **5000 URL** (`sitemap/1.xml` ~4 MB; `sitemap/12.xml` 2905 URL).
- Mỗi `<url>` có `<loc>` (URL tiếng Việt), `<lastmod>` (ngày, `YYYY-MM-DD`), và `xhtml:link hreflang="en"` (bản `/en/...`, bỏ qua).
- **Sắp xếp theo `lastmod` giảm dần** (đã kiểm tra `1.xml` và `12.xml`; `1.xml` bắt đầu từ 2026-11-01 và kết thúc ở 2026-05-08, `12.xml` ở 2025-09…12) → toàn bộ nhóm trung ương là một danh sách giảm dần theo ngày cập nhật.
- `lastmod` = phần ngày của `initialDoc.updatedDate` (đối chiếu văn bản 32515: `updatedDate 2026-10-01T09:25:39` ↔ `lastmod 2026-10-01`).

Lọc mà sitemap hỗ trợ:

| Bộ lọc | Cách làm |
|---|---|
| Phạm vi trung ương / địa phương | Theo số thứ tự file sitemap (1–12 / 13–36). Đối chiếu thêm bằng breadcrumb JSON-LD trong trang chi tiết (`/van-ban/trung-uong`). Ranh giới 12/13 lấy từ comment trong sitemap index — có thể đổi khi số văn bản tăng, nên T13 đọc comment thay vì hardcode. |
| Loại văn bản | **Không có** trong sitemap. Ước lượng trước bằng tiền tố slug (`luat-`, `bo-luat-`, `hien-phap-`, `nghi-quyet-`, `phap-lenh-`, `nghi-dinh-`, `quyet-dinh-`, `thong-tu-`, `van-ban-hop-nhat-`, …) để sắp thứ tự ưu tiên backfill; xác nhận bằng `docType.name` sau khi tải trang chi tiết. |
| Ngày ban hành, cơ quan ban hành | **Không có** trong sitemap. Chỉ biết sau khi tải chi tiết. |
| Tình trạng hiệu lực | **Không có** trong sitemap. Chỉ biết sau khi tải chi tiết. |
| Ngày cập nhật | `lastmod`, đã sắp giảm dần. |

Phân bố tiền tố slug trong `sitemap/1.xml` (5000 URL): `thong-tu` 2422, `quyet-dinh` 773, `nghi-dinh` 697, `van-ban` (chủ yếu VBHN) 564, `nghi-quyet` 250, `chi-thi` 53, `luat-*` ~80, `phap-lenh` 21, …

**URL rác trong sitemap:** `sitemap/12.xml` có **1521 URL dạng `--vbpqta_NNNN`** (nhiều cái có slug tiếng Anh như `decree-…`, `circular-…`). Thử 1 URL → **HTTP 404** ("This page could not be found"). Client phải coi 404 là "bỏ qua", không retry, không tính là lỗi job. Các file 2–11 chưa tải nên chưa biết có bao nhiêu URL loại này.

## 4. Trang chi tiết và ID văn bản

- URL: `https://vbpl.vn/van-ban/chi-tiet/<slug>--<id>`. Ví dụ `…/luat-hoa-giai-o-co-so-so-35-2013-qh13--32515`.
- **ID = phần sau `--` cuối cùng** của slug. Có 3 dạng:
  - Số nguyên (`32515`, `177843`) — ItemID của hệ thống cũ, văn bản đã có từ trước.
  - UUID v1 (`1391cba0-bd3e-11f1-b132-15d48a73cb01`) — văn bản nhập trên hệ thống mới.
  - `vbpqta_NNNN` — đang 404 (xem trên).
- `initialDoc.id` trùng với ID trong URL → dùng làm `source_id`.
- **Slug là phần không ổn định** (sinh từ tiêu đề). Ổn định là ID. Đề xuất: `source_id` = ID; `source_url` = URL đầy đủ lấy từ sitemap / JSON-LD `url` lần crawl gần nhất. Chưa thử xem gọi slug sai + ID đúng có redirect không (không cần cho T13 vì luôn có URL đầy đủ từ sitemap).
- Tab trong trang chọn bằng query `?tabs=` (Nội dung / Thuộc tính / Lược đồ / Lịch sử / Văn bản gốc / Tải về). Chỉ tab Nội dung + Thuộc tính có dữ liệu SSR; các tab khác tải bằng API → không dùng.
- Kích thước trang: 80–190 KB với văn bản thường; văn bản hợp nhất có thể **~1,5 MB** (mẫu VBHN 61/2026: toàn văn 1,1 MB).

## 5. Các trường lấy được

Từ `initialDoc` (ví dụ thật, văn bản 32515):

| Trường cần | Key trong `initialDoc` | Ví dụ | Ghi chú |
|---|---|---|---|
| ID | `id` | `"32515"` | = `source_id` |
| Số hiệu | `docNum` | `"35/2013/QH13"` | có ký tự `Đ` (vd `140/2004/NĐ-CP`) — chuẩn hoá khi tạo `so_hieu_norm` |
| Trích yếu / tiêu đề | `title` | `"Luật Hòa giải ở cơ sở số 35/2013/QH13"` | |
| Loại văn bản | `docType.name` (+ `docType.code`) | `"Luật"` / `"Lu"` | thấy: Luật, Nghị định, Thông tư, Thông tư liên tịch, Quyết định, Chỉ thị, Sắc lệnh, Văn bản hợp nhất |
| Nhóm | `docGroup` | `"VBQPPL"` | |
| Cơ quan ban hành | `agencyName`, chi tiết ở `documentIssues[].agencyName/agencyCode` | `"Quốc hội"` / `QH` | `organization` là **đơn vị nhập liệu** (vd Bộ Tư pháp), **không** phải cơ quan ban hành |
| Người ký, chức danh | `documentIssues[].personName`, `.jobTitleName` | `"Nguyễn Sinh Hùng"`, `"Chủ tịch Quốc hội"` | mảng, có thể nhiều người (liên tịch) |
| Ngày ban hành | `issueDate` | `"2013-06-20T00:00:00"` | ISO không timezone → lấy phần ngày |
| Ngày có hiệu lực | `effFrom` | `"2014-01-01T00:00:00"` | có thể `null` (VBHN) |
| Ngày hết hiệu lực | `effTo` | `"2027-03-01T00:00:00"` | **có thể ở tương lai** trong khi vẫn `CHL` (đã có văn bản thay thế nhưng chưa tới ngày) |
| Tình trạng hiệu lực | `effStatus.code`, `effStatus.name` | `"CHL"`, `"Còn hiệu lực"` | **có thể `null`** (VBHN, một số Quyết định cũ) |
| Ngày cập nhật | `updatedDate` | `"2026-10-01T09:25:39"` | có thể `null`; khớp `lastmod` sitemap |
| Ngành / lĩnh vực | `documentMajors[].name`, `documentFields[].name` | `"Tư pháp"`, `"Phổ biến, giáo dục pháp luật"` | |
| Có toàn văn HTML | `hasContent` | `true` | |
| Toàn văn | `documentContent.content` | `"$2b"` → HTML | xem câu 6 |
| File gốc | `documentContentFileName`, `documentContentFileDocName`, `hasOriginalPdf` | `"VanBanGoc_35.2013.QH13.pdf"` | chỉ có tên file, tải qua API → không dùng |
| Là VBHN / bản dịch | `isConsolidatedDocument`, `isTranslationDoc` | `false` | |
| Phạm vi | (không có key riêng) | | suy từ file sitemap + breadcrumb JSON-LD |

Không có trong SSR:

- **Văn bản thay thế / sửa đổi / được sửa đổi / hợp nhất** (`references: null`, tab Lược đồ tải qua API). Chỉ gián tiếp: văn bản cũ có `effTo` được đặt khi văn bản mới thay thế nó được nhập (32515 có `effTo = 2027-03-01`, đúng ngày hiệu lực của luật thay thế 16/2026/QH16).
- **File PDF/DOC gốc** (tải qua API).
- Phần "hết hiệu lực" của văn bản hết hiệu lực một phần (tab Lịch sử). Toàn văn trả về là bản gốc, không đánh dấu điều khoản nào đã hết hiệu lực.

## 6. Nội dung toàn văn

- **HTML có cấu trúc** (`hasContent = true`): mỗi đoạn là `<p id="<uuid>" class="prov-…">`:

  | class | Ý nghĩa | Ví dụ |
  |---|---|---|
  | `prov-chapter` | Chương | `<strong>Chương I<br>NHỮNG QUY ĐỊNH CHUNG</strong>` |
  | `prov-section` | Mục | |
  | `prov-article` | Điều | `<strong>Điều 1.</strong> <strong>Phạm vi điều chỉnh</strong>` |
  | `prov-clause` | Khoản | `1. Luật này quy định…` |
  | `prov-item` | Điểm | `a) …` |
  | `prov-content` | Đoạn không thuộc cấp nào (phần đầu, nơi nhận, chữ ký) | |
  | `prov-table` | Bảng | |

  Phần quốc hiệu / căn cứ ở đầu văn bản là `<table>` và `<p>` không có class. Văn bản cũ đôi khi còn `class="MsoNormal"` (dán từ Word), ví dụ 170/2007/NĐ-CP chỉ có 3 `prov-article` — **chunker không được chỉ dựa vào class**, phải có fallback regex `^Điều \d+` trên text (đúng như 03-ingestion).
- **Không có toàn văn** (`hasContent = false`): `documentContent.content` là trang HTML rỗng, chỉ có PDF gốc (`hasOriginalPdf = true`) tải qua API. Trong mẫu ngẫu nhiên 10 văn bản có **2/10** như vậy (đều là văn bản cũ, đã hết hiệu lực).

## 7. "Văn bản mới cập nhật"

- Không có trang / feed SSR kiểu "mới cập nhật" (trang chủ và danh sách đều tải bằng API).
- **Dùng `sitemap/1.xml`**: sắp giảm dần theo `lastmod`, đọc từ đầu tới khi `lastmod < since`. Số văn bản trung ương có `lastmod` mỗi ngày trong tháng 10/2026: **2–81, thường 10–45**. Với overlap 7 ngày → ~100–300 URL / lần chạy, tải 1 file sitemap ~4 MB.
- `lastmod` thay đổi cả khi văn bản cũ được sửa metadata (vd 32515 cập nhật 2026-10-01 khi luật thay thế được nhập). **Giả định chưa kiểm chứng:** khi tình trạng hiệu lực của văn bản đổi thì `updatedDate`/`lastmod` cũng đổi. Nếu đúng, `daily_new` bắt được luôn phần lớn thay đổi hiệu lực; `status_refresh` vẫn giữ làm lưới an toàn.
- Hiện tượng: ngày 2026-05-08 có 2451 URL trong `sitemap/1.xml` (đợt chuyển dữ liệu sang hệ thống mới).

## 8. Ước lượng số văn bản trung ương còn hiệu lực

- Tổng URL nhóm trung ương: 11 × 5000 (file 1–11, giả định đều đầy như file 1) + 2905 (file 12) ≈ **57.900 URL**, trừ ít nhất 1521 URL `vbpqta_*` 404 → **~56.000 văn bản** (gồm cả VBHN, chỉ thị, công văn…).
- Mẫu ngẫu nhiên 10 văn bản (6 từ file 1, 4 từ file 12): `HHL` 7, `HHL1P` 1, không có tình trạng 2, `CHL` 0. Cỡ mẫu quá nhỏ để tin cậy; file 1 thiên về văn bản mới cập nhật nên tỉ lệ còn hiệu lực thật có thể cao hơn mẫu.
- **Ước lượng thô: 10–25% còn hiệu lực (CHL + HHL1P) → khoảng 6.000–14.000 văn bản**, trong đó Luật / Bộ luật / Pháp lệnh / Nghị quyết QH chỉ vài trăm.
- Hệ quả cho backfill: **phải tải trang chi tiết mới biết tình trạng**. Quét hết ~56.000 URL ở 1 req/s ≈ **16 giờ** (không tính tải file sitemap). Chấp nhận được cho một lần backfill chạy qua đêm trên Mac, có checkpoint/resume. Ưu tiên theo tiền tố slug giúp có ngay Luật/Nghị định trước.

## 9. Fixture

Lưu trong `backend/tests/fixtures/vbpl/` (HTML thô, nguyên byte; `.gitattributes` đặt `-text` để git không đổi xuống dòng — row `T` của RSC tính độ dài theo byte):

| File | Nguồn | Đặc điểm |
|---|---|---|
| `detail_32515_luat_chl.html` | `/van-ban/chi-tiet/luat-hoa-giai-o-co-so-so-35-2013-qh13--32515` | Luật, ID số, `CHL`, `effTo` ở tương lai, toàn văn có `prov-*` đầy đủ |
| `detail_1391cba0_luat_cchl.html` | `/van-ban/chi-tiet/luat-hoa-giai-o-co-so-so-16-2026-qh16--1391cba0-bd3e-11f1-b132-15d48a73cb01` | Luật, ID UUID, `CCHL`, có `prov-section`, `prov-table` |
| `detail_19680_nghi-dinh_hhl1p.html` | `/van-ban/chi-tiet/nghi-dinh-so-140-2004-nd-cp-quy-dinh-chi-tiet-mot-so-dieu-cua-luat-bien-gioi-quoc-gia--19680` | Nghị định, `HHL1P`, `documentContentFileName = null` |
| `detail_177843_nghi-dinh_hhl_no-content.html` | `/van-ban/chi-tiet/nghi-dinh-so-170-1999-nd-cp-…--177843` | `HHL`, `hasContent = false`, nội dung nhúng thẳng (không qua `$ref`), `updatedDate = null` |
| `detail_vbpqta_1889_404.html` | `/van-ban/chi-tiet/nghi-dinh-103-1997-nd-cp--vbpqta_1889` | HTTP 404 |
| `sitemap_index.xml` | `/sitemap.xml` | index có comment Trung ương / Địa phương |
| `sitemap_1_head.xml` | 20 `<url>` đầu của `/sitemap/1.xml` | có `lastmod` giảm dần, ID UUID và số |

Ngoài fixture còn khảo sát: VBHN `61/2026/VBHN-NĐ-BTC` (`effStatus = null`, `effFrom = null`, trang 1,5 MB — không lưu vì quá lớn), `170/2007/NĐ-CP` (chỉ có `MsoNormal`, ít `prov-*`), `05/1999/NĐ-CP` (`CHL`, `effTo = 2026-12-01`).

## Bảng ánh xạ tình trạng hiệu lực (đề xuất)

Map theo `effStatus.code` (ổn định), không theo `effStatus.name`. Luôn lưu nguyên văn `effStatus.name` vào `status_note` (với mã không chắc chắn thì ghi kèm mã, vd `"Tạm ngưng hiệu lực (TNHL)"`).

Danh sách mã lấy từ bảng i18n `statusMap` của chính trang (các mã đánh dấu ✔ đã gặp trong văn bản thật):

| Mã | Nguyên văn trên vbpl.vn | → `status` | |
|---|---|---|---|
| `CHL` ✔ | Còn hiệu lực | `con_hieu_luc` | |
| `CCHL` ✔ | Chưa có hiệu lực | `chua_co_hieu_luc` | |
| `HHL` ✔ | Hết hiệu lực toàn bộ | `het_hieu_luc_toan_bo` | |
| `HHL1P` ✔ | Hết hiệu lực một phần | `het_hieu_luc_mot_phan` | |
| `HHL1P1` | Văn bản bị bãi bỏ một phần | `het_hieu_luc_mot_phan` | |
| `HHL1P2` | Văn bản bị đính chính một phần | `het_hieu_luc_mot_phan` | đính chính không làm mất hiệu lực, nhưng giữ cùng nhóm "một phần" cho đơn giản; owner có thể đổi thành `con_hieu_luc` |
| `HHL1P3` | Văn bản bị sửa đổi, bổ sung một phần | `het_hieu_luc_mot_phan` | |
| `HHL1P4` | Văn bản bị thay thế một phần | `het_hieu_luc_mot_phan` | |
| `TNHL` | Văn bản bị tạm ngưng hiệu lực | `khong_xac_dinh` | enum không có "tạm ngưng"; không chắc → `khong_xac_dinh` theo 04-crawler |
| `TNGHL1P` | Văn bản bị tạm ngưng hiệu lực một phần | `khong_xac_dinh` | như trên |
| `null` ✔ | (không có) | `khong_xac_dinh` | VBHN và một số văn bản cũ |
| mã lạ | | `khong_xac_dinh` | log warning kèm mã |

Lưu ý: `effTo` có thể ở tương lai khi `code = CHL`. Không tự suy `het_hieu_luc_toan_bo` từ ngày (quy tắc 2: chỉ lấy theo nguồn); `status_refresh` nên ưu tiên các văn bản có `effective_until <= today` mà vẫn `con_hieu_luc` để cập nhật sớm khi vbpl đổi trạng thái.

## Quyết định đã duyệt

Owner duyệt ngày 2026-11-02. `/api/` **không dùng** vì robots.txt cấm (quy tắc 6), không phải vì không gọi được về kỹ thuật; Playwright mở tab Lược đồ cũng gọi `/api/` nên cũng không dùng. Muốn dùng API thì phải xin phép Bộ Tư pháp trước.

1. **Liệt kê bằng sitemap thay vì trang danh sách / API.** `VbplClient.list_documents(page, filters)` trong 04-crawler giả định có trang danh sách phân trang có bộ lọc — không tồn tại ở dạng dùng được. Thay bằng:
   - `list_documents` đọc tuần tự các file sitemap của phạm vi (`page` = số file sitemap, checkpoint = file + vị trí), chỉ lọc được theo phạm vi; lọc loại văn bản ước lượng bằng slug, lọc tình trạng sau khi tải chi tiết.
   - `list_recently_updated(since)` đọc `sitemap/1.xml` (và file tiếp theo nếu cần) tới khi `lastmod < since`.
2. **Quan hệ văn bản không lấy được** (chỉ có qua API bị cấm). Bỏ bước "văn bản mới thay thế X → đặt `status_checked_at = NULL` cho X" trong `run_daily_new`. Thay bằng 3 lớp:
   - **`daily_new` theo `lastmod`:** văn bản đã có trong DB mà xuất hiện lại trong sitemap → tải chi tiết, so `effStatus`/`effFrom`/`effTo`/hash → cập nhật Postgres + payload Qdrant. Bằng chứng văn bản cũ được cập nhật cùng lúc với văn bản thay thế: Luật 35/2013 `updatedDate 2026-10-01T09:25:39` và Luật 16/2026 (thay thế) `2026-10-01T09:25:40`; NĐ 05/1999 và NĐ 170/2007 cùng `2026-09-30T10:02:33`, cùng `effTo 2026-12-01`.
   - **`status_refresh` ưu tiên văn bản quá hạn:** `ORDER BY (status = 'con_hieu_luc' AND effective_until <= current_date) DESC, status_checked_at NULLS FIRST` — bắt trường hợp vbpl tự đổi trạng thái khi tới ngày mà không đổi `lastmod`. Chỉ cập nhật theo giá trị vbpl trả về, không tự suy từ ngày.
   - **Kiểm chứng giả định** "đổi hiệu lực thì `lastmod` đổi": log mỗi lần `status_refresh` thấy thay đổi mà `daily_new` không bắt được; sau vài tuần gần 0 là giả định đúng, nếu không thì tăng batch.
   - (Để sau, chỉ để hiển thị) Quan hệ "bị thay thế bởi" có thể rút từ toàn văn: Điều "hiệu lực thi hành" của văn bản mới thường ghi "Luật số … hết hiệu lực kể từ …" → regex số hiệu, đối chiếu `so_hieu_norm`.
3. **Văn bản không có toàn văn HTML** (`hasContent = false`, chỉ có PDF qua API): **không ingest** (đếm vào `stats.skipped_no_content`, không phải `failed`). Phần lớn là văn bản cũ đã hết hiệu lực nên ảnh hưởng nhỏ.

Ngoài ra, `get_status(source_id)` không có endpoint nhẹ hơn → vẫn tải trang chi tiết (~100–200 KB) rồi chỉ đọc `effStatus`/`effFrom`/`effTo`. Với `status_refresh` batch 500/ngày ≈ 8–9 phút ở 1 req/s.
