"""Increment 6: pinned original-file provenance and gap-tolerant graph binding."""

from dataclasses import replace
from backend.embedding_vector_store.stores.base import SourceRevisionToken
from hashlib import sha256

import pytest

from backend.embedding_vector_store.source_revision import (
    InvalidSourceRevision, SourceRevision, build_source_revision,
    fingerprint_records,
)
from backend.retrieval import (
    HybridRetriever, InvalidHybridEvidence, build_graph_revision,
)
from test_graph_provenance import NAMESPACE, same_file_call_bundle, to_index
from test_hybrid_orchestrator import FakeProvider, FakeStore, record, result


def with_omitted_suffix(original, suffix="\n  \n"):
    """Realistic source whose trailing whitespace is intentionally not chunked."""
    files, parsed, chunks = original
    text = "".join(item.content for item in chunks.files[0].chunks) + suffix
    digest = sha256(text.encode("utf-8")).hexdigest()
    new_scanned = replace(files.files[0], size_bytes=len(text.encode("utf-8")))
    new_parsed = replace(parsed.files[0], source_sha256=digest)
    new_chunked = replace(chunks.files[0], source_sha256=digest)
    return (
        replace(files, files=(new_scanned,)),
        replace(parsed, files=(new_parsed,)),
        replace(chunks, files=(new_chunked,)),
    )


def make_store(chunks):
    sources = tuple(
        record("a" if i == 0 else "b", chunk.content, chunk_id=chunk.chunk_id,
               content_hash=chunk.content_hash, relative_path="src/module.py",
               qualified_name=chunk.qualified_name)
        for i, chunk in enumerate(chunks.files[0].chunks)
    )
    store = FakeStore(sources)
    store.search_results = (result(sources[0]),)
    return store


def test_gap_requires_publication_proof_but_is_still_source_valid():
    inventories = with_omitted_suffix(same_file_call_bundle())
    with pytest.raises(InvalidHybridEvidence):
        build_graph_revision(*inventories)
    proof = build_graph_revision(*inventories, require_published_source=True)
    published = build_source_revision(*inventories)
    assert proof.published_source_revision == published.encode()
    assert SourceRevision.decode(published.encode()) == published
    assert published.manifest_fingerprint == fingerprint_records(to_index(inventories[2]).documents)


def test_graph_expands_whitespace_gap_only_with_exact_pinned_publication():
    inventories = with_omitted_suffix(same_file_call_bundle())
    proof = build_graph_revision(*inventories, require_published_source=True)
    store = make_store(inventories[2])
    store.snapshot = replace(store.snapshot, _token=SourceRevisionToken(store.snapshot._token, proof.published_source_revision))
    store.active_state = replace(store.active_state, snapshot=store.snapshot)
    provider = FakeProvider()
    answer = HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 1, proof)
    assert len(answer.hits) == 1
    assert len(answer.context) == 1
    assert store.lease_exits == 1
    assert provider.queries == ["alpha"]


def test_older_index_never_claims_source_provenance():
    inventories = with_omitted_suffix(same_file_call_bundle())
    proof = build_graph_revision(*inventories, require_published_source=True)
    store = make_store(inventories[2])
    provider = FakeProvider()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 1, proof)
    assert provider.queries == []
    assert store.lease_exits == 1


def test_same_chunks_but_different_omitted_bytes_rejected_before_embedding():
    old = with_omitted_suffix(same_file_call_bundle(), "\n  \n")
    newer = with_omitted_suffix(same_file_call_bundle(), "  \n\n")
    proof = build_graph_revision(*old, require_published_source=True)
    new_revision = build_source_revision(*newer).encode()
    assert old[2].files[0].chunks == newer[2].files[0].chunks
    assert proof.published_source_revision != new_revision
    store = make_store(newer[2])
    store.snapshot = replace(store.snapshot, _token=SourceRevisionToken(store.snapshot._token, new_revision))
    store.active_state = replace(store.active_state, snapshot=store.snapshot)
    provider = FakeProvider()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 1, proof)
    assert provider.queries == []
    assert store.lease_exits == 1


