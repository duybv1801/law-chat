# Báo cáo eval retrieval (T25)

Sinh bởi `scripts/eval_retrieval.py` lúc 2026-11-06 14:23.
Embedding `BAAI/bge-m3` (bge), collection
`legal_chunks`, `RAG_TOP_K=8`. 30 câu trong phạm vi,
5 câu ngoài phạm vi; chạy 20s.

Cách đo: mỗi câu gọi `retrieve` một lần với ngưỡng tắt, sau đó áp từng ngưỡng.
Câu trong phạm vi "trúng @k" khi được trả lời (điểm dense tốt nhất ≥ ngưỡng) **và** một
(số hiệu, Điều) kỳ vọng nằm trong k block nguồn đầu tiên. Câu ngoài phạm vi "từ chối đúng"
khi điểm dense tốt nhất < ngưỡng.

## Phân bố điểm dense tốt nhất

- Câu trong phạm vi: thấp nhất 0.642, trung vị
  0.750.
- Câu ngoài phạm vi: cao nhất 0.561.

## Quét `RAG_MIN_SCORE`

| RAG_MIN_SCORE | Trả lời (trong phạm vi) | recall@1 | recall@3 | recall@5 | recall@8 | Từ chối đúng (ngoài phạm vi) | Balanced acc. |
|---|---|---|---|---|---|---|---|
| 0.30 | 30/30 (100%) | 83% | 97% | 97% | 100% | 0/5 (0%) | 50% |
| 0.35 | 30/30 (100%) | 83% | 97% | 97% | 100% | 1/5 (20%) | 60% |
| 0.40 | 30/30 (100%) | 83% | 97% | 97% | 100% | 1/5 (20%) | 60% |
| 0.45 | 30/30 (100%) | 83% | 97% | 97% | 100% | 1/5 (20%) | 60% |
| 0.50 | 30/30 (100%) | 83% | 97% | 97% | 100% | 2/5 (40%) | 70% |
| 0.55 | 30/30 (100%) | 83% | 97% | 97% | 100% | 4/5 (80%) | 90% |
| 0.60 | 30/30 (100%) | 83% | 97% | 97% | 100% | 5/5 (100%) | 100% |
| 0.65 | 29/30 (97%) | 80% | 93% | 93% | 97% | 5/5 (100%) | 98% |
| 0.70 | 27/30 (90%) | 77% | 90% | 90% | 90% | 5/5 (100%) | 95% |

**Ngưỡng tốt nhất theo balanced accuracy: `0.60`** (trả lời
30/30 câu trong phạm vi, từ chối đúng
5/5 câu ngoài phạm vi, recall@8
100%).

## Chi tiết từng câu

