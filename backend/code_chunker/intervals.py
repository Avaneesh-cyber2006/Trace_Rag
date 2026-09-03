"""Iterative structural interval normalization and candidate selection."""

from __future__ import annotations

from dataclasses import dataclass

from backend.code_parser.models import (
    ParsedFile,
    ParseIssueKind,
    ParseStatus,
    SymbolInfo,
    SymbolKind,
)

from .models import ChunkKind


class InvalidSymbolIntervals(Exception):
    """Raised when parser symbol intervals cannot form a safe partition."""


@dataclass(frozen=True, slots=True)
class SymbolInterval:
    symbol: SymbolInfo
    children: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ChunkCandidate:
    kind: ChunkKind
    start_byte: int
    end_byte: int
    owner: SymbolInfo | None


_MEANINGFUL_KINDS = frozenset(
    {
        SymbolKind.FUNCTION,
        SymbolKind.METHOD,
        SymbolKind.CONSTRUCTOR,
        SymbolKind.CONSTANT,
        SymbolKind.CLASS,
        SymbolKind.INTERFACE,
        SymbolKind.ENUM,
    }
)


def _symbol_key(symbol: SymbolInfo) -> tuple[int, int, str, str]:
    return (
        symbol.location.start_byte,
        -symbol.location.end_byte,
        symbol.kind.value,
        symbol.qualified_name,
    )


def build_symbol_intervals(
    symbols: tuple[SymbolInfo, ...], source_size: int
) -> tuple[SymbolInterval, ...]:
    """Normalize symbols into a deterministic direct-containment forest."""
    ordered = sorted(symbols, key=_symbol_key)
    unique: list[SymbolInfo] = []
    for symbol in ordered:
        if unique and (
            unique[-1].location.start_byte == symbol.location.start_byte
            and unique[-1].location.end_byte == symbol.location.end_byte
        ):
            if unique[-1] == symbol:
                continue
            raise InvalidSymbolIntervals("Incompatible symbols share a source range.")
        unique.append(symbol)

    child_lists: list[list[int]] = [[] for _ in unique]
    stack: list[int] = []
    for index, symbol in enumerate(unique):
        start = symbol.location.start_byte
        end = symbol.location.end_byte
        if start < 0 or end <= start or end > source_size:
            raise InvalidSymbolIntervals("Symbol range is invalid.")
        while stack and start >= unique[stack[-1]].location.end_byte:
            stack.pop()
        if stack:
            parent_index = stack[-1]
            parent = unique[parent_index]
            if end > parent.location.end_byte:
                raise InvalidSymbolIntervals("Symbol ranges cross.")
            if (
                symbol.parent_qualified_name is not None
                and symbol.parent_qualified_name != parent.qualified_name
            ):
                raise InvalidSymbolIntervals("Symbol parent metadata is inconsistent.")
            child_lists[parent_index].append(index)
        elif symbol.parent_qualified_name is not None:
            raise InvalidSymbolIntervals("Symbol parent metadata is inconsistent.")
        stack.append(index)

    return tuple(
        SymbolInterval(symbol, tuple(child_lists[index]))
        for index, symbol in enumerate(unique)
    )


def select_candidates(
    parsed_file: ParsedFile,
    source: bytes,
    intervals: tuple[SymbolInterval, ...],
) -> tuple[ChunkCandidate, ...]:
    """Build a non-overlapping structural partition and successful fallback."""
    candidates: list[ChunkCandidate] = []
    unsafe_locations = tuple(
        issue.location
        for issue in parsed_file.issues
        if issue.kind in (ParseIssueKind.SYNTAX_ERROR, ParseIssueKind.MISSING_NODE)
        and issue.location is not None
    )

    for interval in intervals:
        symbol = interval.symbol
        if symbol.kind not in _MEANINGFUL_KINDS:
            continue
        if not interval.children:
            candidates.append(
                ChunkCandidate(
                    ChunkKind.SYMBOL,
                    symbol.location.start_byte,
                    symbol.location.end_byte,
                    symbol,
                )
            )
            continue

        if parsed_file.status is ParseStatus.PARTIAL and any(
            _ranges_intersect(
                symbol.location.start_byte,
                symbol.location.end_byte,
                issue.start_byte,
                issue.end_byte,
            )
            for issue in unsafe_locations
        ):
            continue

        cursor = symbol.location.start_byte
        for child_index in interval.children:
            child = intervals[child_index].symbol.location
            _append_context(candidates, source, cursor, child.start_byte, symbol)
            cursor = child.end_byte
        _append_context(candidates, source, cursor, symbol.location.end_byte, symbol)

    candidates.sort(key=lambda item: (item.start_byte, item.end_byte))
    if parsed_file.status is ParseStatus.SUCCESS:
        cursor = 0
        existing = tuple(candidates)
        for candidate in existing:
            if candidate.start_byte < cursor:
                raise InvalidSymbolIntervals("Candidate ranges overlap.")
            _append_context(candidates, source, cursor, candidate.start_byte, None)
            cursor = candidate.end_byte
        _append_context(candidates, source, cursor, len(source), None)

    return tuple(sorted(candidates, key=lambda item: (item.start_byte, item.end_byte)))


def _ranges_intersect(start: int, end: int, other_start: int, other_end: int) -> bool:
    if other_start == other_end:
        return start <= other_start < end
    return start < other_end and other_start < end


def _append_context(
    candidates: list[ChunkCandidate],
    source: bytes,
    start: int,
    end: int,
    owner: SymbolInfo | None,
) -> None:
    if start >= end:
        return
    text = source[start:end].decode("utf-8", errors="strict")
    if not any(not character.isspace() for character in text):
        return
    candidates.append(ChunkCandidate(ChunkKind.CONTEXT, start, end, owner))
