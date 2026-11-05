import logging
import unicodedata
import uuid
from datetime import date

import pytest

from app.db.models import LegalChunk, LegalDocument
from app.rag.citations import (
    MODEL_REFUSAL,
    UNCITED_TOP,
    build_citations,
    excerpt,
    extract_markers,
    is_refusal,
    strip_markers,
)
from app.rag.prompt import (
    MAX_HISTORY_MESSAGES,
    SYSTEM_PROMPT,
    TRUNCATED,
    ChatMessage,
    _cut,
    build_prompt,
    estimate_tokens,
    format_source,
    retrieval_query,
)
from app.rag.retriever import SourceBlock


def make_doc(**overrides) -> LegalDocument:
    values = dict(
        id=uuid.uuid4(),
        source="vbpl",
        title="Luật Lao động",
        so_hieu="45/2019/QH14",
        doc_type="Luật",
        issued_date=date(2019, 11, 20),
        effective_date=date(2021, 1, 1),
        status="con_hieu_luc",
        source_url="https://vbpl.vn/doc/1",
    )
    values.update(overrides)
    return LegalDocument(**values)


def make_block(
    text: str = "Điều 5. Quyền\nNgười lao động có quyền làm việc.",
    *,
    doc: LegalDocument | None = None,
    dieu: str | None = "Điều 5",
    dieu_title: str | None = "Quyền và nghĩa vụ của người lao động",
) -> SourceBlock:
    doc = doc or make_doc()
    chunk = LegalChunk(
        id=uuid.uuid4(), document_id=doc.id, ordinal=0, dieu=dieu, dieu_title=dieu_title, text=text
    )
    return SourceBlock(doc, [chunk])


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


# Simple counter for exact budget tests: one token per word.
def count_words(text: str) -> int:
    return len(text.split())


# --- extract_markers ---


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Người lao động có quyền [1].", [1]),
        ("Theo quy định [1, 2] và [2,3].", [1, 2, 3]),
        ("Ý một [1][3]. Ý hai [3].", [1, 3]),
        ("Ý [2] rồi [1] rồi [2].", [2, 1]),
        ("Khoảng trắng [ 4 ,5 ] và [6;7].", [4, 5, 6, 7]),
        ("Không có marker nào.", []),
        ("Không phải marker: [a], [1.2], [], [1,].", []),
        ("Marker không tồn tại [99] vẫn được trích ra.", [99]),
        ("Khoảng [1-3] và [5–6].", [1, 2, 3, 5, 6]),
        ("Trộn [1, 3—4] và [ 2 - 2 ].", [1, 3, 4, 2]),
        ("Ngược [3-1], quá rộng [1-500].", [3, 1, 500]),
        ("Không phải marker: [2019], [1-], [-2], [2019-2020].", []),
    ],
)
def test_extract_markers(answer: str, expected: list[int]) -> None:
    assert extract_markers(answer) == expected


# --- build_citations ---


def test_build_citations_from_db_metadata() -> None:
    doc = make_doc()
    blocks = [make_block(doc=doc), make_block(dieu="Điều 13", dieu_title="Hợp đồng lao động")]
    citations = build_citations([2, 1], blocks)
    assert [c.n for c in citations] == [2, 1]
    first = citations[1].model_dump(mode="json")
    assert first == {
        "n": 1,
        "document_id": str(doc.id),
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
        "source_url": "https://vbpl.vn/doc/1",
        "excerpt": "Người lao động có quyền làm việc.",
        "uncited": False,
    }
    assert citations[0].dieu == "Điều 13"


def test_build_citations_ignores_unknown_markers(caplog: pytest.LogCaptureFixture) -> None:
    blocks = [make_block(), make_block()]
    with caplog.at_level(logging.WARNING):
        citations = build_citations([0, 2, 5, 2], blocks)
    assert [c.n for c in citations] == [2]
    assert not citations[0].uncited
    assert "[0, 5]" in caplog.text


def test_build_citations_uncited_top_sources() -> None:
    blocks = [make_block() for _ in range(5)]
    citations = build_citations([], blocks)
    assert [c.n for c in citations] == list(range(1, UNCITED_TOP + 1))
    assert all(c.uncited for c in citations)
    # Only unknown markers: same as none.
    assert [c.n for c in build_citations([9], blocks)] == [1, 2, 3]


