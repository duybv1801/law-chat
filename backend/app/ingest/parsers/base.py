"""Types shared by the document parsers."""

from dataclasses import dataclass, field
from typing import Any, Literal

ParseErrorCode = Literal[
    "unsupported_format", "corrupt_file", "encrypted_pdf", "scanned_pdf", "empty_document"
]

USER_MESSAGES: dict[ParseErrorCode, str] = {
    "unsupported_format": "Định dạng file không được hỗ trợ. Chỉ nhận PDF, DOCX hoặc TXT.",
    "corrupt_file": "Không đọc được file. File có thể bị hỏng hoặc sai định dạng.",
    "encrypted_pdf": "PDF có mật khẩu, chưa hỗ trợ.",
    "scanned_pdf": "PDF scan chưa hỗ trợ.",
    "empty_document": "Không tìm thấy nội dung văn bản trong file.",
}


class ParseError(Exception):
    """Parsing failure with a stable `code` and a Vietnamese `user_message`.

    `user_message` is what ends up in `legal_documents.ingest_error`.
    """

    def __init__(self, code: ParseErrorCode, user_message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message or USER_MESSAGES[code]


@dataclass
class ParsedDocument:
    """Normalized (NFC) text plus metadata guessed from it (see `detect.py`).

    `detected` is only a suggestion for the upload form; the user confirms it.
    `warnings` are JSON-ready dicts with a `code`, e.g.
    `{"code": "partially_scanned", "scanned_pages": [11, 12]}` (pages are 1-based).
    """

    text: str
    detected: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
