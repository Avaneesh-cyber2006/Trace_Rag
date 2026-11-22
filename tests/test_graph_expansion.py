"""Deterministic Module 6 one-hop expansion over bounded Module 5 evidence."""

from dataclasses import replace
from hashlib import sha256

import pytest

from backend.code_graph.models import (
    CodeGraphInventory, GraphEdge, GraphEdgeKind, GraphNode, GraphNodeKind,
)
from backend.code_parser.models import ParsedLanguage, ParseStatus, SourceLocation, SymbolKind
from backend.retrieval import (
    GraphContext, GraphExpander, HybridHit, InvalidHybridEvidence,
    InvalidRetrievalQuery, LexicalDocument, LexicalIndex,
)

NS = "tracerag-repository-v1:github:example/資料庫"


def doc(char: str, name: str, *, path: str | None = None, kind: str = "symbol", language: str = "python"):
    content = f"def {name}():\n    return '{char}'\n"
    return LexicalDocument(
        repository_namespace=NS,
        chunk_id=char * 64,
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
        relative_path=path or f"src/{name}.py",
        language=language,
        chunk_kind=kind,
        symbol_kind="function",
        qualified_name=f"module.{name}",
        parent_qualified_name=None,
        content=content,
    )


def node(char: str, name: str, *, path: str | None = None, language: ParsedLanguage = ParsedLanguage.PYTHON):
    return GraphNode(
        f"node-{char}", GraphNodeKind.SYMBOL, NS, path or f"src/{name}.py",
        language, ParseStatus.SUCCESS, SymbolKind.FUNCTION, name, f"module.{name}",
        SourceLocation(0, 10, 1, 0, 2, 0),
    )


def edge(char: str, start: str, end: str, kind: GraphEdgeKind = GraphEdgeKind.CALLS):
    return GraphEdge(f"edge-{char}", kind, f"node-{start}", f"node-{end}", None)


def graph(nodes, edges, ns=NS):
    return CodeGraphInventory(ns, tuple(nodes), tuple(edges), (), {}, {}, {}, {})


def hit(document: LexicalDocument, rank: int = 1):
    return HybridHit(
        repository_namespace=document.repository_namespace,
        chunk_id=document.chunk_id,
        content_hash=document.content_hash,
        relative_path=document.relative_path,
        language=document.language,
        chunk_kind=document.chunk_kind,
        symbol_kind=document.symbol_kind,
        qualified_name=document.qualified_name,
        parent_qualified_name=document.parent_qualified_name,
        content=document.content,
        score=0.03,
        rank=rank,
        lexical_rank=rank,
        semantic_rank=None,
    )


def setup(*, nodes=None, edges=None, documents=None):
    documents = tuple(documents) if documents is not None else (doc("a", "alpha"), doc("b", "beta"), doc("c", "gamma"))
    nodes = nodes if nodes is not None else (node("a", "alpha"), node("b", "beta"), node("c", "gamma"))
    edges = edges if edges is not None else (edge("ab", "a", "b"), edge("ac", "a", "c"))
    index = LexicalIndex(NS, documents)
    return index, GraphExpander(index, graph(nodes, edges))


def test_one_hop_returns_only_validated_authoritative_documents():
    idx, expander = setup()
    results = expander.expand((hit(idx.lookup("a" * 64)),), 10)
    assert [entry.document.chunk_id for entry in results] == ["b" * 64, "c" * 64]
    assert [entry.rank for entry in results] == [1, 2]
    assert [entry.edge_kind for entry in results] == [GraphEdgeKind.CALLS] * 2
    assert all(entry.direction == "outgoing" for entry in results)
    assert results[0].document is idx.lookup("b" * 64)
    assert results[0].document.content == doc("b", "beta").content
    assert "def beta" not in repr(results[0])
    assert isinstance(results[0], GraphContext)


def test_graph_reversal_exposes_incoming_neighbors():
    idx, expander = setup()
    results = expander.expand((hit(idx.lookup("b" * 64)),), 10)
    assert [entry.document.chunk_id for entry in results] == ["a" * 64]
    assert results[0].direction == "incoming"


