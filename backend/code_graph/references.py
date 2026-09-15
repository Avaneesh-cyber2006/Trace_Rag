"""Reference ownership and graph-record materialization."""

from dataclasses import dataclass

from backend.code_graph.exceptions import CodeGraphResolutionError
from backend.code_graph.identities import edge_id, reference_id
from backend.code_graph.indexes import GraphIndexes
from backend.code_graph.models import (
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
    UnresolvedReference,
)
from backend.code_graph.resolvers.base import Resolution
from backend.code_parser.models import (
    CallKind,
    CallSite,
    ImportInfo,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
    SourceLocation,
    SymbolKind,
)


_CALLABLE_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CONSTRUCTOR)
_EDGE_KIND = {
    ReferenceKind.IMPORT: GraphEdgeKind.IMPORTS,
    ReferenceKind.CALL: GraphEdgeKind.CALLS,
    ReferenceKind.INHERITS: GraphEdgeKind.INHERITS,
    ReferenceKind.IMPLEMENTS: GraphEdgeKind.IMPLEMENTS,
}


@dataclass(frozen=True, slots=True)
class Reference:
    reference_kind: ReferenceKind
    relative_path: str
    language: ParsedLanguage
    source_node_id: str | None
    location: SourceLocation
    raw_target: str
    call_kind: CallKind | None = None
    blocked_reason: ResolutionFailureReason | None = None


def _contains(outer: SourceLocation, inner: SourceLocation) -> bool:
    return (
        outer.start_byte <= inner.start_byte
        and inner.end_byte <= outer.end_byte
    )


def _overlaps(first: SourceLocation, second: SourceLocation) -> bool:
    return (
        first.start_byte <= second.end_byte
        and second.start_byte <= first.end_byte
    )


def _relevant_parse_loss(file: ParsedFile, location: SourceLocation) -> bool:
    if file.status is ParseStatus.FAILED:
        return True
    if file.status is not ParseStatus.PARTIAL:
        return False
    return any(
        issue.location is None or _overlaps(issue.location, location)
        for issue in file.issues
    )


def _require_file(index: GraphIndexes, file: ParsedFile) -> str:
    indexed = index.files_by_path.get(file.relative_path)
    if indexed != file:
        raise CodeGraphResolutionError("Reference file is not present in graph indexes.")
    return index.file_ids[file.relative_path]


