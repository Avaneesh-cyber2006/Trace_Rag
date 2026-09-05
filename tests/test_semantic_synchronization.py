"""Linear synchronization diff classification contracts."""

from dataclasses import fields, is_dataclass, replace

from backend.code_chunker.models import (
    ChunkFileStatus,
    ChunkKind,
    ChunkedFile,
    CodeChunk,
    CodeChunkInventory,
)
from backend.code_parser.models import ParseStatus, ParsedLanguage
from backend.code_parser.models import SourceLocation, SymbolKind
import math

import pytest

from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import (
    EmbeddingDocumentTooLarge,
    EmbeddingInvalidResponseError,
    EmbeddingInvalidRequestError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStorePublicationError,
    VectorStoreWriteError,
)
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity,
    EmbeddingVector,
    IndexSyncStatus,
    VectorRecord,
)
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.stores.base import (
    CandidateIndex,
    RepositoryIndexSnapshot,
    RepositoryIndexState,
    StoreSearchResult,
    StoredRecord,
)
import backend.embedding_vector_store.synchronization as synchronization_module
from backend.embedding_vector_store.synchronization import (
    SyncDiff,
    _classify_chunks,
    _embed_chunk_inputs,
)
from backend.embedding_vector_store.validation import ChunkInput


IDENTITY = EmbeddingModelIdentity("provider", "model", 3, "v1")
VECTOR = EmbeddingVector((0.1, 0.2, 0.3))


def _current(
    chunk_id: str, content_hash: str, content: str = "content"
) -> ChunkInput:
    chunk = CodeChunk(
        chunk_id,
        ChunkKind.SYMBOL,
        SourceLocation(0, 9, 1, 0, 1, 9),
        content,
        content_hash,
        SymbolKind.FUNCTION,
        "name",
        "name",
        None,
        (),
        None,
        (),
        (),
        (),
        0,
        1,
    )
    return ChunkInput(chunk, "src/main.py", ParsedLanguage.PYTHON)


def _stored(chunk_id: str, content_hash: str) -> StoredRecord:
    return StoredRecord(
        "repo",
        chunk_id,
        content_hash,
        "src/main.py",
        "python",
        "symbol",
        None,
        None,
        None,
        "content",
        VECTOR,
        IDENTITY,
        "document-v1",
        "schema-v1",
    )


