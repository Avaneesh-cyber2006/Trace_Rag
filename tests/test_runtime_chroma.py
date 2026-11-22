"""Increment 9 real Chroma integration, verified on Windows separately."""

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever, RelevancePolicy
from backend.runtime_evidence import RuntimeArtifact, RuntimeArtifactFormat, RuntimeEvidenceRequest, RuntimeFrameLanguage, ingest_runtime_evidence
from test_hybrid_orchestrator_chroma import NAMESPACE, OfflineProvider, _publish, _record


def test_real_chroma_runtime_context_cannot_change_source_or_ranking(tmp_path):
    store = ChromaVectorStore(tmp_path / "runtime-isolated")
    row = _record("a", "invoice cache", (1., 0., 0.))
    _publish(store, (row,))
    provider = OfflineProvider()
    evidence = ingest_runtime_evidence(RuntimeEvidenceRequest(NAMESPACE, (
        RuntimeArtifact("error.log", RuntimeArtifactFormat.STACK_TRACE_TEXT,
            b'Traceback (most recent call last):\n  File "src/a.py", line 5, in calculate\n'
            b'ValueError: SENSITIVE-ERROR-TEXT', RuntimeFrameLanguage.PYTHON),
    )))
    result = HybridRetriever(provider, store).search_with_runtime(
        NAMESPACE, "invoice", 1, evidence,
        relevance_policy=RelevancePolicy(min_query_matches=1),
    )
    assert len(result.hits) == 1
    assert result.hits[0].content == row.content
    assert result.runtime.paths[0].total_matching_frames == 1
    assert result.runtime.source_revision_verified is False
    assert result.runtime.causal_relationship_verified is False
    assert "SENSITIVE-ERROR-TEXT" not in repr(result.runtime)
    assert result.history is None and result.graph_context == ()
    assert provider.calls == 1
