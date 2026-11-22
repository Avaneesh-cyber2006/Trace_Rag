"""Increment 7: policy execution inside an authoritative Module 5 lease."""

from dataclasses import replace
from backend.embedding_vector_store.stores.base import SourceRevisionToken

import pytest

from backend.retrieval import (
    HybridRetriever, InvalidHybridEvidence, InvalidRetrievalQuery,
    RelevancePolicy, build_graph_revision,
)
from test_hybrid_orchestrator import NAMESPACE, FakeProvider, FakeStore, record, result
from test_graph_provenance import same_file_call_bundle


def test_one_pinned_lease_reranks_full_pool_before_requested_top_k():
    a = record("a", "cache")
    b = record("b", "cache invoice payment")
    store = FakeStore((a, b))
    store.search_results = (result(a), result(b))
    provider = FakeProvider()
    hits = HybridRetriever(provider, store).search_relevant(
        NAMESPACE, "cache invoice payment", 1, RelevancePolicy(min_query_matches=2)
    )
    assert len(hits) == 1 and hits[0].chunk_id == b.chunk_id
    assert hits[0].rank == 1
    assert store.calls == ["acquire_active", "read_manifest", "search", "release_active"]
    assert store.observed_snapshots == [store.snapshot, store.snapshot]
    assert provider.queries == ["cache invoice payment"]


def test_legacy_search_still_returns_original_rrf_top_hit():
    a = record("a", "cache")
    b = record("b", "cache invoice payment")
    store = FakeStore((a, b))
    store.search_results = (result(a), result(b))
    old = HybridRetriever(FakeProvider(), store).search(NAMESPACE, "cache invoice payment", 1)
    assert old[0].chunk_id == a.chunk_id


def test_opt_in_gate_can_produce_empty_hits_without_second_search():
    store = FakeStore()
    provider = FakeProvider()
    hits = HybridRetriever(provider, store).search_relevant(
        NAMESPACE, "no matching words", 4, RelevancePolicy()
    )
    assert hits == ()
    assert provider.queries == ["no matching words"] and store.lease_exits == 1


def test_empty_index_skips_query_embedding_with_policy():
    store = FakeStore(())
    provider = FakeProvider()
    hits = HybridRetriever(provider, store).search_relevant(NAMESPACE, "alpha", 1, RelevancePolicy())
    assert hits == ()
    assert provider.queries == [] and store.lease_exits == 1


def test_invalid_policy_type_fails_before_collaborator_calls():
    store = FakeStore()
    provider = FakeProvider()
    with pytest.raises(InvalidRetrievalQuery):
        HybridRetriever(provider, store).search_relevant(NAMESPACE, "alpha", 1, None)
    assert store.calls == []


def test_stale_semantic_source_still_rejected_before_gate():
    store = FakeStore()
    store.search_results = (result(record("b", "secret foreign content")),)
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(FakeProvider(), store).search_relevant(
            NAMESPACE, "alpha", 1, RelevancePolicy(min_query_matches=64)
        )
    assert store.lease_exits == 1


def test_graph_expansion_uses_filtered_seeds_and_same_revision_proof():
    inventories = same_file_call_bundle()
    proof = build_graph_revision(*inventories)
    sources = tuple(
        record("a" if i == 0 else "b", chunk.content,
               chunk_id=chunk.chunk_id, content_hash=chunk.content_hash,
               relative_path="src/module.py", qualified_name=chunk.qualified_name)
        for i, chunk in enumerate(inventories[2].files[0].chunks)
    )
    store = FakeStore(sources)
    store.search_results = (result(sources[0]),)
    provider = FakeProvider()
    output = HybridRetriever(provider, store).search_with_graph_relevant(
        NAMESPACE, "alpha", 1, proof, RelevancePolicy()
    )
    assert len(output.hits) == 1
    assert output.hits[0].chunk_id == sources[0].chunk_id
    assert [entry.document.chunk_id for entry in output.context] == [sources[1].chunk_id]
    assert provider.queries == ["alpha"]
    assert store.calls == ["acquire_active", "read_manifest", "search", "release_active"]


def test_graph_provenance_checked_even_when_policy_would_filter_everything():
    inventories = same_file_call_bundle()
    proof = build_graph_revision(*inventories)
    sources = tuple(
        record("a" if i == 0 else "b", chunk.content, chunk_id=chunk.chunk_id,
               content_hash=chunk.content_hash, relative_path="src/module.py",
               qualified_name=chunk.qualified_name)
        for i, chunk in enumerate(inventories[2].files[0].chunks)
    )
    store = FakeStore(sources)
    store.snapshot = replace(store.snapshot, _token=SourceRevisionToken(store.snapshot._token, "invalid"))
    store.active_state = replace(store.active_state, snapshot=store.snapshot)
    provider = FakeProvider()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_graph_relevant(
            NAMESPACE, "unknown", 1, proof, RelevancePolicy(min_query_matches=64)
        )
    assert provider.queries == [] and store.lease_exits == 1


def test_invalid_graph_relevance_policy_rejected_before_store_access():
    store = FakeStore()
    with pytest.raises(InvalidRetrievalQuery):
        HybridRetriever(FakeProvider(), store).search_with_graph_relevant(
            NAMESPACE, "alpha", 1, object(), RelevancePolicy()
        )
    assert store.calls == []
