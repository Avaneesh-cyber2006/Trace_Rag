"""Module 9 hybrid search's pinned Module 5 evidence boundary."""

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256

import pytest

from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import (
    EmbeddingInvalidResponseError, EmbeddingSpaceMismatch,
    EmbeddingTransientError, InvalidSearchRequest, RepositoryIndexNotFound,
    VectorStoreCorruptionError,
)
from backend.embedding_vector_store.models import EmbeddingModelIdentity, EmbeddingVector
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.stores.base import (
    RepositoryIndexSnapshot, RepositoryIndexState, StoredRecord, StoreSearchResult,
)
from backend.retrieval import (
    HybridRetriever, InvalidHybridEvidence, InvalidLexicalIndex, InvalidRetrievalQuery,
)

NAMESPACE = "tracerag-repository-v1:github:example/資料庫"
IDENTITY = EmbeddingModelIdentity("offline", "test", 3, "v1")


def record(letter: str = "a", content: str = "alpha repository cache", **changes):
    params = dict(
        repository_namespace=NAMESPACE,
        chunk_id=letter * 64,
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
        relative_path=f"src/{letter}.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name=f"test.{letter}",
        parent_qualified_name=None,
        content=content,
        embedding=EmbeddingVector((1.0, 0.0, 0.0)),
        embedding_identity=IDENTITY,
        document_version=EMBEDDING_DOCUMENT_VERSION,
        schema_version="schema-v1",
    )
    params.update(changes)
    return StoredRecord(**params)


def result(source: StoredRecord, **changes):
    fields = dict(
        repository_namespace=source.repository_namespace,
        chunk_id=source.chunk_id,
        content_hash=source.content_hash,
        relative_path=source.relative_path,
        language=source.language,
        chunk_kind=source.chunk_kind,
        symbol_kind=source.symbol_kind,
        qualified_name=source.qualified_name,
        parent_qualified_name=source.parent_qualified_name,
        content=source.content,
        score=0.8,
    )
    fields.update(changes)
    return StoreSearchResult(**fields)


class FakeProvider:
    max_batch_size = 8

    def __init__(self):
        self.identity = IDENTITY
        self.queries = []
        self.query_vector = EmbeddingVector((1.0, 0.0, 0.0))
        self.on_query = lambda: None

    def embed_documents(self, documents):
        raise AssertionError("A search must not embed documents")

    def embed_query(self, text):
        self.queries.append(text)
        self.on_query()
        if isinstance(self.query_vector, BaseException):
            raise self.query_vector
        return self.query_vector


class FakeStore:
    def __init__(self, records=None):
        self.records = (record(),) if records is None else records
        self.snapshot = RepositoryIndexSnapshot(
            repository_namespace=NAMESPACE,
            identity=IDENTITY,
            document_version=EMBEDDING_DOCUMENT_VERSION,
            schema_version="schema-v1",
            expected_chunk_count=len(self.records),
            _token="generation-one",
        )
        self.active_state = RepositoryIndexState(True, self.snapshot)
        self.search_results = tuple(result(r) for r in self.records[:1])
        self.calls = []
        self.lease_open = False
        self.lease_exits = 0
        self.observed_snapshots = []

    def inspect_active(self, namespace):
        raise AssertionError("Must never inspect the active pointer independently")

    @contextmanager
    def acquire_active(self, namespace):
        assert namespace == NAMESPACE
        assert not self.lease_open
        self.calls.append("acquire_active")
        self.lease_open = True
        try:
            yield self.active_state
        finally:
            self.lease_open = False
            self.lease_exits += 1
            self.calls.append("release_active")

    def read_manifest(self, snapshot):
        assert self.lease_open
        self.observed_snapshots.append(snapshot)
        self.calls.append("read_manifest")
        return self.records

    def search(self, snapshot, query, top_k):
        assert self.lease_open
        assert top_k == 100
        assert query.values == (1.0, 0.0, 0.0)
        self.observed_snapshots.append(snapshot)
        self.calls.append("search")
        return self.search_results

    def begin_candidate(self, *args):
        raise AssertionError("A query must not write a candidate")

    def add_reused(self, *args):
        raise AssertionError("A query must not write a candidate")

    def add_embedded(self, *args):
        raise AssertionError("A query must not write a candidate")

    def validate_candidate(self, *args):
        raise AssertionError("A query must not write a candidate")

    def publish(self, *args):
        raise AssertionError("A query must not publish")

    def abort(self, *args):
        raise AssertionError("A query must not abort")

    def delete_repository_index(self, *args):
        raise AssertionError("A query must not delete")


def make(records=None):
    store = FakeStore(records)
    provider = FakeProvider()
    provider.on_query = lambda: (store.lease_open or pytest.fail("Query outside lease"))
    return HybridRetriever(provider, store), store, provider


