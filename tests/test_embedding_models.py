"""Public contracts for the embedding vector store."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from enum import Enum
import math

import pytest

from backend.embedding_vector_store.exceptions import (
    EmbeddingAuthenticationError,
    EmbeddingDocumentError,
    EmbeddingDocumentTooLarge,
    EmbeddingInvalidRequestError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingSpaceMismatch,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    EmbeddingVectorStoreError,
    InvalidCodeChunkInventory,
    InvalidSearchRequest,
    RepositoryIndexNotFound,
    SemanticSearchError,
    VectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStoreError,
    VectorStorePublicationError,
    VectorStoreReadError,
    VectorStoreWriteError,
)
from backend.embedding_vector_store.models import (
    EmbeddingDocument,
    EmbeddingModelIdentity,
    EmbeddingVector,
    IndexSyncResult,
    IndexSyncStatus,
    VectorRecord,
    VectorSearchResult,
)


IDENTITY = EmbeddingModelIdentity("provider", "model", 3, "v1")
VECTOR = EmbeddingVector((0.1, 0.2, 0.3))


@pytest.mark.parametrize(
    ("model", "expected_fields"),
    [
        (
            EmbeddingModelIdentity,
            ["provider", "model", "dimensions", "compatibility_version"],
        ),
        (EmbeddingVector, ["values"]),
        (EmbeddingDocument, ["chunk_id", "text"]),
        (
            VectorRecord,
            [
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
            ],
        ),
        (
            IndexSyncResult,
            [
                "repository_namespace",
                "status",
                "total_chunks",
                "reused_chunks",
                "embedded_chunks",
                "inserted_chunks",
                "updated_chunks",
                "deleted_chunks",
                "embedding_identity",
                "document_version",
            ],
        ),
        (
            VectorSearchResult,
            [
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
def test_public_models_are_frozen_slotted_dataclasses_with_exact_field_order(
    model, expected_fields
):
    assert is_dataclass(model)
    assert [field.name for field in fields(model)] == expected_fields
    assert getattr(model, "__slots__") == tuple(expected_fields)
    assert getattr(model, "__dataclass_params__").frozen is True


def test_public_models_are_immutable_and_have_no_instance_dicts():
    with pytest.raises(FrozenInstanceError):
        IDENTITY.provider = "other"
    with pytest.raises((AttributeError, TypeError)):
        IDENTITY.extra = "not allowed"
    assert not hasattr(IDENTITY, "__dict__")


@pytest.mark.parametrize("value", ("", None, 1))
def test_embedding_identity_rejects_nonempty_string_contract_violations(value):
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        EmbeddingModelIdentity(value, "model", 3, "v1")
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        EmbeddingModelIdentity("provider", value, 3, "v1")
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        EmbeddingModelIdentity("provider", "model", 3, value)


@pytest.mark.parametrize("dimensions", (True, False, 0, -1, 1.5, "3", None))
def test_embedding_identity_requires_a_positive_non_boolean_integer_dimension(dimensions):
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        EmbeddingModelIdentity("provider", "model", dimensions, "v1")


def test_embedding_vector_requires_tuple_values():
    assert EmbeddingVector((1.0, 2.0)).values == (1.0, 2.0)
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        EmbeddingVector([1.0, 2.0])


@pytest.mark.parametrize("value", ("", None, 1))
def test_embedding_document_requires_nonempty_chunk_id_and_text(value):
    with pytest.raises(EmbeddingDocumentError):
        EmbeddingDocument(value, "content")
    with pytest.raises(EmbeddingDocumentError):
        EmbeddingDocument("a" * 64, value)


def test_index_sync_status_has_stable_string_values():
    assert issubclass(IndexSyncStatus, str)
    assert issubclass(IndexSyncStatus, Enum)
    assert {member.name: member.value for member in IndexSyncStatus} == {
        "SUCCESS": "success",
        "UNCHANGED": "unchanged",
    }


def test_vector_record_requires_exact_identifiers_and_nonempty_evidence_fields():
    valid = VectorRecord(
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
    )
    assert valid.embedding is VECTOR

    for field, value in (
        ("chunk_id", "A" * 64),
        ("content_hash", "a" * 63),
        ("relative_path", ""),
        ("language", ""),
        ("chunk_kind", ""),
        ("content", ""),
    ):
        with pytest.raises(EmbeddingDocumentError):
            replace(valid, **{field: value})


@pytest.mark.parametrize(
    "result",
    (
        IndexSyncResult("repo", IndexSyncStatus.SUCCESS, 3, 1, 2, 1, 1, 0, IDENTITY, "v1"),
        IndexSyncResult("repo", IndexSyncStatus.SUCCESS, 0, 0, 0, 0, 0, 2, IDENTITY, "v1"),
        IndexSyncResult("repo", IndexSyncStatus.UNCHANGED, 3, 3, 0, 0, 0, 0, IDENTITY, "v1"),
    ),
)
def test_index_sync_result_accepts_counter_equations(result):
    assert result.reused_chunks + result.embedded_chunks == result.total_chunks
    assert result.inserted_chunks + result.updated_chunks == result.embedded_chunks


@pytest.mark.parametrize(
    "changes",
    (
        {"total_chunks": -1},
        {"reused_chunks": True},
        {"embedded_chunks": 1.0},
        {"inserted_chunks": -1},
        {"updated_chunks": True},
        {"deleted_chunks": -1},
        {"reused_chunks": 0, "embedded_chunks": 2},
        {"inserted_chunks": 2, "updated_chunks": 1},
    ),
)
def test_index_sync_result_rejects_invalid_counters_and_equations(changes):
    values = {
        "repository_namespace": "repo",
        "status": IndexSyncStatus.SUCCESS,
        "total_chunks": 3,
        "reused_chunks": 1,
        "embedded_chunks": 2,
        "inserted_chunks": 1,
        "updated_chunks": 1,
        "deleted_chunks": 0,
        "embedding_identity": IDENTITY,
        "document_version": "v1",
    }
    values.update(changes)
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        IndexSyncResult(**values)


@pytest.mark.parametrize(
    "changes",
    (
        {"embedded_chunks": 1, "inserted_chunks": 1, "reused_chunks": 2},
        {"inserted_chunks": 1, "updated_chunks": 0, "reused_chunks": 3},
        {"updated_chunks": 1, "inserted_chunks": 0, "reused_chunks": 3},
        {"deleted_chunks": 1},
        {"reused_chunks": 2},
    ),
)
def test_unchanged_sync_requires_no_mutations_and_full_reuse(changes):
    values = {
        "repository_namespace": "repo",
        "status": IndexSyncStatus.UNCHANGED,
        "total_chunks": 3,
        "reused_chunks": 3,
        "embedded_chunks": 0,
        "inserted_chunks": 0,
        "updated_chunks": 0,
        "deleted_chunks": 0,
        "embedding_identity": IDENTITY,
        "document_version": "v1",
    }
    values.update(changes)
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        IndexSyncResult(**values)


@pytest.mark.parametrize("score", (-0.01, 1.01, math.inf, -math.inf, math.nan, True, "1"))
def test_vector_search_result_requires_a_finite_normalized_score(score):
    with pytest.raises(InvalidSearchRequest):
        VectorSearchResult(
            "a" * 64,
            "b" * 64,
            "src/main.py",
            "python",
            "symbol",
            None,
            None,
            None,
            "content",
            score,
        )


def test_no_public_model_contains_storage_or_operational_fields():
    forbidden = {"generation", "collection", "api_key", "retry", "distance", "response"}
    for model in (
        EmbeddingModelIdentity,
        EmbeddingVector,
        EmbeddingDocument,
        VectorRecord,
        IndexSyncResult,
        VectorSearchResult,
    ):
        assert forbidden.isdisjoint(field.name for field in fields(model))


@pytest.mark.parametrize(
    ("child", "parent"),
    (
        (EmbeddingVectorStoreConfigurationError, EmbeddingVectorStoreError),
        (InvalidCodeChunkInventory, EmbeddingVectorStoreError),
        (EmbeddingDocumentError, EmbeddingVectorStoreError),
        (EmbeddingDocumentTooLarge, EmbeddingDocumentError),
        (EmbeddingProviderError, EmbeddingVectorStoreError),
        (EmbeddingAuthenticationError, EmbeddingProviderError),
        (EmbeddingRateLimitError, EmbeddingProviderError),
        (EmbeddingTransientError, EmbeddingProviderError),
        (EmbeddingInvalidRequestError, EmbeddingProviderError),
        (EmbeddingInvalidResponseError, EmbeddingProviderError),
        (VectorStoreError, EmbeddingVectorStoreError),
        (VectorStoreConfigurationError, VectorStoreError),
        (VectorStoreReadError, VectorStoreError),
        (VectorStoreWriteError, VectorStoreError),
        (VectorStorePublicationError, VectorStoreError),
        (VectorStoreCorruptionError, VectorStoreError),
        (SemanticSearchError, EmbeddingVectorStoreError),
        (InvalidSearchRequest, SemanticSearchError),
        (RepositoryIndexNotFound, SemanticSearchError),
        (EmbeddingSpaceMismatch, SemanticSearchError),
    ),
)
def test_public_exception_hierarchy_preserves_typed_distinctions(child, parent):
    assert child.__bases__ == (parent,)
