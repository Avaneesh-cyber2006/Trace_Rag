"""Independent checks for graph publication invariants."""

from collections import defaultdict
from typing import TYPE_CHECKING

from backend.code_parser.models import SymbolKind

from .exceptions import CodeGraphIdentityError, CodeGraphInvariantError
from .identities import edge_id, file_node_id, reference_id, symbol_node_id
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
from .structure import structural_edges

if TYPE_CHECKING:
    from .indexes import GraphIndexes


_CALLABLE = (SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CONSTRUCTOR)


def _invalid(message: str = "Graph invariants are invalid.") -> None:
    raise CodeGraphInvariantError(message)


def _edge_endpoints_valid(edge: GraphEdge, source: GraphNode, target: GraphNode) -> bool:
    if edge.edge_kind is GraphEdgeKind.DEFINES:
        return source.node_kind is GraphNodeKind.FILE and target.node_kind is GraphNodeKind.SYMBOL
    if edge.edge_kind is GraphEdgeKind.CONTAINS:
        return source.node_kind is GraphNodeKind.SYMBOL and target.node_kind is GraphNodeKind.SYMBOL
    if edge.edge_kind is GraphEdgeKind.IMPORTS:
        return source.node_kind is GraphNodeKind.FILE and target.node_kind is GraphNodeKind.FILE
    if edge.edge_kind is GraphEdgeKind.CALLS:
        return (
            source.node_kind is GraphNodeKind.SYMBOL
            and target.node_kind is GraphNodeKind.SYMBOL
            and source.symbol_kind in _CALLABLE
            and target.symbol_kind in _CALLABLE
        )
    if edge.edge_kind is GraphEdgeKind.INHERITS:
        return (
            source.node_kind is GraphNodeKind.SYMBOL
            and target.node_kind is GraphNodeKind.SYMBOL
            and source.symbol_kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
            and target.symbol_kind is source.symbol_kind
        )
    if edge.edge_kind is GraphEdgeKind.IMPLEMENTS:
        return (
            source.node_kind is GraphNodeKind.SYMBOL
            and target.node_kind is GraphNodeKind.SYMBOL
            and source.symbol_kind in (SymbolKind.CLASS, SymbolKind.ENUM)
            and target.symbol_kind is SymbolKind.INTERFACE
        )
    return False


def _unresolved_candidates_valid(
    item: UnresolvedReference,
    source: GraphNode | None,
    candidates: tuple[GraphNode, ...],
) -> bool:
    if not candidates:
        return True
    if source is None or item.reason.value != "ambiguous":
        return False
    for target in candidates:
        if item.reference_kind is ReferenceKind.IMPORT:
            valid = source.node_kind is GraphNodeKind.FILE and target.node_kind is GraphNodeKind.FILE
        elif item.reference_kind is ReferenceKind.CALL:
            valid = (
                source.node_kind is GraphNodeKind.SYMBOL
                and target.node_kind is GraphNodeKind.SYMBOL
                and source.symbol_kind in _CALLABLE
                and target.symbol_kind in _CALLABLE
            )
        elif item.reference_kind is ReferenceKind.INHERITS:
            valid = (
                source.node_kind is GraphNodeKind.SYMBOL
                and target.node_kind is GraphNodeKind.SYMBOL
                and source.symbol_kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
                and target.symbol_kind is source.symbol_kind
            )
        else:
            valid = (
                source.node_kind is GraphNodeKind.SYMBOL
                and target.node_kind is GraphNodeKind.SYMBOL
                and source.symbol_kind in (SymbolKind.CLASS, SymbolKind.ENUM)
                and target.symbol_kind is SymbolKind.INTERFACE
            )
        if not valid:
            return False
    return True


def _check_structural_cycles(edges: tuple[GraphEdge, ...]) -> None:
    children: dict[str, list[str]] = defaultdict(list)
    symbols: set[str] = set()
    for edge in edges:
        if edge.edge_kind in (GraphEdgeKind.DEFINES, GraphEdgeKind.CONTAINS):
            children[edge.source_node_id].append(edge.target_node_id)
            symbols.add(edge.target_node_id)
    state: dict[str, int] = {}
    for start in symbols:
        if state.get(start) == 2:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node_id, exiting = stack.pop()
            if exiting:
                state[node_id] = 2
                continue
            current = state.get(node_id, 0)
            if current == 1:
                _invalid("Structural edges contain a cycle.")
            if current == 2:
                continue
            state[node_id] = 1
            stack.append((node_id, True))
            for child in children.get(node_id, ()):
                if state.get(child) == 1:
                    _invalid("Structural edges contain a cycle.")
                if state.get(child) != 2:
                    stack.append((child, False))


