"""Validation and lookup indexes for an optional Module 6 code graph.

Only the graph's repository namespace and its public ``nodes`` tuple are
read. Edges, unresolved references, the graph's public index mappings, and
source files are never touched.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence.exceptions import RuntimeEvidenceGraphError
from backend.runtime_evidence.validation import (
    require_relative_posix_path,
    require_scalar_text,
)

_GRAPH_ERROR_MESSAGE = "runtime evidence graph is invalid"
_LOCATION_FIELDS = (
    "start_byte",
    "end_byte",
    "start_line",
    "start_column",
    "end_line",
    "end_column",
)


@dataclass(frozen=True, slots=True)
class GraphFileEntry:
    node_id: str
    relative_path: str
    components: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphSymbolEntry:
    node_id: str
    start_line: int
    last_covered_line: int


@dataclass(frozen=True, slots=True)
class AssociationIndex:
    files_by_path: Mapping[str, GraphFileEntry]
    files_by_basename: Mapping[str, tuple[GraphFileEntry, ...]]
    symbols_by_file: Mapping[str, tuple[GraphSymbolEntry, ...]]


def _invalid() -> RuntimeEvidenceGraphError:
    return RuntimeEvidenceGraphError(_GRAPH_ERROR_MESSAGE)


def _require_max_namespace_bytes(value: object) -> int:
    if type(value) is bool or type(value) is not int or value <= 0:
        raise _invalid()
    return value


def _require_namespace(value: object, max_bytes: int) -> str:
    try:
        return require_scalar_text(
            value,
            "graph",
            RuntimeEvidenceGraphError,
            allow_empty=False,
            max_bytes=max_bytes,
        )
    except RuntimeEvidenceGraphError:
        raise _invalid() from None


def _require_node_id(value: object) -> str:
    try:
        return require_scalar_text(
            value,
            "graph",
            RuntimeEvidenceGraphError,
            allow_empty=False,
        )
    except RuntimeEvidenceGraphError:
        raise _invalid() from None


def _require_relative_path(value: object) -> str:
    try:
        return require_relative_posix_path(
            value, "graph", RuntimeEvidenceGraphError
        )
    except RuntimeEvidenceGraphError:
        raise _invalid() from None


def _valid_location(location: object) -> tuple[int, int]:
    """Validate the six structural SourceLocation fields without importing it."""
    try:
        fields = tuple(getattr(location, name) for name in _LOCATION_FIELDS)
    except AttributeError:
        raise _invalid() from None
    except Exception:
        raise _invalid() from None
    if any(type(value) is not int for value in fields):
        raise _invalid()
    (
        start_byte,
        end_byte,
        start_line,
        start_column,
        end_line,
        end_column,
    ) = fields
    if start_byte < 0 or start_byte > end_byte:
        raise _invalid()
    if start_line < 1 or end_line < 1 or start_line > end_line:
        raise _invalid()
    if start_column < 0 or end_column < 0:
        raise _invalid()
    if start_line == end_line and start_column > end_column:
        raise _invalid()
    last_covered_line = end_line if end_column > 0 else end_line - 1
    return start_line, last_covered_line


def build_association_index(
    graph: object,
    *,
    repository_namespace: str,
    max_namespace_bytes: int,
) -> AssociationIndex | None:
    """Validate a Module 6 graph and build an immutable association index."""
    if graph is None:
        return None
    max_bytes = _require_max_namespace_bytes(max_namespace_bytes)
    request_namespace = _require_namespace(repository_namespace, max_bytes)
    if type(graph) is not CodeGraphInventory:
        raise _invalid()
    try:
        graph_namespace = graph.repository_namespace
        nodes = graph.nodes
    except AttributeError:
        raise _invalid() from None
    if _require_namespace(graph_namespace, max_bytes) != request_namespace:
        raise _invalid()
    if type(nodes) is not tuple:
        raise _invalid()

    files_by_path: dict[str, GraphFileEntry] = {}
    file_buckets: dict[str, list[GraphFileEntry]] = {}
    symbol_buckets: dict[str, list[GraphSymbolEntry]] = {}
    symbol_paths: list[str] = []
    seen_node_ids: set[str] = set()

    for node in nodes:
        if type(node) is not GraphNode:
            raise _invalid()
        try:
            node_id = node.node_id
            node_namespace = node.repository_namespace
            node_kind = node.node_kind
            relative_path = node.relative_path
            location = node.location
        except AttributeError:
            raise _invalid() from None
        validated_id = _require_node_id(node_id)
        if validated_id in seen_node_ids:
            raise _invalid()
        seen_node_ids.add(validated_id)
        if _require_namespace(node_namespace, max_bytes) != request_namespace:
            raise _invalid()
        if type(node_kind) is not GraphNodeKind:
            raise _invalid()
        path = _require_relative_path(relative_path)
        if node_kind is GraphNodeKind.FILE:
            if location is not None or path in files_by_path:
                raise _invalid()
            components = tuple(path.split("/"))
            entry = GraphFileEntry(validated_id, path, components)
            files_by_path[path] = entry
            file_buckets.setdefault(components[-1], []).append(entry)
        elif node_kind is GraphNodeKind.SYMBOL:
            start_line, last_covered_line = _valid_location(location)
            symbol_buckets.setdefault(path, []).append(
                GraphSymbolEntry(
                    validated_id, start_line, last_covered_line
                )
            )
            symbol_paths.append(path)
        else:
            raise _invalid()

    for path in symbol_paths:
        if path not in files_by_path:
            raise _invalid()

    files_by_path_snapshot = MappingProxyType(
        {path: files_by_path[path] for path in sorted(files_by_path)}
    )
    files_by_basename_snapshot = MappingProxyType(
        {
            basename: tuple(
                sorted(
                    bucket,
                    key=lambda entry: (entry.relative_path, entry.node_id),
                )
            )
            for basename, bucket in sorted(file_buckets.items())
        }
    )
    symbols_by_file_snapshot = MappingProxyType(
        {
            path: tuple(
                sorted(
                    bucket,
                    key=lambda symbol: (
                        symbol.start_line,
                        symbol.last_covered_line,
                        symbol.node_id,
                    ),
                )
            )
            for path, bucket in sorted(symbol_buckets.items())
        }
    )
    return AssociationIndex(
        files_by_path=files_by_path_snapshot,
        files_by_basename=files_by_basename_snapshot,
        symbols_by_file=symbols_by_file_snapshot,
    )
