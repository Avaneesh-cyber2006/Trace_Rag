"""Private Chroma storage naming and metadata encoding."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import logging
import math
from numbers import Real
import os
from pathlib import Path
import re
import secrets
import struct
from threading import Lock
from typing import Iterable, Iterator

import chromadb
from chromadb.api.client import SharedSystemClient
from chromadb.config import Settings
from chromadb.errors import ChromaError, NotFoundError

from ..exceptions import (
    EmbeddingVectorStoreConfigurationError,
    VectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStoreError,
    VectorStorePublicationError,
    VectorStoreReadError,
    VectorStoreWriteError,
)
from ..models import EmbeddingModelIdentity, EmbeddingVector, VectorRecord
from .base import (
    CandidateIndex,
    RepositoryIndexSnapshot,
    RepositoryIndexState,
    StoreSearchResult,
    StoredRecord,
)


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
_PUBLICATION_MESSAGE = "Vector store publication failed."
_READ_MESSAGE = "Vector store data could not be read."
_MANIFEST_PAGE_SIZE = 100
_COSINE_DISTANCE_ENDPOINT_TOLERANCE = 1e-6
_POINTER_KEYS = frozenset({"payload", "sha256"})
_POINTER_PAYLOAD_KEYS = frozenset(
    {"schema_version", "repository_namespace", "active_collection"}
)
_LOGGER = logging.getLogger(__name__)


class _PersistenceConcurrencyState:
    """Process-lifetime ownership and reader state for one local root.

    Chroma caches its persistent clients for the process lifetime, so ownership
    must outlive individual adapter objects too. The OS releases the file lock
    on normal exit or termination. Never unlink its path: that would allow a
    second process to lock a different inode for the same persistence root.
    """

    def __init__(self, root: Path) -> None:
        self.lock = Lock()
        self.readers: dict[str, int] = {}
        self.pending_cleanup: set[str] = set()
        self.cleanup_reservations: set[str] = set()
        self.owner_process_id = os.getpid()
        self.ownership_file = (root / ".tr5-owner.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                # Byte-range locks also cover bytes beyond the end of a file.
                self.ownership_file.seek(0)
                msvcrt.locking(self.ownership_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    self.ownership_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            self.client = chromadb.PersistentClient(
                path=str(root), settings=Settings(anonymized_telemetry=False)
            )
        except BaseException:
            self.ownership_file.close()
            raise


_PERSISTENCE_STATES_LOCK = Lock()
_PERSISTENCE_STATES: dict[str, _PersistenceConcurrencyState] = {}


def _persistence_concurrency_state(root: Path) -> _PersistenceConcurrencyState:
    key = os.path.normcase(str(root))
    with _PERSISTENCE_STATES_LOCK:
        state = _PERSISTENCE_STATES.get(key)
        if state is None:
            state = _PersistenceConcurrencyState(root)
            _PERSISTENCE_STATES[key] = state
        elif state.owner_process_id != os.getpid():
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        return state


def _release_inherited_ownership() -> None:
    """Discard process-bound state without disturbing the parent's lease."""
    global _PERSISTENCE_STATES, _PERSISTENCE_STATES_LOCK
    inherited_states = tuple(_PERSISTENCE_STATES.values())
    _PERSISTENCE_STATES = {}
    _PERSISTENCE_STATES_LOCK = Lock()
    for state in inherited_states:
        # Close only; explicit flock unlock would release the parent's lock too.
        try:
            state.ownership_file.close()
        except OSError:
            pass
    # Chroma's process-global systems and their refcount lock are equally
    # unsafe to reuse after a multithreaded process forks. Do not close/stop
    # them here: inherited teardown could run locked code or mutate storage.
    SharedSystemClient.clear_system_cache()
    SharedSystemClient._refcount_lock = Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_release_inherited_ownership)


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
    mapped_failure = None
    try:
        return EmbeddingModelIdentity(
            provider=metadata["embedding_provider"],  # type: ignore[arg-type]
            model=metadata["embedding_model"],  # type: ignore[arg-type]
            dimensions=metadata["embedding_dimensions"],  # type: ignore[arg-type]
            compatibility_version=metadata["embedding_compatibility_version"],  # type: ignore[arg-type]
        )
    except EmbeddingVectorStoreConfigurationError:
        mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
    if mapped_failure is not None:
        raise mapped_failure


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
    mapped_failure = None
    try:
        values = tuple(embedding)
    except (TypeError, ValueError):
        mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
    if mapped_failure is not None:
        raise mapped_failure
    if len(values) != identity.dimensions:
        _raise_corruption()
    normalized_values: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            _raise_corruption()
        mapped_failure = None
        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        if not math.isfinite(normalized):
            _raise_corruption()
        normalized_values.append(normalized)
    return tuple(normalized_values)