@pytest.mark.parametrize("direction,expected", [
    ("outgoing", ("b", "c")), ("incoming", ()), ("both", ("b", "c")),
])
def test_direction_filters(direction, expected):
    idx, expander = setup()
    results = expander.expand((hit(idx.lookup("a" * 64)),), 10, direction=direction)
    assert [record.document.chunk_id[0] for record in results] == list(expected)


@pytest.mark.parametrize("kind", [GraphEdgeKind.CALLS, GraphEdgeKind.CONTAINS, GraphEdgeKind.INHERITS, GraphEdgeKind.IMPLEMENTS])
def test_resolved_symbol_edges_are_supported(kind):
    idx, expander = setup(edges=(edge("ab", "a", "b", kind),))
    results = expander.expand((hit(idx.lookup("a" * 64)),), 10)
    assert len(results) == 1 and results[0].edge_kind is kind


@pytest.mark.parametrize("kind", [GraphEdgeKind.DEFINES, GraphEdgeKind.IMPORTS])
def test_file_or_import_edges_not_attributed_to_symbol_chunks(kind):
    idx, expander = setup(edges=(edge("ab", "a", "b", kind),))
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()


def test_expansion_never_walks_two_hops():
    idx, expander = setup(edges=(edge("ab", "a", "b"), edge("bc", "b", "c")))
    assert [hit.document.chunk_id[0] for hit in expander.expand((hit(idx.lookup("a" * 64)),), 100)] == ["b"]


def test_deterministic_under_graph_and_document_permutations():
    original_idx, original = setup()
    reversed_idx, reversed_expander = setup(
        documents=tuple(reversed(original_idx.documents)),
        nodes=tuple(reversed((node("a", "alpha"), node("b", "beta"), node("c", "gamma")))),
        edges=tuple(reversed((edge("ab", "a", "b"), edge("ac", "a", "c")))),
    )
    left = original.expand((hit(original_idx.lookup("a" * 64)),), 10)
    right = reversed_expander.expand((hit(reversed_idx.lookup("a" * 64)),), 10)
    assert left == right
    assert original_idx.documents == reversed_idx.documents


def test_top_k_and_per_seed_limits_and_deduplication():
    idx, expander = setup(edges=(edge("ab", "a", "b"), edge("ab2", "a", "b"), edge("ac", "a", "c")))
    seeds = (hit(idx.lookup("a" * 64), 1),)
    assert len(expander.expand(seeds, 1)) == 1
    assert len(expander.expand(seeds, 100, per_seed_limit=1)) == 1
    assert len(expander.expand(seeds, 100)) == 2


def test_all_seed_ids_are_excluded_even_when_linked():
    idx, expander = setup()
    seeds = (hit(idx.lookup("a" * 64), 1), hit(idx.lookup("b" * 64), 2))
    assert [entry.document.chunk_id for entry in expander.expand(seeds, 10)] == ["c" * 64]


def test_seeds_must_be_contiguous_unique_and_match_authoritative_source():
    idx, expander = setup()
    valid = hit(idx.lookup("a" * 64))
    for seeds in (
        (valid, valid),
        (replace(valid, rank=2),),
        (replace(valid, rank=True),),
        (replace(valid, chunk_id="f" * 64),),
        (replace(valid, chunk_id="not-a-hash"),),
        (replace(valid, content="tampered"),),
        (replace(valid, content_hash="0" * 64),),
        (replace(valid, relative_path="other.py"),),
        (replace(valid, repository_namespace="other"),),
        (replace(valid, score=float("nan")),),
        (replace(valid, score=0.0),),
    ):
        with pytest.raises(InvalidHybridEvidence, match="Graph retrieval evidence"):
            expander.expand(seeds, 10)


@pytest.mark.parametrize("bad", [0, -1, 101, True, 1.5, "4"])
def test_invalid_top_k_rejected(bad):
    _, expander = setup()
    with pytest.raises(InvalidRetrievalQuery):
        expander.expand((), bad)


@pytest.mark.parametrize("bad", [0, -1, 33, True, 1.5, "5"])
def test_invalid_per_seed_limit_rejected(bad):
    _, expander = setup()
    with pytest.raises(InvalidRetrievalQuery):
        expander.expand((), 10, per_seed_limit=bad)


@pytest.mark.parametrize("bad", ["", "out", "incoming ", 2, None])
def test_invalid_direction_rejected(bad):
    _, expander = setup()
    with pytest.raises(InvalidRetrievalQuery):
        expander.expand((), 10, direction=bad)