def test_legacy_gapless_graph_rejected_when_pinned_source_differs():
    original = same_file_call_bundle()
    binding = build_graph_revision(*original)
    # Same chunk bytes, but source changes exclusively outside stored chunks.
    new_revision = build_source_revision(*with_omitted_suffix(original)).encode()
    store = make_store(original[2])
    store.snapshot = replace(store.snapshot, _token=SourceRevisionToken(store.snapshot._token, new_revision))
    store.active_state = replace(store.active_state, snapshot=store.snapshot)
    provider = FakeProvider()
    with pytest.raises(InvalidHybridEvidence):
        HybridRetriever(provider, store).search_with_graph(NAMESPACE, "alpha", 1, binding)
    assert provider.queries == []
    assert store.lease_exits == 1


@pytest.mark.parametrize("mutate", [
    lambda r: r + " ",
    lambda r: r.replace('"version":"tracerag-source-revision-v1"', '"version":"unknown"'),
    lambda r: r.replace('"chunks_sha256":"', '"chunks_sha256":"e'),
    lambda r: r.replace('"namespace":', '"extra":null,"namespace":'),
    lambda r: "{}",
    lambda r: None,
])
def test_bad_or_noncanonical_persisted_source_metadata_rejected(mutate):
    raw = build_source_revision(*same_file_call_bundle()).encode()
    with pytest.raises(InvalidSourceRevision):
        SourceRevision.decode(mutate(raw))


def test_mismatched_claims_or_out_of_bounds_chunk_rejected():
    files, parsed, chunks = same_file_call_bundle()
    with pytest.raises(InvalidSourceRevision):
        build_source_revision(files, replace(parsed, files=(replace(parsed.files[0], source_sha256='f'*64),)), chunks)
    first_file = chunks.files[0]
    first_chunk = first_file.chunks[0]
    bad_chunk = replace(first_chunk, location=replace(first_chunk.location, end_byte=files.files[0].size_bytes + 1))
    with pytest.raises(InvalidSourceRevision):
        build_source_revision(files, parsed, replace(chunks, files=(replace(first_file, chunks=(bad_chunk, first_file.chunks[1])),)))


def test_explicit_parameter_is_boolean_only():
    with pytest.raises(InvalidHybridEvidence):
        build_graph_revision(*same_file_call_bundle(), require_published_source=1)


class MemoryPublisher:
    """Protocol-level proof that provenance and chunk reuse publish together."""
    def __init__(self):
        from backend.embedding_vector_store.models import EmbeddingModelIdentity
        self.active = None
        self.records = ()
        self.pending = {}
        self.publications = 0

    def inspect_active(self, namespace):
        from backend.embedding_vector_store.stores.base import RepositoryIndexState
        return RepositoryIndexState(self.active is not None, self.active)

    def acquire_active(self, namespace):
        raise AssertionError('not a search fixture')

    def read_manifest(self, snapshot):
        assert snapshot is self.active
        return self.records

    def begin_candidate(self, namespace, identity, version, count):
        return self._begin(namespace, identity, version, count, None)

    def begin_candidate_with_source_revision(self, namespace, identity, version, count, source_revision):
        return self._begin(namespace, identity, version, count, source_revision)

    def _begin(self, namespace, identity, version, count, source_revision):
        from backend.embedding_vector_store.stores.base import CandidateIndex
        candidate = CandidateIndex(namespace, identity, version, 'schema-v1', count,
                                   SourceRevisionToken(f'candidate-{self.publications}', source_revision))
        self.pending[candidate] = []
        return candidate

    def add_reused(self, candidate, records):
        self.pending[candidate].extend(records)

    def add_embedded(self, candidate, records):
        from backend.embedding_vector_store.stores.base import StoredRecord
        for r in records:
            self.pending[candidate].append(StoredRecord(
                candidate.repository_namespace, r.chunk_id, r.content_hash,
                r.relative_path, r.language, r.chunk_kind, r.symbol_kind,
                r.qualified_name, r.parent_qualified_name, r.content, r.embedding,
                candidate.identity, candidate.document_version, candidate.schema_version,
            ))

    def validate_candidate(self, candidate):
        assert len(self.pending[candidate]) == candidate.expected_chunk_count
        if candidate.source_revision is not None:
            SourceRevision.decode(candidate.source_revision).validate_records(tuple(self.pending[candidate]))

    def publish(self, candidate):
        from backend.embedding_vector_store.stores.base import RepositoryIndexSnapshot
        self.validate_candidate(candidate)
        self.records = tuple(self.pending.pop(candidate))
        self.publications += 1
        self.active = RepositoryIndexSnapshot(
            candidate.repository_namespace, candidate.identity, candidate.document_version,
            candidate.schema_version, candidate.expected_chunk_count,
            SourceRevisionToken(f'published-{self.publications}', candidate.source_revision),
        )
        return self.active

    def abort(self, candidate):
        self.pending.pop(candidate, None)

    def search(self, snapshot, query, top_k):
        raise AssertionError('not a search fixture')

    def delete_repository_index(self, namespace):
        self.active = None
        self.records = ()


