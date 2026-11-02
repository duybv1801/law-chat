import codecs
import unicodedata
from datetime import date

import pytest

from app.ingest.parsers import ParseError, parse_bytes, parse_file
from app.ingest.parsers.detect import detect_metadata
from app.ingest.parsers.normalize import normalize_text, remove_repeated_lines
from app.ingest.parsers.txt import decode

NGHI_DINH = """CHÍNH PHỦ
-------
Số: 140/2004/NĐ-CP
CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc
Hà Nội, ngày 25 tháng 6 năm 2004

NGHỊ ĐỊNH
Quy định chi tiết thi hành một số điều của Luật Đất đai
Căn cứ Luật Tổ chức Chính phủ ngày 25 tháng 12 năm 2001;
CHÍNH PHỦ NGHỊ ĐỊNH:
Chương I
QUY ĐỊNH CHUNG
Điều 1. Phạm vi điều chỉnh
Nghị định này quy định chi tiết thi hành Luật Đất đai.
"""

LUAT = """QUỐC HỘI
Luật số: 45/2019/QH14
CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
LUẬT
BẢO HIỂM XÃ HỘI
Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;
Điều 1. Phạm vi điều chỉnh
1. Luật này quy định chế độ bảo hiểm xã hội.
Luật này đã được Quốc hội khóa XIV thông qua ngày 20 tháng 11 năm 2019.
"""

# "ệ" and "ậ" written with combining marks (NFD), as some editors / cp1258 produce them.
LUAT_NFC = "Luật Bảo hiểm xã hội. Điều 1. Hiệu lực thi hành"
LUAT_NFD = unicodedata.normalize("NFD", LUAT_NFC)


# --- normalize ---


def test_nfc_two_forms_become_one() -> None:
    assert LUAT_NFC != LUAT_NFD
    assert normalize_text(LUAT_NFD) == normalize_text(LUAT_NFC) == LUAT_NFC
    assert unicodedata.is_normalized("NFC", normalize_text(LUAT_NFD))


def test_nfc_through_every_parser(make_docx) -> None:
    from_txt = parse_bytes(LUAT_NFD.encode(), "a.txt").text
    from_docx = parse_bytes(make_docx([LUAT_NFD]), "a.docx").text
    assert from_txt == from_docx == LUAT_NFC


def test_normalize_whitespace_keeps_paragraphs() -> None:
    raw = "﻿  Điều 1.  Phạm   vi\t\r\nnội dung​\r\n\r\n\n\n  Điều 2.  Đối tượng  \n\n"
    assert normalize_text(raw) == "Điều 1. Phạm vi\nnội dung\n\nĐiều 2. Đối tượng"


def test_normalize_is_idempotent() -> None:
    once = normalize_text(NGHI_DINH)
    assert normalize_text(once) == once
    # A dropped control character between two spaces must not leave a double space.
    assert normalize_text("a \x01 b") == "a b"


def test_normalize_invisible_char_does_not_block_nfc() -> None:
    # Soft hyphen / zero-width joiner between a letter and its combining mark.
    for raw in ("e\u00ad\u0302", "Đie\u200d\u0302\u0300u 1"):
        result = normalize_text(raw)
        assert unicodedata.is_normalized("NFC", result)
    assert normalize_text("Đie\u200d\u0302\u0300u 1") == "Điều 1"


def test_remove_repeated_header_footer() -> None:
    pages = [
        ["CÔNG BÁO/Số 12", f"Điều {i}. Nội dung", f"Nội dung chi tiết của điều {i}", f"Trang {i}/4"]
        for i in range(1, 5)
    ]
    pages[2].insert(1, "Chỉ có ở trang 3")
    result = remove_repeated_lines(pages)
    # Header and page number go; content lines differing only by a number stay.
    assert result[0] == ["Điều 1. Nội dung", "Nội dung chi tiết của điều 1"]
    assert "Chỉ có ở trang 3" in result[2]


