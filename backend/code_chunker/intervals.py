"""Iterative structural interval normalization and candidate selection."""

from __future__ import annotations

from dataclasses import dataclass

from backend.code_parser.models import ParsedFile, SymbolInfo, SymbolKind

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
    """Select whole structural leaves; residuals are added in a later phase."""
    del parsed_file, source
    candidates = [
        ChunkCandidate(
            ChunkKind.SYMBOL,
            interval.symbol.location.start_byte,
            interval.symbol.location.end_byte,
            interval.symbol,
        )
        for interval in intervals
        if interval.symbol.kind in _MEANINGFUL_KINDS and not interval.children
    ]
    return tuple(sorted(candidates, key=lambda item: (item.start_byte, item.end_byte)))
