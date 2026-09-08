"""Immutable public contracts for embedding vector-store operations."""

from dataclasses import dataclass, field
from enum import Enum
import math
import re

from .exceptions import (
    EmbeddingDocumentError,
    EmbeddingVectorStoreConfigurationError,
    InvalidSearchRequest,
)


_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_MODEL_CONTRACT_MESSAGE = "Embedding vector store model contract is invalid."
_DOCUMENT_CONTRACT_MESSAGE = "Embedding document contract is invalid."
_SEARCH_RESULT_CONTRACT_MESSAGE = "Semantic search result contract is invalid."


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_lowercase_sha256(value: object) -> bool:
    return isinstance(value, str) and _LOWERCASE_SHA256.fullmatch(value) is not None


def _is_optional_nonempty_string(value: object) -> bool:
    return value is None or _is_nonempty_string(value)


def _is_nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True, slots=True)
class EmbeddingModelIdentity:
    provider: str
    model: str
    dimensions: int
    compatibility_version: str

    def __post_init__(self) -> None:
        if (
            not _is_nonempty_string(self.provider)
            or not _is_nonempty_string(self.model)
            or type(self.dimensions) is not int
            or self.dimensions <= 0
            or not _is_nonempty_string(self.compatibility_version)
        ):
            raise EmbeddingVectorStoreConfigurationError(_MODEL_CONTRACT_MESSAGE)


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    values: tuple[float, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise EmbeddingVectorStoreConfigurationError(_MODEL_CONTRACT_MESSAGE)


@dataclass(frozen=True, slots=True)
class EmbeddingDocument:
    chunk_id: str
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _is_lowercase_sha256(self.chunk_id) or not _is_nonempty_string(self.text):
            raise EmbeddingDocumentError(_DOCUMENT_CONTRACT_MESSAGE)


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    content_hash: str
    relative_path: str
    language: str
    chunk_kind: str
    symbol_kind: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    content: str = field(repr=False)
    embedding: EmbeddingVector = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_lowercase_sha256(self.chunk_id)
            or not _is_lowercase_sha256(self.content_hash)
            or not _is_nonempty_string(self.relative_path)
            or not _is_nonempty_string(self.language)
            or not _is_nonempty_string(self.chunk_kind)
            or not _is_optional_nonempty_string(self.symbol_kind)
            or not _is_optional_nonempty_string(self.qualified_name)
            or not _is_optional_nonempty_string(self.parent_qualified_name)
            or not _is_nonempty_string(self.content)
            or not isinstance(self.embedding, EmbeddingVector)
        ):
            raise EmbeddingDocumentError(_DOCUMENT_CONTRACT_MESSAGE)


class IndexSyncStatus(str, Enum):
    SUCCESS = "success"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class IndexSyncResult:
    repository_namespace: str
    status: IndexSyncStatus
    total_chunks: int
    reused_chunks: int
    embedded_chunks: int
    inserted_chunks: int
    updated_chunks: int
    deleted_chunks: int
    embedding_identity: EmbeddingModelIdentity
    document_version: str

    def __post_init__(self) -> None:
        counters = (
            self.total_chunks,
            self.reused_chunks,
            self.embedded_chunks,
            self.inserted_chunks,
            self.updated_chunks,
            self.deleted_chunks,
        )
        if (
            not _is_nonempty_string(self.repository_namespace)
            or not isinstance(self.status, IndexSyncStatus)
            or not all(_is_nonnegative_integer(counter) for counter in counters)
            or self.inserted_chunks + self.updated_chunks != self.embedded_chunks
            or self.reused_chunks + self.embedded_chunks != self.total_chunks
            or not isinstance(self.embedding_identity, EmbeddingModelIdentity)
            or not _is_nonempty_string(self.document_version)
            or (
                self.status is IndexSyncStatus.UNCHANGED
                and (
                    self.embedded_chunks != 0
                    or self.inserted_chunks != 0
                    or self.updated_chunks != 0
                    or self.deleted_chunks != 0
                    or self.reused_chunks != self.total_chunks
                )
            )
        ):
            raise EmbeddingVectorStoreConfigurationError(_MODEL_CONTRACT_MESSAGE)


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    chunk_id: str
    content_hash: str
    relative_path: str
    language: str
    chunk_kind: str
    symbol_kind: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    content: str = field(repr=False)
    score: float

    def __post_init__(self) -> None:
        if (
            not _is_lowercase_sha256(self.chunk_id)
            or not _is_lowercase_sha256(self.content_hash)
            or not _is_nonempty_string(self.relative_path)
            or not _is_nonempty_string(self.language)
            or not _is_nonempty_string(self.chunk_kind)
            or not _is_optional_nonempty_string(self.symbol_kind)
            or not _is_optional_nonempty_string(self.qualified_name)
            or not _is_optional_nonempty_string(self.parent_qualified_name)
            or not _is_nonempty_string(self.content)
            or type(self.score) not in (int, float)
            or not math.isfinite(self.score)
            or not 0.0 <= self.score <= 1.0
        ):
            raise InvalidSearchRequest(_SEARCH_RESULT_CONTRACT_MESSAGE)
