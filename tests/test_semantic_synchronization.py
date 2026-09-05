"""Linear synchronization diff classification contracts."""

from dataclasses import fields, is_dataclass

from backend.code_chunker.models import ChunkKind, CodeChunk
from backend.code_parser.models import ParsedLanguage
from backend.code_parser.models import SourceLocation, SymbolKind
from backend.embedding_vector_store.models import EmbeddingModelIdentity, EmbeddingVector
from backend.embedding_vector_store.stores.base import StoredRecord
from backend.embedding_vector_store.synchronization import SyncDiff, _classify_chunks
from backend.embedding_vector_store.validation import ChunkInput


IDENTITY = EmbeddingModelIdentity("provider", "model", 3, "v1")
VECTOR = EmbeddingVector((0.1, 0.2, 0.3))


def _current(chunk_id: str, content_hash: str) -> ChunkInput:
    chunk = CodeChunk(
        chunk_id,
        ChunkKind.SYMBOL,
        SourceLocation(0, 9, 1, 0, 1, 9),
        "content",
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
