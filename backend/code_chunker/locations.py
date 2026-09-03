"""Validation and reconstruction of original-byte source locations."""

from __future__ import annotations

from bisect import bisect_right

from backend.code_parser.models import (
    CallSite,
    ImportInfo,
    ParsedFile,
    ParseIssue,
    SourceLocation,
    SymbolInfo,
)


def build_line_starts(source: bytes) -> tuple[int, ...]:
    """Return original-byte offsets for every logical line start."""
    return (0,) + tuple(index + 1 for index, value in enumerate(source) if value == 0x0A)


def _point(line_starts: tuple[int, ...], offset: int) -> tuple[int, int]:
    index = bisect_right(line_starts, offset) - 1
    return index + 1, offset - line_starts[index]


def location_from_offsets(
    source: bytes,
    line_starts: tuple[int, ...],
    start: int,
    end: int,
) -> SourceLocation:
    """Construct a location from authoritative half-open byte offsets."""
    if (
        type(start) is not int
        or type(end) is not int
        or start < 0
        or end < start
        or end > len(source)
        or not line_starts
        or line_starts[0] != 0
    ):
        raise ValueError("Invalid source offsets.")
    start_line, start_column = _point(line_starts, start)
    end_line, end_column = _point(line_starts, end)
    return SourceLocation(start, end, start_line, start_column, end_line, end_column)


def _valid_location(
    location: object,
    source: bytes,
    line_starts: tuple[int, ...],
    *,
    allow_empty: bool,
) -> bool:
    if type(location) is not SourceLocation:
        return False
    values = (
        location.start_byte,
        location.end_byte,
        location.start_line,
        location.start_column,
        location.end_line,
        location.end_column,
    )
    if any(type(value) is not int for value in values):
        return False
    if not allow_empty and location.start_byte == location.end_byte:
        return False
    try:
        expected = location_from_offsets(
            source, line_starts, location.start_byte, location.end_byte
        )
    except ValueError:
        return False
    return expected == location


def validate_parsed_locations(
    parsed: ParsedFile, source: bytes, line_starts: tuple[int, ...]
) -> bool:
    """Validate every public parser location against one verified buffer."""
    if type(parsed.symbols) is not tuple or type(parsed.imports) is not tuple:
        return False
    if type(parsed.calls) is not tuple or type(parsed.issues) is not tuple:
        return False
    for symbol in parsed.symbols:
        if type(symbol) is not SymbolInfo or not _valid_location(
            symbol.location, source, line_starts, allow_empty=False
        ):
            return False
    for import_info in parsed.imports:
        if type(import_info) is not ImportInfo or not _valid_location(
            import_info.location, source, line_starts, allow_empty=False
        ):
            return False
    for call in parsed.calls:
        if type(call) is not CallSite or not _valid_location(
            call.location, source, line_starts, allow_empty=False
        ):
            return False
    for issue in parsed.issues:
        if type(issue) is not ParseIssue:
            return False
        if issue.location is not None and not _valid_location(
            issue.location, source, line_starts, allow_empty=True
        ):
            return False
    return True
