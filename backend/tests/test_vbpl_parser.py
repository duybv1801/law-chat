import json
import logging
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pytest

from app.crawler.vbpl_parser import (
    VbplParseError,
    guess_doc_type,
    html_to_text,
    map_status,
    parse_detail,
    parse_flight_rows,
    parse_sitemap,
    parse_sitemap_index,
    parse_status,
    source_id_from_url,
)
from app.ingest.chunker import chunk_document
from app.ingest.schemas import LegalDocumentIn

FIXTURES = Path(__file__).parent / "fixtures" / "vbpl"


def page(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8")


def words(text: str) -> int:
    return len(text.split())


def push_page(*chunks: str) -> str:
    """A minimal Next.js page whose RSC payload is `chunks`."""
    scripts = "".join(f"<script>self.__next_f.push({json.dumps([1, c])})</script>" for c in chunks)
    return f"<html><body><script>self.__next_f.push([0])</script>{scripts}</body></html>"


def text_row(row_id: str, text: str) -> str:
    return f"{row_id}:T{len(text.encode()):x},{text}"


# --- Detail pages -----------------------------------------------------------------------


def test_luat_chl_metadata() -> None:
    doc = parse_detail(page("detail_32515_luat_chl.html"))
    assert doc.source_id == "32515"
    assert doc.source_url == (
        "https://vbpl.vn/van-ban/chi-tiet/luat-hoa-giai-o-co-so-so-35-2013-qh13--32515"
    )
    assert doc.title == "Luật Hòa giải ở cơ sở số 35/2013/QH13"
    assert doc.so_hieu == "35/2013/QH13"
    assert doc.doc_type == "Luật"
    assert doc.issuing_body == "Quốc hội"
    assert doc.issued_date == date(2013, 6, 20)
    assert doc.status.status == "con_hieu_luc"
    assert doc.status.status_code == "CHL"
    assert doc.status.status_note == "Còn hiệu lực"
    assert doc.status.effective_date == date(2014, 1, 1)
    assert doc.status.expiry_date == date(2027, 3, 1)  # future, still CHL: kept as is
    assert doc.status.updated_date == datetime(2026, 10, 1, 9, 25, 39)
    assert doc.scope == "trung_uong"
    assert doc.is_consolidated is False
    assert doc.signers == ["Nguyễn Sinh Hùng"]
    assert doc.fields == ["Phổ biến, giáo dục pháp luật"]


def test_luat_chl_text_and_chunks() -> None:
    doc = parse_detail(page("detail_32515_luat_chl.html"))
    assert doc.has_content and doc.text is not None
    text = doc.text
    assert text == unicodedata.normalize("NFC", text)
    assert text.startswith("QUỐC HỘI\n")
    lines = text.splitlines()
    # <br> inside the chapter heading -> two lines; inline <strong>s stay on one line.
    assert lines[lines.index("Chương I") + 1] == "NHỮNG QUY ĐỊNH CHUNG"
    assert "Điều 1. Phạm vi điều chỉnh" in lines
    assert lines[-1] == "Nguyễn Sinh Hùng"
    assert "<" not in text and "&nbsp;" not in text and "\xa0" not in text

    chunks = chunk_document(text, title=doc.title, so_hieu=doc.so_hieu, count_tokens=words)
    articles = [c for c in chunks if c.dieu]
    assert chunks[0].dieu_title == "Phần mở đầu"
    assert [c.dieu for c in articles] == [f"Điều {n}" for n in range(1, 34)]
    assert articles[0].chuong == "Chương I. NHỮNG QUY ĐỊNH CHUNG"
    assert articles[0].dieu_title == "Phạm vi điều chỉnh"


def test_luat_uuid_cchl_with_sections() -> None:
    doc = parse_detail(page("detail_1391cba0_luat_cchl.html"))
    assert doc.source_id == "1391cba0-bd3e-11f1-b132-15d48a73cb01"
    assert doc.so_hieu == "16/2026/QH16"
    assert doc.status.status == "chua_co_hieu_luc"
    assert doc.status.status_code == "CCHL"
    assert doc.status.effective_date == date(2027, 3, 1)
    assert doc.status.expiry_date is None
    assert doc.signers == ["Trần Thanh Mẫn"]
    assert doc.text is not None
    chunks = chunk_document(doc.text, title=doc.title, so_hieu=doc.so_hieu, count_tokens=words)
    assert "Mục 1. HÒA GIẢI VIÊN" in {c.muc for c in chunks}
    assert "Điều 8" in {c.dieu for c in chunks}


def test_nghi_dinh_hhl1p() -> None:
    doc = parse_detail(page("detail_19680_nghi-dinh_hhl1p.html"))
    assert doc.source_id == "19680"
    assert doc.doc_type == "Nghị định"
    assert doc.so_hieu == "140/2004/NĐ-CP"
    assert doc.issuing_body == "Chính phủ"
    assert doc.status.status == "het_hieu_luc_mot_phan"
    assert doc.status.status_note == "Hết hiệu lực một phần"
    assert doc.text is not None
    # The chapter heading is split over two <p> (`data-merged`): still two lines.
    chunks = chunk_document(doc.text, title=doc.title, so_hieu=doc.so_hieu, count_tokens=words)
    assert chunks[1].chuong == "Chương I. QUY ĐỊNH CHUNG"
    assert chunks[1].dieu == "Điều 1"
    assert chunks[-1].dieu == "Điều 35"


def test_no_html_content() -> None:
    doc = parse_detail(page("detail_177843_nghi-dinh_hhl_no-content.html"))
    assert doc.source_id == "177843"
    assert doc.has_content is False
    assert doc.text is None
    assert doc.status.status == "het_hieu_luc_toan_bo"
    assert doc.status.updated_date is None
    assert doc.status.expiry_date == date(2001, 7, 1)


def test_404_page_has_no_document() -> None:
    with pytest.raises(VbplParseError) as err:
        parse_detail(page("detail_vbpqta_1889_404.html"))
    assert err.value.code == "no_initial_doc"


def test_not_a_next_page() -> None:
    with pytest.raises(VbplParseError) as err:
        parse_detail("<html><body>Access denied</body></html>")
    assert err.value.code == "no_initial_doc"


def test_id_mismatch() -> None:
    with pytest.raises(VbplParseError) as err:
        parse_detail(page("detail_32515_luat_chl.html"), source_id="19680")
    assert err.value.code == "id_mismatch"
    assert parse_status(page("detail_32515_luat_chl.html"), source_id="32515").status_code == "CHL"


def test_parse_status_matches_detail() -> None:
    for name in ("detail_32515_luat_chl.html", "detail_177843_nghi-dinh_hhl_no-content.html"):
        assert parse_status(page(name)) == parse_detail(page(name)).status


def test_to_document_in() -> None:
    doc = parse_detail(page("detail_32515_luat_chl.html"))
    crawled = datetime(2026, 11, 4, 2, 0)
    data = doc.to_document_in(crawled_at=crawled)
    assert isinstance(data, LegalDocumentIn)
    assert (data.source, data.source_id) == ("vbpl", "32515")
    assert data.effective_date == date(2014, 1, 1)
    assert data.status == "con_hieu_luc"
    assert data.source_url == doc.source_url
    assert data.crawled_at == data.status_checked_at == crawled
    assert data.file_url is None  # original PDF is only reachable via /api/ (disallowed)

    doc.scope = None  # page without breadcrumb: the caller's scope (sitemap group) wins
    assert doc.to_document_in(scope="dia_phuong").scope == "dia_phuong"


# --- RSC payload ------------------------------------------------------------------------


def initial_doc(**overrides: object) -> str:
    doc = {"id": "1", "title": "Luật thử", "hasContent": True, "effStatus": None}
    doc.update(overrides)
    return "5:" + json.dumps({"x": {"initialDoc": doc}}, ensure_ascii=False) + "\n"


def test_text_row_reference_counts_utf8_bytes() -> None:
    body = "<p>Điều 1. Phạm vi</p><p>Nội dung có dấu ở cuối</p>"
    content = initial_doc(documentContent={"content": "$2b"})
    # The text row is followed directly by the next row (no newline), as on vbpl.vn.
    html = push_page(':HL["/a.css","style"]\n', content, text_row("2b", body), "6:null\n")
    doc = parse_detail(html)
    assert doc.text == "Điều 1. Phạm vi\nNội dung có dấu ở cuối"
    assert doc.status.status == "khong_xac_dinh"
    assert doc.status.status_note is None


def test_inline_content_and_escaped_dollar() -> None:
    doc = parse_detail(push_page(initial_doc(documentContent={"content": "$$5 <b>đồng</b>"})))
    assert doc.text == "$5 đồng"


def test_has_content_with_empty_body() -> None:
    doc = parse_detail(push_page(initial_doc(documentContent={"content": "<p> </p>"})))
    assert (doc.has_content, doc.text) == (False, None)


def test_flight_rows_split_across_pushes() -> None:
    flight = (text_row("a", "xin chào\n") + '1:["$","div"]\n').encode()
    rows = parse_flight_rows(flight)
    assert rows == {"a": "xin chào\n".encode(), "1": b'["$","div"]'}


@pytest.mark.parametrize(
    "flight", [b"zz:{}\n", b"2:T10,short", b"2:Tzz,abc", initial_doc().encode()[:-20]]
)
def test_broken_payload(flight: bytes) -> None:
    with pytest.raises(VbplParseError) as err:
        if flight.startswith(b"5:"):
            parse_detail(push_page(flight.decode()))
        else:
            parse_flight_rows(flight)
    assert err.value.code == "bad_payload"


def test_surrogate_pair_split_across_pushes() -> None:
    body = "<p>Điều 1. Ký hiệu 😀 ở đây</p>"
    content = initial_doc(documentContent={"content": "$2b"}) + text_row("2b", body)
    cut = content.index("😀")
    # The browser-side payload is UTF-16: Next.js may cut a pair between two pushes.
    html = push_page(content[:cut] + "\ud83d", "\ude00" + content[cut + 1 :])
    assert parse_detail(html).text == "Điều 1. Ký hiệu 😀 ở đây"


@pytest.mark.parametrize("value", ["$undefined", "$L1", "$@2", "$Sreact.suspense"])
def test_special_rsc_values_are_not_text(value: str) -> None:
    doc = parse_detail(push_page(initial_doc(documentContent={"content": value})))
    assert (doc.has_content, doc.text) == (False, None)


def test_id_match_ignores_case() -> None:
    uuid = "1391cba0-5b2e-4f7a-9c1d-0a1b2c3d4e5f"
    html = push_page(initial_doc(id=uuid))
    assert parse_detail(html, source_id=uuid.upper()).source_id == uuid


def test_missing_reference_row() -> None:
    with pytest.raises(VbplParseError):
        parse_detail(push_page(initial_doc(documentContent={"content": "$3f"})))


# --- HTML -> text -----------------------------------------------------------------------


def test_html_to_text() -> None:
    html = """<html><head><title>x</title></head><body><!-- note -->
      <table><tr><td><p><strong>CHÍNH PHỦ</strong></p><p>Số: 1/2020/NĐ-CP</p></td>
      <td>Hà Nội,
      ngày 1 tháng 1 năm 2020</td></tr></table>
      <p class="prov-chapter"><strong>Chương I<br>QUY ĐỊNH CHUNG</strong></p>
      <p class="prov-article"><strong>Điều 1.</strong> <strong>Phạm vi</strong></p>
      <script>var x = "<p>no</p>";</script>
      <div><span>a)</span> <em>điểm</em>&nbsp;a</div></body></html>"""
    lines = [line.strip() for line in html_to_text(html).splitlines() if line.strip()]
    assert lines == [
        "CHÍNH PHỦ",
        "Số: 1/2020/NĐ-CP",
        "Hà Nội, ngày 1 tháng 1 năm 2020",
        "Chương I",
        "QUY ĐỊNH CHUNG",
        "Điều 1. Phạm vi",
        "a) điểm a",  # NBSP is HTML whitespace too
    ]


# --- Status mapping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "name", "expected"),
    [
        ("CHL", "Còn hiệu lực", ("con_hieu_luc", "Còn hiệu lực")),
        ("CCHL", "Chưa có hiệu lực", ("chua_co_hieu_luc", "Chưa có hiệu lực")),
        ("HHL", "Hết hiệu lực toàn bộ", ("het_hieu_luc_toan_bo", "Hết hiệu lực toàn bộ")),
        ("HHL1P", "Hết hiệu lực một phần", ("het_hieu_luc_mot_phan", "Hết hiệu lực một phần")),
        (
            "HHL1P3",
            "Văn bản bị sửa đổi, bổ sung một phần",
            ("het_hieu_luc_mot_phan", "Văn bản bị sửa đổi, bổ sung một phần (HHL1P3)"),
        ),
        (
            "TNHL",
            "Văn bản bị tạm ngưng hiệu lực",
            ("khong_xac_dinh", "Văn bản bị tạm ngưng hiệu lực (TNHL)"),
        ),
        (None, None, ("khong_xac_dinh", None)),
    ],
)
def test_map_status(code: str | None, name: str | None, expected: tuple[str, str | None]) -> None:
    assert map_status(code, name) == expected