| ID | Câu hỏi | Kỳ vọng | Điểm dense tốt nhất | Hạng Điều kỳ vọng | Top 3 trả về |
|---|---|---|---|---|---|
| hs-01 | Người bao nhiêu tuổi thì phải chịu trách nhiệm hình sự? | 100/2015/QH13 Điều 12 | 0.794 | 1 | 100/2015/QH13 Điều 12; 100/2015/QH13 Điều 90; 100/2015/QH13 Điều 2 |
| hs-02 | Trộm cắp tài sản trị giá bao nhiêu tiền thì bị truy cứu trách nhiệm hình sự? | 100/2015/QH13 Điều 173 | 0.750 | 1 | 100/2015/QH13 Điều 173; 100/2015/QH13 Điều 368; 100/2015/QH13 Điều 171 |
| hs-03 | Lừa đảo chiếm đoạt tài sản bị phạt tù bao nhiêu năm? | 100/2015/QH13 Điều 174 | 0.779 | 1 | 100/2015/QH13 Điều 174; 100/2015/QH13 Điều 170; 100/2015/QH13 Điều 290 |
| hs-04 | Những tình tiết nào được coi là giảm nhẹ trách nhiệm hình sự? | 100/2015/QH13 Điều 51 | 0.757 | 1 | 100/2015/QH13 Điều 51; 100/2015/QH13 Điều 84; 100/2015/QH13 Điều 52 |
| hs-05 | Phạm tội có tính chất côn đồ, tái phạm nguy hiểm có phải là tình tiết tăng nặng không? | 100/2015/QH13 Điều 52 | 0.701 | 1 | 100/2015/QH13 Điều 52; 100/2015/QH13 Điều 85; 100/2015/QH13 Điều 53 |
| hs-06 | Điều kiện để được hưởng án treo là gì? | 100/2015/QH13 Điều 65 | 0.704 | 1 | 100/2015/QH13 Điều 65; 100/2015/QH13 Điều 44; 101/2015/QH13 Điều 363 |
| hs-07 | Cố ý gây thương tích cho người khác với tỷ lệ tổn thương cơ thể 11% thì bị xử lý thế nào? | 100/2015/QH13 Điều 134 | 0.739 | 1 | 100/2015/QH13 Điều 134; 100/2015/QH13 Điều 136; 100/2015/QH13 Điều 138 |
| hs-08 | Đánh bạc trái phép bằng tiền với số tiền bao nhiêu thì bị truy cứu trách nhiệm hình sự? | 100/2015/QH13 Điều 321 | 0.736 | 1 | 100/2015/QH13 Điều 321; 100/2015/QH13 Điều 322; 100/2015/QH13 Điều 266 |
| hs-09 | Công chức nhận hối lộ từ 2 triệu đồng bị phạt thế nào theo Bộ luật Hình sự 100/2015/QH13? | 100/2015/QH13 Điều 354 | 0.730 | 2 | 100/2015/QH13 Điều 364; 100/2015/QH13 Điều 354; 100/2015/QH13 Điều 365 |
| hs-10 | Khác nhau giữa tội cướp tài sản và tội cướp giật tài sản là gì? | 100/2015/QH13 Điều 168, 100/2015/QH13 Điều 171 | 0.699 | 1 | 100/2015/QH13 Điều 171; 100/2015/QH13 Điều 168; 100/2015/QH13 Điều 302 |
| hs-11 | Gây tai nạn giao thông làm chết người khi lái xe thì bị phạt tù bao lâu? | 100/2015/QH13 Điều 260 | 0.697 | 6 | 100/2015/QH13 Điều 264; 100/2015/QH13 Điều 279; 100/2015/QH13 Điều 280 |
| hs-12 | Tàng trữ trái phép chất ma túy bị xử lý hình sự như thế nào? | 100/2015/QH13 Điều 249 | 0.755 | 1 | 100/2015/QH13 Điều 249; 100/2015/QH13 Điều 253; 100/2015/QH13 Điều 250 |
| tths-01 | Nguyên tắc suy đoán vô tội được quy định thế nào? | 101/2015/QH13 Điều 13 | 0.642 | 1 | 101/2015/QH13 Điều 13; 101/2015/QH13 Điều 15; 100/2015/QH13 Điều 129 |
| tths-02 | Thời hạn tạm giam để điều tra tội phạm nghiêm trọng là bao lâu? | 101/2015/QH13 Điều 173 | 0.746 | 1 | 101/2015/QH13 Điều 173; 101/2015/QH13 Điều 459; 101/2015/QH13 Điều 172 |
| tths-03 | Những trường hợp nào được bắt người phạm tội quả tang? | 101/2015/QH13 Điều 111 | 0.719 | 1 | 101/2015/QH13 Điều 111; 101/2015/QH13 Điều 110; 101/2015/QH13 Điều 58 |
| tths-04 | Thời hạn kháng cáo bản án hình sự sơ thẩm là bao nhiêu ngày? | 101/2015/QH13 Điều 333 | 0.779 | 1 | 101/2015/QH13 Điều 333; 92/2015/QH13 Điều 273; 101/2015/QH13 Điều 337 |
| tths-05 | Cơ quan điều tra phải giải quyết tố giác, tin báo về tội phạm trong thời hạn bao lâu? | 101/2015/QH13 Điều 147 | 0.746 | 1 | 101/2015/QH13 Điều 147; 101/2015/QH13 Điều 149; 101/2015/QH13 Điều 146 |
| tths-06 | Ai có quyền kháng cáo bản án sơ thẩm trong vụ án hình sự? | 101/2015/QH13 Điều 331 | 0.725 | 1 | 101/2015/QH13 Điều 331; 101/2015/QH13 Điều 333; 92/2015/QH13 Điều 271 |
| tthd-01 | Thời hạn kháng cáo bản án dân sự sơ thẩm là bao lâu? | 92/2015/QH13 Điều 273 | 0.742 | 2 | 101/2015/QH13 Điều 333; 92/2015/QH13 Điều 273; 92/2015/QH13 Điều 322 |
| tthd-02 | Tòa án nhân dân cấp huyện có thẩm quyền giải quyết những vụ việc dân sự nào? | 92/2015/QH13 Điều 35 | 0.767 | 2 | 92/2015/QH13 Điều 36; 92/2015/QH13 Điều 35; 92/2015/QH13 Điều 37 |
| tthd-03 | Khi nào Tòa án trả lại đơn khởi kiện? | 92/2015/QH13 Điều 192 | 0.744 | 2 | 92/2015/QH13 Điều 194; 92/2015/QH13 Điều 192; 92/2015/QH13 Điều 191 |
| tthd-04 | Có những cách nào để gửi đơn khởi kiện đến Tòa án? | 92/2015/QH13 Điều 190 | 0.769 | 1 | 92/2015/QH13 Điều 190; 92/2015/QH13 Điều 189; 92/2015/QH13 Điều 191 |
| tthd-05 | Những tình tiết, sự kiện nào trong vụ án dân sự không phải chứng minh? | 92/2015/QH13 Điều 92 | 0.730 | 1 | 92/2015/QH13 Điều 92; 92/2015/QH13 Điều 91; 101/2015/QH13 Điều 93 |
| tthd-06 | Ai phải chịu án phí dân sự sơ thẩm? | 92/2015/QH13 Điều 147 | 0.756 | 1 | 92/2015/QH13 Điều 147; 92/2015/QH13 Điều 148; 92/2015/QH13 Điều 146 |
| tthd-07 | Những trường hợp nào không được làm người đại diện trong tố tụng dân sự theo Bộ luật 92/2015/QH13? | 92/2015/QH13 Điều 87 | 0.795 | 1 | 92/2015/QH13 Điều 87; 92/2015/QH13 Điều 88; 92/2015/QH13 Điều 85 |
| tthd-08 | Tòa án tạm đình chỉ giải quyết vụ án dân sự trong những trường hợp nào? | 92/2015/QH13 Điều 214 | 0.786 | 1 | 92/2015/QH13 Điều 214; 92/2015/QH13 Điều 217; 92/2015/QH13 Điều 215 |
| hh-01 | Những loại tàu biển nào phải đăng ký? | 95/2015/QH13 Điều 19 | 0.751 | 1 | 95/2015/QH13 Điều 19; 95/2015/QH13 Điều 20; 95/2015/QH13 Điều 27 |
| hh-02 | Thuyền trưởng có địa vị pháp lý như thế nào trên tàu biển? | 95/2015/QH13 Điều 52 | 0.771 | 1 | 95/2015/QH13 Điều 52; 95/2015/QH13 Điều 60; 95/2015/QH13 Điều 53 |
| hh-03 | Thời hiệu khởi kiện về hư hỏng, mất mát hàng hóa vận chuyển bằng đường biển là bao lâu? | 95/2015/QH13 Điều 169 | 0.813 | 1 | 95/2015/QH13 Điều 169; 95/2015/QH13 Điều 214; 95/2015/QH13 Điều 152 |
| hh-04 | Khi hai tàu đâm va nhau thì lỗi và bồi thường được xác định theo nguyên tắc nào? | 95/2015/QH13 Điều 287 | 0.763 | 1 | 95/2015/QH13 Điều 287; 95/2015/QH13 Điều 288; 95/2015/QH13 Điều 323 |
| oos-01 | Lao động nữ được nghỉ thai sản bao nhiêu tháng? | từ chối | 0.516 | — | 100/2015/QH13 Điều 187; 100/2015/QH13 Điều 170; 100/2015/QH13 Điều 316 |
| oos-02 | Thu nhập bao nhiêu thì phải đóng thuế thu nhập cá nhân? | từ chối | 0.561 | — | 100/2015/QH13 Điều 200; 100/2015/QH13 Điều 190; 100/2015/QH13 Điều 203 |
| oos-03 | Thủ tục cấp giấy chứng nhận quyền sử dụng đất lần đầu gồm những gì? | từ chối | 0.514 | — | 100/2015/QH13 Điều 340; 92/2015/QH13 Điều 269; 100/2015/QH13 Điều 382 |
| oos-04 | Cách nấu phở bò Hà Nội ngon? | từ chối | 0.317 | — | ? (không Điều); 100/2015/QH13 Điều 195; 92/2015/QH13 (không Điều) |
| oos-05 | Thành lập công ty trách nhiệm hữu hạn hai thành viên cần vốn điều lệ tối thiểu bao nhiêu? | từ chối | 0.479 | — | 100/2015/QH13 Điều 360; 100/2015/QH13 Điều 205; 100/2015/QH13 Điều 359 |

