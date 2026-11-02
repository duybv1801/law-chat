"""Split normalized (NFC) legal text into chunks by Part/Chapter/Section/Article.

- One chunk per Điều; the Phần/Chương/Mục it sits under becomes its breadcrumb. Heading
  lines themselves go into the breadcrumb, not into any chunk text.
- Text before the first heading (national motto, "Căn cứ ...") is one "Phần mở đầu" chunk.
- Điều numbers must go up (see `_accepts_dieu`), so a PDF line that wraps at "Điều 5." in
  the middle of a sentence does not start a new article.
- A Điều longer than `MAX_CHUNK_TOKENS` is split by khoản: consecutive khoản are grouped
  until full, each part starts with the Điều heading line again.
- "Phụ lục" / forms after the last Điều become "Phụ lục" chunks.
- Text without any Điều (công văn, messy uploads) and over-long blocks fall back to
  windows of `WINDOW_TOKENS` tokens overlapping by `WINDOW_OVERLAP_TOKENS`.

Tokens are counted with the bge-m3 tokenizer by default (`default_token_counter`); callers
and tests can pass any `count_tokens` function.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Literal

TokenCounter = Callable[[str], int]

MAX_CHUNK_TOKENS = 800
WINDOW_TOKENS = 500
WINDOW_OVERLAP_TOKENS = 50

PREAMBLE_TITLE = "Phần mở đầu"
APPENDIX_TITLE = "Phụ lục"

# Headings, matched at the start of a line. Case-insensitive: "PHẦN THỨ NHẤT", "CHƯƠNG I".
# Group 1 is the label ("Phần thứ nhất", "Chương II", "Mục 1"), group 2 the rest of the line.
PHAN = re.compile(r"^(Phần\s+(?:thứ\s+\w+|[IVXLC]+))\b(.*)$", re.IGNORECASE)
CHUONG = re.compile(r"^(Chương\s+[IVXLC\d]+)\b(.*)$", re.IGNORECASE)
MUC = re.compile(r"^(Mục\s+\d+)\b(.*)$", re.IGNORECASE)
# "Điều 5. Title" (also "Điều 5:" in older documents). Group 1 = number, group 2 = title.
DIEU = re.compile(r"^Điều\s+(\d+[a-zđ]?)\s*[.:]\s*(.*)$", re.IGNORECASE)
# Only used inside one Điều.
KHOAN = re.compile(r"^(\d+)\.\s+")
# Appendix heading (only after the first Điều): an all-caps line such as "PHỤ LỤC I DANH
# MỤC ...", or the bare label "Phụ lục II" / "Mẫu số 01/TK" (not "Phụ lục kèm theo ...").
APPENDIX = re.compile(r"^(Phụ lục|Mẫu số|Biểu mẫu)\b", re.IGNORECASE)
APPENDIX_LABEL = re.compile(
    r"^(Phụ lục|Mẫu|Biểu mẫu)(\s+số)?(\s+[IVXLC\d][\w./-]*)?$", re.IGNORECASE
)
# Line under a bare appendix label: "(Kèm theo Nghị định số ...)".
ATTACHED_TO = re.compile(r"^\((ban hành )?kèm theo\b", re.IGNORECASE)

HeadingKind = Literal["phan", "chuong", "muc"]
BlockKind = Literal["preamble", "article", "appendix", "other"]


@dataclass
class Chunk:
    """One row of `legal_chunks` (without ids)."""

    ordinal: int
    text: str
    embed_text: str
    token_count: int
    phan: str | None = None
    chuong: str | None = None
    muc: str | None = None
    dieu: str | None = None
    dieu_title: str | None = None
    khoan: str | None = None


@dataclass
class _Block:
    kind: BlockKind
    phan: str | None = None
    chuong: str | None = None
    muc: str | None = None
    dieu: str | None = None
    dieu_title: str | None = None
    lines: list[str] = field(default_factory=list)  # for an article, lines[0] is the heading


@dataclass
class _Unit:
    """A piece of text for window packing; `newline` = joined to the previous by "\\n"."""

    text: str
    tokens: int
    newline: bool = True


@cache
def bge_token_counter() -> TokenCounter:
    """Token counter using the tokenizer of the embedding model (only `tokenizer.json` is
    downloaded, into the HuggingFace cache; torch is not imported)."""
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    from app.core.config import settings

    tokenizer = Tokenizer.from_file(hf_hub_download(settings.embedding_model, "tokenizer.json"))
    tokenizer.no_truncation()
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)


def _word_counter(text: str) -> int:
    return len(text.split())


def default_token_counter() -> TokenCounter:
    """bge-m3 tokenizer, or a word count with `EMBEDDING_BACKEND=fake` (no download).

    The first bge call may download `tokenizer.json`: call this once at startup (in a
    thread) instead of on the first upload.
    """
    from app.core.config import settings

    return _word_counter if settings.embedding_backend == "fake" else bge_token_counter()


def _is_upper(text: str) -> bool:
    return any(ch.isalpha() for ch in text) and text == text.upper()


def _next_line(lines: list[str], i: int) -> tuple[str, int]:
    """First non-empty line from index `i`, and its index (`("", len(lines))` if none)."""
    while i < len(lines) and not lines[i]:
        i += 1
    return (lines[i], i) if i < len(lines) else ("", i)


def _heading(line: str) -> tuple[HeadingKind, str, str] | None:
    """`(kind, label, rest)` for a Phần/Chương/Mục heading line.

    The rest of the line must be empty, start with punctuation or be all caps, so body text
    such as "Mục 2 Chương này quy định ..." is not taken as a heading.
    """
    for kind, pattern in (("phan", PHAN), ("chuong", CHUONG), ("muc", MUC)):
        if match := pattern.match(line):
            rest = match.group(2).strip()
            if not rest or rest[0] in ".:-–" or _is_upper(rest):
                return kind, match.group(1), rest.lstrip(".:-– ")
    return None


def _is_appendix(line: str, nxt: str) -> bool:
    """An all-caps "PHỤ LỤC I ..." line, or a bare label ("Phụ lục II", "Mẫu số 01") followed
    by an all-caps title or "(Kèm theo ...)". A bare label alone in a list of forms inside a
    Điều ("Hồ sơ gồm:" / "Mẫu số 01" / "Điều 2. ...") is body text."""
    if APPENDIX.match(line) and _is_upper(line):
        return True
    return bool(APPENDIX_LABEL.match(line)) and (
        (_is_upper(nxt) and not DIEU.match(nxt)) or bool(ATTACHED_TO.match(nxt))
    )


def _dieu_number(label: str) -> tuple[int, bool]:
    """`("5a")` -> `(5, True)`: the number and whether it has a letter suffix."""
    digits = re.match(r"\d+", label)
    assert digits
    return int(digits.group()), len(digits.group()) < len(label)


def _accepts_dieu(match: re.Match[str], last: int) -> bool:
    """Whether a `DIEU` line opens a new article after article number `last` (0 = none yet).

    The next number always does; "Điều 5a" after "Điều 5" too. A jump forward needs a title
    on the line ("Điều 9. Hiệu lực"), so a bare "Điều 9." wrapped from the middle of a
    sentence ("... quy định tại" / "Điều 9." / "...") stays body text. Going backwards never
    does ("Điều 2 của Luật này" quoted inside Điều 7).
    """
    number, lettered = _dieu_number(match.group(1))
    if number == last + 1 or (lettered and number == last):
        return True
    return number > last and bool(match.group(2).strip())


def _title_lines(lines: list[str], i: int, rest: str) -> tuple[str, int]:
    """Join a heading title with the all-caps lines under it ("Chương I" / "NHỮNG QUY ĐỊNH" /
    "CHUNG"). Returns the title and the index of the first line after it."""
    if rest and not _is_upper(rest):
        return rest, i
    parts = [rest] if rest else []
    while True:
        nxt, j = _next_line(lines, i)
        if not _is_upper(nxt) or _heading(nxt) or DIEU.match(nxt) or APPENDIX.match(nxt):
            return " ".join(parts), i
        parts.append(nxt)
        i = j + 1


def _blocks(lines: list[str]) -> list[_Block]:
    """Group lines into preamble / article / appendix / other blocks with breadcrumbs."""
    blocks: list[_Block] = []
    crumb: dict[HeadingKind, str | None] = {"phan": None, "chuong": None, "muc": None}
    current: _Block | None = None
    seen_heading = seen_article = in_appendix = False
    last_dieu = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        dieu = DIEU.match(line)
        if dieu and not _accepts_dieu(dieu, last_dieu):
            dieu = None
        if in_appendix and dieu and dieu.group(2).strip():
            # The body goes on after a false appendix start (forms inside an appendix that
            # number their own clauses "Điều 1." are not the next article).
            in_appendix = False
        if seen_article and _is_appendix(line, _next_line(lines, i)[0]):
            # Breadcrumbs of the body do not apply to appendices.
            in_appendix = True
            current = _Block("appendix", dieu_title=APPENDIX_TITLE, lines=[line])
            blocks.append(current)
            continue
        if not in_appendix and (heading := _heading(line)):
            kind, label, rest = heading
            # Title may go on over the next lines: "Chương I" / "QUY ĐỊNH CHUNG".
            rest, i = _title_lines(lines, i, rest)
            crumb[kind] = f"{label}. {rest}" if rest else label
            # A new Phần resets Chương and Mục; a new Chương resets Mục.
            if kind == "phan":
                crumb["chuong"] = crumb["muc"] = None
            elif kind == "chuong":
                crumb["muc"] = None
            seen_heading, current = True, None
            continue
        if not in_appendix and dieu:
            seen_heading = seen_article = True
            last_dieu = _dieu_number(dieu.group(1))[0]
            current = _Block(
                "article",
                **crumb,
                dieu=f"Điều {dieu.group(1)}",
                dieu_title=dieu.group(2).strip() or None,
                lines=[line],
            )
            blocks.append(current)
            continue
        if current is None:
            if not line:
                continue
            if seen_heading:
                current = _Block("other", **crumb)
            else:
                current = _Block("preamble", dieu_title=PREAMBLE_TITLE)
            blocks.append(current)
        current.lines.append(line)
    return blocks


def _join(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def _fit(words: list[str], count: TokenCounter, limit: int, *, from_end: bool = False) -> int:
    """Largest n such that the first (or last) n words fit in `limit` tokens (binary search,
    so a long paragraph costs O(log n) tokenizer calls, not one per word)."""
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        part = words[-mid:] if from_end else words[:mid]
        if count(" ".join(part)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _units(text: str, count: TokenCounter, size: int, overlap: int) -> list[_Unit]:
    """Paragraphs. A paragraph over `size` tokens is cut into word groups of at most
    `size - overlap` tokens, leaving room for the overlap carried into the next window.
    Joining the units gives back `text`."""
    units: list[_Unit] = []
    for para in text.splitlines():
        if not para:
            continue
        tokens = count(para)
        if tokens <= size:
            units.append(_Unit(para, tokens))
            continue
        words = para.split(" ")
        first = True
        while words:
            n = max(1, _fit(words, count, size - overlap))
            piece = " ".join(words[:n])
            units.append(_Unit(piece, count(piece), newline=first))
            words, first = words[n:], False
    return units


def _carry(window: list[_Unit], count: TokenCounter, overlap: int) -> list[_Unit]:
    """The last ~`overlap` tokens of a window: whole units, then the tail words of the unit
    before them."""
    carry: list[_Unit] = []
    room = overlap
    for unit in reversed(window):
        if unit.tokens <= room:
            carry.insert(0, unit)
            room -= unit.tokens
            continue
        words = unit.text.split(" ")
        if n := _fit(words, count, room, from_end=True):
            tail = " ".join(words[-n:])
            carry.insert(0, _Unit(tail, count(tail)))
        break
    return carry


def _windows(
    text: str,
    count: TokenCounter,
    size: int = WINDOW_TOKENS,
    overlap: int = WINDOW_OVERLAP_TOKENS,
) -> list[str]:
    """Pieces of at most about `size` tokens, cut between paragraphs where possible; each
    piece repeats the last ~`overlap` tokens of the previous one. Token sums are approximate
    (counted per unit)."""
    overlap = min(overlap, size // 2)
    windows: list[list[_Unit]] = []
    current: list[_Unit] = []
    total = 0
    for unit in _units(text, count, size, overlap):
        if current and total + unit.tokens > size:
            windows.append(current)
            current = _carry(current, count, overlap)
            total = sum(u.tokens for u in current)
            if total + unit.tokens > size:
                current, total = [], 0  # no room for an overlap before a big paragraph
        current.append(unit)
        total += unit.tokens
    if current:
        windows.append(current)
    return [
        "".join(("\n" if u.newline else " ") + u.text if k else u.text for k, u in enumerate(w))
        for w in windows
    ]


def _khoan_label(numbers: list[int]) -> str:
    return f"Khoản {numbers[0]}" if len(numbers) == 1 else f"Khoản {numbers[0]}-{numbers[-1]}"


def _split_article(
    block: _Block, count: TokenCounter, max_tokens: int
) -> list[tuple[str, str | None]]:
    """`(text, khoan)` parts of an over-long Điều, each starting with the heading line."""
    heading, body = block.lines[0], block.lines[1:]
    heading_tokens = count(heading)
    if heading_tokens > max_tokens // 2:
        # The whole article is on its heading line (bad extraction): repeating it in every
        # part is impossible, and it is not a title.
        block.dieu_title = None
        return [(piece, None) for piece in _windows(_join(block.lines), count, max_tokens)]
    budget = max_tokens - heading_tokens

    # Khoản must be numbered 1, 2, 3, ... so a stray "2019. " line is not a khoản.
    groups: list[tuple[list[int], list[str]]] = []
    intro: list[str] = []
    for line in body:
        match = KHOAN.match(line)
        if match and int(match.group(1)) == len(groups) + 1:
            groups.append(([len(groups) + 1], [*intro, line]))  # intro goes with khoản 1
            intro = []
        elif groups:
            groups[-1][1].append(line)
        else:
            intro.append(line)
    if not groups:
        return [(f"{heading}\n{piece}", None) for piece in _windows(_join(body), count, budget)]

    parts: list[tuple[str, str | None]] = []
    numbers: list[int] = []
    texts: list[str] = []
    total = 0

    def flush() -> None:
        nonlocal numbers, texts, total
        if texts:
            parts.append((_join([heading, *texts]), _khoan_label(numbers)))
        numbers, texts, total = [], [], 0

    for group_numbers, group_lines in groups:
        group_text = _join(group_lines)
        tokens = count(group_text)
        if tokens > budget:
            # One khoản alone is too long: cut it into windows, all labeled with it.
            flush()
            label = _khoan_label(group_numbers)
            parts.extend(
                (f"{heading}\n{piece}", label) for piece in _windows(group_text, count, budget)
            )
            continue
        if texts and total + tokens > budget:
            flush()
        numbers.extend(group_numbers)
        texts.append(group_text)
        total += tokens
    flush()
    return parts


def _embed_text(text: str, title: str | None, so_hieu: str | None, block: _Block) -> str:
    """Breadcrumb + text (what is actually embedded); missing lines are left out."""
    doc_line = f"{title} ({so_hieu})" if title and so_hieu else title or so_hieu
    crumb = " > ".join(p for p in (block.phan, block.chuong, block.muc) if p)
    dieu_line = ". ".join(p for p in (block.dieu, block.dieu_title) if p)
    if block.kind == "article" and text.startswith(block.lines[0]):
        dieu_line = ""  # the chunk already starts with its heading ("Điều 1." / "ĐIỀU 1:")
    return "\n".join(p for p in (doc_line, crumb, dieu_line, text) if p)


def chunk_document(
    text: str,
    *,
    title: str | None = None,
    so_hieu: str | None = None,
    count_tokens: TokenCounter | None = None,
    max_tokens: int = MAX_CHUNK_TOKENS,
) -> list[Chunk]:
    """Chunks of a normalized legal text, in document order (`ordinal` from 0)."""
    count = count_tokens or default_token_counter()
    lines = text.splitlines()
    if any(DIEU.match(line) for line in lines):
        blocks = _blocks(lines)
    else:
        blocks = [_Block("other", lines=lines)]

    chunks: list[Chunk] = []
    for block in blocks:
        block_text = _join(block.lines)
        if not block_text:
            continue
        block_tokens = count(block_text)
        parts: list[tuple[str, str | None, int | None]]
        if block_tokens <= max_tokens:
            parts = [(block_text, None, block_tokens)]
        elif block.kind == "article":
            parts = [(t, k, None) for t, k in _split_article(block, count, max_tokens)]
        else:
            window = min(WINDOW_TOKENS, max_tokens)
            parts = [(piece, None, None) for piece in _windows(block_text, count, window)]
        for part_text, khoan, tokens in parts:
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=part_text,
                    embed_text=_embed_text(part_text, title, so_hieu, block),
                    token_count=count(part_text) if tokens is None else tokens,
                    phan=block.phan,
                    chuong=block.chuong,
                    muc=block.muc,
                    dieu=block.dieu,
                    dieu_title=block.dieu_title,
                    khoan=khoan,
                )
            )
    return chunks
