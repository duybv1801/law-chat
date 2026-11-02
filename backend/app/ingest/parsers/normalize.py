"""Text normalization shared by every parser (and the vbpl HTML parser).

- Unicode NFC: Vietnamese can be typed with precomposed or combining diacritics; both must
  end up identical, otherwise regexes, hashes and search miss.
- Collapse whitespace inside a line, keep line breaks, keep at most one blank line between
  paragraphs.
- Drop PDF headers / footers that repeat on more than half of the pages.
"""

import re
import unicodedata
from collections import Counter

# Invisible characters that only break matching (zero-width, BOM, soft hyphen).
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SPACES = re.compile(r"\s+")

# Lines kept even when they repeat at a page edge: structure the chunker relies on.
_STRUCTURAL = re.compile(r"^(Phần|Chương|Mục|Điều)\s|^\d+[a-zđ]?[.)]\s|^[a-zđ]\)\s", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+")
MAX_WORDS_PAGE_NUMBER = 2
EDGE_LINES = 3
MIN_PAGES_FOR_REPEATS = 3


def normalize_line(line: str) -> str:
    # Drop invisible and control characters before NFC (they block composition) and before
    # collapsing spaces (dropping one between two spaces would leave a double space).
    line = "".join(
        ch for ch in line.translate(_INVISIBLE) if ch.isspace() or unicodedata.category(ch) != "Cc"
    )
    # \s also matches tabs and NBSP.
    return _SPACES.sub(" ", unicodedata.normalize("NFC", line)).strip()


def normalize_text(text: str) -> str:
    """NFC, clean whitespace, at most one blank line between paragraphs. Idempotent."""
    out: list[str] = []
    for raw in unicodedata.normalize("NFC", text).splitlines():
        line = normalize_line(raw)
        if line or (out and out[-1]):
            out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _repeat_key(line: str) -> str | None:
    """Key for comparing edge lines across pages. `None` = never treat as header/footer.

    Digits are masked only in page-number-like lines (at most 2 words, e.g. "Trang 3/10",
    "- 3 -"), so content lines that differ only by a number are not taken as repeats.
    """
    if not line or _STRUCTURAL.match(line):
        return None
    if len(_WORD.findall(line)) <= MAX_WORDS_PAGE_NUMBER:
        return re.sub(r"\d+", "#", line)
    return line


def _edge_indexes(lines: list[str]) -> set[int]:
    filled = [i for i, line in enumerate(lines) if line]
    return set(filled[:EDGE_LINES] + filled[-EDGE_LINES:])


def remove_repeated_lines(pages: list[list[str]]) -> list[list[str]]:
    """Drop lines near the top / bottom of a page that repeat on > 50% of the pages.

    `pages` holds normalized lines per page. Documents with fewer than 3 pages are returned
    unchanged (two pages are not enough to tell a header from content).
    """
    if len(pages) < MIN_PAGES_FOR_REPEATS:
        return pages
    edges = [_edge_indexes(lines) for lines in pages]
    counts: Counter[str] = Counter()
    for lines, edge in zip(pages, edges, strict=True):
        counts.update({key for i in edge if (key := _repeat_key(lines[i]))})
    repeated = {key for key, n in counts.items() if n > len(pages) / 2}
    if not repeated:
        return pages
    return [
        [line for i, line in enumerate(lines) if not (i in edge and _repeat_key(line) in repeated)]
        for lines, edge in zip(pages, edges, strict=True)
    ]