def _ids(values: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(value.chunk.chunk_id if isinstance(value, ChunkInput) else value.chunk_id for value in values)


class _RecordingProvider:
    def __init__(
        self,
        max_batch_size: int,
        identity: EmbeddingModelIdentity = IDENTITY,
        outcomes: tuple[object, ...] = (),
        events: list[object] | None = None,
    ) -> None:
        self.max_batch_size = max_batch_size
        self.identity = identity
        self.outcomes = list(outcomes)
        self.calls: list[tuple[object, ...]] = []
        self.events = events

    def embed_documents(self, documents: tuple[object, ...]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(documents)
        if self.events is not None:
            self.events.append(
                ("embed_documents", tuple(document.chunk_id for document in documents))
            )
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome  # type: ignore[return-value]
        return tuple(
            EmbeddingVector((float(index), 0.25, 0.5))
            for index, _ in enumerate(documents, start=1)
        )

    def embed_query(self, query_text: str) -> EmbeddingVector:
        raise AssertionError("synchronization must not embed queries")


class _RecordingStore:
    def __init__(
        self,
        manifest: tuple[StoredRecord, ...],
        events: list[object],
        failures: dict[str, Exception] | None = None,
    ) -> None:
        self.events = events
        self.failures = failures or {}
        self.old_snapshot = RepositoryIndexSnapshot(
            "repo",
            IDENTITY,
            EMBEDDING_DOCUMENT_VERSION,
            "schema-v1",
            len(manifest),
            object(),
        )
        self.active_snapshot = self.old_snapshot
        self.manifest = manifest
        self.candidate_records: dict[
            CandidateIndex, list[StoredRecord | VectorRecord]
        ] = {}

    def _fail(self, stage: str) -> None:
        failure = self.failures.get(stage)
        if failure is not None:
            raise failure

    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState:
        self.events.append(("inspect_active", repository_namespace))
        return RepositoryIndexState(True, self.active_snapshot)

    def read_manifest(
        self, snapshot: RepositoryIndexSnapshot
    ) -> tuple[StoredRecord, ...]:
        self.events.append(("read_manifest", snapshot))
        assert snapshot is self.old_snapshot
        return self.manifest

    def begin_candidate(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        expected_chunk_count: int,
    ) -> CandidateIndex:
        self.events.append(
            (
                "begin_candidate",
                repository_namespace,
                identity,
                document_version,
                expected_chunk_count,
            )
        )
        candidate = CandidateIndex(
            repository_namespace,
            identity,
            document_version,
            "schema-v1",
            expected_chunk_count,
            object(),
        )
        self.candidate_records[candidate] = []
        return candidate

    def add_reused(
        self, candidate: CandidateIndex, records: tuple[StoredRecord, ...]
    ) -> None:
        self.events.append(("add_reused", tuple(record.chunk_id for record in records)))
        self._fail("add_reused")
        self.candidate_records[candidate].extend(records)

    def add_embedded(
        self, candidate: CandidateIndex, records: tuple[VectorRecord, ...]
    ) -> None:
        self.events.append(("add_embedded", tuple(record.chunk_id for record in records)))
        self._fail("add_embedded")
        self.candidate_records[candidate].extend(records)

    def validate_candidate(self, candidate: CandidateIndex) -> None:
        self.events.append("validate_candidate")
        self._fail("validate_candidate")
        assert len(self.candidate_records[candidate]) == candidate.expected_chunk_count

    def publish(self, candidate: CandidateIndex) -> RepositoryIndexSnapshot:
        self.events.append("publish")
        self._fail("publish")
        self.active_snapshot = RepositoryIndexSnapshot(
            candidate.repository_namespace,
            candidate.identity,
            candidate.document_version,
            candidate.schema_version,
            candidate.expected_chunk_count,
            object(),
        )
        return self.active_snapshot

    def abort(self, candidate: CandidateIndex) -> None:
        self.events.append("abort")
        self._fail("abort")
        self.candidate_records.pop(candidate, None)

    def search(
        self,
        snapshot: RepositoryIndexSnapshot,
        query: EmbeddingVector,
        top_k: int,
    ) -> tuple[StoreSearchResult, ...]:
        assert snapshot is self.old_snapshot
        record = self.manifest[0]
        return (
            StoreSearchResult(
                record.repository_namespace,
                record.chunk_id,
                record.content_hash,
                record.relative_path,
                record.language,
                record.chunk_kind,
                record.symbol_kind,
                record.qualified_name,
                record.parent_qualified_name,
                record.content,
                1.0,
            ),
        )

    def delete_repository_index(self, repository_namespace: str) -> None:
        raise AssertionError("synchronization must not delete repository indexes")


def _batch_inputs() -> tuple[ChunkInput, ...]:
    return (
        _current("a" * 64, "b" * 64, "first\r\ntrailing  "),
        _current("c" * 64, "d" * 64, "second\r\n  "),
        _current("e" * 64, "f" * 64, "third\r\n\t"),
    )


@pytest.mark.parametrize(
    ("capacity", "expected_sizes"),
    ((1, (1, 1, 1)), (2, (2, 1)), (5, (3,))),
)
def test_embedding_batches_have_exact_capacity_and_preserve_document_source(
    capacity: int, expected_sizes: tuple[int, ...]
):
    provider = _RecordingProvider(capacity)
    inputs = _batch_inputs()

    records = _embed_chunk_inputs(inputs, provider, RetryPolicy(sleeper=lambda _: None))

    assert tuple(len(call) for call in provider.calls) == expected_sizes
    assert all(call for call in provider.calls)
    assert tuple(record.chunk_id for record in records) == tuple(
        item.chunk.chunk_id for item in inputs
    )
    assert tuple(record.content for record in records) == tuple(
        item.chunk.content for item in inputs
    )
    documents = tuple(document for call in provider.calls for document in call)
    assert tuple(document.text.endswith(item.chunk.content) for document, item in zip(documents, inputs, strict=True)) == (
        True,
        True,
        True,
    )


def test_embedding_batches_associate_each_response_position_with_its_chunk():
    inputs = _batch_inputs()
    vectors = (
        EmbeddingVector((1.0, 0.0, 0.0)),
        EmbeddingVector((2.0, 0.0, 0.0)),
        EmbeddingVector((3.0, 0.0, 0.0)),
    )
    provider = _RecordingProvider(2, outcomes=(vectors[:2], vectors[2:]))

    records = _embed_chunk_inputs(inputs, provider, RetryPolicy(sleeper=lambda _: None))

    assert tuple(record.embedding for record in records) == vectors
    assert tuple(record.content_hash for record in records) == tuple(
        item.chunk.content_hash for item in inputs
    )


@pytest.mark.parametrize(
    ("inputs", "malformed"),
    (
        ((_batch_inputs()[0],), ()),
        (_batch_inputs()[:2], (VECTOR, EmbeddingVector((0.1, 0.2)))),
        (_batch_inputs()[:2], (VECTOR, EmbeddingVector((0.1, math.nan, 0.3)))),
        (_batch_inputs()[:2], (VECTOR, EmbeddingVector((0.1, math.inf, 0.3)))),
    ),
)
def test_embedding_batches_reject_a_malformed_response_without_returning_records(
    inputs: tuple[ChunkInput, ...],
    malformed: tuple[EmbeddingVector, ...],
):
    provider = _RecordingProvider(2, outcomes=(malformed,))

    with pytest.raises(EmbeddingInvalidResponseError):
        _embed_chunk_inputs(inputs, provider, RetryPolicy(sleeper=lambda _: None))

    assert len(provider.calls) == 1


def test_embedding_batches_retry_only_typed_transient_or_rate_limit_failures():
    transient_provider = _RecordingProvider(
        1,
        outcomes=(EmbeddingTransientError("temporary"), (VECTOR,)),
    )
    rate_limited_provider = _RecordingProvider(
        1,
        outcomes=(EmbeddingRateLimitError("limited"), (VECTOR,)),
    )
    permanent_provider = _RecordingProvider(
        1,
        outcomes=(EmbeddingInvalidRequestError("invalid"),),
    )
    policy = RetryPolicy(sleeper=lambda _: None)
    input_item = (_batch_inputs()[0],)

    assert _embed_chunk_inputs(input_item, transient_provider, policy)[0].embedding is VECTOR
    assert _embed_chunk_inputs(input_item, rate_limited_provider, policy)[0].embedding is VECTOR
    with pytest.raises(EmbeddingInvalidRequestError):
        _embed_chunk_inputs(input_item, permanent_provider, policy)

    assert len(transient_provider.calls) == 2
    assert len(rate_limited_provider.calls) == 2
    assert len(permanent_provider.calls) == 1


def test_embedding_batches_propagate_oversize_without_retrying_or_normalizing():
    provider = _RecordingProvider(1, outcomes=(EmbeddingDocumentTooLarge("too large"),))
    item = _batch_inputs()[0]

    with pytest.raises(EmbeddingDocumentTooLarge):
        _embed_chunk_inputs((item,), provider, RetryPolicy(sleeper=lambda _: None))

    assert len(provider.calls) == 1
    assert provider.calls[0][0].text.endswith(item.chunk.content)


@pytest.mark.parametrize("capacity", (0, -1, True, 1.5))
def test_embedding_batches_reject_malformed_provider_capacity(capacity: object):
    provider = _RecordingProvider(capacity)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        _embed_chunk_inputs(_batch_inputs(), provider, RetryPolicy(sleeper=lambda _: None))

    assert provider.calls == []


def test_sync_diff_is_frozen_slotted_with_exact_categories():
    assert is_dataclass(SyncDiff)
    assert SyncDiff.__slots__ == ("unchanged", "new", "updated", "deleted", "compatible")
    assert [field.name for field in fields(SyncDiff)] == [
        "unchanged",
        "new",
        "updated",
        "deleted",
        "compatible",
    ]
    assert SyncDiff.__dataclass_params__.frozen is True


def test_classification_reuses_same_id_and_hash_and_reembeds_changed_hash():
    same = _stored("a" * 64, "b" * 64)
    changed = _stored("b" * 64, "c" * 64)
    current = (
        _current(same.chunk_id, same.content_hash),
        _current(changed.chunk_id, "d" * 64),
        _current("e" * 64, "f" * 64),
    )

    diff = _classify_chunks(current, (same, changed), True, True)

    assert diff.compatible is True
    assert _ids(diff.unchanged) == (same.chunk_id,)
    assert _ids(diff.new) == ("e" * 64,)
    assert _ids(diff.updated) == (changed.chunk_id,)
    assert _ids(diff.deleted) == ()


def test_classification_handles_first_index_and_stored_only_deletions():
    current = (_current("a" * 64, "b" * 64), _current("c" * 64, "d" * 64))
    stored = (_stored("e" * 64, "f" * 64),)

    first = _classify_chunks(current, (), True, True)
    mixed = _classify_chunks(current, stored, True, True)

    assert first.compatible is True
    assert _ids(first.unchanged) == ()
    assert _ids(first.new) == ("a" * 64, "c" * 64)
    assert _ids(first.updated) == ()
    assert _ids(first.deleted) == ()
    assert _ids(mixed.new) == ("a" * 64, "c" * 64)
    assert _ids(mixed.deleted) == ("e" * 64,)


def test_classification_preserves_current_and_stored_order_when_inputs_are_reordered():
    stored = (
        _stored("a" * 64, "b" * 64),
        _stored("c" * 64, "d" * 64),
        _stored("e" * 64, "f" * 64),
    )
    current = (
        _current("e" * 64, "f" * 64),
        _current("a" * 64, "b" * 64),
        _current("8" * 64, "7" * 64),
    )

    diff = _classify_chunks(current, stored, True, True)

    assert _ids(diff.unchanged) == ("e" * 64, "a" * 64)
    assert _ids(diff.new) == ("8" * 64,)
    assert _ids(diff.deleted) == ("c" * 64,)


def test_incompatible_identity_or_document_forces_all_current_reembedding():
    current = (
        _current("a" * 64, "b" * 64),
        _current("c" * 64, "d" * 64),
        _current("e" * 64, "f" * 64),
    )
    stored = (
        _stored("a" * 64, "b" * 64),
        _stored("c" * 64, "0" * 64),
        _stored("1" * 64, "2" * 64),
    )

    for identity_compatible, document_compatible in ((False, True), (True, False), (False, False)):
        diff = _classify_chunks(current, stored, identity_compatible, document_compatible)

        assert diff.compatible is False
        assert _ids(diff.unchanged) == ()
        assert _ids(diff.new) == ("e" * 64,)
        assert _ids(diff.updated) == ("a" * 64, "c" * 64)
        assert _ids(diff.deleted) == ("1" * 64,)


def test_classification_scales_linearly_with_current_and_stored_items():
    current = tuple(_current(f"{index:064x}", f"{index + 1:064x}") for index in range(200))
    stored = tuple(_stored(f"{index:064x}", f"{index + 1:064x}") for index in range(100, 300))

    diff = _classify_chunks(current, stored, True, True)

    assert len(diff.unchanged) == 100
    assert len(diff.new) == 100
    assert len(diff.updated) == 0
    assert len(diff.deleted) == 100


def _stored_for_sync(
    item: ChunkInput, *, content_hash: str | None = None
) -> StoredRecord:
    return StoredRecord(
        "repo",
        item.chunk.chunk_id,
        item.chunk.content_hash if content_hash is None else content_hash,
        item.relative_path,
        item.language.value,
        item.chunk.kind.value,
        item.chunk.symbol_kind.value if item.chunk.symbol_kind else None,
        item.chunk.qualified_name,
        item.chunk.parent_qualified_name,
        item.chunk.content,
        VECTOR,
        IDENTITY,
        EMBEDDING_DOCUMENT_VERSION,
        "schema-v1",
    )


def _changed_compatible_inventory(
) -> tuple[CodeChunkInventory, tuple[ChunkInput, ...], tuple[StoredRecord, ...]]:
    items = (
        _current("a" * 64, "b" * 64, "unchanged"),
        _current("c" * 64, "d" * 64, "updated"),
        _current("e" * 64, "f" * 64, "new"),
    )
    chunks = tuple(
        replace(
            item.chunk,
            location=SourceLocation(
                index * 10, index * 10 + 9, index + 1, 0, index + 1, 9
            ),
        )
        for index, item in enumerate(items)
    )
    file = ChunkedFile(
        "src/main.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        ChunkFileStatus.SUCCESS,
        "0" * 64,
        (),
        chunks,
        (),
    )
    inventory = CodeChunkInventory(".", "repo", 1, 1, 0, 0, 3, (file,))
    current = tuple(
        ChunkInput(chunk, file.relative_path, file.language) for chunk in chunks
    )
    deleted = _stored_for_sync(_current("1" * 64, "2" * 64, "deleted"))
    manifest = (
        _stored_for_sync(current[0]),
        _stored_for_sync(current[1], content_hash="3" * 64),
        deleted,
    )
    return inventory, current, manifest


def test_semantic_indexer_orchestration_builds_and_publishes_complete_candidate(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[object] = []
    inventory, current, manifest = _changed_compatible_inventory()
    provider = _RecordingProvider(10, events=events)
    store = _RecordingStore(manifest, events)
    original_validate = synchronization_module.validate_and_flatten_inventory
    original_classify = synchronization_module._classify_chunks

    def recording_validate(
        value: CodeChunkInventory,
    ) -> tuple[str, tuple[ChunkInput, ...]]:
        events.append("validate_inventory")
        return original_validate(value)

    def recording_classify(
        current_inputs: tuple[ChunkInput, ...],
        stored_records: tuple[StoredRecord, ...],
        identity_compatible: bool,
        document_compatible: bool,
    ) -> SyncDiff:
        events.append(
            (
                "classify",
                _ids(current_inputs),
                _ids(stored_records),
                identity_compatible,
                document_compatible,
            )
        )
        return original_classify(
            current_inputs,
            stored_records,
            identity_compatible,
            document_compatible,
        )

    monkeypatch.setattr(
        synchronization_module, "validate_and_flatten_inventory", recording_validate
    )
    monkeypatch.setattr(synchronization_module, "_classify_chunks", recording_classify)

    result = synchronization_module.SemanticIndexer(
        provider, store, RetryPolicy(sleeper=lambda _: None)
    ).synchronize(inventory)

    unchanged_id = current[0].chunk.chunk_id
    updated_id = current[1].chunk.chunk_id
    new_id = current[2].chunk.chunk_id
    assert events == [
        "validate_inventory",
        ("inspect_active", "repo"),
        ("read_manifest", store.old_snapshot),
        (
            "classify",
            _ids(current),
            _ids(manifest),
            True,
            True,
        ),
        (
            "begin_candidate",
            "repo",
            IDENTITY,
            EMBEDDING_DOCUMENT_VERSION,
            3,
        ),
        ("add_reused", (unchanged_id,)),
        ("embed_documents", (new_id, updated_id)),
        ("add_embedded", (new_id, updated_id)),
        "validate_candidate",
        "publish",
    ]
    assert result.repository_namespace == "repo"
    assert result.status is IndexSyncStatus.SUCCESS
    assert result.total_chunks == 3
    assert result.reused_chunks == 1
    assert result.embedded_chunks == 2
    assert result.inserted_chunks == 1
    assert result.updated_chunks == 1
    assert result.deleted_chunks == 1
    assert result.embedding_identity is IDENTITY
    assert result.document_version == EMBEDDING_DOCUMENT_VERSION
    candidate_records = next(iter(store.candidate_records.values()))
    assert tuple(record.chunk_id for record in candidate_records) == (
        unchanged_id,
        new_id,
        updated_id,
    )
    assert manifest[2].chunk_id not in {
        record.chunk_id for record in candidate_records
    }
    assert store.active_snapshot is not store.old_snapshot


def test_semantic_indexer_unchanged_fast_path_skips_provider_and_store_mutations():
    events: list[object] = []
    inventory, current, _ = _changed_compatible_inventory()
    manifest = tuple(_stored_for_sync(item) for item in current)

    class FailIfCalledProvider(_RecordingProvider):
        def embed_documents(self, documents: tuple[object, ...]) -> tuple[EmbeddingVector, ...]:
            raise AssertionError("unchanged synchronization must not embed documents")

        def embed_query(self, query_text: str) -> EmbeddingVector:
            raise AssertionError("unchanged synchronization must not embed queries")

    class FailIfCalledStore(_RecordingStore):
        def begin_candidate(self, *args: object, **kwargs: object) -> CandidateIndex:
            raise AssertionError("unchanged synchronization must not create a candidate")

        def add_reused(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("unchanged synchronization must not write reused records")

        def add_embedded(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("unchanged synchronization must not write embedded records")

        def validate_candidate(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("unchanged synchronization must not validate a candidate")

        def publish(self, *args: object, **kwargs: object) -> RepositoryIndexSnapshot:
            raise AssertionError("unchanged synchronization must not publish")

        def abort(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("unchanged synchronization must not abort")

    provider = FailIfCalledProvider(10)
    store = FailIfCalledStore(manifest, events)

    result = synchronization_module.SemanticIndexer(
        provider, store, RetryPolicy(sleeper=lambda _: None)
    ).synchronize(inventory)

    assert result.status is IndexSyncStatus.UNCHANGED
    assert result.total_chunks == len(current)
    assert result.reused_chunks == len(current)
    assert result.embedded_chunks == 0
    assert result.inserted_chunks == 0
    assert result.updated_chunks == 0
    assert result.deleted_chunks == 0
    assert provider.calls == []
    assert events == [
        ("inspect_active", "repo"),
        ("read_manifest", store.old_snapshot),
    ]


@pytest.mark.parametrize(
    ("failure_stage", "failure"),
    (
        ("provider", EmbeddingInvalidRequestError("invalid")),
        ("add_embedded", VectorStoreWriteError("write failed")),
        ("validate_candidate", VectorStoreCorruptionError("invalid candidate")),
        ("publish", VectorStorePublicationError("publish failed")),
    ),
)
def test_semantic_indexer_failure_preservation_aborts_and_keeps_old_active_searchable(
    failure_stage: str, failure: Exception
):
    events: list[object] = []
    inventory, _, manifest = _changed_compatible_inventory()
    provider = _RecordingProvider(
        10,
        outcomes=(failure,) if failure_stage == "provider" else (),
        events=events,
    )
    store = _RecordingStore(
        manifest,
        events,
        failures={failure_stage: failure} if failure_stage != "provider" else None,
    )

    with pytest.raises(type(failure)) as caught:
        synchronization_module.SemanticIndexer(
            provider, store, RetryPolicy(sleeper=lambda _: None)
        ).synchronize(inventory)

    assert caught.value is failure
    assert events[-1] == "abort"
    assert store.active_snapshot is store.old_snapshot
    assert store.search(store.old_snapshot, VECTOR, 1)[0].content == manifest[0].content
    if failure_stage == "publish":
        assert events[-2:] == ["publish", "abort"]
    else:
        assert "publish" not in events


def test_semantic_indexer_failure_preservation_does_not_mask_primary_when_abort_fails():
    events: list[object] = []
    inventory, _, manifest = _changed_compatible_inventory()
    primary = VectorStoreWriteError("primary write failure")
    store = _RecordingStore(
        manifest,
        events,
        failures={
            "add_embedded": primary,
            "abort": VectorStoreWriteError("secondary abort failure"),
        },
    )

    with pytest.raises(VectorStoreWriteError) as caught:
        synchronization_module.SemanticIndexer(
            _RecordingProvider(10, events=events),
            store,
            RetryPolicy(sleeper=lambda _: None),
        ).synchronize(inventory)

    assert caught.value is primary
    assert events[-1] == "abort"
    assert store.active_snapshot is store.old_snapshot


def test_semantic_indexer_orchestration_validates_dependencies_eagerly():
    _, _, manifest = _changed_compatible_inventory()
    store = _RecordingStore(manifest, [])

    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        synchronization_module.SemanticIndexer(_RecordingProvider(0), store)
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        synchronization_module.SemanticIndexer(
            _RecordingProvider(10), object()  # type: ignore[arg-type]
        )
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        synchronization_module.SemanticIndexer(
            _RecordingProvider(10), store, object()  # type: ignore[arg-type]
        )
