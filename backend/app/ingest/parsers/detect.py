"""Guess `so_hieu`, `doc_type`, `issued_date`, `title` from the start of a legal text.

Regex heuristics on normalized (NFC) text. Results only pre-fill the upload form; any field
that cannot be found is `None`. `issued_date_source` says where the date came from, so the
UI can ask the user to double-check a `fallback` date.
"""

import re
from datetime import date
from typing import Any, Literal

# Longest first, so "Thông tư liên tịch" wins over "Thông tư".
DOC_TYPES = sorted(
    [
        "Hiến pháp",
        "Bộ luật",
        "Luật",
        "Pháp lệnh",
        "Lệnh",
        "Nghị quyết liên tịch",
        "Nghị quyết",
        "Nghị định",
        "Thông tư liên tịch",
        "Thông tư",
        "Quyết định",
        "Chỉ thị",
        "Văn bản hợp nhất",
        "Công văn",
        "Thông báo",
    ],
    key=len,
    reverse=True,
)

# Fallback from the issuer code in the number, e.g. "140/2004/NĐ-CP" -> Nghị định.
# QH is ambiguous (Luật / Nghị quyết), so it is not mapped.
_SUFFIX_TYPES = [
    ("TTLT", "Thông tư liên tịch"),
    ("NQLT", "Nghị quyết liên tịch"),
    ("NĐ", "Nghị định"),
    ("TT", "Thông tư"),
    ("QĐ", "Quyết định"),
    ("NQ", "Nghị quyết"),
    ("CT", "Chỉ thị"),
    ("PL", "Pháp lệnh"),
    ("L-CTN", "Lệnh"),
    ("VBHN", "Văn bản hợp nhất"),
]

# Types whose own date is in the closing "... thông qua ngày ..." clause, not in the header.
PASSED_TYPES = frozenset(
    {"Hiến pháp", "Bộ luật", "Luật", "Pháp lệnh", "Nghị quyết", "Nghị quyết liên tịch"}
)

# Without "Điều 1" (e.g. a Công văn) the head stops here, so it never reads into the body.
HEAD_MAX_LINES = 40
HEAD_MAX_CHARS = 3000

# "45/2019/QH14", "140/2004/NĐ-CP", "12/CT-TTg" (year part optional only after "Số:").
_ISSUER = r"[A-ZĐ][A-Za-zĐđ0-9-]*[A-Za-zĐđ0-9]"
_SO_HIEU_LABELED = re.compile(rf"\b[Ss]ố\s*[:.]?\s*(\d+[a-zA-Z]?/(?:\d{{4}}/)?{_ISSUER})")
_SO_HIEU_ANY = re.compile(rf"(?<![\w/])(\d+[a-zA-Z]?/\d{{4}}/{_ISSUER})")
_DATE_WORDS = r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})"
_DATE_NUMERIC = r"ngày\s+(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"
_ISSUED = re.compile(rf"{_DATE_WORDS}|{_DATE_NUMERIC}", re.IGNORECASE)
_PASSED = re.compile(rf"thông qua\s+{_DATE_WORDS}", re.IGNORECASE)
# "Hà Nội, ngày 25 tháng 6 năm 2004". Not anchored to the line start: PDF header columns can
# share a line ("Số: 140/2004/NĐ-CP Hà Nội, ngày ..."); the place name has no digit or comma.
_PLACE_DATE = re.compile(
    rf"(?:^|\s)[^\d,\n]{{2,40}},\s*(?:{_DATE_WORDS}|{_DATE_NUMERIC})", re.IGNORECASE
)
# Re.IGNORECASE folds Unicode on str, so "ĐIỀU 1." matches too (text must be NFC).
_ARTICLE_1 = re.compile(r"^Điều\s+1\b", re.IGNORECASE)
# "Căn cứ ..." lines cite other documents: their numbers and dates are not this document's.
_CITATION = re.compile(r"^Căn cứ\b", re.IGNORECASE)
_TITLE_STOP = re.compile(r"^(Căn cứ|Điều\s+\d|Chương\s|Phần\s|Số\b)", re.IGNORECASE)
TITLE_MAX_LINES = 4


IssuedDateSource = Literal["passed", "place_date", "fallback"]


def _head(text: str) -> list[str]:
    """Lines before "Điều 1" (preamble: issuer, number, date, title), capped."""
    lines: list[str] = []
    chars = 0
    for line in text.splitlines():
        if _ARTICLE_1.match(line) or len(lines) >= HEAD_MAX_LINES or chars >= HEAD_MAX_CHARS:
            break
        if line:
            lines.append(line)
            chars += len(line)
    return lines


