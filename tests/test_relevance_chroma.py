"""Real Chroma smoke tests: relevance selection under one pinned generation."""

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever, RelevancePolicy
from test_hybrid_orchestrator_chroma import (
    NAMESPACE, OfflineProvider, _publish, _record,
)


def test_real_chroma_relevance_gate_and_rerank(tmp_path):
    store = ChromaVectorStore(tmp_path / "relevance-isolated")
    a = _record("a", "cache lookup", (1., 0., 0.))
    b = _record("b", "cache invoice payment", (0., 1., 0.))
    _publish(store, (a, b))
    provider = OfflineProvider()
    hits = HybridRetriever(provider, store).search_relevant(
        NAMESPACE, "cache invoice payment", 1, RelevancePolicy(min_query_matches=2)
    )
    assert len(hits) == 1 and hits[0].chunk_id == b.chunk_id
    assert hits[0].content == b.content
    assert hits[0].rank == 1 and provider.calls == 1


def test_real_chroma_empty_gate_and_legacy_search_unchanged(tmp_path):
    store = ChromaVectorStore(tmp_path / "relevance-empty")
    a = _record("a", "cache lookup", (1., 0., 0.))
    _publish(store, (a,))
    provider = OfflineProvider()
    retriever = HybridRetriever(provider, store)
    assert retriever.search_relevant(NAMESPACE, "unseen", 1, RelevancePolicy()) == ()
    legacy = retriever.search(NAMESPACE, "unseen", 1)
    assert len(legacy) == 1 and legacy[0].content == a.content
    assert provider.calls == 2
