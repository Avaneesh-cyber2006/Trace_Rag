"""Structural resource bounds: counters and object lifetimes, never timings."""

from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import weakref

import pytest

from backend.code_chunker.models import (
    ChunkFileStatus, ChunkKind, ChunkedFile, CodeChunk, CodeChunkInventory,
)
from backend.code_parser.models import ParseStatus, ParsedLanguage, SourceLocation
from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import (
    EmbeddingDocumentTooLarge, InvalidSearchRequest,
)
from backend.embedding_vector_store.models import (
    EmbeddingDocument, EmbeddingModelIdentity, EmbeddingVector, IndexSyncStatus,
)
from backend.embedding_vector_store.search import SemanticSearcher
from backend.embedding_vector_store.stores.base import (
    CandidateIndex, RepositoryIndexSnapshot, RepositoryIndexState,
    StoreSearchResult, StoredRecord,
)
import backend.embedding_vector_store.synchronization as synchronization
from backend.embedding_vector_store.validation import ChunkInput


IDENTITY = EmbeddingModelIdentity("counting", "offline", 3, "v1")
SCHEMA = "tracerag-chroma-schema-v1"


def _chunk(index, content=None):
    source = content if content is not None else f"value_{index} = {index}\r\n\t  "
    return CodeChunk(
        f"{index:064x}", ChunkKind.CONTEXT,
        SourceLocation(index * 100, index * 100 + 99, index + 1, 0, index + 1, 99),
        source, sha256(source.encode()).hexdigest(), None, None, None, None,
        (), None, (), (), (), 0, 1,
    )


def _inventory(chunks):
    if not chunks:
        return CodeChunkInventory(".", "repo", 0, 0, 0, 0, 0, ())
    file = ChunkedFile(
        "src/large.py", ParsedLanguage.PYTHON, ParseStatus.SUCCESS,
        ChunkFileStatus.SUCCESS, "0" * 64, (), tuple(chunks), (),
    )
    return CodeChunkInventory(".", "repo", 1, 1, 0, 0, len(chunks), (file,))


def _stored(chunk):
    return StoredRecord(
        "repo", chunk.chunk_id, chunk.content_hash, "src/large.py", "python",
        "context", None, None, None, chunk.content,
        EmbeddingVector((1.0, 0.0, 0.0)), IDENTITY,
        EMBEDDING_DOCUMENT_VERSION, SCHEMA,
    )


class _CountingProvider:
    identity = IDENTITY

    def __init__(self, capacity=17, *, vector_tracker=None, max_document_chars=None):
        self.max_batch_size = capacity
        self.batch_sizes = []
        self.document_ids = []
        self.query_calls = 0
        self.vector_tracker = vector_tracker
        self.max_document_chars = max_document_chars
        self.rejected_text = None

    def embed_documents(self, documents):
        # Record only scalar evidence; retaining documents/vectors here would
        # attribute fake-owned memory to core orchestration.
        self.batch_sizes.append(len(documents))
        self.document_ids.extend(document.chunk_id for document in documents)
        for document in documents:
            if self.max_document_chars and len(document.text) > self.max_document_chars:
                self.rejected_text = document.text
                raise EmbeddingDocumentTooLarge("Document exceeds provider limit.")
        vectors = tuple(_TrackedVector((1.0, float(i), 0.0)) for i in range(len(documents)))
        if self.vector_tracker is not None:
            for vector in vectors:
                self.vector_tracker.watch(vector)
        return vectors

    def embed_query(self, query_text):
        self.query_calls += 1
        return EmbeddingVector((1.0, 0.0, 0.0))


