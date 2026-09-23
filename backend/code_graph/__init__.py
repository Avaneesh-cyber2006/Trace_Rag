"""Public interface for TraceRAG's Code Graph Builder."""

from .builder import CodeGraphBuilder, build_code_graph
from .exceptions import (
    CodeGraphError,
    CodeGraphIdentityError,
    CodeGraphInvariantError,
    CodeGraphResolutionError,
    CodeGraphValidationError,
)
from .models import (
    CodeGraphInventory,
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
    UnresolvedReference,
)

__all__ = [
    "CodeGraphBuilder",
    "CodeGraphError",
    "CodeGraphIdentityError",
    "CodeGraphInventory",
    "CodeGraphInvariantError",
    "CodeGraphResolutionError",
    "CodeGraphValidationError",
    "GraphEdge",
    "GraphEdgeKind",
    "GraphNode",
    "GraphNodeKind",
    "ReferenceKind",
    "ResolutionFailureReason",
    "UnresolvedReference",
    "build_code_graph",
]
