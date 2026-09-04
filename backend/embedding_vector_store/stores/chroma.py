"""Private Chroma storage naming and metadata encoding."""

from __future__ import annotations

from hashlib import sha256
import math
from numbers import Real
from pathlib import Path
import re
import struct
from typing import Iterable

import chromadb
from chromadb.config import Settings
from chromadb.errors import ChromaError

from ..exceptions import (
    EmbeddingVectorStoreConfigurationError,
    VectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStoreWriteError,
)
from ..models import EmbeddingModelIdentity, EmbeddingVector, VectorRecord
from .base import CandidateIndex, RepositoryIndexSnapshot, StoredRecord


STORAGE_SCHEMA_VERSION = "tracerag-chroma-schema-v1"

_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_GENERATION_TOKEN = re.compile(r"[a-z0-9]+")
_RECORD_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "repository_namespace",
        "chunk_id",
        "content_hash",
        "relative_path",
        "language",
        "chunk_kind",
        "embedding_provider",
        "embedding_model",
        "embedding_dimensions",
        "embedding_compatibility_version",
        "document_version",
    }
)
_RECORD_OPTIONAL_KEYS = frozenset(
    {"symbol_kind", "qualified_name", "parent_qualified_name"}
)
_CONTROL_KEYS = frozenset(
    {
        "schema_version",
        "repository_namespace",
        "embedding_provider",
        "embedding_model",
        "embedding_dimensions",
        "embedding_compatibility_version",
        "document_version",
        "expected_chunk_count",
    }
)
_CORRUPTION_MESSAGE = "Vector store data is incompatible or corrupt."
_CONFIGURATION_MESSAGE = "Vector store configuration is invalid."
_WRITE_MESSAGE = "Vector store record is invalid."
_MANIFEST_PAGE_SIZE = 100


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_lowercase_sha256(value: object) -> bool:
    return isinstance(value, str) and _LOWERCASE_SHA256.fullmatch(value) is not None


def _is_positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


def _is_nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _raise_corruption() -> None:
    raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE)


def _identity_from_metadata(metadata: dict[str, object]) -> EmbeddingModelIdentity:
    values = (
        metadata.get("embedding_provider"),
        metadata.get("embedding_model"),
        metadata.get("embedding_compatibility_version"),
    )
    if not all(_is_nonempty_string(value) for value in values) or not _is_positive_integer(
        metadata.get("embedding_dimensions")
    ):
        _raise_corruption()
    try:
        return EmbeddingModelIdentity(
            provider=metadata["embedding_provider"],  # type: ignore[arg-type]
            model=metadata["embedding_model"],  # type: ignore[arg-type]
            dimensions=metadata["embedding_dimensions"],  # type: ignore[arg-type]
            compatibility_version=metadata["embedding_compatibility_version"],  # type: ignore[arg-type]
        )
    except EmbeddingVectorStoreConfigurationError as error:
        raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error


def _metadata_for_identity(identity: EmbeddingModelIdentity) -> dict[str, str | int]:
    return {
        "embedding_provider": identity.provider,
        "embedding_model": identity.model,
        "embedding_dimensions": identity.dimensions,
        "embedding_compatibility_version": identity.compatibility_version,
    }


def _validated_embedding_values(
    embedding: object, identity: EmbeddingModelIdentity
) -> tuple[float, ...]:
    if isinstance(embedding, (str, bytes)) or not isinstance(embedding, Iterable):
        _raise_corruption()
    try:
        values = tuple(embedding)
    except (TypeError, ValueError) as error:
        raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error
    if len(values) != identity.dimensions:
        _raise_corruption()
    normalized_values: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            _raise_corruption()
        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError) as error:
            raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error
        if not math.isfinite(normalized):
            _raise_corruption()
        normalized_values.append(normalized)
    return tuple(normalized_values)


def _project_embedding_values(
    embedding: EmbeddingVector, identity: EmbeddingModelIdentity
) -> EmbeddingVector:
    try:
        values = _validated_embedding_values(embedding.values, identity)
        projected = tuple(
            struct.unpack("!f", struct.pack("!f", value))[0] for value in values
        )
    except (OverflowError, struct.error, VectorStoreCorruptionError) as error:
        raise VectorStoreWriteError(_WRITE_MESSAGE) from error
    if not all(math.isfinite(value) for value in projected):
        raise VectorStoreWriteError(_WRITE_MESSAGE)
    return EmbeddingVector(projected)


