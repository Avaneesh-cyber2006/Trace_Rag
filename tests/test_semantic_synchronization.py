"""Linear synchronization diff classification contracts."""

from dataclasses import fields, is_dataclass

from backend.code_chunker.models import ChunkKind, CodeChunk
from backend.code_parser.models import ParsedLanguage
from backend.code_parser.models import SourceLocation, SymbolKind
import math

import pytest

from backend.embedding_vector_store.exceptions import (
    EmbeddingDocumentTooLarge,
    EmbeddingInvalidResponseError,
    EmbeddingInvalidRequestError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
)
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity,
    EmbeddingVector,
)
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.stores.base import StoredRecord
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
    ) -> None:
        self.max_batch_size = max_batch_size
        self.identity = identity
        self.outcomes = list(outcomes)
        self.calls: list[tuple[object, ...]] = []

    def embed_documents(self, documents: tuple[object, ...]) -> tuple[EmbeddingVector, ...]:
        self.calls.append(documents)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome  # type: ignore[return-value]
        return tuple(
            EmbeddingVector((float(index), 0.25, 0.5))
            for index, _ in enumerate(documents, start=1)
        )


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
