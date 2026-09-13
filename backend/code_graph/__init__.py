"""Public interface for TraceRAG's Code Graph Builder."""

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
]