class MemoryEmbedder:
    from backend.embedding_vector_store.models import EmbeddingModelIdentity
    identity = EmbeddingModelIdentity('offline', 'test', 3, 'v1')
    max_batch_size = 8

    def __init__(self):
        self.embedded = 0

    def embed_documents(self, documents):
        from backend.embedding_vector_store.models import EmbeddingVector
        self.embedded += len(documents)
        return tuple(EmbeddingVector((1., 0., 0.)) for _ in documents)

    def embed_query(self, text):
        raise AssertionError('not a search fixture')


def test_source_revision_is_atomically_published_reused_and_downgraded():
    from backend.embedding_vector_store.models import IndexSyncStatus
    from backend.embedding_vector_store.synchronization import SemanticIndexer
    old = with_omitted_suffix(same_file_call_bundle(), '\n  \n')
    new = with_omitted_suffix(same_file_call_bundle(), '  \n\n')
    store, provider = MemoryPublisher(), MemoryEmbedder()
    indexer = SemanticIndexer(provider, store)
    assert indexer.synchronize_with_sources(*old).status is IndexSyncStatus.SUCCESS
    assert provider.embedded == 2 and store.publications == 1
    assert SourceRevision.decode(store.active.source_revision) == build_source_revision(*old)
    assert indexer.synchronize_with_sources(*old).status is IndexSyncStatus.UNCHANGED
    assert store.publications == 1
    assert indexer.synchronize_with_sources(*new).status is IndexSyncStatus.SUCCESS
    assert provider.embedded == 2 and store.publications == 2
    assert SourceRevision.decode(store.active.source_revision) == build_source_revision(*new)
    assert indexer.synchronize(new[2]).status is IndexSyncStatus.SUCCESS
    assert provider.embedded == 2 and store.publications == 3
    assert store.active.source_revision is None
    assert indexer.synchronize(new[2]).status is IndexSyncStatus.UNCHANGED
    assert store.publications == 3


def test_invalid_source_revision_precedes_any_store_or_provider_call():
    from backend.embedding_vector_store.synchronization import SemanticIndexer
    original = same_file_call_bundle()
    store, provider = MemoryPublisher(), MemoryEmbedder()
    indexer = SemanticIndexer(provider, store)
    bad = replace(original[1], files=(replace(original[1].files[0], source_sha256='a'*64),))
    with pytest.raises(InvalidSourceRevision):
        indexer.synchronize_with_sources(original[0], bad, original[2])
    assert store.publications == 0 and provider.embedded == 0


def test_provenance_publication_fails_closed_for_legacy_store():
    """Do not silently drop source claims when the adapter has no extension."""
    from backend.embedding_vector_store.exceptions import EmbeddingVectorStoreConfigurationError
    from backend.embedding_vector_store.synchronization import SemanticIndexer

    class LegacyPublisher(MemoryPublisher):
        begin_candidate_with_source_revision = None

    publisher, provider = LegacyPublisher(), MemoryEmbedder()
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        SemanticIndexer(provider, publisher).synchronize_with_sources(
            *with_omitted_suffix(same_file_call_bundle())
        )
    assert provider.embedded == 0
    assert publisher.publications == 0
