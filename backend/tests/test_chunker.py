from pathlib import Path

import pytest

from app.ingest.chunker import (
    WINDOW_OVERLAP_TOKENS,
    bge_token_counter,
    chunk_document,
    default_token_counter,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chunker" / "luat_mau.txt"
TITLE = "Luật Thử nghiệm chunker"
SO_HIEU = "99/2025/QH15"
MAX = 80


def words(text: str) -> int:
    """Whitespace token counter: tests do not need the bge-m3 tokenizer."""
    return len(text.split())


@pytest.fixture
def chunks():
    text = FIXTURE.read_text(encoding="utf-8")
    return chunk_document(text, title=TITLE, so_hieu=SO_HIEU, count_tokens=words, max_tokens=MAX)


def test_chunk_count_and_order(chunks):
    # preamble, Điều 1, 2, 3 (3 parts), 4, 5, appendix
    assert len(chunks) == 9
    assert [c.ordinal for c in chunks] == list(range(9))
    assert [c.dieu for c in chunks] == [
        None, "Điều 1", "Điều 2", "Điều 3", "Điều 3", "Điều 3", "Điều 4", "Điều 5", None,
    ]  # fmt: skip


def test_preamble(chunks):
    first = chunks[0]
    assert first.dieu is None
    assert first.dieu_title == "Phần mở đầu"
    assert first.chuong is None
    assert first.text.startswith("QUỐC HỘI")
    assert first.text.endswith("Quốc hội ban hành Luật Thử nghiệm chunker.")
    # Heading lines go to the breadcrumb, never into a chunk.
    assert all("NHỮNG QUY ĐỊNH CHUNG" not in c.text for c in chunks)


def test_breadcrumb(chunks):
    by_dieu = {c.dieu: c for c in chunks if c.dieu}
    assert by_dieu["Điều 1"].chuong == "Chương I. NHỮNG QUY ĐỊNH CHUNG"
    assert by_dieu["Điều 1"].muc is None
    assert by_dieu["Điều 3"].chuong == "Chương II. QUYỀN VÀ NGHĨA VỤ"
    assert by_dieu["Điều 3"].muc == "Mục 1. QUYỀN CỦA CÁ NHÂN"
    assert by_dieu["Điều 5"].muc == "Mục 2. NGHĨA VỤ CỦA CÁ NHÂN"
    assert by_dieu["Điều 1"].dieu_title == "Phạm vi điều chỉnh"


def test_short_articles_are_not_cut(chunks):
    dieu2 = next(c for c in chunks if c.dieu == "Điều 2")
    assert dieu2.khoan is None
    assert dieu2.text == (
        "Điều 2. Đối tượng áp dụng\n1. Cơ quan nhà nước.\n2. Tổ chức, cá nhân có liên quan."
    )
    # Signature and "Phụ lục kèm theo ..." stay with the last Điều (not an appendix heading).
    dieu5 = next(c for c in chunks if c.dieu == "Điều 5")
    assert "Phụ lục kèm theo Luật này" in dieu5.text
    assert dieu5.text.endswith("Nguyễn Văn A")


def test_long_article_split_by_khoan(chunks):
    parts = [c for c in chunks if c.dieu == "Điều 3"]
    assert [p.khoan for p in parts] == ["Khoản 1", "Khoản 2-3", "Khoản 4-5"]
    for part in parts:
        assert part.text.startswith("Điều 3. Giải thích từ ngữ\n")
        assert part.token_count <= MAX
    # The intro goes with khoản 1; points a), b) stay with their khoản.
    assert "Trong Luật này, các từ ngữ" in parts[0].text
    assert "a) Chunk phải giữ" in parts[1].text and "b) Chunk không" in parts[1].text
    # Every body line is in exactly one part, nothing lost or duplicated.
    body = FIXTURE.read_text(encoding="utf-8").split("Điều 3. Giải thích từ ngữ\n")[1]
    body_lines = body.split("Điều 4.")[0].strip().splitlines()
    got = [line for p in parts for line in p.text.splitlines()[1:]]
    assert got == body_lines


def test_appendix(chunks):
    last = chunks[-1]
    assert last.dieu is None and last.dieu_title == "Phụ lục"
    assert last.chuong is None and last.muc is None
    assert last.text.startswith("PHỤ LỤC I\nDANH MỤC MẪU THỬ")


def test_embed_text(chunks):
    dieu4 = next(c for c in chunks if c.dieu == "Điều 4")
    assert dieu4.embed_text == (
        "Luật Thử nghiệm chunker (99/2025/QH15)\n"
        "Chương II. QUYỀN VÀ NGHĨA VỤ > Mục 1. QUYỀN CỦA CÁ NHÂN\n"
        "Điều 4. Quyền được thông tin\n"
        "Cá nhân có quyền được thông tin về văn bản pháp luật."
    )
    assert chunks[0].embed_text.startswith(f"{TITLE} ({SO_HIEU})\nPhần mở đầu\nQUỐC HỘI")


def test_embed_text_skips_missing_lines():
    [chunk] = chunk_document("Điều 1. Phạm vi\nNội dung.", count_tokens=words)
    assert chunk.embed_text == "Điều 1. Phạm vi\nNội dung."
    [chunk] = chunk_document(
        "Điều 1. Phạm vi\nNội dung.", so_hieu="1/2025/NĐ-CP", count_tokens=words
    )
    assert chunk.embed_text == "1/2025/NĐ-CP\nĐiều 1. Phạm vi\nNội dung."


def test_heading_variants():
    text = (
        "PHẦN THỨ NHẤT\nQUY ĐỊNH CHUNG\nCHƯƠNG I: PHẠM VI\nĐiều 1: Phạm vi\nNội dung.\n"
        "Mục 2 Chương này quy định chi tiết.\nĐiều 2. Khác\nNội dung."
    )
    chunks = chunk_document(text, count_tokens=words)
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2"]
    assert chunks[0].phan == "PHẦN THỨ NHẤT. QUY ĐỊNH CHUNG"
    assert chunks[0].chuong == "CHƯƠNG I. PHẠM VI"
    assert chunks[0].dieu_title == "Phạm vi"
    # Body line starting with "Mục 2" is not a heading.
    assert chunks[0].muc is None
    assert "Mục 2 Chương này" in chunks[0].text


def test_new_chuong_resets_muc():
    text = "Chương I\nA\nMục 1\nB\nĐiều 1. X\nx\nChương II\nC\nĐiều 2. Y\ny"
    chunks = chunk_document(text, count_tokens=words)
    assert (chunks[0].chuong, chunks[0].muc) == ("Chương I. A", "Mục 1. B")
    assert (chunks[1].chuong, chunks[1].muc) == ("Chương II. C", None)


def test_khoan_numbers_must_follow_sequence():
    # "2019. " is not khoản 2, so it stays inside khoản 1.
    text = "Điều 1. X\n1. " + "a " * 30 + "\n2019. năm\n2. " + "b " * 30 + "\n3. " + "c " * 30
    parts = chunk_document(text, count_tokens=words, max_tokens=50)
    assert [p.khoan for p in parts] == ["Khoản 1", "Khoản 2", "Khoản 3"]
    assert "2019. năm" in parts[0].text


def test_single_khoan_too_long_is_windowed():
    long_khoan = "1. " + " ".join(f"w{i}" for i in range(300))
    text = f"Điều 7. Dài\n{long_khoan}\n2. ngắn"
    parts = chunk_document(text, count_tokens=words, max_tokens=120)
    assert all(p.text.startswith("Điều 7. Dài\n") for p in parts)
    assert {p.khoan for p in parts[:-1]} == {"Khoản 1"}
    assert parts[-1].khoan == "Khoản 2"
    assert len(parts) >= 4


def test_unstructured_text_falls_back_to_windows():
    paragraphs = [" ".join(f"p{i}w{j}" for j in range(40)) for i in range(40)]  # 1600 words
    chunks = chunk_document("\n".join(paragraphs), count_tokens=words)
    assert len(chunks) > 3
    for chunk in chunks:
        assert chunk.dieu is None and chunk.dieu_title is None and chunk.khoan is None
        assert chunk.token_count <= 500 + WINDOW_OVERLAP_TOKENS
    # Consecutive windows overlap, and together they cover every word in order.
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        assert cur.text.split()[0] in prev.text.split()
    seen: list[str] = []
    for chunk in chunks:
        for word in chunk.text.split():
            if word not in seen:
                seen.append(word)
    assert seen == " ".join(paragraphs).split()


def test_long_paragraph_windows_overlap_and_keep_text():
    text = " ".join(f"w{i}" for i in range(1200))  # one line, no Điều
    chunks = chunk_document(text, count_tokens=words)
    assert len(chunks) >= 3
    assert "\n" not in chunks[0].text  # word groups of one paragraph are joined by spaces
    first, second = chunks[0].text.split(), chunks[1].text.split()
    overlap = len(set(first) & set(second))
    assert 0 < overlap <= WINDOW_OVERLAP_TOKENS


def test_short_unstructured_text_is_one_chunk():
    [chunk] = chunk_document("Công văn số 1\nNội dung ngắn.", count_tokens=words)
    assert chunk.text == "Công văn số 1\nNội dung ngắn."


def test_empty_text():
    assert chunk_document("", count_tokens=words) == []


def test_article_on_one_huge_line_is_kept():
    text = "Điều 1. " + "x " * 900
    chunks = chunk_document(text, count_tokens=words)
    assert len(chunks) >= 2
    assert {c.dieu for c in chunks} == {"Điều 1"}
    assert chunks[0].dieu_title is None  # the "title" was the whole article
    assert all(c.token_count <= 800 for c in chunks)
    assert sum(c.text.count("x") for c in chunks) >= 900


def test_bare_form_label_in_article_is_not_appendix():
    text = "Điều 1. A\nHồ sơ gồm:\nMẫu số 01\nĐiều 2. B\nbody2"
    chunks = chunk_document(text, count_tokens=words)
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2"]
    assert "Mẫu số 01" in chunks[0].text


def test_bare_appendix_label_with_title_starts_appendix():
    text = "Điều 1. A\nx\nPhụ lục II\n(Kèm theo Nghị định số 1/2025/NĐ-CP)\nnội dung"
    last = chunk_document(text, count_tokens=words)[-1]
    assert last.dieu_title == "Phụ lục"
    assert last.text.startswith("Phụ lục II\n(Kèm theo")


def test_forms_in_appendix_numbering_their_own_dieu():
    # A contract template inside the appendix: its "Điều 1." is not the next article.
    text = "Điều 1. A\nx\nĐiều 2. B\ny\nPHỤ LỤC I\nHỢP ĐỒNG MẪU\nĐiều 1. Đối tượng\nz"
    chunks = chunk_document(text, count_tokens=words)
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2", None]
    assert chunks[-1].text.endswith("Điều 1. Đối tượng\nz")


def test_wrapped_dieu_reference_does_not_cut_article():
    text = "Điều 1. A\nTheo quy định tại\nĐiều 5.\nhết câu.\nĐiều 2. B\ny"
    chunks = chunk_document(text, count_tokens=words)
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2"]
    assert "Điều 5.\nhết câu." in chunks[0].text


def test_dieu_numbering():
    text = "Điều 1.\nPhạm vi\nĐiều 2.\nx\nĐiều 2a. Mới\ny\nĐiều 9. Hiệu lực\nz\nĐiều 3 của Luật\nw"
    chunks = chunk_document(text, count_tokens=words)
    # Bare next number is fine, letter suffix too, a jump needs a title, never backwards.
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2", "Điều 2a", "Điều 9"]


def test_heading_title_over_several_lines():
    text = (
        "Chương I\nNHỮNG QUY ĐỊNH\nCHUNG\nĐiều 1. X\nx\nCHƯƠNG II: QUYỀN\nVÀ NGHĨA VỤ\nĐiều 2. Y\ny"
    )
    chunks = chunk_document(text, count_tokens=words)
    assert [c.dieu for c in chunks] == ["Điều 1", "Điều 2"]
    assert chunks[0].chuong == "Chương I. NHỮNG QUY ĐỊNH CHUNG"
    assert chunks[1].chuong == "CHƯƠNG II. QUYỀN VÀ NGHĨA VỤ"


def test_embed_text_does_not_repeat_variant_heading():
    [chunk] = chunk_document("ĐIỀU 1: PHẠM VI\nNội dung.", count_tokens=words)
    assert chunk.embed_text == "ĐIỀU 1: PHẠM VI\nNội dung."


def test_windows_respect_max_tokens():
    paragraphs = [" ".join(f"p{i}w{j}" for j in range(20)) for i in range(60)]
    chunks = chunk_document("\n".join(paragraphs), count_tokens=words, max_tokens=100)
    assert len(chunks) > 10
    assert all(c.token_count <= 100 for c in chunks)


def test_windows_cut_between_paragraphs():
    # 150-word paragraphs fit whole in a 500-token window: never cut inside one.
    paragraphs = [" ".join(f"p{i}w{j}" for j in range(150)) for i in range(10)]
    chunks = chunk_document("\n".join(paragraphs), count_tokens=words)
    for chunk in chunks:
        lines = chunk.text.split("\n")
        assert all(line in paragraphs for line in lines[1:])
        assert lines[-1] in paragraphs
    # The overlap is the tail of the previous paragraph.
    assert chunks[1].text.split("\n")[0] == " ".join(f"p2w{j}" for j in range(100, 150))


def test_counts_tokens_once_per_short_block():
    calls: list[str] = []

    def counting(text: str) -> int:
        calls.append(text)
        return words(text)

    chunk_document("Điều 1. A\nx\nĐiều 2. B\ny", count_tokens=counting)
    assert len(calls) == 2


def test_default_counter_needs_no_download_with_fake_backend():
    # conftest sets EMBEDDING_BACKEND=fake.
    assert default_token_counter()("Điều 1. Phạm vi") == 4


@pytest.mark.slow
def test_bge_token_counter():
    count = bge_token_counter()
    assert 0 < count("Điều 1. Phạm vi điều chỉnh") < 20
    text = FIXTURE.read_text(encoding="utf-8")
    chunks = chunk_document(text, title=TITLE, so_hieu=SO_HIEU)
    assert [c.dieu for c in chunks][1:6] == ["Điều 1", "Điều 2", "Điều 3", "Điều 4", "Điều 5"]
