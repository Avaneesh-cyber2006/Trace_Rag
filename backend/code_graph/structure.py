"""Direct structural relationships between graph nodes."""

from collections import defaultdict
from collections.abc import Mapping
import heapq
from typing import TYPE_CHECKING

from backend.code_parser.models import SymbolInfo

from .exceptions import CodeGraphValidationError
from .identities import edge_id
from .models import GraphEdge, GraphEdgeKind, GraphNode

if TYPE_CHECKING:
    from .indexes import GraphIndexes


def _reject_parent() -> None:
    raise CodeGraphValidationError("Invalid code graph inputs.")


def _check_parent_cycles(parent_ids: Mapping[str, str | None]) -> None:
    completed: set[str] = set()
    for symbol_id in parent_ids:
        if symbol_id in completed:
            continue
        trail: list[str] = []
        in_trail: set[str] = set()
        current: str | None = symbol_id
        while current is not None and current not in completed:
            if current in in_trail:
                _reject_parent()
            in_trail.add(current)
            trail.append(current)
            current = parent_ids[current]
        completed.update(trail)


def _resolve_parent_ids(
    nodes_by_id: Mapping[str, GraphNode],
    symbols_by_id: Mapping[str, SymbolInfo],
) -> dict[str, str | None]:
    """Resolve exact same-file parents through one range-event sweep per file."""

    symbols_by_file: defaultdict[str, list[str]] = defaultdict(list)
    for symbol_id in symbols_by_id:
        symbols_by_file[nodes_by_id[symbol_id].relative_path].append(symbol_id)

    parent_ids: dict[str, str | None] = {}
    for file_symbol_ids in symbols_by_file.values():
        ordered = sorted(
            file_symbol_ids,
            key=lambda symbol_id: (
                nodes_by_id[symbol_id].location.start_byte,
                -nodes_by_id[symbol_id].location.end_byte,
                symbol_id,
            ),
        )
        active_by_name: defaultdict[str, set[str]] = defaultdict(set)
        endings: list[tuple[int, str, str]] = []
        offset = 0
        while offset < len(ordered):
            start = nodes_by_id[ordered[offset]].location.start_byte
            while endings and endings[0][0] <= start:
                _, expired_id, expired_name = heapq.heappop(endings)
                active_by_name[expired_name].discard(expired_id)

            group_end = offset
            while (
                group_end < len(ordered)
                and nodes_by_id[ordered[group_end]].location.start_byte == start
            ):
                symbol_id = ordered[group_end]
                symbol = symbols_by_id[symbol_id]
                active_by_name[symbol.qualified_name].add(symbol_id)
                heapq.heappush(
                    endings,
                    (symbol.location.end_byte, symbol_id, symbol.qualified_name),
                )
                group_end += 1

            for position in range(offset, group_end):
                symbol_id = ordered[position]
                symbol = symbols_by_id[symbol_id]
                if symbol.parent_qualified_name is None:
                    parent_ids[symbol_id] = None
                    continue
                candidates = tuple(
                    candidate_id
                    for candidate_id in active_by_name.get(
                        symbol.parent_qualified_name, ()
                    )
                    if nodes_by_id[candidate_id].location.end_byte
                    >= symbol.location.end_byte
                )
                if len(candidates) != 1 or candidates[0] == symbol_id:
                    _reject_parent()
                parent_ids[symbol_id] = candidates[0]
            offset = group_end

    _check_parent_cycles(parent_ids)
    return parent_ids


def structural_edges(index: "GraphIndexes") -> tuple[GraphEdge, ...]:
    """Return one direct structural edge for every indexed symbol."""

    edges: list[GraphEdge] = []
    for symbol_id, parent_id in index.parent_ids.items():
        node = index.nodes_by_id[symbol_id]
        source = (
            index.file_ids[node.relative_path] if parent_id is None else parent_id
        )
        kind = (
            GraphEdgeKind.DEFINES
            if parent_id is None
            else GraphEdgeKind.CONTAINS
        )
        edges.append(
            GraphEdge(
                edge_id(
                    index.repository_namespace,
                    kind,
                    source,
                    symbol_id,
                    None,
                ),
                kind,
                source,
                symbol_id,
                None,
            )
        )
    return tuple(edges)
