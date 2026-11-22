"""Real Module 5 Chroma integration for Module 9's pinned hybrid retrieval.

These tests intentionally require the project's pinned chromadb installation;
no live embedding service or network access is used.
"""

from hashlib import sha256

import pytest

pytest.importorskip("chromadb")

from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import RepositoryIndexNotFound
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity, EmbeddingVector, VectorRecord,
)
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.retrieval import HybridRetriever

NAMESPACE = "tracerag-repository-v1:github:example/資料庫"
IDENTITY = EmbeddingModelIdentity("offline", "hybrid-bridge", 3, "v1")


class OfflineProvider:
    identity = IDENTITY
    max_batch_size = 10

    def __init__(self):
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        return EmbeddingVector((1.0, 0.0, 0.0))

    def embed_documents(self, documents):
        raise AssertionError("Search must not request document embeddings")


def _record(letter, source, embedding):
    return VectorRecord(
        chunk_id=letter * 64,
        content_hash=sha256(source.encode("utf-8")).hexdigest(),
        relative_path=f"src/{letter}.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name=f"demo.{letter}",
        parent_qualified_name=None,
        content=source,
        embedding=EmbeddingVector(embedding),
    )


def _publish(store, records):
    candidate = store.begin_candidate(
        NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, len(records)
    )
    if records:
        store.add_embedded(candidate, records)
    return store.publish(candidate)


def test_real_chroma_hybrid_returns_authoritative_source_from_one_snapshot(tmp_path):
    store = ChromaVectorStore(tmp_path / "isolated-chroma")
    first = _record("a", "def calculate_tax(): return subtotal", (1.0, 0.0, 0.0))
    second = _record("b", "def load_index(): return directory", (0.0, 1.0, 0.0))
    _publish(store, (first, second))
    provider = OfflineProvider()

    hits = HybridRetriever(provider, store).search(NAMESPACE, "calculate tax", 2)

    assert provider.calls == 1
    assert hits[0].chunk_id == first.chunk_id
    assert hits[0].content == first.content
    assert hits[0].content_hash == first.content_hash
    assert hits[0].lexical_rank == 1 and hits[0].semantic_rank == 1
    assert len(hits) <= 2


def test_real_chroma_empty_published_generation_skips_embedding(tmp_path):
    store = ChromaVectorStore(tmp_path / "isolated-empty")
    _publish(store, ())
    provider = OfflineProvider()
    assert HybridRetriever(provider, store).search(NAMESPACE, "anything", 2) == ()
    assert provider.calls == 0


def test_real_chroma_unindexed_repository_is_not_silent_empty(tmp_path):
    store = ChromaVectorStore(tmp_path / "isolated-never-indexed")
    provider = OfflineProvider()
    with pytest.raises(RepositoryIndexNotFound):
        HybridRetriever(provider, store).search(NAMESPACE, "anything", 2)
    assert provider.calls == 0
