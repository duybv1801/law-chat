"""Document parsers for PDF, DOCX and TXT.

`parse_bytes` / `parse_file` pick the parser by extension, normalize the text (NFC) and
guess metadata. Checking magic bytes of uploads is the upload endpoint's job (T11).
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.ingest.parsers import docx, pdf, txt
from app.ingest.parsers.base import ParsedDocument, ParseError
from app.ingest.parsers.detect import detect_metadata
from app.ingest.parsers.normalize import normalize_text

__all__ = ["SUPPORTED_EXTENSIONS", "ParseError", "ParsedDocument", "parse_bytes", "parse_file"]

# Each extractor returns (raw text, warnings).
_EXTRACTORS: dict[str, Callable[[bytes], tuple[str, list[dict[str, Any]]]]] = {
    ".pdf": pdf.extract_text,
    ".docx": docx.extract_text,
    ".txt": txt.extract_text,
}
SUPPORTED_EXTENSIONS = frozenset(_EXTRACTORS)


def build_parsed(raw_text: str, warnings: list[dict[str, Any]] | None = None) -> ParsedDocument:
    """Normalize extracted text and attach detected metadata (also used by the vbpl parser)."""
    text = normalize_text(raw_text)
    if not text:
        raise ParseError("empty_document")
    return ParsedDocument(text=text, detected=detect_metadata(text), warnings=warnings or [])


def parse_bytes(data: bytes, filename: str) -> ParsedDocument:
    """Parse file content; the format comes from `filename`'s extension.

    Raises `ParseError` (unsupported format, corrupt file, scanned PDF, no text).
    CPU-bound: call from async code with `asyncio.to_thread`.
    """
    extract = _EXTRACTORS.get(Path(filename).suffix.lower())
    if extract is None:
        raise ParseError("unsupported_format")
    return build_parsed(*extract(data))


def parse_file(path: Path) -> ParsedDocument:
    return parse_bytes(path.read_bytes(), path.name)
