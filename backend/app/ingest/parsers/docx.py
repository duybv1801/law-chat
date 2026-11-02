"""DOCX text extraction with python-docx."""

import io
from typing import Any

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingest.parsers.base import ParseError


def _table_lines(table: Table) -> list[str]:
    """Cell texts row by row. Merged cells appear once (python-docx repeats them).

    Legal documents put the issuer, number and date in a header table, so tables are kept.
    """
    lines: list[str] = []
    seen: set[object] = set()  # holds the lxml elements, so identity stays stable
    for row in table.rows:
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            lines.append(cell.text)
    return lines


def extract_text(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    # Any failure while reading the package is a broken file: BadZipFile, zlib.error,
    # lxml's XMLSyntaxError (a SyntaxError), KeyError for a missing part, ...
    try:
        document = docx.Document(io.BytesIO(data))
        lines: list[str] = []
        # Body order: paragraphs and tables interleaved as in the document.
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                lines.append(block.text)  # soft line breaks come through as "\n"
            else:
                lines.extend(_table_lines(block))
    except Exception as exc:
        raise ParseError("corrupt_file") from exc
    return "\n".join(lines), []
