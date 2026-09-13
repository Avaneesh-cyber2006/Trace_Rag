"""Deterministic identities for code graph records."""

import json
from hashlib import sha256

from backend.code_parser.models import SourceLocation, SymbolInfo

from .exceptions import CodeGraphIdentityError
from .models import GraphEdgeKind, ReferenceKind


def _digest(payload: list[object]) -> str:
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CodeGraphIdentityError("Invalid graph identity material.") from exc
    return sha256(encoded).hexdigest()


def file_node_id(namespace: str, path: str) -> str:
    return _digest(["tracerag-code-graph-node-v1", namespace, "file", path])


def symbol_node_id(namespace: str, path: str, symbol: SymbolInfo) -> str:
    return _digest(
        [
            "tracerag-code-graph-node-v1",
            namespace,
            "symbol",
            path,
            symbol.kind.value,
            symbol.qualified_name,
            symbol.location.start_byte,
            symbol.location.end_byte,
        ]
    )


def edge_id(
    namespace: str,
    kind: GraphEdgeKind,
    source: str,
    target: str,
    location: SourceLocation | None,
) -> str:
    location_material = (
        None if location is None else [location.start_byte, location.end_byte]
    )
    return _digest(
        [
            "tracerag-code-graph-edge-v1",
            namespace,
            kind.value,
            source,
            target,
            location_material,
        ]
    )


def reference_id(
    namespace: str,
    path: str,
    kind: ReferenceKind,
    source: str | None,
    location: SourceLocation,
    raw_target: str,
) -> str:
    return _digest(
        [
            "tracerag-code-graph-unresolved-v1",
            namespace,
            path,
            kind.value,
            source,
            [location.start_byte, location.end_byte],
            raw_target,
        ]
    )
