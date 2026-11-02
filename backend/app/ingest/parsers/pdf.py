"""PDF text extraction with pymupdf."""

from typing import Any

import pymupdf

from app.ingest.parsers.base import ParseError
from app.ingest.parsers.normalize import normalize_line, remove_repeated_lines

# A page with fewer non-space characters than this (after dropping repeated headers /
# footers) and at least one image is a scanned page. OCR is out of scope for now.
MIN_CHARS_PER_PAGE = 50
# Share of text pages among non-blank pages: below the first the PDF is rejected as a scan,
# below the second it is accepted with a `partially_scanned` warning.
MIN_TEXT_PAGE_RATIO = 0.1
WARN_TEXT_PAGE_RATIO = 0.9


def _page_lines(doc: pymupdf.Document) -> tuple[list[list[str]], list[bool]]:
    """Normalized lines and "has an image" flag for every page."""
    pages: list[list[str]] = []
    has_image: list[bool] = []
    for page in doc:
        text = page.get_text("text", sort=True)
        pages.append([normalize_line(line) for line in text.splitlines()])
        has_image.append(bool(page.get_images()))
    return pages, has_image


def _scan_warnings(pages: list[list[str]], has_image: list[bool]) -> list[dict[str, Any]]:
    """Reject mostly-scanned PDFs; list the scanned pages (1-based) of partly scanned ones.

    Pages with little text and no image are blank (or a short signature page) and do not count.
    """
    text_pages: list[int] = []
    scanned_pages: list[int] = []
    for number, (lines, image) in enumerate(zip(pages, has_image, strict=True), start=1):
        if sum(len(line.replace(" ", "")) for line in lines) >= MIN_CHARS_PER_PAGE:
            text_pages.append(number)
        elif image:
            scanned_pages.append(number)
    if not scanned_pages:
        return []
    ratio = len(text_pages) / (len(text_pages) + len(scanned_pages))
    if ratio < MIN_TEXT_PAGE_RATIO:
        raise ParseError("scanned_pdf")
    if ratio < WARN_TEXT_PAGE_RATIO:
        return [{"code": "partially_scanned", "scanned_pages": scanned_pages}]
    return []


def extract_text(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Text (pages separated by a blank line) and warnings such as `partially_scanned`."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # FileDataError (a RuntimeError), ValueError, ...
        raise ParseError("corrupt_file") from exc
    with doc:
        if doc.needs_pass:
            raise ParseError("encrypted_pdf")
        # A valid PDF has at least one page; zero means a broken file that MuPDF "repaired".
        if doc.page_count == 0:
            raise ParseError("corrupt_file")
        try:
            pages, has_image = _page_lines(doc)
        except Exception as exc:
            raise ParseError("corrupt_file") from exc
        repaired = doc.is_repaired

    # Count characters only after headers / footers are gone: a text stamp repeated on every
    # page of a scan must not make it look like text.
    pages = remove_repeated_lines(pages)
    warnings = _scan_warnings(pages, has_image)
    # No text in a file MuPDF had to repair: report it broken, not "no content".
    if repaired and not any(line for lines in pages for line in lines):
        raise ParseError("corrupt_file")
    # Blank line between pages; normalize_text later collapses extra blanks.
    return "\n\n".join("\n".join(lines) for lines in pages), warnings
