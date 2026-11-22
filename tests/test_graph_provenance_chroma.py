"""Offline, real-Chroma coverage of source-revision-bound graph expansion."""
from hashlib import sha256

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.models import EmbeddingVector, VectorRecord
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever, build_graph_revision
from test_graph_provenance import same_file_call_bundle, NAMESPACE
from test_hybrid_orchestrator_chroma import IDENTITY, OfflineProvider


def test_real_chroma_graph_context_from_same_published_revision(tmp_path):
    _, _, chunk_inventory = inventories = same_file_call_bundle()
    binding = build_graph_revision(*inventories)
    store = ChromaVectorStore(tmp_path / "graph-revision-isolated")
    chunks = chunk_inventory.files[0].chunks
    records = tuple(
        VectorRecord(
            chunk_id=chunk.chunk_id,
            content_hash=sha256(chunk.content.encode("utf-8")).hexdigest(),
            relative_path="src/module.py",
            language="python",
            chunk_kind="symbol",
            symbol_kind="function",
            qualified_name=chunk.qualified_name,
            parent_qualified_name=None,
            content=chunk.content,
            embedding=EmbeddingVector((1., 0., 0.) if chunk.qualified_name == "alpha" else (0., 1., 0.)),
        )
        for chunk in chunks
    )
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 2)
    store.add_embedded(candidate, records)
    store.publish(candidate)

    provider = OfflineProvider()
    result = HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 1, binding)
    assert provider.calls == 1
    assert len(result.hits) == 1
    assert result.hits[0].chunk_id == records[0].chunk_id
    assert len(result.context) == 1
    assert result.context[0].document.content == records[1].content