def _validate_graph(graph: CodeGraphInventory, index: "GraphIndexes") -> None:
    """Verify canonical IDs, projections, relationships, and derived indexes."""

    if type(graph) is not CodeGraphInventory:
        _invalid("Expected a CodeGraphInventory.")
    if graph.repository_namespace != index.repository_namespace:
        _invalid("Graph namespace differs from its validated inputs.")

    nodes_by_id: dict[str, GraphNode] = {}
    expected_nodes: dict[str, GraphNode] = {}
    for path, parsed in index.files_by_path.items():
        file_node = GraphNode(
            file_node_id(index.repository_namespace, path),
            GraphNodeKind.FILE,
            index.repository_namespace,
            path,
            parsed.language,
            parsed.status,
            None,
            None,
            None,
            None,
        )
        expected_nodes[file_node.node_id] = file_node
    for symbol_id, symbol in index.symbols_by_id.items():
        indexed_node = index.nodes_by_id.get(symbol_id)
        if indexed_node is None:
            _invalid("Indexed symbol has no node projection.")
        parsed = index.files_by_path.get(indexed_node.relative_path)
        if parsed is None or symbol not in parsed.symbols:
            _invalid("Indexed symbol does not belong to an indexed file.")
        projected = GraphNode(
            symbol_node_id(index.repository_namespace, indexed_node.relative_path, symbol),
            GraphNodeKind.SYMBOL,
            index.repository_namespace,
            indexed_node.relative_path,
            parsed.language,
            parsed.status,
            symbol.kind,
            symbol.name,
            symbol.qualified_name,
            symbol.location,
        )
        expected_nodes[projected.node_id] = projected

    if len(graph.nodes) != len({node.node_id for node in graph.nodes}):
        _invalid("Node IDs must be unique.")
    if graph.nodes != tuple(sorted(graph.nodes, key=lambda node: node.node_id)):
        _invalid("Nodes are not canonically sorted.")
    for node in graph.nodes:
        if node.repository_namespace != index.repository_namespace:
            _invalid("Node namespace differs from the graph namespace.")
        nodes_by_id[node.node_id] = node
    if nodes_by_id != expected_nodes:
        _invalid("Nodes do not match the indexed parser evidence.")

    if len(graph.edges) != len({edge.edge_id for edge in graph.edges}):
        _invalid("Edge IDs must be unique.")
    if graph.edges != tuple(sorted(graph.edges, key=lambda edge: edge.edge_id)):
        _invalid("Edges are not canonically sorted.")
    for edge in graph.edges:
        if type(edge.edge_kind) is not GraphEdgeKind:
            _invalid("Edge kind is invalid.")
        source = nodes_by_id.get(edge.source_node_id)
        target = nodes_by_id.get(edge.target_node_id)
        if source is None or target is None or not _edge_endpoints_valid(edge, source, target):
            _invalid("An edge has an invalid or dangling endpoint.")
        if edge.edge_kind in (GraphEdgeKind.DEFINES, GraphEdgeKind.CONTAINS):
            if source.relative_path != target.relative_path or target.node_kind is not GraphNodeKind.SYMBOL:
                _invalid("Structural edges must directly connect symbols in one file.")
            expected_parent = index.parent_ids.get(target.node_id)
            expected_source = (
                index.file_ids[target.relative_path]
                if expected_parent is None
                else expected_parent
            )
            expected_kind = GraphEdgeKind.DEFINES if expected_parent is None else GraphEdgeKind.CONTAINS
            if edge.source_node_id != expected_source or edge.edge_kind is not expected_kind:
                _invalid("Structural edges differ from indexed direct parents.")
        if edge.edge_id != edge_id(
            index.repository_namespace,
            edge.edge_kind,
            edge.source_node_id,
            edge.target_node_id,
            edge.location,
        ):
            _invalid("Edge identity does not match its content.")

    expected_structural = structural_edges(index)
    actual_structural = tuple(
        edge for edge in graph.edges
        if edge.edge_kind in (GraphEdgeKind.DEFINES, GraphEdgeKind.CONTAINS)
    )
    if actual_structural != tuple(sorted(expected_structural, key=lambda edge: edge.edge_id)):
        _invalid("Each symbol must have exactly one indexed structural parent.")
    _check_structural_cycles(actual_structural)

    if len(graph.unresolved_references) != len(
        {item.reference_id for item in graph.unresolved_references}
    ):
        _invalid("Unresolved reference IDs must be unique.")
    if graph.unresolved_references != tuple(
        sorted(graph.unresolved_references, key=lambda item: item.reference_id)
    ):
        _invalid("Unresolved references are not canonically sorted.")
    for item in graph.unresolved_references:
        if (
            type(item.reference_kind) is not ReferenceKind
            or type(item.reason) is not ResolutionFailureReason
            or type(item.relative_path) is not str
            or not item.relative_path
            or type(item.raw_target) is not str
            or (item.source_node_id is not None and (
                type(item.source_node_id) is not str or not item.source_node_id
            ))
        ):
            _invalid("Unresolved reference fields are invalid.")
        if item.repository_namespace != index.repository_namespace:
            _invalid("Unresolved reference namespace is invalid.")
        parsed = index.files_by_path.get(item.relative_path)
        if parsed is None or parsed.language is not item.language:
            _invalid("Unresolved reference file or language is invalid.")
        source = nodes_by_id.get(item.source_node_id) if item.source_node_id else None
        if item.source_node_id is not None and (
            source is None or source.relative_path != item.relative_path
        ):
            _invalid("Unresolved reference source is outside its file.")
        if item.reference_kind is ReferenceKind.IMPORT:
            source_valid = source is not None and source.node_kind is GraphNodeKind.FILE
        elif item.reference_kind is ReferenceKind.CALL:
            source_valid = source is None or (
                source.node_kind is GraphNodeKind.SYMBOL
                and source.symbol_kind in _CALLABLE
            )
        elif item.reference_kind is ReferenceKind.INHERITS:
            source_valid = source is not None and source.symbol_kind in (
                SymbolKind.CLASS,
                SymbolKind.INTERFACE,
            )
        else:
            source_valid = source is not None and source.symbol_kind in (
                SymbolKind.CLASS,
                SymbolKind.ENUM,
            )
        if not source_valid:
            _invalid("Unresolved reference has an invalid source kind.")
        candidate_ids = item.candidate_node_ids
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            _invalid("Unresolved candidate IDs must be unique and sorted.")
        candidates = tuple(nodes_by_id.get(candidate_id) for candidate_id in candidate_ids)
        if any(candidate is None for candidate in candidates):
            _invalid("Unresolved reference has a dangling candidate.")
        if not _unresolved_candidates_valid(item, source, candidates):
            _invalid("Unresolved reference candidates have invalid kinds.")
        if item.reference_id != reference_id(
            index.repository_namespace,
            item.relative_path,
            item.reference_kind,
            item.source_node_id,
            item.location,
            item.raw_target,
        ):
            _invalid("Unresolved reference identity does not match its content.")

    outgoing: dict[str, list[GraphEdge]] = {node_id: [] for node_id in nodes_by_id}
    incoming: dict[str, list[GraphEdge]] = {node_id: [] for node_id in nodes_by_id}
    by_file: dict[str, list[GraphNode]] = {path: [] for path in index.files_by_path}
    by_name: dict[str, list[GraphNode]] = defaultdict(list)
    for node in graph.nodes:
        by_file[node.relative_path].append(node)
        if node.node_kind is GraphNodeKind.SYMBOL and node.qualified_name is not None:
            by_name[node.qualified_name].append(node)
    for edge in graph.edges:
        outgoing[edge.source_node_id].append(edge)
        incoming[edge.target_node_id].append(edge)

    expected_outgoing = {
        key: tuple(sorted(values, key=lambda edge: edge.edge_id))
        for key, values in sorted(outgoing.items())
    }
    expected_incoming = {
        key: tuple(sorted(values, key=lambda edge: edge.edge_id))
        for key, values in sorted(incoming.items())
    }
    expected_by_file = {
        key: tuple(sorted(values, key=lambda node: node.node_id))
        for key, values in sorted(by_file.items())
    }
    expected_by_name = {
        key: tuple(sorted(values, key=lambda node: node.node_id))
        for key, values in sorted(by_name.items())
    }
    if dict(graph.outgoing_edges) != expected_outgoing:
        _invalid("Outgoing edge index is inconsistent.")
    if dict(graph.incoming_edges) != expected_incoming:
        _invalid("Incoming edge index is inconsistent.")
    if dict(graph.nodes_by_file) != expected_by_file:
        _invalid("File node index is inconsistent.")
    if dict(graph.nodes_by_qualified_name) != expected_by_name:
        _invalid("Qualified-name node index is inconsistent.")


def validate_graph(graph: CodeGraphInventory, index: "GraphIndexes") -> None:
    """Translate malformed record values into the public invariant error."""

    try:
        _validate_graph(graph, index)
    except CodeGraphInvariantError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, CodeGraphIdentityError) as exc:
        raise CodeGraphInvariantError("Graph invariants are invalid.") from exc
