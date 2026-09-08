"""Internal values and lifecycle protocol for vector-store adapters."""

from dataclasses import dataclass, field
from contextlib import AbstractContextManager
import math
import re
from typing import Protocol, runtime_checkable

from ..models import EmbeddingModelIdentity, EmbeddingVector, VectorRecord


_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_optional_nonempty_string(value: object) -> bool:
    return value is None or _is_nonempty_string(value)


def _is_lowercase_sha256(value: object) -> bool:
    return isinstance(value, str) and _LOWERCASE_SHA256.fullmatch(value) is not None


def _is_nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _validate_handle(
    repository_namespace: object,
    identity: object,
    document_version: object,
    schema_version: object,
    expected_chunk_count: object,
    token: object,
) -> None:
    if (
        not _is_nonempty_string(repository_namespace)
        or not isinstance(identity, EmbeddingModelIdentity)
        or not _is_nonempty_string(document_version)
        or not _is_nonempty_string(schema_version)
        or not _is_nonnegative_integer(expected_chunk_count)
        or token is None
    ):
        raise ValueError("Vector store internal contract is invalid.")


@dataclass(frozen=True, slots=True)
class RepositoryIndexSnapshot:
    """An adapter-owned immutable view of one published repository index."""

    repository_namespace: str
    identity: EmbeddingModelIdentity
    document_version: str
    schema_version: str
    expected_chunk_count: int
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_handle(
            self.repository_namespace,
            self.identity,
            self.document_version,
            self.schema_version,
            self.expected_chunk_count,
            self._token,
        )


@dataclass(frozen=True, slots=True)
class CandidateIndex:
    """An adapter-owned isolated repository-index candidate."""

    repository_namespace: str
    identity: EmbeddingModelIdentity
    document_version: str
    schema_version: str
    expected_chunk_count: int
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_handle(
            self.repository_namespace,
            self.identity,
            self.document_version,
            self.schema_version,
            self.expected_chunk_count,
            self._token,
        )


@dataclass(frozen=True, slots=True)
class RepositoryIndexState:
    """Whether a repository has an active snapshot."""

    indexed: bool
    snapshot: RepositoryIndexSnapshot | None

    def __post_init__(self) -> None:
        if (
            type(self.indexed) is not bool
            or (self.indexed and not isinstance(self.snapshot, RepositoryIndexSnapshot))
            or (not self.indexed and self.snapshot is not None)
        ):
            raise ValueError("Vector store internal contract is invalid.")


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """Complete stored vector evidence used for future synchronization."""

    repository_namespace: str
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
    embedding_identity: EmbeddingModelIdentity
    document_version: str
    schema_version: str

    def __post_init__(self) -> None:
        if (
            not _is_nonempty_string(self.repository_namespace)
            or not _is_lowercase_sha256(self.chunk_id)
            or not _is_lowercase_sha256(self.content_hash)
            or not _is_nonempty_string(self.relative_path)
            or not _is_nonempty_string(self.language)
            or not _is_nonempty_string(self.chunk_kind)
            or not _is_optional_nonempty_string(self.symbol_kind)
            or not _is_optional_nonempty_string(self.qualified_name)
            or not _is_optional_nonempty_string(self.parent_qualified_name)
            or not _is_nonempty_string(self.content)
            or not isinstance(self.embedding, EmbeddingVector)
            or not isinstance(self.embedding_identity, EmbeddingModelIdentity)
            or not _is_nonempty_string(self.document_version)
            or not _is_nonempty_string(self.schema_version)
        ):
            raise ValueError("Vector store internal contract is invalid.")


@dataclass(frozen=True, slots=True)
class StoreSearchResult:
    """Provider-neutral normalized search evidence from one repository."""

    repository_namespace: str
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
            not _is_nonempty_string(self.repository_namespace)
            or not _is_lowercase_sha256(self.chunk_id)
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
            raise ValueError("Vector store internal contract is invalid.")


@runtime_checkable
class VectorStore(Protocol):
    """Manage immutable repository index snapshots through an adapter."""

    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState: ...

    def acquire_active(
        self, repository_namespace: str
    ) -> AbstractContextManager[RepositoryIndexState]:
        """Pin the active generation before validation until the context exits."""
        ...

    def read_manifest(
        self, snapshot: RepositoryIndexSnapshot
    ) -> tuple[StoredRecord, ...]: ...

    def begin_candidate(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        expected_chunk_count: int,
    ) -> CandidateIndex: ...

    def add_reused(
        self, candidate: CandidateIndex, records: tuple[StoredRecord, ...]
    ) -> None: ...

    def add_embedded(
        self, candidate: CandidateIndex, records: tuple[VectorRecord, ...]
    ) -> None: ...

    def validate_candidate(self, candidate: CandidateIndex) -> None: ...

    def publish(self, candidate: CandidateIndex) -> RepositoryIndexSnapshot: ...

    def abort(self, candidate: CandidateIndex) -> None: ...

    def search(
        self,
        snapshot: RepositoryIndexSnapshot,
        query: EmbeddingVector,
        top_k: int,
    ) -> tuple[StoreSearchResult, ...]: ...

    def delete_repository_index(self, repository_namespace: str) -> None: ...