def test_unknown_status_code_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert map_status("XYZ", "Lạ") == ("khong_xac_dinh", "Lạ (XYZ)")
    assert "XYZ" in caplog.text
    assert map_status("XYZ", None) == ("khong_xac_dinh", "XYZ")


# --- Sitemaps and URLs ------------------------------------------------------------------


def test_sitemap_index_groups() -> None:
    files = parse_sitemap_index(page("sitemap_index.xml"))
    assert len(files) == 37
    assert files[0].scope is None  # static pages
    central = [f.url for f in files if f.scope == "trung_uong"]
    local = [f.url for f in files if f.scope == "dia_phuong"]
    assert central == [f"https://vbpl.vn/sitemap/{n}.xml" for n in range(1, 13)]
    assert local == [f"https://vbpl.vn/sitemap/{n}.xml" for n in range(13, 37)]


def test_sitemap_entries() -> None:
    refs = parse_sitemap(page("sitemap_1_head.xml"))
    assert len(refs) == 20
    assert refs[0].source_id == "b9f40250-d5d6-11f1-8046-8b7376d665cf"
    assert refs[0].lastmod == date(2026, 11, 1)
    assert refs[0].doc_type_guess == "Thông tư"
    assert all("/en/" not in r.url for r in refs)  # hreflang alternates ignored
    lastmods = [r.lastmod for r in refs]
    assert lastmods == sorted(lastmods, reverse=True)  # type: ignore[type-var]