class _CountingStore:
    """Candidate sink keeps IDs only, so vector liveness measures core retention."""

    def __init__(self, manifest=None):
        self.manifest = manifest
        self.calls = Counter()
        self.reuse_sizes = []
        self.write_sizes = []
        self.written_ids = []
        self.search_limits = []
        self.active = None if manifest is None else RepositoryIndexSnapshot(
            "repo", IDENTITY, EMBEDDING_DOCUMENT_VERSION, SCHEMA, len(manifest), object(),
        )

    def inspect_active(self, repository_namespace):
        assert repository_namespace == "repo"
        self.calls["inspect"] += 1
        return RepositoryIndexState(self.active is not None, self.active)

    @contextmanager
    def acquire_active(self, repository_namespace):
        self.calls["acquire"] += 1
        yield self.inspect_active(repository_namespace)

    def read_manifest(self, snapshot):
        assert snapshot is self.active
        self.calls["manifest"] += 1
        return self.manifest

    def begin_candidate(self, namespace, identity, document_version, count):
        self.calls["begin"] += 1
        self.written_ids = []
        return CandidateIndex(namespace, identity, document_version, SCHEMA, count, object())

    def add_reused(self, candidate, records):
        self.calls["reuse"] += 1
        self.reuse_sizes.append(len(records))
        self.written_ids.extend(record.chunk_id for record in records)

    def add_embedded(self, candidate, records):
        self.calls["write"] += 1
        self.write_sizes.append(len(records))
        self.written_ids.extend(record.chunk_id for record in records)

    def validate_candidate(self, candidate):
        self.calls["validate"] += 1
        assert len(self.written_ids) == candidate.expected_chunk_count
        assert len(set(self.written_ids)) == len(self.written_ids)

    def publish(self, candidate):
        self.calls["publish"] += 1
        self.active = RepositoryIndexSnapshot(
            candidate.repository_namespace, candidate.identity, candidate.document_version,
            candidate.schema_version, candidate.expected_chunk_count, object(),
        )
        return self.active

    def abort(self, candidate):
        self.calls["abort"] += 1
        self.written_ids = []

    def search(self, snapshot, query, top_k):
        assert snapshot is self.active
        self.calls["search"] += 1
        self.search_limits.append(top_k)
        return tuple(StoreSearchResult(
            record.repository_namespace, record.chunk_id, record.content_hash,
            record.relative_path, record.language, record.chunk_kind, record.symbol_kind,
            record.qualified_name, record.parent_qualified_name, record.content, 1.0,
        ) for record in self.manifest[:top_k])

    def delete_repository_index(self, namespace):
        raise AssertionError("This operation is outside synchronization/search.")


class _TrackedDocument(EmbeddingDocument):
    __slots__ = ("__weakref__",)


class _TrackedVector(EmbeddingVector):
    __slots__ = ("__weakref__",)


class _LifetimeCounter:
    def __init__(self):
        self.live = 0
        self.peak = 0
        self.created = 0

    def watch(self, value):
        self.live += 1
        self.created += 1
        self.peak = max(self.peak, self.live)
        weakref.finalize(value, self._released)

    def _released(self):
        self.live -= 1


class _CountingId(str):
    operations = Counter()

    def __hash__(self):
        self.operations["hash"] += 1
        return super().__hash__()

    def __eq__(self, other):
        self.operations["equal"] += 1
        return super().__eq__(other)

    def __ne__(self, other):
        self.operations["equal"] += 1
        return super().__ne__(other)


