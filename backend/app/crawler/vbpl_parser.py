"""Parsers for vbpl.vn pages: document detail (Next.js RSC payload) and sitemaps.

Everything that depends on vbpl.vn's markup lives here (see `docs/vbpl-findings.md`):

- The detail page embeds the document in `self.__next_f.push([1, "..."])` scripts. Joined,
  they form the RSC "flight" stream: rows `<hex id>:<json>\\n` or `<hex id>:T<hex len>,<text>`
  (the length counts UTF-8 bytes, no trailing newline). One JSON row holds `"initialDoc"`;
  its `documentContent.content` is either inline HTML or a `"$<id>"` reference to a text row.
- Full text is HTML with `prov-chapter` / `prov-article` / ... paragraphs. It is turned into
  one line per paragraph and handed to the chunker, which finds Chương / Điều by regex (old
  documents pasted from Word have few `prov-*` classes, so the classes are not relied on).
- Listing uses sitemaps: the index groups files under `<!-- Trung ương -->` /
  `<!-- Địa phương -->` comments; each file lists URLs sorted by `lastmod` descending.
"""

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.ingest.parsers import ParseError, build_parsed
from app.ingest.schemas import LegalDocumentIn, Scope, Status

logger = logging.getLogger(__name__)


class VbplError(Exception):
    """Base class of crawler errors."""


