"""Increment 8: optional real-Chroma end-to-end historical annotations."""

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever, RelevancePolicy
from test_hybrid_orchestrator_chroma import (
    NAMESPACE, OfflineProvider, _publish, _record,
)
from test_git_history_models import _change, _inventory


def test_real_chroma_history_is_separate_from_verified_results(tmp_path):
    store = ChromaVectorStore(tmp_path / "history-isolated")
    r = _record("a", "invoice cache", (1., 0., 0.))
    _publish(store, (r,))
    provider = OfflineProvider()
    history = _inventory(
        repository_namespace=NAMESPACE,
        changes=(_change(new_path=r.relative_path),),
    )
    output = HybridRetriever(provider, store).search_with_history(
        NAMESPACE, "invoice", 1, history,
        relevance_policy=RelevancePolicy(min_query_matches=1),
    )
    assert len(output.hits) == 1
    assert output.hits[0].content == r.content
    assert output.history.source_revision_verified is False
    assert output.history.paths[0].total_matching_changes == 1
    assert output.graph_context == ()
    assert provider.calls == 1