def test_pins_manifest_embedding_search_and_fusion_to_same_snapshot():
    retriever, store, provider = make()
    hits = retriever.search(NAMESPACE, "alpha cache", 1)
    assert len(hits) == 1
    assert (hits[0].chunk_id, hits[0].content, hits[0].content_hash) == (
        store.records[0].chunk_id, store.records[0].content, store.records[0].content_hash
    )
    assert hits[0].semantic_rank == 1 and hits[0].lexical_rank == 1
    assert store.calls == ["acquire_active", "read_manifest", "search", "release_active"]
    assert store.observed_snapshots == [store.snapshot, store.snapshot]
    assert not store.lease_open and store.lease_exits == 1
    assert provider.queries == ["alpha cache"]


def test_semantic_only_hit_is_included_from_manifest():
    records = (record("a", "lexical term"), record("b", "semantic elsewhere"))
    retriever, store, _ = make(records)
    store.search_results = (result(records[1]),)
    hits = retriever.search(NAMESPACE, "lexical", 2)
    assert {h.chunk_id for h in hits} == {"a" * 64, "b" * 64}
    semantic_hit = next(h for h in hits if h.chunk_id == "b" * 64)
    assert semantic_hit.lexical_rank is None and semantic_hit.semantic_rank == 1


def test_pinned_generation_not_replaced_by_later_active_pointer():
    retriever, store, provider = make()
    old = store.snapshot

    def mutate_active_pointer():
        store.active_state = RepositoryIndexState(
            True, replace(old, _token="generation-two")
        )
    provider.on_query = mutate_active_pointer
    hits = retriever.search(NAMESPACE, "alpha", 1)
    assert len(hits) == 1
    assert store.observed_snapshots == [old, old]
    assert store.lease_exits == 1


def test_empty_published_generation_does_not_embed_or_search():
    retriever, store, provider = make(())
    store.search_results = ()
    assert retriever.search(NAMESPACE, "alpha", 10) == ()
    assert provider.queries == [] and store.calls == [
        "acquire_active", "read_manifest", "release_active"
    ]


def test_no_active_index_raises_and_releases_lease():
    retriever, store, provider = make()
    store.active_state = RepositoryIndexState(False, None)
    with pytest.raises(RepositoryIndexNotFound):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.lease_exits == 1 and provider.queries == []


@pytest.mark.parametrize("value", ("", " ", "\x00", "a" * 4097, None, 3))
def test_invalid_query_rejected_without_acquiring_snapshot(value):
    retriever, store, _ = make()
    with pytest.raises((InvalidRetrievalQuery, InvalidSearchRequest)):
        retriever.search(NAMESPACE, value, 1)
    assert store.calls == []


@pytest.mark.parametrize("value", (0, -1, 101, True, 1.0, None))
def test_invalid_top_k_rejected_before_collaborators(value):
    retriever, store, _ = make()
    with pytest.raises(InvalidSearchRequest):
        retriever.search(NAMESPACE, "alpha", value)
    assert store.calls == []


def test_invalid_namespace_rejected_before_collaborators():
    retriever, store, _ = make()
    with pytest.raises((InvalidSearchRequest, InvalidRetrievalQuery)):
        retriever.search("other\x00repo", "alpha", 1)
    assert store.calls == []


@pytest.mark.parametrize("value", (0, True, 10_001, "60", None))
def test_invalid_rrf_parameter_rejected(value):
    provider = FakeProvider()
    store = FakeStore()
    with pytest.raises(InvalidRetrievalQuery):
        HybridRetriever(provider, store, rrf_k=value)


def test_invalid_provider_or_store_rejected():
    with pytest.raises(InvalidRetrievalQuery):
        HybridRetriever(object(), FakeStore())
    with pytest.raises(InvalidRetrievalQuery):
        HybridRetriever(FakeProvider(), object())


def test_embedding_space_mismatch_stops_before_manifest_read():
    retriever, store, provider = make()
    provider.identity = replace(IDENTITY, model="different")
    with pytest.raises(EmbeddingSpaceMismatch):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.calls == ["acquire_active", "release_active"]


def test_corrupt_snapshot_schema_fails_without_manifest():
    retriever, store, _ = make()
    store.active_state = RepositoryIndexState(True, replace(store.snapshot, document_version="wrong"))
    with pytest.raises(VectorStoreCorruptionError):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.calls == ["acquire_active", "release_active"]


def test_unbounded_manifest_count_stops_before_manifest_or_provider():
    retriever, store, provider = make()
    store.active_state = RepositoryIndexState(True, replace(store.snapshot, expected_chunk_count=8193))
    with pytest.raises(InvalidLexicalIndex):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.calls == ["acquire_active", "release_active"]
    assert not provider.queries


def test_manifest_count_mismatch_fails_closed():
    retriever, store, provider = make()
    store.records = ()
    with pytest.raises(VectorStoreCorruptionError):
        retriever.search(NAMESPACE, "alpha", 1)
    assert not provider.queries and store.lease_exits == 1


