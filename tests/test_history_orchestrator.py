"""Increment 8: historical hints cannot modify hybrid retrieval semantics."""

import pytest

from backend.retrieval import (
    HistoricalRetrievalResult, HybridRetriever, InvalidHybridEvidence,
    InvalidRetrievalQuery, RelevancePolicy, build_graph_revision,
)
from test_hybrid_orchestrator import NAMESPACE, FakeProvider, FakeStore, record, result
from test_git_history_models import _change, _inventory
from test_graph_provenance import same_file_call_bundle


def evidence(*changes):
    return _inventory(repository_namespace=NAMESPACE, changes=changes)


def test_history_retrieval_one_snapshot_same_hits_as_legacy():
    r = record()
    provider = FakeProvider()
    store = FakeStore((r,))
    h = HybridRetriever(provider, store).search_with_history(
        NAMESPACE, "alpha cache", 1, evidence(_change(new_path="src/a.py"))
    )
    assert isinstance(h, HistoricalRetrievalResult)
    assert len(h.hits) == 1
    assert h.hits[0].chunk_id == r.chunk_id
    assert h.graph_context == ()
    assert h.history.source_revision_verified is False
    assert h.history.paths[0].total_matching_changes == 1
    assert store.calls == ["acquire_active", "read_manifest", "search", "release_active"]
    assert provider.queries == ["alpha cache"]


def test_history_relevance_uses_selected_hit_only():
    a = record("a", "cache")
    b = record("b", "cache invoice payment")
    store = FakeStore((a, b))
    store.search_results = (result(a), result(b))
    output = HybridRetriever(FakeProvider(), store).search_with_history(
        NAMESPACE, "cache invoice payment", 1,
        evidence(_change(new_path="src/a.py"), _change(new_path="src/b.py")),
        relevance_policy=RelevancePolicy(min_query_matches=2),
    )
    assert [x.relative_path for x in output.hits] == ["src/b.py"]
    assert [x.relative_path for x in output.history.paths] == ["src/b.py"]


def test_history_graph_option_keeps_graph_provenance_check():
    inventories = same_file_call_bundle()
    binding = build_graph_revision(*inventories)
    chunks = inventories[2].files[0].chunks
    rows = tuple(
        record("a" if n == 0 else "b", c.content,
               chunk_id=c.chunk_id, content_hash=c.content_hash,
               relative_path="src/module.py", qualified_name=c.qualified_name)
        for n, c in enumerate(chunks)
    )
    store = FakeStore(rows)
    store.search_results = (result(rows[0]),)
    provider = FakeProvider()
    out = HybridRetriever(provider, store).search_with_history(
        NAMESPACE, "alpha", 1, evidence(_change(new_path="src/module.py")),
        graph_revision=binding,
    )
    assert len(out.graph_context) == 1
    assert out.history.paths[0].relative_path == "src/module.py"
    assert provider.queries == ["alpha"] and store.lease_exits == 1


def test_wrong_namespace_rejected_before_embedding_or_lease():
    store, provider = FakeStore(), FakeProvider()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_history(
            NAMESPACE, "alpha", 1, _inventory(repository_namespace="other")
        )
    assert provider.queries == [] and store.calls == []


def test_invalid_options_rejected_before_store_call():
    store = FakeStore()
    retriever = HybridRetriever(FakeProvider(), store)
    for kwargs in ({"history_top_k": True}, {"history_top_k": 21},
                   {"relevance_policy": object()}, {"graph_revision": object()},
                   {"graph_top_k": 101}):
        with pytest.raises(InvalidRetrievalQuery):
            retriever.search_with_history(NAMESPACE, "alpha", 1, evidence(), **kwargs)
    assert store.calls == []


def test_empty_index_does_not_embed_and_exposes_no_paths():
    store, provider = FakeStore(()), FakeProvider()
    out = HybridRetriever(provider, store).search_with_history(NAMESPACE, "alpha", 3, evidence())
    assert out.hits == () and out.history.paths == ()
    assert provider.queries == [] and store.lease_exits == 1


def test_history_does_not_override_provenance_rejection():
    inventories = same_file_call_bundle()
    binding = build_graph_revision(*inventories)
    provider, store = FakeProvider(), FakeStore()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_history(
            NAMESPACE, "alpha", 1, evidence(), graph_revision=binding,
        )
    assert provider.queries == [] and store.lease_exits == 1