def test_repeated_lines_need_majority_and_keep_structure() -> None:
    # Header on exactly half of the pages: not > 50% -> kept.
    pages = [["Header", "a"], ["Header", "b"], ["c"], ["d"]]
    assert remove_repeated_lines(pages) == pages
    # Khoản / Điều lines are never stripped even if they repeat at page edges.
    pages = [["1. Quy định chung", f"Nội dung trang {i}"] for i in range(4)]
    assert remove_repeated_lines(pages) == pages
    # Điểm lines ("a) ...") too.
    pages = [["a) Bãi bỏ;", f"Nội dung trang {i}"] for i in range(4)]
    assert remove_repeated_lines(pages) == pages
    # Fewer than 3 pages: unchanged.
    two = [["Header", "a"], ["Header", "b"]]
    assert remove_repeated_lines(two) == two


# --- txt ---


def test_txt_encodings() -> None:
    text = "Điều 1. Luật này quy định về bảo hiểm xã hội."
    assert decode(text.encode("utf-8")) == text
    assert decode(codecs.BOM_UTF8 + text.encode("utf-8")) == text
    assert decode(text.encode("utf-16")) == text  # with BOM
    assert decode(text.encode("utf-16-le")) == text
    assert decode(text.encode("utf-16-be")) == text


def test_txt_stray_nul_stays_utf8() -> None:
    raw = "Điều 1 ab\x00c".encode()
    assert len(raw) % 2 == 0
    assert decode(raw) == "Điều 1 ab\x00c"


def test_txt_cp1258_fallback_is_nfc() -> None:
    # cp1258 has no precomposed "ậ": it is "â" + combining dot below.
    raw = "Luật Đất đai".encode("cp1258")
    assert parse_bytes(raw, "luat.TXT").text == "Luật Đất đai"


# --- docx ---


def test_docx_paragraph_order_and_header_table(make_docx) -> None:
    data = make_docx(
        ["NGHỊ ĐỊNH", "Quy định chi tiết", "Điều 1. Phạm vi", "Nội dung điều 1"],
        header_table=[["CHÍNH PHỦ\nSố: 140/2004/NĐ-CP", "Hà Nội, ngày 25 tháng 6 năm 2004"]],
    )
    parsed = parse_bytes(data, "nd.docx")
    assert parsed.text.splitlines() == [
        "CHÍNH PHỦ",
        "Số: 140/2004/NĐ-CP",
        "Hà Nội, ngày 25 tháng 6 năm 2004",
        "NGHỊ ĐỊNH",
        "Quy định chi tiết",
        "Điều 1. Phạm vi",
        "Nội dung điều 1",
    ]
    assert parsed.detected == {
        "so_hieu": "140/2004/NĐ-CP",
        "doc_type": "Nghị định",
        "issued_date": date(2004, 6, 25),
        "issued_date_source": "place_date",
        "title": "Nghị định quy định chi tiết",
    }


def test_docx_merged_cells_once(make_docx) -> None:
    import io

    import docx

    document = docx.Document(io.BytesIO(make_docx(["x"], header_table=[["A", "B"], ["C", "D"]])))
    table = document.tables[0]
    table.cell(0, 0).merge(table.cell(0, 1))
    buffer = io.BytesIO()
    document.save(buffer)
    assert parse_bytes(buffer.getvalue(), "m.docx").text.count("A") == 1


def test_docx_corrupt() -> None:
    with pytest.raises(ParseError) as exc:
        parse_bytes(b"PK\x03\x04 not really a zip", "x.docx")
    assert exc.value.code == "corrupt_file"


