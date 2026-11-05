"""Marker extraction and citation building (docs/05-rag-chat.md, "Citations").

The LLM only writes markers `[n]`. Everything a citation shows (link, dates, validity) is
read from the `LegalDocument` behind source `n`, never from the answer text (rule 2).
"""

import logging
import re
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel

from app.ingest.schemas import STATUS_LABELS
from app.rag.retriever import SourceBlock

# The sentence system prompt rule 3 asks for when the sources are not enough.
MODEL_REFUSAL = "Không tìm thấy quy định liên quan trong cơ sở dữ liệu"

# One marker item: "3" or a range "1-3" / "1–3" / "1—3".
_ITEM = r"\d{1,3}(?:\s*[-–—]\s*\d{1,3})?"
# "[1]", "[1, 2]", "[1;2]", "[1-3]", "[1, 3-4]"; "[1][3]" is two markers. Not "[a]",
# "[1.2]" or "[2019]". Numbers beyond the source count are dropped by `build_citations`.
MARKER = re.compile(rf"\[\s*({_ITEM}(?:\s*[,;]\s*{_ITEM})*)\s*\]")
_RANGE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)")
# A wider "range" is not a citation range: only its two ends are kept.
MAX_RANGE = 20
# A marker with the spaces before it, for `strip_markers`.
_MARKER_WITH_SPACE = re.compile(rf"[ \t]*{MARKER.pattern}")
EXCERPT_CHARS = 200
# Sources shown when the model cited none.
UNCITED_TOP = 3

logger = logging.getLogger(__name__)


class Citation(BaseModel):
    n: int
    document_id: uuid.UUID
    title: str
    so_hieu: str | None
    doc_type: str | None
    dieu: str | None
    dieu_title: str | None
    issued_date: date | None
    effective_date: date | None
    status: str
    status_label: str
    source: str
    source_url: str | None
    excerpt: str
    # The model cited nothing: these are the top sources, shown as "Nguồn tham khảo".
    uncited: bool = False


def _numbers(item: str) -> list[int]:
    """`3` -> [3]; `1-3` -> [1, 2, 3]; a reversed or too wide range keeps both ends."""
    if not (match := _RANGE.fullmatch(item.strip())):
        return [int(item)]
    start, end = int(match[1]), int(match[2])
    if start <= end <= start + MAX_RANGE:
        return list(range(start, end + 1))
    return [start, end]


def extract_markers(answer: str) -> list[int]:
    """Source numbers cited in `answer`, without duplicates, in order of appearance."""
    found = (
        number
        for match in MARKER.finditer(answer)
        for item in re.split(r"[,;]", match.group(1))
        for number in _numbers(item)
    )
    return list(dict.fromkeys(found))


def strip_markers(text: str) -> str:
    """`text` without its `[n]` markers (and the space before them)."""
    return _MARKER_WITH_SPACE.sub("", text)


def _normalize(text: str) -> str:
    # NFC: providers may return decomposed diacritics; collapse whitespace and case.
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def is_refusal(answer: str, source_count: int) -> bool:
    """The model said the sources were not enough (system prompt rule 3): the answer
    contains `MODEL_REFUSAL` and cites no valid source `[1..source_count]`. An answer that
    contains the sentence but still cites sources is a partial answer, not a refusal."""
    if _normalize(MODEL_REFUSAL) not in _normalize(answer):
        return False
    return not any(1 <= n <= source_count for n in extract_markers(answer))


def block_excerpt(block: SourceBlock) -> str:
    """Excerpt of the chunk that matched best, without the "Điều X. ..." heading line that
    every part of a split Điều repeats (the citation shows `dieu` / `dieu_title`)."""
    text = block.best_chunk.text
    if block.dieu:
        heading, sep, rest = text.partition("\n")
        if sep and heading.startswith(block.dieu) and rest.strip():
            text = rest
    return excerpt(text)


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def citation_for(n: int, block: SourceBlock, *, uncited: bool = False) -> Citation:
    doc = block.document
    return Citation(
        n=n,
        document_id=doc.id,
        title=doc.title,
        so_hieu=doc.so_hieu,
        doc_type=doc.doc_type,
        dieu=block.dieu,
        dieu_title=block.dieu_title,
        issued_date=doc.issued_date,
        effective_date=doc.effective_date,
        status=doc.status,
        status_label=STATUS_LABELS.get(doc.status, doc.status),
        source=doc.source,
        source_url=doc.source_url,
        excerpt=block_excerpt(block),
        uncited=uncited,
    )


def build_citations(
    markers: Sequence[int], blocks: Sequence[SourceBlock], *, refused: bool = False
) -> list[Citation]:
    """One citation per valid marker (source `[n]` is `blocks[n - 1]`), in marker order.

    Unknown numbers are dropped with a warning. When no valid marker is left and the answer
    is not a refusal (`refused`, see `is_refusal`), the top `UNCITED_TOP` sources are
    returned with `uncited=True`."""
    citations: list[Citation] = []
    invalid: list[int] = []
    for n in dict.fromkeys(markers):
        if 1 <= n <= len(blocks):
            citations.append(citation_for(n, blocks[n - 1]))
        else:
            invalid.append(n)
    if invalid:
        logger.warning("Answer cites unknown sources %s (%d sources)", invalid, len(blocks))
    if not citations and not refused:
        citations = [
            citation_for(n, block, uncited=True)
            for n, block in enumerate(blocks[:UNCITED_TOP], start=1)
        ]
    return citations