def _project_embedding_values(
    embedding: EmbeddingVector, identity: EmbeddingModelIdentity
) -> EmbeddingVector:
    mapped_failure = None
    try:
        values = _validated_embedding_values(embedding.values, identity)
        projected = tuple(
            struct.unpack("!f", struct.pack("!f", value))[0] for value in values
        )
    except (OverflowError, struct.error, VectorStoreCorruptionError):
        mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
    if mapped_failure is not None:
        raise mapped_failure
    if not all(math.isfinite(value) for value in projected):
        raise VectorStoreWriteError(_WRITE_MESSAGE)
    return EmbeddingVector(projected)


def _score_from_cosine_distance(distance: object) -> float:
    if isinstance(distance, bool) or not isinstance(distance, Real):
        _raise_corruption()
    mapped_failure = None
    try:
        normalized = float(distance)
    except (OverflowError, TypeError, ValueError):
        mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
    if mapped_failure is not None:
        raise mapped_failure
    if not math.isfinite(normalized):
        _raise_corruption()
    if abs(normalized) <= _COSINE_DISTANCE_ENDPOINT_TOLERANCE:
        normalized = 0.0
    elif abs(normalized - 2.0) <= _COSINE_DISTANCE_ENDPOINT_TOLERANCE:
        normalized = 2.0
    elif not 0.0 <= normalized <= 2.0:
        _raise_corruption()
    return 1.0 - normalized / 2.0


