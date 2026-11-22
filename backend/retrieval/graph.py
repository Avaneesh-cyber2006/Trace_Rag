"""One-hop, bounded Module 6 graph context over published Module 5 chunks.

This is a *pure projection*, not an implicit graph-to-vector-store join.  The
caller must supply a CodeGraphInventory derived from the same source revision
as the pinned lexical snapshot.  Module 6 does not currently provide a source
revision/generation binding that can prove this; consequently this class is
not automatically invoked by HybridRetriever.  It cannot certify a graph's
freshness merely because its namespace and symbol names agree.

Only Module 5's authoritative lexical documents are returned as source text.
Unresolved references and file-level/undefined chunk associations are never
expanded.  No filesystem, network, Chroma, embedding, or graph traversal beyond
one resolved edge is performed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
import re

from backend.code_parser.models import ParsedLanguage, ParseStatus, SymbolKind
from backend.code_graph.models import (
    CodeGraphInventory,
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
)

from .errors import InvalidHybridEvidence, InvalidRetrievalQuery
from .hybrid import HybridHit
from .lexical import LexicalDocument, LexicalIndex, _valid_path

_GRAPH_ERROR = "Graph retrieval evidence is invalid or inconsistent."
_QUERY_ERROR = "Graph retrieval request is invalid or exceeds its limit."
_MAX_NODES = 65_536
_MAX_EDGES = 262_144
_MAX_SEEDS = 100
_MAX_RESULTS = 100
_MAX_PER_SEED = 32
_MAX_EDGES_PER_NODE = 2_048
_MAX_CANDIDATES_PER_SEED = 4_096
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_EDGES = frozenset((
    GraphEdgeKind.CALLS,
    GraphEdgeKind.CONTAINS,
    GraphEdgeKind.INHERITS,
    GraphEdgeKind.IMPLEMENTS,
))


def _symbol_key(value: GraphNode | LexicalDocument) -> tuple[str, str, str, str] | None:
    """Match exact relative path, language, qualified name and symbol kind."""
    if isinstance(value, GraphNode):
        if (
            value.node_kind is not GraphNodeKind.SYMBOL
            or value.qualified_name is None
            or value.symbol_kind is None
        ):
            return None
        return (
            value.relative_path,
            value.language.value,
            value.qualified_name,
            value.symbol_kind.value,
        )
    if (
        value.chunk_kind not in ("symbol", "fragment")
        or value.qualified_name is None
        or value.symbol_kind is None
    ):
        return None
    return (
        value.relative_path, value.language, value.qualified_name, value.symbol_kind,
    )


@dataclass(frozen=True, slots=True)
class GraphContext:
    """One source chunk related by an explicitly resolved, one-hop graph edge.

    ``document`` is the *same immutable object* supplied by the lexical index.
    The graph contributes only a relationship and never provides source text.
    """

    document: LexicalDocument = field(repr=False)
    seed_chunk_id: str
    edge_id: str
    edge_kind: GraphEdgeKind
    direction: str
    rank: int


class GraphExpander:
    """Project graph neighbors onto trusted chunk evidence with strict bounds.

    A graph alone cannot certify it belongs to the active Chroma generation.
    Callers MUST establish same-source-revision provenance before calling it.
    Passing a stale but structurally valid graph is an application error.
    """

    __slots__ = ("_index", "_node_docs", "_chunk_nodes", "_outgoing", "_incoming")

    def __init__(self, index: LexicalIndex, graph: CodeGraphInventory) -> None:
        if (
            type(index) is not LexicalIndex
            or type(graph) is not CodeGraphInventory
            or graph.repository_namespace != index.repository_namespace
            or len(graph.nodes) > _MAX_NODES
            or len(graph.edges) > _MAX_EDGES
        ):
            raise InvalidHybridEvidence(_GRAPH_ERROR)

        nodes_by_id: dict[str, GraphNode] = {}
        keys_to_nodes: defaultdict[tuple[str, str, str, str], list[str]] = defaultdict(list)
        for node in graph.nodes:
            if (
                type(node) is not GraphNode
                or type(node.node_id) is not str
                or not node.node_id
                or node.node_id in nodes_by_id
                or node.repository_namespace != index.repository_namespace
                or not isinstance(node.node_kind, GraphNodeKind)
                or not _valid_path(node.relative_path)
                or type(node.language) is not ParsedLanguage
                or type(node.parse_status) is not ParseStatus
                or (node.symbol_kind is not None and type(node.symbol_kind) is not SymbolKind)
                or (node.qualified_name is not None and type(node.qualified_name) is not str)
            ):
                raise InvalidHybridEvidence(_GRAPH_ERROR)
            nodes_by_id[node.node_id] = node
            key = _symbol_key(node)
            if key is not None:
                keys_to_nodes[key].append(node.node_id)

        documents_by_key: defaultdict[tuple[str, str, str, str], list[LexicalDocument]] = defaultdict(list)
        for document in index.documents:
            key = _symbol_key(document)
            if key is not None:
                documents_by_key[key].append(document)

        # Never guess when Module 6 has overloaded/ambiguous graph symbols.
        # Multiple chunks for one unambiguous symbol (fragments) are allowed.
        node_docs: dict[str, tuple[LexicalDocument, ...]] = {}
        chunk_nodes: defaultdict[str, list[str]] = defaultdict(list)
        for key, node_ids in keys_to_nodes.items():
            if len(node_ids) != 1:
                continue
            node_id = node_ids[0]
            docs = tuple(sorted(documents_by_key.get(key, ()), key=lambda d: d.chunk_id))
            if docs:
                node_docs[node_id] = docs
                for doc in docs:
                    chunk_nodes[doc.chunk_id].append(node_id)

        outgoing: defaultdict[str, list[GraphEdge]] = defaultdict(list)
        incoming: defaultdict[str, list[GraphEdge]] = defaultdict(list)
        seen_edges: set[str] = set()
        for edge in graph.edges:
            if (
                type(edge) is not GraphEdge
                or type(edge.edge_id) is not str
                or not edge.edge_id
                or edge.edge_id in seen_edges
                or not isinstance(edge.edge_kind, GraphEdgeKind)
                or type(edge.source_node_id) is not str
                or type(edge.target_node_id) is not str
                or edge.source_node_id not in nodes_by_id
                or edge.target_node_id not in nodes_by_id
            ):
                raise InvalidHybridEvidence(_GRAPH_ERROR)
            seen_edges.add(edge.edge_id)
            if edge.edge_kind in _ALLOWED_EDGES:
                outgoing[edge.source_node_id].append(edge)
                incoming[edge.target_node_id].append(edge)

        if any(
            len(edges) > _MAX_EDGES_PER_NODE
            for bucket in (outgoing, incoming) for edges in bucket.values()
        ):
            raise InvalidHybridEvidence(_GRAPH_ERROR)

        def order(edge: GraphEdge) -> tuple[str, str, str, str]:
            return (edge.edge_kind.value, edge.source_node_id, edge.target_node_id, edge.edge_id)

        self._index = index
        self._node_docs = node_docs
        self._chunk_nodes = {key: tuple(sorted(ids)) for key, ids in chunk_nodes.items()}
        self._outgoing = {key: tuple(sorted(edges, key=order)) for key, edges in outgoing.items()}
        self._incoming = {key: tuple(sorted(edges, key=order)) for key, edges in incoming.items()}

    def expand(
        self,
        seeds: tuple[HybridHit, ...],
        top_k: int,
        *,
        direction: str = "both",
        per_seed_limit: int = _MAX_PER_SEED,
    ) -> tuple[GraphContext, ...]:
        """Return one-hop *additional* context, never modifying seed rank/score.

        Seed order is authoritative RRF order. Within a seed, edges are sorted
        by kind, direction, endpoint and ID; documents by authoritative ID.
        A chunk can appear at most once in the returned context, and no seed is
        returned as its own context. No recursive walk is performed.
        """
        if (
            type(seeds) is not tuple
            or len(seeds) > _MAX_SEEDS
            or type(top_k) is not int
            or not 1 <= top_k <= _MAX_RESULTS
            or type(direction) is not str
            or direction not in ("both", "outgoing", "incoming")
            or type(per_seed_limit) is not int
            or not 1 <= per_seed_limit <= _MAX_PER_SEED
        ):
            raise InvalidRetrievalQuery(_QUERY_ERROR)

        seed_ids: set[str] = set()
        for expected_rank, seed in enumerate(seeds, 1):
            if (
                type(seed) is not HybridHit
                or type(seed.rank) is not int
                or seed.rank != expected_rank
            ):
                raise InvalidHybridEvidence(_GRAPH_ERROR)
            if type(seed.chunk_id) is not str or _HASH.fullmatch(seed.chunk_id) is None:
                raise InvalidHybridEvidence(_GRAPH_ERROR)
            document = self._index.lookup(seed.chunk_id)
            if (
                document is None
                or seed.chunk_id in seed_ids
                or any(
                    getattr(document, name) != getattr(seed, name)
                    for name in (
                        "repository_namespace", "content_hash", "relative_path",
                        "language", "chunk_kind", "symbol_kind", "qualified_name",
                        "parent_qualified_name", "content",
                    )
                )
                or type(seed.score) not in (float, int)
                or not math.isfinite(seed.score)
                or seed.score <= 0
            ):
                raise InvalidHybridEvidence(_GRAPH_ERROR)
            seed_ids.add(seed.chunk_id)

        emitted: set[str] = set()
        contexts: list[GraphContext] = []
        for seed in seeds:
            within_seed = 0
            examined = 0
            for node_id in self._chunk_nodes.get(seed.chunk_id, ()):
                choices: list[tuple[str, GraphEdge, str]] = []
                if direction in ("both", "outgoing"):
                    choices.extend(("outgoing", edge, edge.target_node_id) for edge in self._outgoing.get(node_id, ()))
                if direction in ("both", "incoming"):
                    choices.extend(("incoming", edge, edge.source_node_id) for edge in self._incoming.get(node_id, ()))
                choices.sort(key=lambda choice: (
                    choice[1].edge_kind.value, choice[0], choice[2], choice[1].edge_id
                ))
                for edge_direction, edge, neighbor_id in choices:
                    for doc in self._node_docs.get(neighbor_id, ()):
                        examined += 1
                        if examined > _MAX_CANDIDATES_PER_SEED:
                            break
                        if doc.chunk_id in seed_ids or doc.chunk_id in emitted:
                            continue
                        emitted.add(doc.chunk_id)
                        contexts.append(GraphContext(
                            document=doc,
                            seed_chunk_id=seed.chunk_id,
                            edge_id=edge.edge_id,
                            edge_kind=edge.edge_kind,
                            direction=edge_direction,
                            rank=len(contexts) + 1,
                        ))
                        within_seed += 1
                        if len(contexts) == top_k:
                            return tuple(contexts)
                        if within_seed == per_seed_limit:
                            break
                    if within_seed == per_seed_limit or examined > _MAX_CANDIDATES_PER_SEED:
                        break
                if within_seed == per_seed_limit or examined > _MAX_CANDIDATES_PER_SEED:
                    break
        return tuple(contexts)