@pytest.mark.parametrize("size", [512, 4096])
def test_manifest_classification_bounds_hash_and_equality_work(size):
    # A nested scan replacing dictionary lookups exceeds the operation budget.
    current = tuple(ChunkInput(
        replace(_chunk(i), chunk_id=_CountingId(f"{i:064x}")),
        "src/large.py", ParsedLanguage.PYTHON,
    ) for i in range(size))
    stored = tuple(replace(
        _stored(_chunk(i)), chunk_id=_CountingId(f"{i:064x}"),
        content_hash="f" * 64 if i % 2 else _chunk(i).content_hash,
    ) for i in range(size // 2, size + size // 2))
    _CountingId.operations.clear()

    diff = synchronization._classify_chunks(current, stored, True, True)

    work = _CountingId.operations.copy()
    assert 2 * size <= work["hash"] <= 8 * size
    assert work["equal"] <= 8 * size
    assert len(diff.new) == size // 2
    assert len(diff.updated) == size // 4
    assert len(diff.unchanged) == size // 4
    assert len(diff.deleted) == size // 2


@pytest.mark.parametrize(("size", "capacity"), [(1025, 17), (257, 1), (4096, 63)])
def test_provider_and_candidate_writes_are_bounded_and_cover_each_chunk_once(size, capacity):
    # One final repository-sized add_embedded call violates this bound even if
    # the provider itself receives small batches.
    provider = _CountingProvider(capacity)
    store = _CountingStore()
    result = synchronization.SemanticIndexer(provider, store).synchronize(
        _inventory(tuple(_chunk(i) for i in range(size)))
    )
    assert all(0 < count <= capacity for count in provider.batch_sizes)
    assert len(provider.batch_sizes) == (size + capacity - 1) // capacity
    assert sum(provider.batch_sizes) == size
    assert all(0 < count <= capacity for count in store.write_sizes)
    assert len(store.write_sizes) == (size + capacity - 1) // capacity
    expected_ids = [f"{i:064x}" for i in range(size)]
    assert provider.document_ids == expected_ids
    assert store.written_ids == expected_ids
    assert result.embedded_chunks == result.inserted_chunks == size
    assert result.reused_chunks == result.updated_chunks == result.deleted_chunks == 0
    assert store.calls["validate"] == store.calls["publish"] == 1
    assert provider.query_calls == store.calls["abort"] == 0


def test_rendered_documents_are_released_before_rendering_the_next_batch(monkeypatch):
    # Rendering eagerly, or retaining the preceding document tuple while the
    # next tuple is built, exceeds one batch of live rendered documents.
    tracker = _LifetimeCounter()
    original = synchronization.build_embedding_document

    def tracked_render(*args):
        rendered = original(*args)
        tracked = _TrackedDocument(rendered.chunk_id, rendered.text)
        tracker.watch(tracked)
        return tracked

    monkeypatch.setattr(synchronization, "build_embedding_document", tracked_render)
    provider = _CountingProvider(17)
    synchronization.SemanticIndexer(provider, _CountingStore()).synchronize(
        _inventory(tuple(_chunk(i) for i in range(1025)))
    )
    assert tracker.created == 1025
    assert tracker.peak <= 17
    assert tracker.live == 0


def test_incremental_candidate_bounds_reuse_writes_and_embeds_only_changes():
    # Rebuilding a changed candidate must not forward the entire unchanged
    # manifest in one write or accidentally re-embed its reused rows.
    original = tuple(_chunk(i) for i in range(1025))
    current = original[:-1] + (_chunk(1024, "changed"), _chunk(1025))
    provider = _CountingProvider(17)
    store = _CountingStore(tuple(_stored(chunk) for chunk in original))
    result = synchronization.SemanticIndexer(provider, store).synchronize(_inventory(current))
    assert all(0 < count <= 17 for count in store.reuse_sizes)
    assert len(store.reuse_sizes) == 61
    assert sum(store.reuse_sizes) == 1024
    assert provider.batch_sizes == store.write_sizes == [2]
    assert provider.document_ids == [f"{1025:064x}", f"{1024:064x}"]
    assert set(store.written_ids) == {f"{i:064x}" for i in range(1026)}
    assert result.reused_chunks == 1024
    assert result.embedded_chunks == 2
    assert result.inserted_chunks == result.updated_chunks == 1
    assert result.deleted_chunks == 0


def test_core_does_not_retain_repository_wide_vectors_between_candidate_writes():
    # The sink and provider retain no vector references. A repository-wide
    # vector/VectorRecord collection in the indexer therefore raises this peak.
    tracker = _LifetimeCounter()
    provider = _CountingProvider(17, vector_tracker=tracker)
    store = _CountingStore()
    synchronization.SemanticIndexer(provider, store).synchronize(
        _inventory(tuple(_chunk(i) for i in range(1025)))
    )
    assert tracker.created == 1025
    assert tracker.peak <= 17
    assert tracker.live == 0


def test_large_noop_does_not_render_embed_or_write_candidates(monkeypatch):
    chunks = tuple(_chunk(i) for i in range(4096))
    provider = _CountingProvider()
    store = _CountingStore(tuple(_stored(chunk) for chunk in chunks))

    def forbidden_render(*args):
        raise AssertionError("An unchanged chunk must not be rendered.")

    monkeypatch.setattr(synchronization, "build_embedding_document", forbidden_render)
    result = synchronization.SemanticIndexer(provider, store).synchronize(_inventory(chunks))
    assert result.status is IndexSyncStatus.UNCHANGED
    assert result.reused_chunks == 4096
    assert result.embedded_chunks == 0
    assert provider.batch_sizes == []
    assert provider.query_calls == 0
    assert store.calls == Counter(inspect=1, manifest=1)


@pytest.mark.parametrize("manifest", [None, ()])
def test_empty_inventory_and_active_empty_search_make_zero_provider_calls(manifest):
    provider = _CountingProvider()
    store = _CountingStore(manifest)
    synchronization.SemanticIndexer(provider, store).synchronize(_inventory(()))
    assert store.active.expected_chunk_count == 0
    assert SemanticSearcher(provider, store).search("repo", "query", 100) == ()
    assert provider.batch_sizes == []
    assert provider.query_calls == store.calls["search"] == 0


@pytest.mark.parametrize("top_k", [1, 7, 100])
def test_search_sends_only_bounded_top_k_and_never_reads_a_manifest(top_k):
    provider = _CountingProvider()
    store = _CountingStore(tuple(_stored(_chunk(i)) for i in range(4096)))
    results = SemanticSearcher(provider, store).search("repo", "query", top_k)
    assert len(results) == top_k
    assert store.search_limits == [top_k]
    assert store.calls == Counter(acquire=1, inspect=1, search=1)
    assert provider.query_calls == 1
    assert provider.batch_sizes == []


@pytest.mark.parametrize("top_k", [0, -1, 8, 1000000, True])
def test_invalid_top_k_is_rejected_before_any_provider_or_store_work(top_k):
    provider = _CountingProvider()
    store = _CountingStore(tuple(_stored(_chunk(i)) for i in range(512)))
    with pytest.raises(InvalidSearchRequest):
        SemanticSearcher(provider, store, max_top_k=7).search("repo", "query", top_k)
    assert store.calls == Counter()
    assert provider.batch_sizes == []
    assert provider.query_calls == 0


def test_oversize_document_aborts_without_truncation_mutation_or_losing_old_search():
    old_chunk = _chunk(99999, "old evidence\r\n\t  ")
    source = "\u03bb\r\n\t  " * 4096 + "EXACT-END\r\n  "
    chunks = tuple(_chunk(i) for i in range(17)) + (_chunk(17, source),)
    inventory = _inventory(chunks)
    before = tuple((c.chunk_id, c.content_hash, c.content.encode()) for c in chunks)
    provider = _CountingProvider(17, max_document_chars=1024)
    store = _CountingStore((_stored(old_chunk),))
    old_snapshot = store.active

    with pytest.raises(EmbeddingDocumentTooLarge):
        synchronization.SemanticIndexer(provider, store).synchronize(inventory)

    # The oversize is in the second provider batch, after partial candidate work.
    assert provider.batch_sizes == [17, 1]
    assert store.write_sizes == [17]
    assert provider.rejected_text.split("\n---TRACERAG-SOURCE---\n", 1)[1] == source
    assert tuple((c.chunk_id, c.content_hash, c.content.encode()) for c in chunks) == before
    assert store.calls["abort"] == 1
    assert store.calls["publish"] == store.calls["validate"] == 0
    assert store.active is old_snapshot
    assert store.written_ids == []
    results = SemanticSearcher(provider, store).search("repo", "old evidence", 1)
    assert len(results) == 1
    assert results[0].chunk_id == old_chunk.chunk_id
    assert results[0].content_hash == old_chunk.content_hash
    assert results[0].content.encode() == old_chunk.content.encode()