def test_docx_malformed_xml(make_docx) -> None:
    import io
    import zipfile

    source = zipfile.ZipFile(io.BytesIO(make_docx(["Điều 1. Nội dung"])))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == "word/document.xml":
                content = content[: len(content) // 2]
            target.writestr(item, content)
    with pytest.raises(ParseError) as exc:
        parse_bytes(out.getvalue(), "a.docx")
    assert exc.value.code == "corrupt_file"


# --- pdf ---


def test_pdf_text_and_repeated_footer(make_pdf) -> None:
    pages = [
        f"Điều {i}. Quy định số {i}\nNội dung của điều {i} về bảo hiểm xã hội.\nTrang {i}"
        for i in range(1, 5)
    ]
    parsed = parse_bytes(make_pdf(pages), "luat.pdf")
    lines = parsed.text.splitlines()
    assert "Điều 1. Quy định số 1" in lines
    assert "Nội dung của điều 4 về bảo hiểm xã hội." in lines
    assert not any(line.startswith("Trang") for line in lines)
    assert unicodedata.is_normalized("NFC", parsed.text)


def test_pdf_scan_raises(make_pdf) -> None:
    data = make_pdf([NGHI_DINH.replace("\n\n", "\n")], image_only=True)
    with pytest.raises(ParseError) as exc:
        parse_bytes(data, "scan.pdf")
    assert exc.value.code == "scanned_pdf"
    assert exc.value.user_message == "PDF scan chưa hỗ trợ."


def test_pdf_scan_with_text_footer_raises(make_pdf) -> None:
    import pymupdf

    doc = pymupdf.open(stream=make_pdf([NGHI_DINH.replace("\n\n", "\n")] * 4, image_only=True))
    for page in doc:
        page.insert_text((50, 820), "Công báo điện tử - Cổng thông tin Chính phủ - www.congbao.vn")
    with pytest.raises(ParseError) as exc:
        parse_bytes(doc.tobytes(), "scan.pdf")
    assert exc.value.code == "scanned_pdf"


def test_pdf_partially_scanned_warns(make_pdf) -> None:
    import pymupdf

    page_text = "Điều {}. Nội dung đủ dài của điều này về bảo hiểm xã hội và đất đai."
    doc = pymupdf.open(stream=make_pdf([page_text.format(i) for i in range(1, 11)]))
    scan = pymupdf.open(stream=make_pdf(["Trang scan"] * 90, image_only=True))
    doc.insert_pdf(scan)
    parsed = parse_bytes(doc.tobytes(), "mixed.pdf")
    assert "Điều 10. Nội dung" in parsed.text
    assert parsed.warnings == [{"code": "partially_scanned", "scanned_pages": list(range(11, 101))}]


def test_pdf_blank_page_no_warning(make_pdf) -> None:
    long = "Điều 1. Nội dung đủ dài của điều này về bảo hiểm xã hội và đất đai."
    parsed = parse_bytes(make_pdf([long, "", long]), "blank.pdf")
    assert parsed.warnings == []


def test_pdf_zero_pages_is_corrupt(monkeypatch) -> None:
    import pymupdf

    class Empty:
        needs_pass = False
        page_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(pymupdf, "open", lambda **kwargs: Empty())
    with pytest.raises(ParseError) as exc:
        parse_bytes(b"%PDF-1.7", "x.pdf")
    assert exc.value.code == "corrupt_file"


def test_pdf_extraction_error_is_parse_error(make_pdf, monkeypatch) -> None:
    import pymupdf

    def boom(*args, **kwargs):
        raise ValueError("bad content stream")

    monkeypatch.setattr(pymupdf.Page, "get_text", boom)
    with pytest.raises(ParseError) as exc:
        parse_bytes(make_pdf(["Điều 1. Nội dung"]), "x.pdf")
    assert exc.value.code == "corrupt_file"


def test_pdf_corrupt() -> None:
    with pytest.raises(ParseError) as exc:
        parse_bytes(b"%PDF-1.7 garbage", "x.pdf")
    assert exc.value.code == "corrupt_file"


def test_pdf_encrypted(make_pdf) -> None:
    import pymupdf

    doc = pymupdf.open(stream=make_pdf(["Điều 1. Nội dung khá dài để không bị coi là scan."]))
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(ParseError) as exc:
        parse_bytes(data, "x.pdf")
    assert exc.value.code == "encrypted_pdf"


# --- dispatch ---


def test_unsupported_and_empty(tmp_path) -> None:
    with pytest.raises(ParseError) as exc:
        parse_bytes(b"abc", "x.doc")
    assert exc.value.code == "unsupported_format"
    with pytest.raises(ParseError) as exc:
        parse_bytes(b" \n\t\n", "x.txt")
    assert exc.value.code == "empty_document"

    path = tmp_path / "nd.txt"
    path.write_text(NGHI_DINH, encoding="utf-8")
    assert parse_file(path).detected["so_hieu"] == "140/2004/NĐ-CP"


# --- detect ---


def test_detect_nghi_dinh() -> None:
    assert detect_metadata(normalize_text(NGHI_DINH)) == {
        "so_hieu": "140/2004/NĐ-CP",
        "doc_type": "Nghị định",
        "issued_date": date(2004, 6, 25),
        "issued_date_source": "place_date",
        "title": "Nghị định quy định chi tiết thi hành một số điều của Luật Đất đai",
    }


def test_detect_luat_date_from_passed_clause() -> None:
    assert detect_metadata(normalize_text(LUAT)) == {
        "so_hieu": "45/2019/QH14",
        "doc_type": "Luật",
        "issued_date": date(2019, 11, 20),
        "issued_date_source": "passed",
        "title": "Luật bảo hiểm xã hội",
    }


@pytest.mark.parametrize(
    ("text", "so_hieu"),
    [
        ("Số: 45/2019/QH14", "45/2019/QH14"),
        ("Số:01/2021/TTLT-BTP-BTC", "01/2021/TTLT-BTP-BTC"),
        ("Số: 12/CT-TTg\nHà Nội", "12/CT-TTg"),
        ("Số 08/2022/TT-BTC.", "08/2022/TT-BTC"),
        ("Thực hiện Nghị định 15/2020/NĐ-CP về xử phạt", "15/2020/NĐ-CP"),
        ("Số: ……/QĐ-UBND", None),
        ("Không có số hiệu 2020", None),
    ],
)
def test_detect_so_hieu(text: str, so_hieu: str | None) -> None:
    assert detect_metadata(text)["so_hieu"] == so_hieu


def test_detect_so_hieu_only_in_preamble() -> None:
    text = "Điều 1. Bãi bỏ\nBãi bỏ Nghị định số 15/2020/NĐ-CP."
    assert detect_metadata(text)["so_hieu"] is None


@pytest.mark.parametrize(
    ("so_hieu", "doc_type"),
    [
        ("01/2021/TTLT-BTP-BTC", "Thông tư liên tịch"),
        ("08/2022/TT-BTC", "Thông tư"),
        ("12/CT-TTg", "Chỉ thị"),
        ("1234/QĐ-UBND", "Quyết định"),
        ("45/2019/QH14", None),  # Luật or Nghị quyết: ambiguous
    ],
)
def test_detect_doc_type_from_number(so_hieu: str, doc_type: str | None) -> None:
    detected = detect_metadata(f"Số: {so_hieu}\nVề việc gì đó")
    assert detected["doc_type"] == doc_type
    assert detected["title"] is None


def test_detect_type_heading_variants() -> None:
    text = "BỘ TÀI CHÍNH\nTHÔNG TƯ LIÊN TỊCH\nHướng dẫn thực hiện\nCăn cứ"
    assert detect_metadata(text)["doc_type"] == "Thông tư liên tịch"
    text = "LUẬT\nSỬA ĐỔI, BỔ SUNG MỘT SỐ ĐIỀU CỦA LUẬT ĐẤT ĐAI\nCăn cứ Hiến pháp"
    detected = detect_metadata(text)
    assert detected["doc_type"] == "Luật"
    assert detected["title"] == "Luật sửa đổi, bổ sung một số điều của luật đất đai"
    # The enacting formula "QUYẾT ĐỊNH:" before Điều 1 is not a type heading.
    text = "Số: 08/2022/TT-BTC\nCăn cứ Luật\nQUYẾT ĐỊNH:\nĐiều 1. X"
    assert detect_metadata(text)["doc_type"] == "Thông tư"


def test_detect_quyet_dinh_title_stops_at_issuer() -> None:
    text = "QUYẾT ĐỊNH\nVề việc ban hành quy chế\nBỘ TRƯỞNG BỘ TƯ PHÁP\nCăn cứ"
    assert detect_metadata(text)["title"] == "Quyết định về việc ban hành quy chế"


@pytest.mark.parametrize(
    ("text", "issued"),
    [
        ("Hà Nội, ngày 01/02/2020", date(2020, 2, 1)),
        ("TP. Hồ Chí Minh, Ngày 5 tháng 3 năm 2021", date(2021, 3, 5)),
        ("ngày 31 tháng 2 năm 2020\nngày 1 tháng 3 năm 2020", date(2020, 3, 1)),  # invalid skipped
        ("Hà Nội, ngày … tháng … năm 2020", None),
    ],
)
def test_detect_issued_date(text: str, issued: date | None) -> None:
    assert detect_metadata(text)["issued_date"] == issued


def test_detect_uppercase_article_ends_head() -> None:
    # The date inside "ĐIỀU 1" is an effective date, not the issue date.
    text = (
        "QUYẾT ĐỊNH\nVề việc ban hành quy chế\n"
        "ĐIỀU 1. Hiệu lực\nCó hiệu lực từ ngày 1 tháng 7 năm 2020."
    )
    assert detect_metadata(text)["issued_date"] is None


def test_detect_head_capped_without_article() -> None:
    body = "\n".join(f"Đoạn {i} của công văn." for i in range(50))
    text = f"CÔNG VĂN\n{body}\nHà Nội, ngày 1 tháng 7 năm 2020"
    assert detect_metadata(text)["issued_date"] is None


def test_detect_issued_date_skips_cited_documents() -> None:
    # Law without its own header date: "Căn cứ" dates belong to other documents.
    text = (
        "LUẬT\nBẢO HIỂM\nCăn cứ Nghị quyết số 51/2001/QH10 ngày 19 tháng 6 năm 2015;\n"
        "Điều 1. Phạm vi\nLuật này đã được Quốc hội thông qua ngày 20 tháng 11 năm 2019."
    )
    detected = detect_metadata(text)
    assert detected["issued_date"] == date(2019, 11, 20)
    assert detected["issued_date_source"] == "passed"
    assert detected["so_hieu"] is None  # 51/2001/QH10 is the cited resolution
    # Fallback (no place line, no "thông qua"): skips the "Căn cứ" line.
    text = "NGHỊ ĐỊNH\nCăn cứ Luật ngày 19 tháng 6 năm 2015;\nngày 2 tháng 3 năm 2020\nĐiều 1. X"
    detected = detect_metadata(text)
    assert detected["issued_date"] == date(2020, 3, 2)
    assert detected["issued_date_source"] == "fallback"


def test_detect_place_date_before_other_dates() -> None:
    # Not a law: a "thông qua" clause in the body is about another document.
    text = (
        "THÔNG TƯ\nHướng dẫn Nghị định ngày 14 tháng 12 năm 2020\n"
        "Số: 08/2022/TT-BTC Hà Nội, ngày 5 tháng 3 năm 2022\n"
        "Điều 1. X\nLuật đã được thông qua ngày 20 tháng 11 năm 2019."
    )
    detected = detect_metadata(text)
    assert detected["issued_date"] == date(2022, 3, 5)
    assert detected["issued_date_source"] == "place_date"


def test_detect_future_date_ignored() -> None:
    assert detect_metadata("Hà Nội, ngày 1 tháng 1 năm 2999")["issued_date"] is None


def test_detect_empty_text() -> None:
    assert detect_metadata("") == {
        "so_hieu": None,
        "doc_type": None,
        "issued_date": None,
        "issued_date_source": None,
        "title": None,
    }
