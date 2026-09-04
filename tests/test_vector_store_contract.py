"""Internal lifecycle contracts at the vector-store boundary."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import inspect
import math

import pytest

from backend.embedding_vector_store.models import EmbeddingModelIdentity, EmbeddingVector
from backend.embedding_vector_store.stores.base import (
    CandidateIndex,
    RepositoryIndexSnapshot,
    RepositoryIndexState,
    StoreSearchResult,
    StoredRecord,
    VectorStore,
)


IDENTITY = EmbeddingModelIdentity("provider", "model", 3, "v1")
VECTOR = EmbeddingVector((0.1, 0.2, 0.3))
TOKEN = object()


def _snapshot() -> RepositoryIndexSnapshot:
    return RepositoryIndexSnapshot("repo", IDENTITY, "document-v1", "schema-v1", 2, TOKEN)


def _candidate() -> CandidateIndex:
    return CandidateIndex("repo", IDENTITY, "document-v1", "schema-v1", 2, TOKEN)


def _record() -> StoredRecord:
    return StoredRecord(
        "repo",
        "a" * 64,
        "b" * 64,
        "src/main.py",
        "python",
        "symbol",
        None,
        None,
        None,
        "def main(): pass",
        VECTOR,
        IDENTITY,
        "document-v1",
        "schema-v1",
    )


def test_vector_store_exposes_the_canonical_lifecycle_signatures():
    expected = {
        "inspect_active": ["self", "repository_namespace"],
        "read_manifest": ["self", "snapshot"],
        "begin_candidate": [
            "self",
            "repository_namespace",
            "identity",
            "document_version",
            "expected_chunk_count",
        ],
        "add_reused": ["self", "candidate", "records"],
        "add_embedded": ["self", "candidate", "records"],
        "validate_candidate": ["self", "candidate"],
        "publish": ["self", "candidate"],
        "abort": ["self", "candidate"],
        "search": ["self", "snapshot", "query", "top_k"],
        "delete_repository_index": ["self", "repository_namespace"],
    }

    for name, parameter_names in expected.items():
        assert list(inspect.signature(getattr(VectorStore, name)).parameters) == parameter_names


@pytest.mark.parametrize(
    ("value_type", "expected_fields"),
    [
        (RepositoryIndexState, ["indexed", "snapshot"]),
        (
            RepositoryIndexSnapshot,
            [
                "repository_namespace",
                "identity",
                "document_version",
                "schema_version",
                "expected_chunk_count",
                "_token",
            ],
        ),
        (
            CandidateIndex,
            [
                "repository_namespace",
                "identity",
                "document_version",
                "schema_version",
                "expected_chunk_count",
                "_token",
            ],
        ),
        (
            StoredRecord,
            [
                "repository_namespace",
                "chunk_id",
                "content_hash",
                "relative_path",
                "language",
                "chunk_kind",
                "symbol_kind",
                "qualified_name",
                "parent_qualified_name",
                "content",
                "embedding",
                "embedding_identity",
                "document_version",
                "schema_version",
            ],
        ),
        (
            StoreSearchResult,
            [
                "repository_namespace",
                "chunk_id",
                "content_hash",
                "relative_path",
                "language",
                "chunk_kind",
                "symbol_kind",
                "qualified_name",
                "parent_qualified_name",
                "content",
                "score",
            ],
        ),
    ],
)
def test_internal_values_are_frozen_slotted_dataclasses_with_exact_fields(
    value_type, expected_fields
):
    assert is_dataclass(value_type)
    assert [field.name for field in fields(value_type)] == expected_fields
    assert value_type.__slots__ == tuple(expected_fields)
    assert value_type.__dataclass_params__.frozen is True


def test_repository_index_state_requires_matching_indexed_and_snapshot_states():
    snapshot = _snapshot()
    assert RepositoryIndexState(False, None).snapshot is None
    assert RepositoryIndexState(True, snapshot).snapshot is snapshot

    with pytest.raises(ValueError):
        RepositoryIndexState(True, None)
    with pytest.raises(ValueError):
        RepositoryIndexState(False, snapshot)


@pytest.mark.parametrize("handle_factory", (_snapshot, _candidate))
def test_handle_exposes_only_logical_contract_fields_and_hides_an_opaque_token(handle_factory):
    handle = handle_factory()
    assert handle.repository_namespace == "repo"
    assert handle.identity is IDENTITY
    assert handle.document_version == "document-v1"
    assert handle.schema_version == "schema-v1"
    assert handle.expected_chunk_count == 2
    assert handle._token is TOKEN
    assert not hasattr(handle, "generation")
    assert not hasattr(handle, "collection")


@pytest.mark.parametrize(
    "changes",
    (
        {"repository_namespace": ""},
        {"identity": "identity"},
        {"document_version": ""},
        {"schema_version": ""},
        {"expected_chunk_count": -1},
        {"expected_chunk_count": True},
        {"_token": None},
    ),
)
def test_handles_validate_logical_contract_invariants(changes):
    for handle in (_snapshot(), _candidate()):
        with pytest.raises(ValueError):
            replace(handle, **changes)


def test_stored_record_carries_vector_evidence_embedding_and_versions():
    record = _record()
    assert record.embedding is VECTOR
    assert record.embedding_identity is IDENTITY
    assert record.document_version == "document-v1"
    assert record.schema_version == "schema-v1"


@pytest.mark.parametrize(
    "changes",
    (
        {"repository_namespace": ""},
        {"chunk_id": "x"},
        {"content_hash": "x"},
        {"relative_path": ""},
        {"language": ""},
        {"chunk_kind": ""},
        {"content": ""},
        {"embedding": "vector"},
        {"embedding_identity": "identity"},
        {"document_version": ""},
        {"schema_version": ""},
    ),
)
def test_stored_record_validates_its_complete_evidence(changes):
    with pytest.raises(ValueError):
        replace(_record(), **changes)


@pytest.mark.parametrize("score", (-0.01, 1.01, math.nan, math.inf, True, "1"))
def test_store_search_result_requires_repository_namespace_and_normalized_score(score):
    with pytest.raises(ValueError):
        StoreSearchResult(
            "repo", "a" * 64, "b" * 64, "src/main.py", "python", "symbol", None,
            None, None, "content", score,
        )


def test_internal_values_are_immutable_and_not_reexported_from_package_top_level():
    with pytest.raises(FrozenInstanceError):
        _snapshot().repository_namespace = "other"

    import backend.embedding_vector_store as package

    for name in (
        "RepositoryIndexState",
        "RepositoryIndexSnapshot",
        "CandidateIndex",
        "StoredRecord",
        "StoreSearchResult",
    ):
        assert not hasattr(package, name)
        assert name not in getattr(package, "__all__", ())
