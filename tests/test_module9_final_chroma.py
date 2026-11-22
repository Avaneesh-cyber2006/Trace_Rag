"""Offline real Chroma end-to-end integration of all Module 9 evidence streams."""

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.synchronization import SemanticIndexer
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever, InvalidHybridEvidence, RelevancePolicy, build_graph_revision
from test_source_revision_chroma import LocalIndexerProvider
from test_source_revision_persistence import with_omitted_suffix
from test_hybrid_orchestrator_chroma import OfflineProvider
from test_graph_provenance import NAMESPACE, same_file_call_bundle
from test_git_history_models import _change, _inventory
from test_runtime_orchestrator import runtime


def test_real_chroma_combines_relevance_graph_history_and_runtime(tmp_path):
    inventories = with_omitted_suffix(same_file_call_bundle())
    graph = build_graph_revision(*inventories, require_published_source=True)
    store = ChromaVectorStore(tmp_path / "all-module9-evidence")
    SemanticIndexer(LocalIndexerProvider(), store).synchronize_with_sources(*inventories)
    history = _inventory(repository_namespace=NAMESPACE, changes=(
        _change(new_path="src/module.py"),
    ))
    provider = OfflineProvider()
    output = HybridRetriever(provider, store).search_with_runtime(
        NAMESPACE, "alpha", 1, runtime("src/module.py"),
        relevance_policy=RelevancePolicy(min_query_matches=1),
        graph_revision=graph, git_history=history,
    )
    assert provider.calls == 1
    assert len(output.hits) == 1
    assert len(output.graph_context) == 1
    assert output.graph_context[0].document.relative_path == "src/module.py"
    assert output.runtime.paths[0].total_matching_frames == 1
    assert output.history.paths[0].total_matching_changes == 1
    assert output.runtime.source_revision_verified is False
    assert output.runtime.causal_relationship_verified is False
    assert output.history.source_revision_verified is False
    assert "PRIVATE RUNTIME SECRET" not in repr(output)

    reloaded = ChromaVectorStore(tmp_path / "all-module9-evidence")
    second = HybridRetriever(OfflineProvider(), reloaded).search_with_runtime(
        NAMESPACE, "alpha", 1, runtime("src/module.py"),
        graph_revision=graph, git_history=history,
    )
    assert second.graph_context
    assert second.hits[0].content_hash == output.hits[0].content_hash