def test_build_citations_refusal_has_none() -> None:
    assert build_citations([], [make_block()], refused=True) == []
    assert build_citations([], []) == []


def test_build_citations_user_upload_without_url() -> None:
    doc = make_doc(source="user_upload", owner_id=uuid.uuid4(), source_url=None, so_hieu=None)
    (citation,) = build_citations([1], [make_block(doc=doc)])
    assert citation.source == "user_upload"
    assert citation.source_url is None


def test_is_refusal() -> None:
    assert is_refusal("Không tìm thấy quy định liên quan trong cơ sở dữ liệu.", 3)
    assert is_refusal("Rất tiếc, không tìm thấy quy định liên quan trong cơ sở dữ liệu.", 3)
    assert not is_refusal("Người lao động có quyền làm việc [1].", 3)


def test_is_refusal_partial_answer_with_valid_marker_is_not_refusal() -> None:
    answer = f"{MODEL_REFUSAL} về mức phạt, nhưng nguyên tắc chung là bồi thường [2]."
    assert not is_refusal(answer, 3)
    # A marker beyond the sources does not count.
    assert is_refusal(f"{MODEL_REFUSAL} [7].", 3)


def test_is_refusal_decomposed_diacritics_and_spacing() -> None:
    nfd = unicodedata.normalize("NFD", f"{MODEL_REFUSAL}.")
    assert nfd != f"{MODEL_REFUSAL}."
    assert is_refusal(nfd, 3)
    assert is_refusal(MODEL_REFUSAL.upper().replace(" ", "\n  "), 3)


def test_strip_markers() -> None:
    assert strip_markers("Được nghỉ 12 ngày [3]. Theo [1, 2-4] và [5][6].") == (
        "Được nghỉ 12 ngày. Theo và."
    )
    assert strip_markers("Năm [2019] giữ nguyên.") == "Năm [2019] giữ nguyên."


def test_system_prompt_uses_refusal_sentence() -> None:
    assert f'"{MODEL_REFUSAL}."' in SYSTEM_PROMPT


def test_excerpt_from_best_chunk_without_heading() -> None:
    doc = make_doc()
    first, matched = (
        LegalChunk(
            id=uuid.uuid4(),
            document_id=doc.id,
            ordinal=i,
            dieu="Điều 5",
            dieu_title="Quyền",
            text=f"Điều 5. Quyền\n{body}",
        )
        for i, body in enumerate(["Khoản 1 về việc làm.", "Khoản 4 về nghỉ phép năm."])
    )
    block = SourceBlock(doc, [first, matched], best=matched)
    (citation,) = build_citations([1], [block])
    assert citation.excerpt == "Khoản 4 về nghỉ phép năm."
    # Without a known best chunk: the first one. A chunk without Điều keeps its first line.
    assert build_citations([1], [SourceBlock(doc, [first])])[0].excerpt == "Khoản 1 về việc làm."
    preamble = make_block("QUỐC HỘI\nLuật này quy định...", dieu=None)
    assert build_citations([1], [preamble])[0].excerpt == "QUỐC HỘI Luật này quy định..."


def test_excerpt() -> None:
    assert excerpt("a\n  b") == "a b"
    long = excerpt("x" * 500)
    assert len(long) == 201 and long.endswith("…")


# --- prompt ---


def test_format_source() -> None:
    block = make_block()
    assert format_source(1, block) == (
        "[1] Luật Lao động (45/2019/QH14) — Điều 5. Quyền và nghĩa vụ của người lao động\n"
        "Tình trạng: Còn hiệu lực | Nguồn: vbpl.vn\n"
        "---\n"
        "Điều 5. Quyền\nNgười lao động có quyền làm việc."
    )
    upload = make_block(
        doc=make_doc(source="user_upload", so_hieu=None, status="het_hieu_luc_mot_phan"),
        dieu=None,
        dieu_title="Phần mở đầu",
    )
    assert format_source(3, upload).startswith(
        "[3] Luật Lao động — Phần mở đầu\n"
        "Tình trạng: Hết hiệu lực một phần | Nguồn: tài liệu người dùng tải lên\n"
    )


