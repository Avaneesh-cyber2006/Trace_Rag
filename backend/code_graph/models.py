"""Immutable public values produced by the Code Graph Builder."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TypeVar

from backend.code_parser.models import (
    ParsedLanguage,
    ParseStatus,
    SourceLocation,
    SymbolKind,
)

from .exceptions import CodeGraphInvariantError


class GraphNodeKind(str, Enum):
    FILE = "file"
    SYMBOL = "symbol"


class GraphEdgeKind(str, Enum):
    DEFINES = "defines"
    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"


class ReferenceKind(str, Enum):
    IMPORT = "import"
    CALL = "call"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"


class ResolutionFailureReason(str, Enum):
    EXTERNAL = "external"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    INCOMPLETE_PARSE = "incomplete_parse"


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    node_kind: GraphNodeKind
    repository_namespace: str
    relative_path: str
    language: ParsedLanguage
    parse_status: ParseStatus
    symbol_kind: SymbolKind | None
    name: str | None
    qualified_name: str | None
    location: SourceLocation | None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_id: str
    edge_kind: GraphEdgeKind
    source_node_id: str
    target_node_id: str
    location: SourceLocation | None


@dataclass(frozen=True, slots=True)
class UnresolvedReference:
    reference_id: str
    reference_kind: ReferenceKind
    reason: ResolutionFailureReason
    repository_namespace: str
    relative_path: str
    language: ParsedLanguage
    source_node_id: str | None
    location: SourceLocation
    raw_target: str
    candidate_node_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.candidate_node_ids) is not tuple or not all(
            isinstance(candidate_id, str) for candidate_id in self.candidate_node_ids
        ):
            raise CodeGraphInvariantError("candidate_node_ids must be a tuple of strings")


_T = TypeVar("_T")


def _require_record_tuple(value: object, item_type: type[_T], field_name: str) -> None:
    if type(value) is not tuple or not all(
        isinstance(item, item_type) for item in value
    ):
        raise CodeGraphInvariantError(
            f"{field_name} must be a tuple of {item_type.__name__} values"
        )


def _snapshot_index(
    mapping: object,
    item_type: type[_T],
    field_name: str,
) -> Mapping[str, tuple[_T, ...]]:
    if not isinstance(mapping, Mapping):
        raise CodeGraphInvariantError(f"{field_name} must be a mapping")

    snapshot: dict[str, tuple[_T, ...]] = {}
    for key, values in mapping.items():
        if not isinstance(key, str) or type(values) not in (list, tuple):
            raise CodeGraphInvariantError(
                f"{field_name} must map strings to lists or tuples"
            )
        value_tuple = tuple(values)
        if not all(isinstance(item, item_type) for item in value_tuple):
            raise CodeGraphInvariantError(
                f"{field_name} values must contain only {item_type.__name__} values"
            )
        snapshot[key] = value_tuple
    return MappingProxyType(snapshot)


@dataclass(frozen=True, slots=True)
class CodeGraphInventory:
    repository_namespace: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    unresolved_references: tuple[UnresolvedReference, ...]
    outgoing_edges: Mapping[str, tuple[GraphEdge, ...]]
    incoming_edges: Mapping[str, tuple[GraphEdge, ...]]
    nodes_by_file: Mapping[str, tuple[GraphNode, ...]]
    nodes_by_qualified_name: Mapping[str, tuple[GraphNode, ...]]

    def __post_init__(self) -> None:
        _require_record_tuple(self.nodes, GraphNode, "nodes")
        _require_record_tuple(self.edges, GraphEdge, "edges")
        _require_record_tuple(
            self.unresolved_references,
            UnresolvedReference,
            "unresolved_references",
        )
        object.__setattr__(
            self,
            "outgoing_edges",
            _snapshot_index(self.outgoing_edges, GraphEdge, "outgoing_edges"),
        )
        object.__setattr__(
            self,
            "incoming_edges",
            _snapshot_index(self.incoming_edges, GraphEdge, "incoming_edges"),
        )
        object.__setattr__(
            self,
            "nodes_by_file",
            _snapshot_index(self.nodes_by_file, GraphNode, "nodes_by_file"),
        )
        object.__setattr__(
            self,
            "nodes_by_qualified_name",
            _snapshot_index(
                self.nodes_by_qualified_name,
                GraphNode,
                "nodes_by_qualified_name",
            ),
        )