def _own_lines(lines: list[str]) -> str:
    """Head without the "Căn cứ" lines."""
    return "\n".join(line for line in lines if not _CITATION.match(line))


def _is_upper(line: str) -> bool:
    return any(ch.isalpha() for ch in line) and line == line.upper()


def detect_so_hieu(head: str) -> str | None:
    match = _SO_HIEU_LABELED.search(head) or _SO_HIEU_ANY.search(head)
    return match.group(1) if match else None


def _type_line(lines: list[str]) -> tuple[int, str] | None:
    """Index and canonical type of the all-caps heading such as "NGHỊ ĐỊNH"."""
    for i, line in enumerate(lines):
        # "QUYẾT ĐỊNH:" right before Điều 1 is the enacting formula, not the type heading.
        if not _is_upper(line) or line.endswith(":"):
            continue
        for doc_type in DOC_TYPES:
            upper = doc_type.upper()
            if line == upper or line.startswith(upper + " "):
                return i, doc_type
    return None


def _type_from_so_hieu(so_hieu: str | None) -> str | None:
    if not so_hieu:
        return None
    issuer = so_hieu.rsplit("/", 1)[-1].upper()
    for prefix, doc_type in _SUFFIX_TYPES:
        if issuer == prefix or issuer.startswith(prefix + "-"):
            return doc_type
    return None


def _title(lines: list[str], index: int, doc_type: str) -> str:
    """Type heading + the subject lines after it, e.g. "Luật bảo hiểm xã hội".

    All-caps subjects are lowercased (proper nouns lose their capitals; the user edits).
    """
    parts = [lines[index][len(doc_type) :].strip()]
    mixed_seen = False
    for line in lines[index + 1 : index + 1 + TITLE_MAX_LINES]:
        if _TITLE_STOP.match(line) or line.endswith(":"):
            break
        if not any(ch.isalpha() for ch in line):  # "-------" separators
            continue
        upper = _is_upper(line)
        if upper and mixed_seen:  # e.g. "BỘ TRƯỞNG BỘ ..." after "Về việc ..."
            break
        mixed_seen = mixed_seen or not upper
        parts.append(line)
    subject = " ".join(p.lower() if _is_upper(p) else p for p in parts if p)
    if subject:
        subject = subject[0].lower() + subject[1:]
    return f"{doc_type} {subject}".strip()


def _valid_date(day: str, month: str, year: str) -> date | None:
    """The date, or `None` if it does not exist (31/2) or lies in the future."""
    try:
        found = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return found if found <= date.today() else None


def _first_date(pattern: re.Pattern[str], text: str) -> date | None:
    for match in pattern.finditer(text):
        groups = [g for g in match.groups() if g is not None]
        if found := _valid_date(*groups):
            return found
    return None


def detect_issued_date(
    head: str, text: str, doc_type: str | None
) -> tuple[date | None, IssuedDateSource | None]:
    """First date found, by priority: "thông qua ngày" (laws, or unknown type), the
    "<place>, ngày ..." line, any other date in `head` (which has no "Căn cứ" lines)."""
    sources: list[tuple[IssuedDateSource, re.Pattern[str], str]] = [
        ("place_date", _PLACE_DATE, head),
        ("fallback", _ISSUED, head),
    ]
    if doc_type is None or doc_type in PASSED_TYPES:
        # Laws carry the date at the end: "... thông qua ngày 20 tháng 11 năm 2019."
        sources.insert(0, ("passed", _PASSED, text))
    for source, pattern, haystack in sources:
        if found := _first_date(pattern, haystack):
            return found, source
    return None, None


def detect_metadata(text: str) -> dict[str, Any]:
    """Return `{so_hieu, doc_type, issued_date, issued_date_source, title}`; missing values
    are `None`."""
    lines = _head(text)
    head = _own_lines(lines)
    so_hieu = detect_so_hieu(head)
    type_line = _type_line(lines)
    if type_line:
        index, doc_type = type_line
        title: str | None = _title(lines, index, doc_type)
    else:
        doc_type, title = _type_from_so_hieu(so_hieu), None
    issued_date, issued_date_source = detect_issued_date(head, text, doc_type)
    return {
        "so_hieu": so_hieu,
        "doc_type": doc_type,
        "issued_date": issued_date,
        "issued_date_source": issued_date_source,
        "title": title,
    }