## Kết luận và đề xuất (viết tay, chạy lại script sẽ ghi đè file — chép lại phần này)

- **Đề xuất `RAG_MIN_SCORE=0.60`** (đã đặt trong `.env.example` và default của `config.py`).
  Với bge-m3 thật, khoảng trống giữa hai nhóm rõ: câu trong phạm vi thấp nhất 0.642
  (`tths-01`, câu ngắn "suy đoán vô tội"), câu ngoài phạm vi cao nhất 0.561 (`oos-02`,
  thuế TNCN). 0.60 nằm giữa, cách mỗi bên ~0.04.
- Ngưỡng cũ 0.30 không từ chối câu nào ngoài phạm vi (kể cả "nấu phở" 0.317): mọi câu
  ngoài phạm vi đều đi tới LLM, chỉ dựa vào LLM tự từ chối.
- Từ 0.65 bắt đầu từ chối nhầm câu trong phạm vi (`tths-01`), 0.70 từ chối nhầm 3/30.
- Xếp hạng tốt: recall@1 87%, recall@3 97%, recall@8 100%. Các câu trượt hạng 1 đều có
  Điều kỳ vọng trong top 6 (`hs-11` Điều 260 ở hạng 6 do các tội giao thông khác gần nghĩa;
  `tthd-01` Điều 333 BLTTHS cùng chủ đề "thời hạn kháng cáo" đứng trước Điều 273 BLTTDS).

Giới hạn:
- Chỉ 5 văn bản (4 bộ luật có Điều) và 5 câu ngoài phạm vi: ngưỡng có biên an toàn mỏng.
  Câu hỏi pháp luật "gần" dữ liệu (vd. tội phạm thuộc luật chuyên ngành chưa có) sẽ có
  điểm cao hơn các câu ngoài phạm vi ở đây.
- Khi kho văn bản lớn lên sau backfill, câu ngoài phạm vi hôm nay có thể thành trong phạm
  vi và phân bố điểm sẽ đổi: **chạy lại script sau mỗi lần backfill lớn** và bổ sung câu hỏi.
- Script đo câu hỏi nguyên văn; chat thật dùng `retrieval_query` (câu hỏi ngắn < 8 từ được
  ghép câu trước), thường làm điểm cao hơn.