def test_sitemap_skips_non_documents() -> None:
    xml = """<urlset><url><loc>https://vbpl.vn/gioi-thieu</loc></url>
      <url><loc>https://vbpl.vn/van-ban/chi-tiet/a-b--12?x=1&amp;y=2</loc></url>
      <url><loc>https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-103--vbpqta_1889</loc>
      <lastmod>bad</lastmod></url></urlset>"""
    refs = parse_sitemap(xml)
    assert [(r.source_id, r.lastmod) for r in refs] == [("12", None), ("vbpqta_1889", None)]
    assert refs[0].url.endswith("?x=1&y=2")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://vbpl.vn/van-ban/chi-tiet/luat-so-35-2013-qh13--32515", "32515"),
        (
            "https://vbpl.vn/van-ban/chi-tiet/x--1391cba0-bd3e-11f1-b132-15d48a73cb01/",
            "1391cba0-bd3e-11f1-b132-15d48a73cb01",
        ),  # noqa: E501
        ("https://vbpl.vn/van-ban/chi-tiet/a--b--77?tabs=1", "77"),
        ("https://vbpl.vn/van-ban/trung-uong", None),
        ("https://vbpl.vn/van-ban/chi-tiet/x--", None),
    ],
)
def test_source_id_from_url(url: str, expected: str | None) -> None:
    assert source_id_from_url(url) == expected


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("luat-hoa-giai-o-co-so--1", "Luật"),
        ("bo-luat-dan-su--1", "Bộ luật"),
        ("thong-tu-lien-tich-so-1--1", "Thông tư liên tịch"),
        ("thong-tu-so-1--1", "Thông tư"),
        ("phap-lenh-so-1--1", "Pháp lệnh"),
        ("lenh-cong-bo--1", "Lệnh"),
        ("luatxyz--1", None),
        ("cong-van-so-1--1", None),
    ],
)
def test_guess_doc_type(slug: str, expected: str | None) -> None:
    assert guess_doc_type(f"https://vbpl.vn/van-ban/chi-tiet/{slug}") == expected