@pytest.mark.parametrize("changes", (
    {"repository_namespace": "other"},
    {"document_version": "wrong"},
    {"schema_version": "wrong"},
    {"embedding_identity": EmbeddingModelIdentity("offline", "other", 3, "v1")},
    {"relative_path": "../escape.py"},
    {"content": "tampered"},
))
def test_corrupt_manifest_evidence_fails_before_query(changes):
    retriever, store, provider = make()
    store.records = (replace(store.records[0], **changes),)
    with pytest.raises(VectorStoreCorruptionError):
        retriever.search(NAMESPACE, "alpha", 1)
    assert provider.queries == [] and store.lease_exits == 1


def test_duplicate_manifest_ids_rejected_before_query():
    a = record()
    retriever, store, provider = make((a, a))
    with pytest.raises(InvalidLexicalIndex):
        retriever.search(NAMESPACE, "alpha", 1)
    assert not provider.queries


def test_oversized_document_rejected_before_query():
    retriever, store, provider = make((record(content="x" * 16385),))
    with pytest.raises(VectorStoreCorruptionError):
        retriever.search(NAMESPACE, "x", 1)
    assert not provider.queries


def test_unrelated_semantic_chunk_is_rejected_without_leaking_content():
    retriever, store, _ = make()
    store.search_results = (result(record("b", "foreign private contents")),)
    with pytest.raises(InvalidHybridEvidence, match="^Hybrid retrieval evidence is invalid or inconsistent\\.$"):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.lease_exits == 1


@pytest.mark.parametrize("changes", (
    {"relative_path": "src/changed.py"},
    {"chunk_kind": "file"},
    {"language": "typescript"},
    {"qualified_name": "changed"},
    {"content": "changed"},
))
def test_semantic_metadata_or_content_mismatch_rejected(changes):
    retriever, store, _ = make()
    fields = dict(changes)
    if "content" in fields:
        fields["content_hash"] = sha256(fields["content"].encode("utf-8")).hexdigest()
    store.search_results = (result(store.records[0], **fields),)
    with pytest.raises(InvalidHybridEvidence):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.lease_exits == 1


def test_semantic_duplicate_hits_rejected_by_module5_normalizer():
    retriever, store, _ = make()
    store.search_results = (result(store.records[0]), result(store.records[0]))
    with pytest.raises(VectorStoreCorruptionError):
        retriever.search(NAMESPACE, "alpha", 1)
    assert store.lease_exits == 1


def test_bad_provider_vector_rejected_and_lease_released():
    retriever, store, provider = make()
    provider.query_vector = EmbeddingVector((1.0,))
    with pytest.raises(EmbeddingInvalidResponseError):
        retriever.search(NAMESPACE, "alpha", 1)
    assert "search" not in store.calls and store.lease_exits == 1


def test_retry_policy_applies_only_to_embedding_query():
    retriever, store, provider = make()
    responses = iter((EmbeddingTransientError("secret"), EmbeddingVector((1., 0., 0.))))

    def embed(text):
        provider.queries.append(text)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    provider.embed_query = embed
    retriever = HybridRetriever(
        provider, store,
        retry_policy=RetryPolicy(max_attempts=2, sleeper=lambda delay: None),
    )
    assert len(retriever.search(NAMESPACE, "alpha", 1)) == 1
    assert len(provider.queries) == 2 and store.lease_exits == 1


def test_lexical_results_are_deterministic_when_manifest_order_changes():
    records = (record("a", "query query"), record("b", "query second"))
    first, s1, _ = make(records)
    second, s2, _ = make(tuple(reversed(records)))
    s1.search_results = s2.search_results = ()
    assert first.search(NAMESPACE, "query", 2) == second.search(NAMESPACE, "query", 2)


def test_no_semantic_results_still_returns_lexical_results():
    retriever, store, _ = make()
    store.search_results = ()
    (hit,) = retriever.search(NAMESPACE, "alpha", 1)
    assert hit.lexical_rank == 1 and hit.semantic_rank is None


def test_zero_matching_lexical_terms_still_returns_semantic_results():
    retriever, _, _ = make()
    (hit,) = retriever.search(NAMESPACE, "irrelevant", 1)
    assert hit.lexical_rank is None and hit.semantic_rank == 1


def test_unpaired_surrogate_manifest_content_is_treated_as_corruption():
    retriever, store, provider = make()
    store.records = (replace(store.records[0], content="secret\ud800"),)
    with pytest.raises(VectorStoreCorruptionError):
        retriever.search(NAMESPACE, "alpha", 1)
    assert provider.queries == [] and store.lease_exits == 1


def test_backend_manifest_read_error_propagates_and_releases_lease():
    from backend.embedding_vector_store.exceptions import VectorStoreReadError

    retriever, store, provider = make()

    def failure(snapshot):
        assert store.lease_open
        raise VectorStoreReadError("sanitized backend read failed")

    store.read_manifest = failure
    with pytest.raises(VectorStoreReadError, match="sanitized backend read failed"):
        retriever.search(NAMESPACE, "alpha", 1)
    assert provider.queries == [] and store.lease_exits == 1
