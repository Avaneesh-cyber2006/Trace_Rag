"""Bounded physical-line splitting and decoding for runtime evidence."""

from dataclasses import dataclass

from backend.runtime_evidence.models import RuntimeIssueCode

_UTF8_BOM = b"\xef\xbb\xbf"
_BLANK_BYTES = frozenset((0x20, 0x09))


@dataclass(frozen=True, slots=True)
class PhysicalLine:
    number: int
    raw: bytes
    blank: bool


@dataclass(frozen=True, slots=True)
class LineDecodeResult:
    text: str | None
    issue: RuntimeIssueCode | None


def _is_blank(raw: bytes) -> bool:
    return all(byte in _BLANK_BYTES for byte in raw)


def strip_utf8_bom(content: bytes) -> bytes:
    """Remove at most one leading UTF-8 BOM."""
    if content.startswith(_UTF8_BOM):
        return content[len(_UTF8_BOM):]
    return content


def split_physical_lines(content: bytes) -> tuple[PhysicalLine, ...]:
    """Split artifact bytes into normalized one-based physical lines."""
    if not content:
        return ()
    raw_lines = content.split(b"\n")
    if content.endswith(b"\n"):
        raw_lines.pop()
    lines = []
    for index, raw in enumerate(raw_lines):
        if index == 0 and raw.startswith(_UTF8_BOM):
            raw = raw[len(_UTF8_BOM):]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        lines.append(PhysicalLine(index + 1, raw, _is_blank(raw)))
    return tuple(lines)


def decode_physical_line(
    line: PhysicalLine, max_line_bytes: int
) -> LineDecodeResult:
    """Decode one physical line in the documented failure order."""
    if line.blank:
        return LineDecodeResult(text=None, issue=None)
    if len(line.raw) > max_line_bytes:
        return LineDecodeResult(
            text=None, issue=RuntimeIssueCode.LINE_TOO_LONG
        )
    if b"\x00" in line.raw:
        return LineDecodeResult(
            text=None, issue=RuntimeIssueCode.NUL_BYTE
        )
    try:
        text = line.raw.decode("utf-8")
    except UnicodeDecodeError:
        return LineDecodeResult(
            text=None, issue=RuntimeIssueCode.INVALID_UTF8
        )
    return LineDecodeResult(text=text, issue=None)
