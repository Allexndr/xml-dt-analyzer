"""Text position helpers using the editor (CodeMirror 6) convention.

Offsets are counted in UTF-16 code units; ``\\r\\n``, ``\\n`` and ``\\r`` are each a
single line break. Lines and columns are 1-based.
"""

from __future__ import annotations

import re

_BREAK = re.compile(r"\r\n|\r|\n")

# Single-entry cache of the UTF-16 encoding of the last text that was sliced.
_cache: tuple[str, bytes] | None = None


def _u16(text: str) -> bytes:
    global _cache
    if _cache is not None and _cache[0] is text:
        return _cache[1]
    data = text.encode("utf-16-le", "surrogatepass")
    _cache = (text, data)
    return data


def utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units."""
    if text.isascii():
        return len(text)
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def slice_utf16(text: str, start: int, end: int) -> str:
    """Return the substring between two UTF-16 offsets."""
    if start < 0 or end < start:
        raise ValueError("invalid UTF-16 range")
    if text.isascii():
        return text[start:end]
    return _u16(text)[start * 2 : end * 2].decode("utf-16-le", "surrogatepass")


def line_col_from_offset(text: str, offset_utf16: int) -> tuple[int, int]:
    """Convert a UTF-16 offset to 1-based ``(line, column)`` (column in UTF-16 units)."""
    if offset_utf16 < 0 or offset_utf16 > utf16_len(text):
        raise ValueError("offset out of range")
    prefix = slice_utf16(text, 0, offset_utf16)
    breaks = [m.end() for m in _BREAK.finditer(prefix)]
    # An offset between "\r" and "\n" is still on the line before the break.
    if prefix.endswith("\r") and slice_utf16(text, offset_utf16, offset_utf16 + 1) == "\n":
        breaks.pop()
    line_begin = breaks[-1] if breaks else 0
    return len(breaks) + 1, utf16_len(prefix[line_begin:]) + 1


def offset_from_line_col(text: str, line: int, col: int) -> int:
    """Convert 1-based ``(line, column)`` to a UTF-16 offset; raises ValueError if invalid."""
    if line < 1 or col < 1:
        raise ValueError("line and column are 1-based")
    begin = 0
    cur = 1
    end = len(text)
    for m in _BREAK.finditer(text):
        if cur == line:
            end = m.start()
            break
        cur += 1
        begin = m.end()
    if cur != line:
        raise ValueError("line out of range")
    if col - 1 > utf16_len(text[begin:end]):
        raise ValueError("column out of range")
    return utf16_len(text[:begin]) + col - 1