def test_invalid_seed_container_rejected():
    idx, expander = setup()
    with pytest.raises(InvalidRetrievalQuery):
        expander.expand([hit(idx.lookup("a" * 64))], 10)


def test_empty_graph_and_empty_seed_do_not_leak_source_or_search():
    idx, expander = setup(nodes=(), edges=())
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()
    assert expander.expand((), 10) == ()


def test_foreign_graph_namespace_rejected_before_expansion():
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((), (), "different-namespace"))


def test_foreign_node_rejected():
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((replace(node("a", "alpha"), repository_namespace="foreign"),), ()))


def test_duplicate_node_identity_rejected():
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    one = node("a", "alpha")
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((one, one), ()))


def test_dangling_edge_rejected():
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((node("a", "alpha"),), (edge("ab", "a", "b"),)))


def test_duplicate_edge_identity_rejected():
    idx = LexicalIndex(NS, (doc("a", "alpha"), doc("b", "beta")))
    duplicated = edge("ab", "a", "b")
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((node("a", "alpha"), node("b", "beta")), (duplicated, duplicated)))


def test_ambiguous_graph_names_never_guess_a_binding():
    idx = LexicalIndex(NS, (doc("a", "alpha"), doc("b", "beta")))
    a = node("a", "alpha")
    a2 = replace(a, node_id="another-alpha-id")
    expander = GraphExpander(idx, graph((a, a2, node("b", "beta")), (edge("ab", "a", "b"),)))
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()


@pytest.mark.parametrize("change", [
    {"relative_path": "src/unrelated.py"},
    {"qualified_name": "module.renamed"},
    {"language": ParsedLanguage.JAVA},
    {"symbol_kind": SymbolKind.METHOD},
])
def test_mismatched_graph_symbol_does_not_attach_to_chunk(change):
    idx = LexicalIndex(NS, (doc("a", "alpha"), doc("b", "beta")))
    a = node("a", "alpha")
    b = replace(node("b", "beta"), **change)
    expander = GraphExpander(idx, graph((a, b), (edge("ab", "a", "b"),)))
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()


def test_context_chunks_cannot_be_falsely_associated_to_symbols():
    idx, expander = setup(documents=(doc("a", "alpha", kind="context"), doc("b", "beta")))
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()


def test_graph_does_not_reconstitute_text_for_chunks_absent_from_index():
    idx, expander = setup(documents=(doc("a", "alpha"),))
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()


def test_fragments_of_one_unambiguous_symbol_are_allowed():
    a = doc("a", "alpha")
    fragment = doc("d", "beta", kind="fragment")
    b = doc("b", "beta")
    idx, expander = setup(documents=(a, fragment, b))
    output = expander.expand((hit(a),), 10)
    assert [entry.document.chunk_id[0] for entry in output] == ["b", "d"]


def test_immutable_graph_context_and_source_documents():
    idx, expander = setup()
    result = expander.expand((hit(idx.lookup("a" * 64)),), 10)[0]
    with pytest.raises(AttributeError):
        result.rank = 77
    with pytest.raises(AttributeError):
        result.document.content = "forged"
    assert isinstance(idx.documents, tuple)


def test_self_edge_never_reintroduces_seed():
    idx, expander = setup(edges=(edge("self", "a", "a"),))
    assert expander.expand((hit(idx.lookup("a" * 64)),), 10) == ()


@pytest.mark.parametrize("change", [
    {"language": "python"},
    {"parse_status": "success"},
    {"symbol_kind": "function"},
    {"qualified_name": ["bad"]},
])
def test_malformed_graph_node_fields_rejected_without_raw_backend_errors(change):
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((replace(node("a", "alpha"), **change),), ()))


def test_malformed_graph_edge_endpoint_is_rejected():
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((node("a", "alpha"),), (replace(edge("a", "a", "a"), source_node_id=[]),)))


def test_invalid_graph_node_path_is_rejected():
    idx = LexicalIndex(NS, (doc("a", "alpha"),))
    with pytest.raises(InvalidHybridEvidence):
        GraphExpander(idx, graph((replace(node("a", "alpha"), relative_path="../escape.py"),), ()))