def import_reference(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> Reference:
    """Create module-level import evidence sourced by its FILE node."""

    source_node_id = _require_file(index, file)
    return Reference(
        ReferenceKind.IMPORT,
        file.relative_path,
        file.language,
        source_node_id,
        item.location,
        item.module,
    )


def call_reference(index: GraphIndexes, file: ParsedFile, item: CallSite) -> Reference:
    """Create call evidence only when one exact callable owner is proven."""

    _require_file(index, file)
    candidates: tuple[str, ...] = ()
    if item.caller_qualified_name is not None:
        candidates = tuple(
            node_id
            for node_id in index.by_qualified_name.get(
                item.caller_qualified_name, ()
            )
            if (
                (node := index.nodes_by_id[node_id]).relative_path
                == file.relative_path
                and node.node_kind is GraphNodeKind.SYMBOL
                and node.symbol_kind in _CALLABLE_KINDS
                and node.location is not None
                and _contains(node.location, item.location)
            )
        )

    source_node_id: str | None = None
    blocked_reason: ResolutionFailureReason | None = None
    if len(candidates) == 1:
        source_node_id = candidates[0]
    elif len(candidates) > 1:
        blocked_reason = ResolutionFailureReason.AMBIGUOUS
    else:
        blocked_reason = (
            ResolutionFailureReason.INCOMPLETE_PARSE
            if _relevant_parse_loss(file, item.location)
            else ResolutionFailureReason.UNSUPPORTED
        )

    return Reference(
        ReferenceKind.CALL,
        file.relative_path,
        file.language,
        source_node_id,
        item.location,
        item.callee_text,
        item.kind,
        blocked_reason,
    )


def type_references(
    index: GraphIndexes, file: ParsedFile, symbol_id: str
) -> tuple[Reference, ...]:
    """Create exact base and implementation evidence for one declaration."""

    _require_file(index, file)
    symbol = index.symbols_by_id.get(symbol_id)
    node = index.nodes_by_id.get(symbol_id)
    if (
        symbol is None
        or node is None
        or node.node_kind is not GraphNodeKind.SYMBOL
        or node.relative_path != file.relative_path
    ):
        raise CodeGraphResolutionError("Type source is not present in its indexed file.")

    return tuple(
        Reference(
            kind,
            file.relative_path,
            file.language,
            symbol_id,
            symbol.location,
            raw_target,
        )
        for kind, raw_targets in (
            (ReferenceKind.INHERITS, symbol.base_types),
            (ReferenceKind.IMPLEMENTS, symbol.implemented_types),
        )
        for raw_target in raw_targets
    )


def _require_reference_source(
    index: GraphIndexes, reference: Reference
) -> GraphNode | None:
    if reference.relative_path not in index.files_by_path:
        raise CodeGraphResolutionError("Reference path is not present in graph indexes.")
    parsed = index.files_by_path[reference.relative_path]
    if parsed.language is not reference.language:
        raise CodeGraphResolutionError("Reference language does not match its indexed file.")
    if reference.source_node_id is None:
        return None
    source = index.nodes_by_id.get(reference.source_node_id)
    if source is None or source.relative_path != reference.relative_path:
        raise CodeGraphResolutionError("Reference source is not present in its indexed file.")
    return source


def _validate_edge_endpoints(
    reference: Reference, source: GraphNode, target: GraphNode
) -> None:
    if reference.reference_kind is ReferenceKind.IMPORT:
        valid = (
            source.node_kind is GraphNodeKind.FILE
            and target.node_kind is GraphNodeKind.FILE
        )
    elif reference.reference_kind is ReferenceKind.CALL:
        valid = (
            source.node_kind is GraphNodeKind.SYMBOL
            and target.node_kind is GraphNodeKind.SYMBOL
            and source.symbol_kind in _CALLABLE_KINDS
            and target.symbol_kind in _CALLABLE_KINDS
            and (
                reference.call_kind is not CallKind.CONSTRUCTOR
                or target.symbol_kind is SymbolKind.CONSTRUCTOR
            )
        )
    elif reference.reference_kind is ReferenceKind.INHERITS:
        valid = (
            source.node_kind is GraphNodeKind.SYMBOL
            and target.node_kind is GraphNodeKind.SYMBOL
            and source.symbol_kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
            and source.symbol_kind is target.symbol_kind
        )
    else:
        valid = (
            source.node_kind is GraphNodeKind.SYMBOL
            and target.node_kind is GraphNodeKind.SYMBOL
            and source.symbol_kind in (SymbolKind.CLASS, SymbolKind.ENUM)
            and target.symbol_kind is SymbolKind.INTERFACE
        )
    if not valid:
        raise CodeGraphResolutionError("Resolver returned invalid graph endpoints.")


def materialize(
    index: GraphIndexes, reference: Reference, resolution: Resolution
) -> GraphEdge | UnresolvedReference:
    """Convert one terminal resolution into exactly one graph record."""

    if type(reference) is not Reference or type(resolution) is not Resolution:
        raise CodeGraphResolutionError("Invalid materialization input.")
    source = _require_reference_source(index, reference)

    if reference.blocked_reason is not None:
        if type(reference.blocked_reason) is not ResolutionFailureReason:
            raise CodeGraphResolutionError("Invalid blocked reference reason.")
        reason = reference.blocked_reason
        candidates: tuple[str, ...] = ()
    elif resolution.reason is not None:
        reason = resolution.reason
        candidates = resolution.candidate_node_ids
    else:
        if source is None:
            raise CodeGraphResolutionError("A resolved edge requires a source node.")
        target_id = resolution.target_node_id
        if target_id is None:
            raise CodeGraphResolutionError("Resolved outcome has no target.")
        target = index.nodes_by_id.get(target_id)
        if target is None:
            raise CodeGraphResolutionError("Resolver target is not an indexed node.")
        _validate_edge_endpoints(reference, source, target)
        kind = _EDGE_KIND[reference.reference_kind]
        return GraphEdge(
            edge_id(
                index.repository_namespace,
                kind,
                source.node_id,
                target.node_id,
                reference.location,
            ),
            kind,
            source.node_id,
            target.node_id,
            reference.location,
        )

    if any(candidate not in index.nodes_by_id for candidate in candidates):
        raise CodeGraphResolutionError("Resolver candidate is not an indexed node.")
    return UnresolvedReference(
        reference_id(
            index.repository_namespace,
            reference.relative_path,
            reference.reference_kind,
            reference.source_node_id,
            reference.location,
            reference.raw_target,
        ),
        reference.reference_kind,
        reason,
        index.repository_namespace,
        reference.relative_path,
        reference.language,
        reference.source_node_id,
        reference.location,
        reference.raw_target,
        candidates,
    )
