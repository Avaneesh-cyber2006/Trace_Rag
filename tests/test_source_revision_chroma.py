"""Increment 6 real-Chroma publication, reuse, corruption and retrieval tests."""

from dataclasses import replace

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.exceptions import VectorStoreCorruptionError
from backend.embedding_vector_store.models import EmbeddingVector, IndexSyncStatus
from backend.embedding_vector_store.source_revision import SourceRevision, build_source_revision
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.embedding_vector_store.synchronization import SemanticIndexer
from backend.retrieval import HybridRetriever, InvalidHybridEvidence, build_graph_revision
from test_graph_provenance import NAMESPACE, same_file_call_bundle
from test_source_revision_persistence import with_omitted_suffix
from test_hybrid_orchestrator_chroma import IDENTITY, OfflineProvider


class LocalIndexerProvider:
    identity = IDENTITY
    max_batch_size = 8

    def __init__(self):
        self.embedded_batches = 0

    def embed_documents(self, documents):
        self.embedded_batches += 1
        return tuple(EmbeddingVector((1., 0., 0.)) for _ in documents)

    def embed_query(self, text):
        raise AssertionError("Index synchronization must not embed queries")


def test_persisted_original_file_proof_allows_gap_and_survives_reload(tmp_path):
    inventories = with_omitted_suffix(same_file_call_bundle())
    store = ChromaVectorStore(tmp_path / "source-verified")
    writer = LocalIndexerProvider()
    indexer = SemanticIndexer(writer, store)
    synced = indexer.synchronize_with_sources(*inventories)
    assert synced.status is IndexSyncStatus.SUCCESS
    assert writer.embedded_batches == 1
    pinned = store.inspect_active(NAMESPACE).snapshot
    assert pinned is not None
    assert SourceRevision.decode(pinned.source_revision) == build_source_revision(*inventories)
    assert store.read_manifest(pinned)
    assert indexer.synchronize_with_sources(*inventories).status is IndexSyncStatus.UNCHANGED
    assert writer.embedded_batches == 1

    reader = OfflineProvider()
    proof = build_graph_revision(*inventories, require_published_source=True)
    answer = HybridRetriever(reader, store).search_with_graph(NAMESPACE, "alpha", 1, proof)
    assert answer.hits and answer.context
    assert reader.calls == 1

    # A new adapter for the same root reopens the durable generation metadata.
    again = ChromaVectorStore(tmp_path / "source-verified")
    assert again.inspect_active(NAMESPACE).snapshot.source_revision == pinned.source_revision


def test_original_source_changes_without_chunk_changes_forces_new_generation(tmp_path):
    old = with_omitted_suffix(same_file_call_bundle(), "\n  \n")
    new = with_omitted_suffix(same_file_call_bundle(), "  \n\n")
    store = ChromaVectorStore(tmp_path / "source-drift")
    writer = LocalIndexerProvider()
    indexer = SemanticIndexer(writer, store)
    indexer.synchronize_with_sources(*old)
    before = store.inspect_active(NAMESPACE).snapshot
    assert before is not None
    assert indexer.synchronize_with_sources(*new).status is IndexSyncStatus.SUCCESS
    after = store.inspect_active(NAMESPACE).snapshot
    assert after is not None and before._token != after._token
    assert before.source_revision != after.source_revision
    assert writer.embedded_batches == 1  # unchanged vector evidence reused
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(OfflineProvider(), store).search_with_graph(
            NAMESPACE, "alpha", 1,
            build_graph_revision(*old, require_published_source=True)
        )
    answer = HybridRetriever(OfflineProvider(), store).search_with_graph(
        NAMESPACE, "alpha", 1,
        build_graph_revision(*new, require_published_source=True)
    )
    assert answer.context


def test_legacy_resynchronization_downgrades_provenance_without_reembedding(tmp_path):
    inventories = with_omitted_suffix(same_file_call_bundle())
    store = ChromaVectorStore(tmp_path / "no-silent-proof")
    writer = LocalIndexerProvider()
    indexer = SemanticIndexer(writer, store)
    indexer.synchronize_with_sources(*inventories)
    downgraded = indexer.synchronize(inventories[2])
    assert downgraded.status is IndexSyncStatus.SUCCESS
    assert writer.embedded_batches == 1
    assert store.inspect_active(NAMESPACE).snapshot.source_revision is None
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(OfflineProvider(), store).search_with_graph(
            NAMESPACE, "alpha", 1,
            build_graph_revision(*inventories, require_published_source=True)
        )


def test_candidate_metadata_and_manifest_must_agree_before_publish(tmp_path):
    inventories = with_omitted_suffix(same_file_call_bundle())
    store = ChromaVectorStore(tmp_path / "reject-inconsistent")
    writer = LocalIndexerProvider()
    SemanticIndexer(writer, store).synchronize(inventories[2])
    active_before = store.inspect_active(NAMESPACE).snapshot
    assert active_before is not None
    wrong_proof = replace(build_source_revision(*inventories), manifest_fingerprint="e" * 64)
    candidate = store.begin_candidate_with_source_revision(
        NAMESPACE, IDENTITY, active_before.document_version,
        active_before.expected_chunk_count, wrong_proof.encode(),
    )
    try:
        store.add_reused(candidate, store.read_manifest(active_before))
        with pytest.raises(VectorStoreCorruptionError):
            store.validate_candidate(candidate)
        assert store.inspect_active(NAMESPACE).snapshot._token == active_before._token
    finally:
        store.abort(candidate)