class VbplParseError(VbplError):
    """The page does not have the expected structure.

    `code`: `no_initial_doc` (no document data: anti-bot page, error page or a redesign),
    `bad_payload` (RSC payload or JSON broken), `id_mismatch` (page is another document).
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# effStatus.code -> status enum. Codes outside `_CERTAIN_CODES` keep the code in status_note.
STATUS_BY_CODE: dict[str, Status] = {
    "CHL": "con_hieu_luc",
    "CCHL": "chua_co_hieu_luc",
    "HHL": "het_hieu_luc_toan_bo",
    "HHL1P": "het_hieu_luc_mot_phan",
    "HHL1P1": "het_hieu_luc_mot_phan",
    "HHL1P2": "het_hieu_luc_mot_phan",
    "HHL1P3": "het_hieu_luc_mot_phan",
    "HHL1P4": "het_hieu_luc_mot_phan",
    "TNHL": "khong_xac_dinh",
    "TNGHL1P": "khong_xac_dinh",
}
_CERTAIN_CODES = frozenset({"CHL", "CCHL", "HHL", "HHL1P"})

# Slug prefix -> doc type, longest first. Only an estimate for backfill ordering / filtering;
# the real type is `docType.name` on the detail page.
SLUG_DOC_TYPES: tuple[tuple[str, str], ...] = (
    ("thong-tu-lien-tich", "Thông tư liên tịch"),
    ("nghi-quyet-lien-tich", "Nghị quyết liên tịch"),
    ("van-ban-hop-nhat", "Văn bản hợp nhất"),
    ("hien-phap", "Hiến pháp"),
    ("bo-luat", "Bộ luật"),
    ("phap-lenh", "Pháp lệnh"),
    ("nghi-quyet", "Nghị quyết"),
    ("nghi-dinh", "Nghị định"),
    ("quyet-dinh", "Quyết định"),
    ("thong-tu", "Thông tư"),
    ("chi-thi", "Chỉ thị"),
    ("sac-lenh", "Sắc lệnh"),
    ("luat", "Luật"),
    ("lenh", "Lệnh"),
)

_PUSH = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
_ROW_ID = re.compile(rb"[0-9a-f]*")  # hint rows (":HL[...]") have no id
_REF = re.compile(r"\$([0-9a-f]+)")
_INITIAL_DOC = re.compile(r'"initialDoc"\s*:\s*')
_LD_JSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_SPACES = re.compile(r"\s+")

# Elements that start a new line. Table cells too: one line per cell, as the DOCX parser.
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "header", "footer", "blockquote", "pre",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd",
        "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "hr",
    }
)  # fmt: skip
_SKIP_TAGS = frozenset({"script", "style", "head", "template", "noscript"})


@dataclass(frozen=True)
class VbplStatus:
    """Validity part of a document (what `status_refresh` compares)."""

    status: Status
    status_code: str | None  # effStatus.code as on vbpl.vn
    status_note: str | None  # effStatus.name, verbatim
    effective_date: date | None
    expiry_date: date | None
    updated_date: datetime | None  # vbpl.vn local time (no tz), matches sitemap `lastmod`


@dataclass
class VbplDocument:
    source_id: str
    source_url: str | None
    title: str
    so_hieu: str | None
    doc_type: str | None
    issuing_body: str | None
    issued_date: date | None
    status: VbplStatus
    is_consolidated: bool
    scope: Scope | None  # from the breadcrumb; None if the page does not say
    has_content: bool
    text: str | None  # normalized full text; None without HTML full text (PDF only)
    signers: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)  # lĩnh vực

    def to_document_in(
        self, *, crawled_at: datetime | None = None, scope: Scope = "trung_uong"
    ) -> LegalDocumentIn:
        """Indexer input. `scope` is the fallback when the page has no breadcrumb."""
        return LegalDocumentIn(
            source="vbpl",
            source_id=self.source_id,
            title=self.title,
            so_hieu=self.so_hieu,
            doc_type=self.doc_type,
            issuing_body=self.issuing_body,
            issued_date=self.issued_date,
            effective_date=self.status.effective_date,
            expiry_date=self.status.expiry_date,
            status=self.status.status,
            status_note=self.status.status_note,
            is_consolidated=self.is_consolidated,
            scope=self.scope or scope,
            source_url=self.source_url,
            crawled_at=crawled_at,
            status_checked_at=crawled_at,
        )


@dataclass(frozen=True)
class DocRef:
    """One document URL from a sitemap."""

    url: str
    source_id: str
    lastmod: date | None

    @property
    def doc_type_guess(self) -> str | None:
        return guess_doc_type(self.url)


@dataclass(frozen=True)
class SitemapFile:
    url: str
    scope: Scope | None  # None: static pages or an unknown group


# --- Helpers ----------------------------------------------------------------------------


def source_id_from_url(url: str) -> str | None:
    """`.../chi-tiet/<slug>--<id>` -> `<id>` (the part after the last `--`)."""
    segment = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    if "--" not in segment:
        return None
    return segment.rsplit("--", 1)[1] or None


def guess_doc_type(url: str) -> str | None:
    segment = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    for prefix, doc_type in SLUG_DOC_TYPES:
        if segment == prefix or segment.startswith(prefix + "-"):
            return doc_type
    return None


def map_status(code: str | None, name: str | None) -> tuple[Status, str | None]:
    """(status enum, status_note). Unknown or uncertain codes keep the code in the note."""
    name = _clean(name)
    if code is None:
        return "khong_xac_dinh", name
    status = STATUS_BY_CODE.get(code)
    if status is None:
        logger.warning("Unknown vbpl effStatus code %r (%r)", code, name)
        status = "khong_xac_dinh"
    if code in _CERTAIN_CODES:
        return status, name
    return status, f"{name} ({code})" if name else code


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _SPACES.sub(" ", value).strip() or None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _names(items: Any, key: str = "name") -> list[str]:
    if not isinstance(items, list):
        return []
    names = (_clean(item.get(key)) for item in items if isinstance(item, dict))
    return list(dict.fromkeys(n for n in names if n))  # unique, in order


# --- RSC flight payload -----------------------------------------------------------------


def extract_flight(page: str) -> bytes:
    """Concatenated RSC payload strings of a Next.js page, as UTF-8 (row lengths are bytes)."""
    chunks: list[str] = []
    for raw in _PUSH.findall(page):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VbplParseError("bad_payload", "push() argument is not JSON") from exc
        # [0] = bootstrap, [1, "<chunk>"] = data, [2, ...] / [3, ...] = other kinds.
        if len(item) > 1 and item[0] == 1 and isinstance(item[1], str):
            chunks.append(item[1])
    # A surrogate pair split across two pushes decodes as two lone surrogates; the
    # UTF-16 round trip joins them back before encoding.
    joined = "".join(chunks).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    return joined.encode("utf-8")


def parse_flight_rows(flight: bytes) -> dict[str, bytes]:
    """Row id -> raw row value (JSON row text, or the bytes of a `T` text row)."""
    rows: dict[str, bytes] = {}
    pos, end = 0, len(flight)
    while pos < end:
        colon = flight.find(b":", pos)
        if colon < 0 or not _ROW_ID.fullmatch(flight, pos, colon):
            raise VbplParseError("bad_payload", f"bad row header at byte {pos}")
        row_id = flight[pos:colon].decode("ascii")
        if flight.startswith(b"T", colon + 1):
            comma = flight.find(b",", colon)
            try:
                length = int(flight[colon + 2 : comma], 16)
            except ValueError as exc:
                raise VbplParseError("bad_payload", f"bad text row {row_id}") from exc
            start = comma + 1
            if comma < 0 or start + length > end:
                raise VbplParseError("bad_payload", f"truncated text row {row_id}")
            rows[row_id] = flight[start : start + length]
            pos = start + length
        else:
            newline = flight.find(b"\n", colon)
            newline = end if newline < 0 else newline
            rows[row_id] = flight[colon + 1 : newline]
            pos = newline + 1
    return rows


def _initial_doc(rows: dict[str, bytes]) -> dict[str, Any]:
    for value in rows.values():
        if b'"initialDoc"' not in value:
            continue
        text = value.decode("utf-8")
        marker = _INITIAL_DOC.search(text)
        if marker is None:
            continue
        try:
            doc, _ = json.JSONDecoder().raw_decode(text, marker.end())
        except json.JSONDecodeError as exc:
            raise VbplParseError("bad_payload", "initialDoc is not JSON") from exc
        if isinstance(doc, dict) and doc.get("id"):
            return doc
    raise VbplParseError("no_initial_doc")


def _resolve(value: Any, rows: dict[str, bytes]) -> str:
    """A string from the payload: `"$2b"` points to row `2b`, `"$$..."` is an escaped `$`.
    Other `$` values (`$undefined`, `$L1`, `$@2`, ...) are not text and resolve to ""."""
    if not isinstance(value, str):
        return ""
    if value.startswith("$$"):
        return value[1:]
    ref = _REF.fullmatch(value)
    if ref is None:
        return "" if value.startswith("$") else value
    row = rows.get(ref.group(1))
    if row is None:
        raise VbplParseError("bad_payload", f"missing row {ref.group(1)}")
    return row.decode("utf-8")


# --- Full text --------------------------------------------------------------------------


def _walk(node: LexborNode, buffer: list[str], lines: list[str]) -> None:
    for child in node.iter(include_text=True):
        tag = child.tag
        if tag == "-text":
            # HTML whitespace (source line breaks included) is a single space.
            buffer.append(_SPACES.sub(" ", child.text_content or ""))
        elif tag == "br":
            buffer.append("\n")
        elif tag in _SKIP_TAGS or tag.startswith("-"):  # -comment, -doctype
            continue
        elif tag in _BLOCK_TAGS:
            _flush(buffer, lines)
            _walk(child, buffer, lines)
            _flush(buffer, lines)
        else:
            _walk(child, buffer, lines)


def _flush(buffer: list[str], lines: list[str]) -> None:
    if buffer:
        lines.extend("".join(buffer).split("\n"))
        buffer.clear()


def html_to_text(content: str) -> str:
    """One line per paragraph / table cell, `<br>` as a line break. Not normalized."""
    tree = LexborHTMLParser(content)
    root = tree.body or tree.root
    if root is None:
        return ""
    buffer: list[str] = []
    lines: list[str] = []
    _walk(root, buffer, lines)
    _flush(buffer, lines)
    return "\n".join(lines)


# --- Detail page ------------------------------------------------------------------------


def _ld_json(page: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in _LD_JSON.findall(page):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _scope_from_breadcrumb(items: list[dict[str, Any]]) -> Scope | None:
    for item in items:
        if item.get("@type") != "BreadcrumbList":
            continue
        for element in item.get("itemListElement") or []:
            link = element.get("item") if isinstance(element, dict) else None
            path = urlsplit(link).path.rstrip("/") if isinstance(link, str) else ""
            if path.endswith("/van-ban/trung-uong"):
                return "trung_uong"
            if path.endswith("/van-ban/dia-phuong"):
                return "dia_phuong"
    return None


def _status(doc: dict[str, Any]) -> VbplStatus:
    eff = doc.get("effStatus") if isinstance(doc.get("effStatus"), dict) else {}
    code = _clean(eff.get("code"))
    status, note = map_status(code, eff.get("name"))
    return VbplStatus(
        status=status,
        status_code=code,
        status_note=note,
        effective_date=_date(doc.get("effFrom")),
        expiry_date=_date(doc.get("effTo")),
        updated_date=_datetime(doc.get("updatedDate")),
    )


def _load(page: str, source_id: str | None) -> tuple[dict[str, Any], dict[str, bytes]]:
    rows = parse_flight_rows(extract_flight(page))
    doc = _initial_doc(rows)
    if source_id is not None and str(doc["id"]).lower() != source_id.lower():
        raise VbplParseError("id_mismatch", f"expected {source_id}, page has {doc['id']}")
    return doc, rows


def parse_status(page: str, source_id: str | None = None) -> VbplStatus:
    """Only the validity fields of a detail page (skips the full text)."""
    doc, _ = _load(page, source_id)
    return _status(doc)


def parse_detail(page: str, url: str | None = None, source_id: str | None = None) -> VbplDocument:
    """Metadata + full text of a detail page. Raises `VbplParseError`; with `source_id`,
    also when the page is another document.

    CPU-bound for large documents (consolidated texts reach 1.5 MB): call from async code
    with `asyncio.to_thread`.
    """
    doc, rows = _load(page, source_id)
    ld = _ld_json(page)
    legislation = next((i for i in ld if i.get("@type") == "Legislation"), {})
    title = _clean(doc.get("title")) or _clean(legislation.get("name"))
    if title is None:
        raise VbplParseError("bad_payload", "document has no title")

    issues = doc.get("documentIssues")
    issuing_body = _clean(doc.get("agencyName")) or ", ".join(_names(issues, "agencyName"))
    doc_type = doc.get("docType") if isinstance(doc.get("docType"), dict) else {}
    content = doc.get("documentContent") if isinstance(doc.get("documentContent"), dict) else {}

    text: str | None = None
    if doc.get("hasContent"):
        try:
            text = build_parsed(html_to_text(_resolve(content.get("content"), rows))).text
        except ParseError:
            text = None  # hasContent but an empty body: same as no full text

    canonical = legislation.get("url")
    return VbplDocument(
        source_id=str(doc["id"]),
        source_url=canonical if isinstance(canonical, str) and canonical else url,
        title=title,
        so_hieu=_clean(doc.get("docNum")),
        doc_type=_clean(doc_type.get("name")),
        issuing_body=issuing_body or None,
        issued_date=_date(doc.get("issueDate")),
        status=_status(doc),
        is_consolidated=bool(doc.get("isConsolidatedDocument")),
        scope=_scope_from_breadcrumb(ld),
        has_content=text is not None,
        text=text,
        signers=_names(issues, "personName"),
        fields=_names(doc.get("documentFields")),
    )


# --- Sitemaps ---------------------------------------------------------------------------

_INDEX_TOKEN = re.compile(r"<!--(.*?)-->|<loc>\s*(.*?)\s*</loc>", re.S)
_URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S)
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
_LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.S)


def _group_scope(comment: str) -> Scope | None:
    label = _SPACES.sub(" ", comment).strip().casefold()
    if "trung ương" in label:
        return "trung_uong"
    if "địa phương" in label:
        return "dia_phuong"
    return None


def parse_sitemap_index(xml: str) -> list[SitemapFile]:
    """Sitemap files in order, with the scope of the comment group they follow.

    Regex instead of an XML parser: comments carry the grouping (ElementTree drops them),
    and the files are simple, flat lists.
    """
    files: list[SitemapFile] = []
    scope: Scope | None = None
    for token in _INDEX_TOKEN.finditer(xml):
        comment, loc = token.groups()
        if comment is not None:
            scope = _group_scope(comment)
        elif loc:
            files.append(SitemapFile(url=html_lib.unescape(loc), scope=scope))
    return files


def parse_sitemap(xml: str) -> list[DocRef]:
    """Document URLs of one sitemap file, in file order (`lastmod` descending).

    URLs that are not document detail pages (no `--<id>`) are dropped.
    """
    refs: list[DocRef] = []
    for block in _URL_BLOCK.findall(xml):
        loc = _LOC.search(block)
        if loc is None:
            continue
        url = html_lib.unescape(loc.group(1))
        source_id = source_id_from_url(url)
        if source_id is None or "/van-ban/chi-tiet/" not in url:
            continue
        lastmod = _LASTMOD.search(block)
        day = _date(lastmod.group(1)) if lastmod else None
        refs.append(DocRef(url=url, source_id=source_id, lastmod=day))
    return refs