class ChromaVectorStore:
    """Own Chroma-specific persistence details behind the vector-store boundary."""

    def __init__(self, persistence_root: str | Path) -> None:
        if not isinstance(persistence_root, (str, Path)) or not str(persistence_root):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        try:
            root = Path(persistence_root).expanduser().resolve()
            if root.exists() and not root.is_dir():
                raise OSError("persistence root is not a directory")
            root.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(root),
                settings=Settings(anonymized_telemetry=False),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE) from error
        self._persistence_root = root
        self._client = client

    @staticmethod
    def _namespace_digest(repository_namespace: str) -> str:
        return sha256(repository_namespace.encode("utf-8")).hexdigest()

    def _collection_identifier(
        self, repository_namespace: str, generation_token: str
    ) -> str:
        if not _is_nonempty_string(repository_namespace):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        if (
            not isinstance(generation_token, str)
            or _GENERATION_TOKEN.fullmatch(generation_token) is None
        ):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        identifier = (
            f"tr5-{self._namespace_digest(repository_namespace)}-{generation_token}"
        )
        if len(identifier) > 512:
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        return identifier

    def _encode_record(self, record: StoredRecord) -> dict[str, object]:
        if not isinstance(record, StoredRecord):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        metadata: dict[str, str | int] = {
            "schema_version": record.schema_version,
            "repository_namespace": record.repository_namespace,
            "chunk_id": record.chunk_id,
            "content_hash": record.content_hash,
            "relative_path": record.relative_path,
            "language": record.language,
            "chunk_kind": record.chunk_kind,
            "document_version": record.document_version,
            **_metadata_for_identity(record.embedding_identity),
        }
        for key in _RECORD_OPTIONAL_KEYS:
            value = getattr(record, key)
            if value is not None:
                metadata[key] = value
        encoded = {
            "identifier": record.chunk_id,
            "embedding": record.embedding.values,
            "document": record.content,
            "metadata": metadata,
        }
        try:
            self._decode_record(record.repository_namespace, **encoded)
        except VectorStoreCorruptionError as error:
            raise VectorStoreWriteError(_WRITE_MESSAGE) from error
        return encoded

    def _decode_record(
        self,
        repository_namespace: str,
        *,
        identifier: object,
        embedding: object,
        document: object,
        metadata: object,
    ) -> StoredRecord:
        if not _is_nonempty_string(repository_namespace) or type(metadata) is not dict:
            _raise_corruption()
        keys = frozenset(metadata)
        if not _RECORD_REQUIRED_KEYS.issubset(keys) or not keys.issubset(
            _RECORD_REQUIRED_KEYS | _RECORD_OPTIONAL_KEYS
        ):
            _raise_corruption()
        if (
            metadata.get("schema_version") != STORAGE_SCHEMA_VERSION
            or metadata.get("repository_namespace") != repository_namespace
            or not _is_lowercase_sha256(identifier)
            or metadata.get("chunk_id") != identifier
            or not _is_lowercase_sha256(metadata.get("content_hash"))
            or not _is_nonempty_string(metadata.get("relative_path"))
            or not _is_nonempty_string(metadata.get("language"))
            or not _is_nonempty_string(metadata.get("chunk_kind"))
            or not _is_nonempty_string(metadata.get("document_version"))
            or not _is_nonempty_string(document)
        ):
            _raise_corruption()
        for key in _RECORD_OPTIONAL_KEYS:
            if key in metadata and not _is_nonempty_string(metadata[key]):
                _raise_corruption()
        identity = _identity_from_metadata(metadata)
        normalized_values = _validated_embedding_values(embedding, identity)
        try:
            return StoredRecord(
                repository_namespace=repository_namespace,
                chunk_id=identifier,
                content_hash=metadata["content_hash"],  # type: ignore[arg-type]
                relative_path=metadata["relative_path"],  # type: ignore[arg-type]
                language=metadata["language"],  # type: ignore[arg-type]
                chunk_kind=metadata["chunk_kind"],  # type: ignore[arg-type]
                symbol_kind=metadata.get("symbol_kind"),  # type: ignore[arg-type]
                qualified_name=metadata.get("qualified_name"),  # type: ignore[arg-type]
                parent_qualified_name=metadata.get("parent_qualified_name"),  # type: ignore[arg-type]
                content=document,
                embedding=EmbeddingVector(tuple(normalized_values)),
                embedding_identity=identity,
                document_version=metadata["document_version"],  # type: ignore[arg-type]
                schema_version=STORAGE_SCHEMA_VERSION,
            )
        except (EmbeddingVectorStoreConfigurationError, ValueError) as error:
            raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error

    def _encode_control_metadata(
        self, handle: RepositoryIndexSnapshot | CandidateIndex
    ) -> dict[str, str | int]:
        if not isinstance(handle, (RepositoryIndexSnapshot, CandidateIndex)):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        metadata = {
            "schema_version": handle.schema_version,
            "repository_namespace": handle.repository_namespace,
            "document_version": handle.document_version,
            "expected_chunk_count": handle.expected_chunk_count,
            **_metadata_for_identity(handle.identity),
        }
        try:
            self._decode_snapshot_metadata(handle.repository_namespace, metadata, object())
        except VectorStoreCorruptionError as error:
            raise VectorStoreWriteError(_WRITE_MESSAGE) from error
        return metadata

    def _decode_snapshot_metadata(
        self, repository_namespace: str, metadata: object, token: object
    ) -> RepositoryIndexSnapshot:
        if (
            not _is_nonempty_string(repository_namespace)
            or type(metadata) is not dict
            or frozenset(metadata) != _CONTROL_KEYS
            or metadata.get("schema_version") != STORAGE_SCHEMA_VERSION
            or metadata.get("repository_namespace") != repository_namespace
            or not _is_nonempty_string(metadata.get("document_version"))
            or not _is_nonnegative_integer(metadata.get("expected_chunk_count"))
        ):
            _raise_corruption()
        identity = _identity_from_metadata(metadata)
        try:
            return RepositoryIndexSnapshot(
                repository_namespace=repository_namespace,
                identity=identity,
                document_version=metadata["document_version"],  # type: ignore[arg-type]
                schema_version=STORAGE_SCHEMA_VERSION,
                expected_chunk_count=metadata["expected_chunk_count"],  # type: ignore[arg-type]
                _token=token,
            )
        except ValueError as error:
            raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error

    def _candidate_collection_name(self, candidate: CandidateIndex) -> str:
        if not isinstance(candidate, CandidateIndex) or not isinstance(
            candidate._token, str
        ):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        try:
            self._encode_control_metadata(candidate)
            return self._collection_identifier(
                candidate.repository_namespace, candidate._token
            )
        except (VectorStoreConfigurationError, VectorStoreWriteError) as error:
            raise VectorStoreWriteError(_WRITE_MESSAGE) from error

    def _create_candidate_collection(self, candidate: CandidateIndex) -> None:
        """Create one isolated, externally embedded physical candidate."""
        collection_name = self._candidate_collection_name(candidate)
        metadata = self._encode_control_metadata(candidate)
        try:
            self._client.create_collection(
                name=collection_name,
                metadata=metadata,
                embedding_function=None,
                configuration={"hnsw": {"space": "cosine"}},
            )
        except (ChromaError, RuntimeError, TypeError, ValueError) as error:
            raise VectorStoreWriteError(_WRITE_MESSAGE) from error

    def _candidate_collection(self, candidate: CandidateIndex):
        """Open a candidate only after its complete control metadata validates."""
        collection_name = self._candidate_collection_name(candidate)
        try:
            collection = self._client.get_collection(
                name=collection_name, embedding_function=None
            )
        except (ChromaError, RuntimeError, TypeError, ValueError) as error:
            raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error
        decoded = self._decode_snapshot_metadata(
            candidate.repository_namespace, collection.metadata, candidate._token
        )
        if (
            decoded.identity != candidate.identity
            or decoded.document_version != candidate.document_version
            or decoded.schema_version != candidate.schema_version
            or decoded.expected_chunk_count != candidate.expected_chunk_count
        ):
            _raise_corruption()
        return collection

    def _write_embedded_candidate(
        self, candidate: CandidateIndex, records: tuple[VectorRecord, ...]
    ) -> None:
        """Persist one complete candidate batch using caller-supplied embeddings."""
        if (
            not isinstance(candidate, CandidateIndex)
            or type(records) is not tuple
            or len(records) != candidate.expected_chunk_count
            or any(not isinstance(record, VectorRecord) for record in records)
        ):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        if len({record.chunk_id for record in records}) != len(records):
            raise VectorStoreWriteError(_WRITE_MESSAGE)

        encoded_records: list[dict[str, object]] = []
        for record in records:
            try:
                stored = StoredRecord(
                    repository_namespace=candidate.repository_namespace,
                    chunk_id=record.chunk_id,
                    content_hash=record.content_hash,
                    relative_path=record.relative_path,
                    language=record.language,
                    chunk_kind=record.chunk_kind,
                    symbol_kind=record.symbol_kind,
                    qualified_name=record.qualified_name,
                    parent_qualified_name=record.parent_qualified_name,
                    content=record.content,
                    embedding=_project_embedding_values(
                        record.embedding, candidate.identity
                    ),
                    embedding_identity=candidate.identity,
                    document_version=candidate.document_version,
                    schema_version=candidate.schema_version,
                )
                encoded_records.append(self._encode_record(stored))
            except (ValueError, VectorStoreCorruptionError) as error:
                raise VectorStoreWriteError(_WRITE_MESSAGE) from error

        collection = self._candidate_collection(candidate)
        try:
            if collection.count() != 0:
                raise ValueError("candidate already contains records")
            collection.add(
                ids=[record["identifier"] for record in encoded_records],
                embeddings=[list(record["embedding"]) for record in encoded_records],
                documents=[record["document"] for record in encoded_records],
                metadatas=[record["metadata"] for record in encoded_records],
            )
            if collection.count() != candidate.expected_chunk_count:
                raise ValueError("candidate record count is incomplete")
        except (ChromaError, RuntimeError, TypeError, ValueError) as error:
            raise VectorStoreWriteError(_WRITE_MESSAGE) from error

    def _read_candidate_manifest(
        self, candidate: CandidateIndex
    ) -> tuple[StoredRecord, ...]:
        """Return every persisted candidate record after exact evidence validation."""
        collection = self._candidate_collection(candidate)
        try:
            count = collection.count()
        except (ChromaError, RuntimeError, TypeError, ValueError) as error:
            raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error
        if type(count) is not int or count != candidate.expected_chunk_count:
            _raise_corruption()

        records: list[StoredRecord] = []
        identifiers: set[str] = set()
        for offset in range(0, count, _MANIFEST_PAGE_SIZE):
            limit = min(_MANIFEST_PAGE_SIZE, count - offset)
            try:
                page = collection.get(
                    limit=limit,
                    offset=offset,
                    include=["documents", "embeddings", "metadatas"],
                )
                page_ids = page["ids"]
                documents = page["documents"]
                embeddings = page["embeddings"]
                metadatas = page["metadatas"]
            except (ChromaError, KeyError, RuntimeError, TypeError, ValueError) as error:
                raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error
            if isinstance(embeddings, (str, bytes)) or not isinstance(
                embeddings, Iterable
            ):
                _raise_corruption()
            try:
                embedding_rows = tuple(embeddings)
            except (TypeError, ValueError) as error:
                raise VectorStoreCorruptionError(_CORRUPTION_MESSAGE) from error
            if (
                not isinstance(page_ids, list)
                or not isinstance(documents, list)
                or not isinstance(metadatas, list)
                or len(page_ids) != limit
                or len(documents) != limit
                or len(embedding_rows) != limit
                or len(metadatas) != limit
            ):
                _raise_corruption()
            for identifier, document, embedding, metadata in zip(
                page_ids, documents, embedding_rows, metadatas, strict=True
            ):
                if not isinstance(identifier, str) or identifier in identifiers:
                    _raise_corruption()
                identifiers.add(identifier)
                record = self._decode_record(
                    candidate.repository_namespace,
                    identifier=identifier,
                    embedding=embedding,
                    document=document,
                    metadata=metadata,
                )
                if (
                    record.embedding_identity != candidate.identity
                    or record.document_version != candidate.document_version
                    or record.schema_version != candidate.schema_version
                ):
                    _raise_corruption()
                records.append(record)
        if len(records) != count or len(identifiers) != count:
            _raise_corruption()
        return tuple(sorted(records, key=lambda record: record.chunk_id))


__all__ = ("ChromaVectorStore",)
