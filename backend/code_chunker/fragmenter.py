"""Deterministic exact-byte fragmentation for selected source ranges."""

from __future__ import annotations

from bisect import bisect_right

from .intervals import ChunkCandidate
from .models import ChunkerConfig


class FragmentationFailure(Exception):
    """Raised when an exact safe partition cannot be produced."""


def fragment_candidate(
    candidate: ChunkCandidate, source: bytes, config: ChunkerConfig
) -> tuple[tuple[int, int], ...]:
    """Split a candidate exhaustively at deterministic byte boundaries."""
    start = candidate.start_byte
    end = candidate.end_byte
    if start < 0 or end <= start or end > len(source):
        raise FragmentationFailure("Invalid candidate range.")
    if end - start <= config.max_chunk_bytes:
        return ((start, end),)

    newline_ends = tuple(
        offset + 1 for offset in range(start, end) if source[offset] == 0x0A
    )
    pieces: list[tuple[int, int]] = []
    current = start
    while current < end:
        if end - current <= config.max_chunk_bytes:
            boundary = end
        else:
            boundary = _last_boundary(
                newline_ends, current, current + config.target_chunk_bytes
            )
            if boundary is None:
                boundary = _last_boundary(
                    newline_ends, current, current + config.max_chunk_bytes
                )
            if boundary is None:
                boundary = min(current + config.max_chunk_bytes, end)
                if (
                    boundary < end
                    and source[boundary - 1] == 0x0D
                    and source[boundary] == 0x0A
                ):
                    boundary -= 1
                while boundary > current:
                    try:
                        source[current:boundary].decode("utf-8", errors="strict")
                        break
                    except UnicodeDecodeError:
                        boundary -= 1
        if boundary <= current or boundary - current > config.max_chunk_bytes:
            raise FragmentationFailure("Fragmentation made no safe progress.")
        pieces.append((current, boundary))
        current = boundary

    if pieces[0][0] != start or pieces[-1][1] != end:
        raise FragmentationFailure("Fragmentation did not exhaust the range.")
    return tuple(pieces)


def _last_boundary(
    boundaries: tuple[int, ...], current: int, limit: int
) -> int | None:
    index = bisect_right(boundaries, limit) - 1
    if index >= 0 and boundaries[index] > current:
        return boundaries[index]
    return None