def test_build_prompt_structure() -> None:
    blocks = [make_block(), make_block(dieu="Điều 13")]
    history = [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(10)
    ]
    prompt = build_prompt("Quyền của người lao động?", blocks, history, max_context_tokens=100_000)
    assert prompt.messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    turns = prompt.messages[1:-1]
    assert [m["content"] for m in turns] == [f"m{i}" for i in range(4, 10)]
    assert len(turns) == MAX_HISTORY_MESSAGES
    last = prompt.messages[-1]
    assert last["role"] == "user"
    assert last["content"].startswith("NGUỒN:\n\n[1] Luật Lao động")
    assert "\n\n[2] Luật Lao động (45/2019/QH14) — Điều 13." in last["content"]
    assert last["content"].endswith("CÂU HỎI: Quyền của người lao động?")
    assert prompt.blocks == blocks
    assert prompt.blocks_dropped == 0 and not prompt.truncated
    assert prompt.history_dropped == 4  # beyond the last 6


def _fixed_cost(question: str) -> int:
    """Tokens of a prompt without history and sources, with `count_words`."""
    return build_prompt(question, [], max_context_tokens=10**6, count_tokens=count_words).tokens


def exchange(i: int, n: int = 20) -> list[ChatMessage]:
    return [
        ChatMessage(role="user", content=words(n, f"q{i}_")),
        ChatMessage(role="assistant", content=words(n, f"a{i}_")),
    ]


def test_build_prompt_drops_history_before_sources() -> None:
    blocks = [make_block(words(50, "a")), make_block(words(50, "b"))]
    history = [m for i in range(3) for m in exchange(i)]
    full = build_prompt("q", blocks, history, max_context_tokens=10**6, count_tokens=count_words)
    # Each exchange costs 48; room for everything but ~1.25 exchanges: two oldest go.
    max_tokens = int((full.tokens - 60) / 0.7) + 1
    prompt = build_prompt(
        "q", blocks, history, max_context_tokens=max_tokens, count_tokens=count_words
    )
    assert prompt.history_dropped == 4
    assert prompt.messages[1:-1] == history[4:]
    assert prompt.blocks == blocks
    assert prompt.tokens <= int(max_tokens * 0.7)


def test_build_prompt_drops_last_sources_after_history() -> None:
    blocks = [make_block(words(100, p)) for p in "abc"]
    history = exchange(0, 15)
    full = build_prompt("q", blocks, history, max_context_tokens=10**6, count_tokens=count_words)
    # No history fits, and one source too many.
    max_tokens = int((full.tokens - 140) / 0.7) + 1
    prompt = build_prompt(
        "q", blocks, history, max_context_tokens=max_tokens, count_tokens=count_words
    )
    assert prompt.history_dropped == 2
    assert len(prompt.messages) == 2
    assert prompt.blocks == blocks[:2]
    assert prompt.blocks_dropped == 1
    content = prompt.messages[-1]["content"]
    assert "[2] " in content and "[3] " not in content and "c0" not in content
    assert prompt.tokens <= int(max_tokens * 0.7)


def roles(messages: list[ChatMessage]) -> list[str]:
    return [m["role"] for m in messages]


def test_build_prompt_history_strips_old_markers() -> None:
    history = [
        ChatMessage(role="user", content="Nghỉ phép năm bao nhiêu ngày?"),
        ChatMessage(role="assistant", content="Người lao động được nghỉ 12 ngày [3]."),
    ]
    prompt = build_prompt("Thế còn thai sản?", [make_block()], history, max_context_tokens=10**5)
    assert prompt.messages[2] == {
        "role": "assistant",
        "content": "Người lao động được nghỉ 12 ngày.",
    }


