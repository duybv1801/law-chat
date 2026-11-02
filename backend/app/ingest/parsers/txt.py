"""Plain text decoding: BOM, then UTF-16 without BOM, UTF-8, finally cp1258."""

import codecs
from typing import Any

# Share of NUL bytes on one byte parity needed to call it UTF-16. Vietnamese text is mostly
# ASCII letters and spaces, so real UTF-16 is far above this; a stray NUL in 8-bit text is not.
MIN_UTF16_NUL_RATIO = 0.3


def _utf16_without_bom(data: bytes) -> str | None:
    """Guess BOM-less UTF-16 from NUL bytes (rare in any 8-bit text)."""
    if len(data) < 2 or len(data) % 2 or b"\x00" not in data:
        return None
    even_nuls = data[0::2].count(0)
    odd_nuls = data[1::2].count(0)
    if max(even_nuls, odd_nuls) < MIN_UTF16_NUL_RATIO * (len(data) // 2):
        return None
    encoding = "utf-16-le" if odd_nuls > even_nuls else "utf-16-be"
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return None


def decode(data: bytes) -> str:
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if (text := _utf16_without_bom(data)) is not None:
        return text
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Legacy Windows Vietnamese; uses combining tone marks, which NFC composes later.
        return data.decode("cp1258", errors="replace")


def extract_text(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    return decode(data), []