class ChromaVectorStore:
    """Own Chroma-specific persistence details behind the vector-store boundary."""

    def __init__(self, persistence_root: str | Path) -> None:
        if not isinstance(persistence_root, (str, Path)) or not str(persistence_root):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        mapped_failure = None
        try:
            root = Path(persistence_root).expanduser().resolve()
            if root.exists() and not root.is_dir():
                raise OSError("persistence root is not a directory")
            root.mkdir(parents=True, exist_ok=True)
            concurrency_state = _persistence_concurrency_state(root)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        self._persistence_root = root
        self._client = concurrency_state.client
        self._owner_process_id = concurrency_state.owner_process_id
        self._concurrency_state = concurrency_state

    def _assert_owner_process(self) -> None:
        if os.getpid() != self._owner_process_id:
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)

    def _writer_guard_key(self, repository_namespace: str) -> str:
        """Identify one supported process/storage deployment for core guarding."""
        self._assert_owner_process()
        if not _is_nonempty_string(repository_namespace):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        return os.path.normcase(str(self._persistence_root))

    def _acquire_snapshot_reader(self, collection_name: str) -> bool:
        with self._concurrency_state.lock:
            if collection_name in self._concurrency_state.cleanup_reservations:
                return False
            self._concurrency_state.readers[collection_name] = (
                self._concurrency_state.readers.get(collection_name, 0) + 1
            )
            return True

    def _delete_reserved_collection(self, collection_name: str) -> None:
        """Run a reserved best-effort delete without holding the state lock."""
        try:
            self._client.delete_collection(collection_name)
        except Exception:
            _LOGGER.warning("Vector store cleanup failed.")
        finally:
            with self._concurrency_state.lock:
                self._concurrency_state.cleanup_reservations.discard(collection_name)

    def _release_snapshot_reader(self, collection_name: str) -> None:
        delete_pending = False
        with self._concurrency_state.lock:
            remaining = self._concurrency_state.readers.get(collection_name, 0) - 1
            if remaining > 0:
                self._concurrency_state.readers[collection_name] = remaining
            else:
                self._concurrency_state.readers.pop(collection_name, None)
                if collection_name in self._concurrency_state.pending_cleanup:
                    self._concurrency_state.pending_cleanup.remove(collection_name)
                    if (
                        collection_name
                        not in self._concurrency_state.cleanup_reservations
                    ):
                        self._concurrency_state.cleanup_reservations.add(collection_name)
                        delete_pending = True
        if delete_pending:
            self._delete_reserved_collection(collection_name)

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

    def _active_pointer_path(self, repository_namespace: str) -> Path:
        if not _is_nonempty_string(repository_namespace):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        return self._persistence_root / (
            f".tr5-active-{self._namespace_digest(repository_namespace)}.json"
        )

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def _active_pointer_bytes(
        self, repository_namespace: str, collection_name: str
    ) -> bytes:
        payload = {
            "schema_version": STORAGE_SCHEMA_VERSION,
            "repository_namespace": repository_namespace,
            "active_collection": collection_name,
        }
        envelope = {
            "payload": payload,
            "sha256": sha256(self._canonical_json(payload)).hexdigest(),
        }
        return self._canonical_json(envelope)

    def _read_active_collection_name(self, repository_namespace: str) -> str | None:
        pointer_path = self._active_pointer_path(repository_namespace)
        mapped_failure = None
        try:
            pointer_bytes = pointer_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        mapped_failure = None
        try:
            envelope = json.loads(pointer_bytes.decode("utf-8"))
            if (
                type(envelope) is not dict
                or frozenset(envelope) != _POINTER_KEYS
                or self._canonical_json(envelope) != pointer_bytes
                or not _is_lowercase_sha256(envelope.get("sha256"))
            ):
                _raise_corruption()
            payload = envelope.get("payload")
            if (
                type(payload) is not dict
                or frozenset(payload) != _POINTER_PAYLOAD_KEYS
                or payload.get("schema_version") != STORAGE_SCHEMA_VERSION
                or payload.get("repository_namespace") != repository_namespace
                or not _is_nonempty_string(payload.get("active_collection"))
                or envelope["sha256"]
                != sha256(self._canonical_json(payload)).hexdigest()
            ):
                _raise_corruption()
            return payload["active_collection"]
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def _snapshot_for_collection(
        self, repository_namespace: str, collection_name: str
    ) -> RepositoryIndexSnapshot:
        collection = self._read_collection(repository_namespace, collection_name)
        snapshot = self._decode_snapshot_metadata(
            repository_namespace,
            self._read_collection_metadata(collection),
            collection_name,
        )
        count = self._read_collection_count(collection)
        if type(count) is not int or count != snapshot.expected_chunk_count:
            _raise_corruption()
        self._read_manifest_from_collection(
            repository_namespace,
            snapshot.identity,
            snapshot.document_version,
            snapshot.schema_version,
            snapshot.expected_chunk_count,
            collection,
        )
        return snapshot

    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState:
        """Resolve only the explicitly published snapshot for one repository."""
        self._assert_owner_process()
        collection_name = self._read_active_collection_name(repository_namespace)
        if collection_name is None:
            return RepositoryIndexState(indexed=False, snapshot=None)
        return RepositoryIndexState(
            indexed=True,
            snapshot=self._snapshot_for_collection(repository_namespace, collection_name),
        )

    @contextmanager
    def acquire_active(self, repository_namespace: str) -> Iterator[RepositoryIndexState]:
        """Resolve and pin one active generation for the entire caller operation."""
        self._assert_owner_process()
        # Cleanup reserves deletion under this same lock. Resolve the pointer
        # and register the reader together, before opening/validating Chroma.
        # Publication can replace the pointer meanwhile, but cannot retire this
        # selected generation until the context and any nested searches exit.
        with self._concurrency_state.lock:
            collection_name = self._read_active_collection_name(repository_namespace)
            if collection_name is not None:
                if collection_name in self._concurrency_state.cleanup_reservations:
                    _raise_corruption()
                self._concurrency_state.readers[collection_name] = (
                    self._concurrency_state.readers.get(collection_name, 0) + 1
                )
        try:
            if collection_name is None:
                yield RepositoryIndexState(indexed=False, snapshot=None)
            else:
                yield RepositoryIndexState(
                    indexed=True,
                    snapshot=self._snapshot_for_collection(repository_namespace, collection_name),
                )
        finally:
            if collection_name is not None:
                self._release_snapshot_reader(collection_name)

    def _read_collection(self, repository_namespace: str, collection_name: object):
        """Open one adapter-owned collection, separating outage from corruption."""
        if not self._is_collection_locator(repository_namespace, collection_name):
            _raise_corruption()
        mapped_failure = None
        try:
            return self._client.get_collection(
                name=collection_name, embedding_function=None
            )
        except NotFoundError:
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except (TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    @staticmethod
    def _read_collection_metadata(collection: object) -> object:
        mapped_failure = None
        try:
            return collection.metadata
        except NotFoundError:
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except (TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    @staticmethod
    def _read_collection_count(collection: object) -> object:
        mapped_failure = None
        try:
            return collection.count()
        except NotFoundError:
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except (TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def begin_candidate(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        expected_chunk_count: int,
    ) -> CandidateIndex:
        """Create one private immutable-generation candidate."""
        self._assert_owner_process()
        mapped_failure = None
        try:
            candidate = CandidateIndex(
                repository_namespace=repository_namespace,
                identity=identity,
                document_version=document_version,
                schema_version=STORAGE_SCHEMA_VERSION,
                expected_chunk_count=expected_chunk_count,
                _token=secrets.token_hex(16),
            )
        except ValueError:
            mapped_failure = VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        self._create_candidate_collection(candidate)
        return candidate

    def read_manifest(
        self, snapshot: RepositoryIndexSnapshot
    ) -> tuple[StoredRecord, ...]:
        """Read one explicit snapshot without consulting the active pointer."""
        self._assert_owner_process()
        if not isinstance(snapshot, RepositoryIndexSnapshot):
            raise VectorStoreReadError(_READ_MESSAGE)
        collection = self._read_collection(
            snapshot.repository_namespace, snapshot._token
        )
        decoded = self._decode_snapshot_metadata(
            snapshot.repository_namespace,
            self._read_collection_metadata(collection),
            snapshot._token,
        )
        if (
            decoded.identity != snapshot.identity
            or decoded.document_version != snapshot.document_version
            or decoded.schema_version != snapshot.schema_version
            or decoded.expected_chunk_count != snapshot.expected_chunk_count
        ):
            _raise_corruption()
        return self._read_manifest_from_collection(
            snapshot.repository_namespace,
            snapshot.identity,
            snapshot.document_version,
            snapshot.schema_version,
            snapshot.expected_chunk_count,
            collection,
        )

    def search(
        self,
        snapshot: RepositoryIndexSnapshot,
        query: EmbeddingVector,
        top_k: int,
    ) -> tuple[StoreSearchResult, ...]:
        """Search while retaining the immutable snapshot against publication cleanup."""
        self._assert_owner_process()
        if not isinstance(snapshot, RepositoryIndexSnapshot) or not isinstance(
            snapshot._token, str
        ):
            return self._search_snapshot(snapshot, query, top_k)
        if not self._acquire_snapshot_reader(snapshot._token):
            raise VectorStoreReadError(_READ_MESSAGE)
        try:
            return self._search_snapshot(snapshot, query, top_k)
        finally:
            self._release_snapshot_reader(snapshot._token)

    def _search_snapshot(
        self,
        snapshot: RepositoryIndexSnapshot,
        query: EmbeddingVector,
        top_k: int,
    ) -> tuple[StoreSearchResult, ...]:
        """Search one explicit immutable snapshot using cosine distance."""
        if (
            not isinstance(snapshot, RepositoryIndexSnapshot)
            or not isinstance(query, EmbeddingVector)
            or type(top_k) is not int
            or top_k <= 0
        ):
            raise VectorStoreReadError(_READ_MESSAGE)
        mapped_failure = None
        try:
            query_values = _validated_embedding_values(query.values, snapshot.identity)
        except VectorStoreCorruptionError:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

        collection = self._read_collection(
            snapshot.repository_namespace, snapshot._token
        )
        decoded_snapshot = self._decode_snapshot_metadata(
            snapshot.repository_namespace,
            self._read_collection_metadata(collection),
            snapshot._token,
        )
        if (
            decoded_snapshot.identity != snapshot.identity
            or decoded_snapshot.document_version != snapshot.document_version
            or decoded_snapshot.schema_version != snapshot.schema_version
            or decoded_snapshot.expected_chunk_count != snapshot.expected_chunk_count
        ):
            _raise_corruption()
        count = self._read_collection_count(collection)
        if type(count) is not int or count != snapshot.expected_chunk_count:
            _raise_corruption()
        if count == 0:
            return ()

        n_results = min(top_k, count)
        mapped_failure = None
        try:
            query_result = collection.query(
                query_embeddings=[list(query_values)],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
            identifiers = query_result["ids"]
            documents = query_result["documents"]
            metadatas = query_result["metadatas"]
            distances = query_result["distances"]
        except NotFoundError:
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except (AttributeError, KeyError, TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

        rows: list[object] = []
        for value in (identifiers, documents, metadatas, distances):
            if not isinstance(value, list) or len(value) != 1:
                _raise_corruption()
            row = value[0]
            if not isinstance(row, list):
                _raise_corruption()
            rows.append(row)
        identifier_row, document_row, metadata_row, distance_row = rows
        result_count = len(identifier_row)  # type: ignore[arg-type]
        if (
            result_count > n_results
            or len(document_row) != result_count  # type: ignore[arg-type]
            or len(metadata_row) != result_count  # type: ignore[arg-type]
            or len(distance_row) != result_count  # type: ignore[arg-type]
        ):
            _raise_corruption()

        results: list[StoreSearchResult] = []
        for identifier, document, metadata, distance in zip(
            identifier_row,  # type: ignore[arg-type]
            document_row,  # type: ignore[arg-type]
            metadata_row,  # type: ignore[arg-type]
            distance_row,  # type: ignore[arg-type]
            strict=True,
        ):
            identity = self._validated_record_evidence(
                snapshot.repository_namespace,
                identifier=identifier,
                document=document,
                metadata=metadata,
            )
            if (
                identity != snapshot.identity
                or metadata["document_version"] != snapshot.document_version
                or metadata["schema_version"] != snapshot.schema_version
            ):
                _raise_corruption()
            mapped_failure = None
            try:
                results.append(
                    StoreSearchResult(
                        repository_namespace=snapshot.repository_namespace,
                        chunk_id=identifier,
                        content_hash=metadata["content_hash"],
                        relative_path=metadata["relative_path"],
                        language=metadata["language"],
                        chunk_kind=metadata["chunk_kind"],
                        symbol_kind=metadata.get("symbol_kind"),
                        qualified_name=metadata.get("qualified_name"),
                        parent_qualified_name=metadata.get("parent_qualified_name"),
                        content=document,
                        score=_score_from_cosine_distance(distance),
                    )
                )
            except (EmbeddingVectorStoreConfigurationError, ValueError):
                mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
            if mapped_failure is not None:
                raise mapped_failure
        return tuple(results)

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
        mapped_failure = None
        try:
            self._decode_record(record.repository_namespace, **encoded)
        except VectorStoreCorruptionError:
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
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
        identity = self._validated_record_evidence(
            repository_namespace,
            identifier=identifier,
            document=document,
            metadata=metadata,
        )
        normalized_values = _validated_embedding_values(embedding, identity)
        mapped_failure = None
        try:
            return StoredRecord(
                repository_namespace=repository_namespace,
                chunk_id=identifier,  # type: ignore[arg-type]
                content_hash=metadata["content_hash"],  # type: ignore[index,arg-type]
                relative_path=metadata["relative_path"],  # type: ignore[index,arg-type]
                language=metadata["language"],  # type: ignore[index,arg-type]
                chunk_kind=metadata["chunk_kind"],  # type: ignore[index,arg-type]
                symbol_kind=metadata.get("symbol_kind"),  # type: ignore[union-attr,arg-type]
                qualified_name=metadata.get("qualified_name"),  # type: ignore[union-attr,arg-type]
                parent_qualified_name=metadata.get("parent_qualified_name"),  # type: ignore[union-attr,arg-type]
                content=document,  # type: ignore[arg-type]
                embedding=EmbeddingVector(tuple(normalized_values)),
                embedding_identity=identity,
                document_version=metadata["document_version"],  # type: ignore[index,arg-type]
                schema_version=STORAGE_SCHEMA_VERSION,
            )
        except (EmbeddingVectorStoreConfigurationError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def _validated_record_evidence(
        self,
        repository_namespace: str,
        *,
        identifier: object,
        document: object,
        metadata: object,
    ) -> EmbeddingModelIdentity:
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
        return _identity_from_metadata(metadata)

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
        mapped_failure = None
        try:
            self._decode_snapshot_metadata(handle.repository_namespace, metadata, object())
        except VectorStoreCorruptionError:
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
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
        mapped_failure = None
        try:
            return RepositoryIndexSnapshot(
                repository_namespace=repository_namespace,
                identity=identity,
                document_version=metadata["document_version"],  # type: ignore[arg-type]
                schema_version=STORAGE_SCHEMA_VERSION,
                expected_chunk_count=metadata["expected_chunk_count"],  # type: ignore[arg-type]
                _token=token,
            )
        except ValueError:
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def _candidate_collection_name(self, candidate: CandidateIndex) -> str:
        if not isinstance(candidate, CandidateIndex) or not isinstance(
            candidate._token, str
        ):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        mapped_failure = None
        try:
            self._encode_control_metadata(candidate)
            return self._collection_identifier(
                candidate.repository_namespace, candidate._token
            )
        except (VectorStoreConfigurationError, VectorStoreWriteError):
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def _is_collection_locator(
        self, repository_namespace: str, collection_name: object
    ) -> bool:
        if not isinstance(collection_name, str):
            return False
        prefix = f"tr5-{self._namespace_digest(repository_namespace)}-"
        if not collection_name.startswith(prefix):
            return False
        token = collection_name[len(prefix) :]
        return (
            _GENERATION_TOKEN.fullmatch(token) is not None
            and self._collection_identifier(repository_namespace, token)
            == collection_name
        )

    def _create_candidate_collection(self, candidate: CandidateIndex) -> None:
        """Create one isolated, externally embedded physical candidate."""
        collection_name = self._candidate_collection_name(candidate)
        metadata = self._encode_control_metadata(candidate)
        mapped_failure = None
        try:
            self._client.create_collection(
                name=collection_name,
                metadata=metadata,
                embedding_function=None,
                configuration={"hnsw": {"space": "cosine"}},
            )
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def _candidate_collection(self, candidate: CandidateIndex):
        """Open a candidate only after its complete control metadata validates."""
        collection_name = self._candidate_collection_name(candidate)
        collection = self._read_collection(candidate.repository_namespace, collection_name)
        decoded = self._decode_snapshot_metadata(
            candidate.repository_namespace,
            self._read_collection_metadata(collection),
            candidate._token,
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

        stored_records: list[StoredRecord] = []
        for record in records:
            mapped_failure = None
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
                stored_records.append(stored)
            except (ValueError, VectorStoreCorruptionError):
                mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
            if mapped_failure is not None:
                raise mapped_failure

        collection = self._candidate_collection(candidate)
        mapped_failure = None
        try:
            if self._read_collection_count(collection) != 0:
                raise ValueError("candidate already contains records")
        except (ChromaError, RuntimeError, TypeError, ValueError):
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        self._append_stored_candidate(candidate, tuple(stored_records))
        if self._read_collection_count(self._candidate_collection(candidate)) != candidate.expected_chunk_count:
            raise VectorStoreWriteError(_WRITE_MESSAGE)

    def _append_stored_candidate(
        self, candidate: CandidateIndex, records: tuple[StoredRecord, ...]
    ) -> None:
        if (
            not isinstance(candidate, CandidateIndex)
            or type(records) is not tuple
            or any(not isinstance(record, StoredRecord) for record in records)
            or len({record.chunk_id for record in records}) != len(records)
        ):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        encoded_records: list[dict[str, object]] = []
        for record in records:
            if (
                record.repository_namespace != candidate.repository_namespace
                or record.embedding_identity != candidate.identity
                or record.document_version != candidate.document_version
                or record.schema_version != candidate.schema_version
            ):
                raise VectorStoreWriteError(_WRITE_MESSAGE)
            encoded_records.append(self._encode_record(record))
        collection = self._candidate_collection(candidate)
        mapped_failure = None
        try:
            existing_ids = collection.get(include=[])["ids"]
            if (
                not isinstance(existing_ids, list)
                or len(existing_ids) + len(records) > candidate.expected_chunk_count
                or not set(existing_ids).isdisjoint(record.chunk_id for record in records)
            ):
                raise ValueError("candidate record set is invalid")
            if not records:
                return
            collection.add(
                ids=[record["identifier"] for record in encoded_records],
                embeddings=[list(record["embedding"]) for record in encoded_records],
                documents=[record["document"] for record in encoded_records],
                metadatas=[record["metadata"] for record in encoded_records],
            )
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

    def add_reused(
        self, candidate: CandidateIndex, records: tuple[StoredRecord, ...]
    ) -> None:
        """Copy store-authoritative records into an isolated candidate."""
        self._assert_owner_process()
        self._append_stored_candidate(candidate, records)

    def add_embedded(
        self, candidate: CandidateIndex, records: tuple[VectorRecord, ...]
    ) -> None:
        """Append validated external-vector records to an isolated candidate."""
        self._assert_owner_process()
        if (
            not isinstance(candidate, CandidateIndex)
            or type(records) is not tuple
            or any(not isinstance(record, VectorRecord) for record in records)
            or len({record.chunk_id for record in records}) != len(records)
        ):
            raise VectorStoreWriteError(_WRITE_MESSAGE)
        stored_records: list[StoredRecord] = []
        mapped_failure = None
        try:
            for record in records:
                stored_records.append(
                    StoredRecord(
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
                )
        except (ValueError, VectorStoreCorruptionError):
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        self._append_stored_candidate(candidate, tuple(stored_records))

    def _read_manifest_from_collection(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        schema_version: str,
        expected_chunk_count: int,
        collection: object,
    ) -> tuple[StoredRecord, ...]:
        """Read every record after exact evidence and completeness validation."""
        count = self._read_collection_count(collection)
        if type(count) is not int or count != expected_chunk_count:
            _raise_corruption()

        records: list[StoredRecord] = []
        identifiers: set[str] = set()
        for offset in range(0, count, _MANIFEST_PAGE_SIZE):
            limit = min(_MANIFEST_PAGE_SIZE, count - offset)
            mapped_failure = None
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
            except NotFoundError:
                mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
            except (KeyError, TypeError, ValueError):
                mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
            except VectorStoreError:
                raise
            except Exception:
                mapped_failure = VectorStoreReadError(_READ_MESSAGE)
            if mapped_failure is not None:
                raise mapped_failure
            if isinstance(embeddings, (str, bytes)) or not isinstance(
                embeddings, Iterable
            ):
                _raise_corruption()
            mapped_failure = None
            try:
                embedding_rows = tuple(embeddings)
            except (TypeError, ValueError):
                mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
            if mapped_failure is not None:
                raise mapped_failure
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
                    repository_namespace,
                    identifier=identifier,
                    embedding=embedding,
                    document=document,
                    metadata=metadata,
                )
                if (
                    record.embedding_identity != identity
                    or record.document_version != document_version
                    or record.schema_version != schema_version
                ):
                    _raise_corruption()
                records.append(record)
        if len(records) != count or len(identifiers) != count:
            _raise_corruption()
        return tuple(sorted(records, key=lambda record: record.chunk_id))

    def _read_candidate_manifest(
        self, candidate: CandidateIndex
    ) -> tuple[StoredRecord, ...]:
        """Return every persisted candidate record after exact evidence validation."""
        collection = self._candidate_collection(candidate)
        return self._read_manifest_from_collection(
            candidate.repository_namespace,
            candidate.identity,
            candidate.document_version,
            candidate.schema_version,
            candidate.expected_chunk_count,
            collection,
        )

    def validate_candidate(self, candidate: CandidateIndex) -> None:
        """Validate the complete logical candidate before publication."""
        self._assert_owner_process()
        self._read_candidate_manifest(candidate)

    def _resolve_publication_outcome(
        self, candidate: CandidateIndex, old_collection: str | None
    ) -> RepositoryIndexSnapshot:
        """Accept only an explicitly committed, complete candidate pointer."""
        mapped_failure = None
        try:
            state = self.inspect_active(candidate.repository_namespace)
        except VectorStoreCorruptionError:
            raise
        except VectorStoreReadError:
            mapped_failure = VectorStorePublicationError(_PUBLICATION_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure
        if state.snapshot is not None and state.snapshot._token == self._candidate_collection_name(
            candidate
        ):
            return state.snapshot
        current_collection = state.snapshot._token if state.snapshot is not None else None
        if current_collection == old_collection:
            raise VectorStorePublicationError(_PUBLICATION_MESSAGE)
        _raise_corruption()

    def _cleanup_obsolete_collection(
        self, old_collection: str | None, new_collection: str
    ) -> None:
        """Best-effort retirement after durable pointer publication succeeds."""
        if old_collection is None or old_collection == new_collection:
            return
        delete_reserved = False
        with self._concurrency_state.lock:
            if self._concurrency_state.readers.get(old_collection, 0) > 0:
                self._concurrency_state.pending_cleanup.add(old_collection)
                return
            if old_collection not in self._concurrency_state.cleanup_reservations:
                self._concurrency_state.pending_cleanup.discard(old_collection)
                self._concurrency_state.cleanup_reservations.add(old_collection)
                delete_reserved = True
        if delete_reserved:
            self._delete_reserved_collection(old_collection)

    def _repository_collection_names(
        self, repository_namespace: str
    ) -> tuple[str, ...]:
        """Discover and fully validate adapter collections for one exact namespace."""
        prefix = f"tr5-{self._namespace_digest(repository_namespace)}-"
        mapped_failure = None
        try:
            listed = self._client.list_collections()
        except (TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

        names: list[str] = []
        mapped_failure = None
        try:
            for collection in listed:
                collection_name = collection.name
                if isinstance(collection_name, str) and collection_name.startswith(prefix):
                    names.append(collection_name)
        except (AttributeError, TypeError, ValueError):
            mapped_failure = VectorStoreCorruptionError(_CORRUPTION_MESSAGE)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreReadError(_READ_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure

        for collection_name in sorted(names):
            self._snapshot_for_collection(repository_namespace, collection_name)
        return tuple(sorted(names))

    def delete_repository_index(self, repository_namespace: str) -> None:
        """Delete only the fully validated index for one exact namespace."""
        self._assert_owner_process()
        if not _is_nonempty_string(repository_namespace):
            raise VectorStoreConfigurationError(_CONFIGURATION_MESSAGE)

        active_collection = self._read_active_collection_name(repository_namespace)
        collection_names = self._repository_collection_names(repository_namespace)
        if active_collection is not None and active_collection not in collection_names:
            _raise_corruption()

        pointer_path = self._active_pointer_path(repository_namespace)
        if active_collection is not None:
            mapped_failure = None
            try:
                pointer_path.unlink()
            except OSError:
                mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
            if mapped_failure is not None:
                raise mapped_failure

        failed = False
        for collection_name in collection_names:
            try:
                self._client.delete_collection(collection_name)
            except VectorStoreError:
                raise
            except Exception:
                failed = True
        if failed:
            raise VectorStoreWriteError(_WRITE_MESSAGE)

    def publish(self, candidate: CandidateIndex) -> RepositoryIndexSnapshot:
        """Publish a complete candidate through one durable pointer replacement."""
        self._assert_owner_process()
        previous_state = self.inspect_active(candidate.repository_namespace)
        old_collection = (
            previous_state.snapshot._token
            if previous_state.snapshot is not None
            else None
        )
        self.validate_candidate(candidate)
        collection_name = self._candidate_collection_name(candidate)
        pointer_path = self._active_pointer_path(candidate.repository_namespace)
        temporary_path = pointer_path.with_suffix(".candidate")
        pointer_bytes = self._active_pointer_bytes(
            candidate.repository_namespace, collection_name
        )
        try:
            with temporary_path.open("wb") as stream:
                stream.write(pointer_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            # Revalidate after the durable candidate bytes are prepared and
            # immediately before the pointer replacement commit point.
            self.validate_candidate(candidate)
            os.replace(temporary_path, pointer_path)
        except OSError:
            pass
        # Resolve outside the I/O handler: even a failed recovery must never
        # retain the raw write/replace exception through implicit context.
        snapshot = self._resolve_publication_outcome(candidate, old_collection)
        try:
            self._cleanup_obsolete_collection(old_collection, collection_name)
        except Exception:
            _LOGGER.warning("Vector store cleanup failed.")
        return snapshot

    def abort(self, candidate: CandidateIndex) -> None:
        """Remove an unreachable candidate without changing active authority."""
        self._assert_owner_process()
        collection_name = self._candidate_collection_name(candidate)
        active = self.inspect_active(candidate.repository_namespace)
        if active.snapshot is not None and active.snapshot._token == collection_name:
            return
        mapped_failure = None
        try:
            self._client.delete_collection(collection_name)
        except VectorStoreError:
            raise
        except Exception:
            mapped_failure = VectorStoreWriteError(_WRITE_MESSAGE)
        if mapped_failure is not None:
            raise mapped_failure


__all__ = ("ChromaVectorStore",)