@pytest.mark.parametrize(
    "history_roles",
    [
        # Window cut in the middle of an exchange.
        ["user", "assistant"] * 4 + ["assistant"],
        # Previous question refused without an answer saved.
        ["user", "assistant", "user"],
        ["assistant", "user", "user", "assistant", "system", "user"],
        ["user", "user", "user"],
    ],
)
def test_build_prompt_roles_alternate(history_roles: list[str]) -> None:
    history = [ChatMessage(role=r, content=f"m{i}") for i, r in enumerate(history_roles)]  # type: ignore[typeddict-item]
    prompt = build_prompt("q", [make_block()], history, max_context_tokens=10**5)
    got = roles(prompt.messages)
    assert got[0] == "system" and got[-1] == "user"
    turns = got[1:-1]
    assert turns == ["user", "assistant"] * (len(turns) // 2)
    assert len(turns) <= MAX_HISTORY_MESSAGES
    assert prompt.history_dropped == len(history) - len(turns)


def test_build_prompt_history_dropped_counts_filtered_messages() -> None:
    history = [
        *exchange(0, 2),
        ChatMessage(role="system", content="x"),
        ChatMessage(role="user", content=""),
        *exchange(1, 2),
    ]
    prompt = build_prompt("q", [make_block()], history, max_context_tokens=10**5)
    assert prompt.messages[1:-1] == [*exchange(0, 2), *exchange(1, 2)]
    assert prompt.history_dropped == 2


def test_build_prompt_cuts_best_source_when_alone_too_long() -> None:
    blocks = [make_block(words(1000, "a")), make_block(words(10, "b"))]
    max_tokens = int((_fixed_cost("q") + 200) / 0.7)
    prompt = build_prompt("q", blocks, max_context_tokens=max_tokens, count_tokens=count_words)
    assert prompt.blocks == blocks[:1]
    assert prompt.truncated
    content = prompt.messages[-1]["content"]
    assert content.count("[1] ") == 1 and "b0" not in content
    assert TRUNCATED.strip() in content
    assert "a100 " in content and "a999" not in content
    assert prompt.tokens <= int(max_tokens * 0.7)


def test_cut_keeps_text_that_fits() -> None:
    text = words(10)
    assert _cut(text, 10, count_words) == (text, False)


def test_cut_ends_on_line_break() -> None:
    # The last whitespace before the cut is a line break, not the space far before it.
    text = "một hai\nba-bốn-năm-sáu-bảy-tám"
    assert _cut(text, len(TRUNCATED) + 12, len) == ("một hai" + TRUNCATED, True)
    assert _cut("x" * 100, 3, len) == ("", False)


def test_build_prompt_not_truncated_when_best_source_fits() -> None:
    # Count "\n\n" as 1 token: parts of a source cost more than the joined source.
    def count(text: str) -> int:
        return count_words(text) + text.count("\n\n")

    blocks = [make_block(words(100, "a"))]
    full = build_prompt("q", blocks, max_context_tokens=10**6, count_tokens=count)
    prompt = build_prompt(
        "q", blocks, max_context_tokens=int(full.tokens / 0.7) + 1, count_tokens=count
    )
    assert prompt.blocks == blocks
    assert not prompt.truncated
    assert TRUNCATED not in prompt.messages[-1]["content"]


def test_estimate_tokens() -> None:
    assert estimate_tokens("abcd") == 2
    # Vietnamese letters (precomposed or decomposed) count as Latin.
    assert estimate_tokens("người lao động") == 7
    assert estimate_tokens(unicodedata.normalize("NFD", "ạ")) == 1
    # Other scripts count one token per character.
    assert estimate_tokens("劳动法") == 3
    assert estimate_tokens("ab😀") == 2


def test_build_prompt_default_estimate_respects_budget() -> None:
    blocks = [make_block("Người lao động có quyền làm việc. " * 300) for _ in range(8)]
    prompt = build_prompt("Quyền làm việc?", blocks, max_context_tokens=8000)
    assert 0 < len(prompt.blocks) < 8
    assert prompt.tokens <= 8000 * 0.7


def test_retrieval_query() -> None:
    previous = "Người lao động được nghỉ phép năm bao nhiêu ngày?"
    assert (
        retrieval_query("Thế còn trường hợp kia?", previous)
        == f"{previous}\nThế còn trường hợp kia?"
    )
    long_question = "Người sử dụng lao động có được đơn phương chấm dứt hợp đồng không?"
    assert retrieval_query(long_question, previous) == long_question
    assert retrieval_query("Ngắn?", None) == "Ngắn?"
