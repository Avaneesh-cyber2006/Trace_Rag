"""Chroma storage-schema and repository-isolation contracts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
from hashlib import sha256
import inspect
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Event
import weakref

import pytest

from backend.embedding_vector_store.exceptions import (
    EmbeddingInvalidResponseError,
    EmbeddingSpaceMismatch,
    EmbeddingTransientError,
    RepositoryIndexNotFound,
    VectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStorePublicationError,
    VectorStoreReadError,
    VectorStoreWriteError,
)
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity,
    EmbeddingVector,
    VectorRecord,
)
from backend.embedding_vector_store.stores.base import (
    CandidateIndex,
    RepositoryIndexSnapshot,
    StoreSearchResult,
    StoredRecord,
)
from backend.embedding_vector_store.stores.chroma import (
    STORAGE_SCHEMA_VERSION,
    ChromaVectorStore,
)
import backend.embedding_vector_store.stores.chroma as chroma_module
from backend.embedding_vector_store.search import SemanticSearcher
from backend.embedding_vector_store.retry import RetryPolicy
import backend.embedding_vector_store.search as search_module


NAMESPACE = "tracerag-repository-v1:github:example/資料庫"
IDENTITY = EmbeddingModelIdentity("gemini", "gemini-embedding-001", 3, "retrieval-3072-v1")
VECTOR = EmbeddingVector((0.25, -0.5, 0.75))


def _store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(tmp_path / "external-vector-data")


def _record(**changes: object) -> StoredRecord:
    content = "def 計算():\r\n    return 'λ  ' \n"
    record = StoredRecord(
        NAMESPACE,
        "a" * 64,
        sha256(content.encode("utf-8")).hexdigest(),
        "src/資料/δelta.py",
        "python",
        "symbol",
        "function",
        "資料.計算",
        "資料",
        content,
        VECTOR,
        IDENTITY,
        "tracerag-embedding-document-v1",
        STORAGE_SCHEMA_VERSION,
    )
    return replace(record, **changes)


def _snapshot(**changes: object) -> RepositoryIndexSnapshot:
    snapshot = RepositoryIndexSnapshot(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        STORAGE_SCHEMA_VERSION,
        7,
        object(),
    )
    return replace(snapshot, **changes)


def _candidate(**changes: object) -> CandidateIndex:
    candidate = CandidateIndex(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        STORAGE_SCHEMA_VERSION,
        2,
        "candidate10",
    )
    return replace(candidate, **changes)


def _vector_record(
    chunk_id: str, content: str, values: tuple[float, ...]
) -> VectorRecord:
    return VectorRecord(
        chunk_id,
        sha256(content.encode("utf-8")).hexdigest(),
        "src/資料/δelta.py",
        "python",
        "symbol",
        "function",
        "資料.計算",
        "資料",
        content,
        EmbeddingVector(values),
    )


def _candidate_records() -> tuple[VectorRecord, VectorRecord]:
    return (
        _vector_record(
            "c" * 64,
            "def 計算():\r\n    return 'λ  ' \n",
            (1.0, 0.0, 0.0),
        ),
        _vector_record(
            "e" * 64,
            "class 資料:\n    値 = 'two'\n",
            (0.0, 1.0, 0.0),
        ),
    )


def _publish_records(
    store: ChromaVectorStore,
    repository_namespace: str,
    records: tuple[VectorRecord, ...],
) -> RepositoryIndexSnapshot:
    candidate = store.begin_candidate(
        repository_namespace,
        IDENTITY,
        "tracerag-embedding-document-v1",
        len(records),
    )
    store.add_embedded(candidate, records)
    return store.publish(candidate)


@pytest.mark.parametrize("operation", ["add_embedded", "add_reused"])
def test_candidate_appends_reject_duplicates_and_overflow_after_reopen(tmp_path, operation):
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, _candidate().document_version, 2)
    records = _candidate_records() if operation == "add_embedded" else (
        _record(chunk_id="c" * 64), _record(chunk_id="e" * 64),
    )
    append = getattr(store, operation)
    with pytest.raises(VectorStoreWriteError):
        append(candidate, (records[0], records[0]))
    assert store._candidate_collection(candidate).count() == 0
    append(candidate, (records[0],))

    reopened = _store(tmp_path)
    append = getattr(reopened, operation)
    with pytest.raises(VectorStoreWriteError):
        append(candidate, (records[0],))
    assert reopened._candidate_collection(candidate).count() == 1
    append(candidate, (records[1],))
    with pytest.raises(VectorStoreWriteError):
        append(candidate, (replace(records[0], chunk_id="f" * 64),))
    assert reopened._candidate_collection(candidate).count() == 2
    reopened.validate_candidate(candidate)
    snapshot = reopened.publish(candidate)
    assert {record.chunk_id for record in reopened.read_manifest(snapshot)} == {
        "c" * 64, "e" * 64,
    }


def test_candidate_append_checks_persisted_ids_after_writer_process_exit(tmp_path):
    # The first batch belongs to a different process; duplicate checks cannot
    # depend on a process-local cache populated by earlier append calls.
    script = """
import runpy
import sys
from pathlib import Path
fixtures = runpy.run_path("tests/test_chroma_vector_store.py")
store = fixtures["_store"](Path(sys.argv[1]))
candidate = fixtures["_candidate"]()
store._create_candidate_collection(candidate)
store.add_embedded(candidate, (fixtures["_candidate_records"]()[0],))
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], check=True,
        capture_output=True, text=True, timeout=30,
    )
    reopened = _store(tmp_path)
    candidate = _candidate()
    records = _candidate_records()
    with pytest.raises(VectorStoreWriteError):
        reopened.add_embedded(candidate, (records[0],))
    assert reopened._candidate_collection(candidate).count() == 1
    reopened.add_embedded(candidate, (records[1],))
    snapshot = reopened.publish(candidate)
    assert {record.chunk_id for record in reopened.read_manifest(snapshot)} == {
        "c" * 64, "e" * 64,
    }


@pytest.mark.parametrize("count", [True, -1, 1.0, "1", None, 3])
@pytest.mark.parametrize("empty", [False, True])
def test_candidate_append_rejects_invalid_persisted_count(tmp_path, monkeypatch, count, empty):
    from chromadb.api.models.Collection import Collection

    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, _candidate().document_version, 2)
    original_count = Collection.count
    monkeypatch.setattr(Collection, "count", lambda self: count)
    with pytest.raises(VectorStoreWriteError):
        store.add_embedded(candidate, () if empty else (_candidate_records()[0],))
    assert original_count(store._candidate_collection(candidate)) == 0


@pytest.mark.parametrize("ids", [None, (), "", [None], [True], [1], [[]],
                                    ["z" * 64], ["f" * 64], ["c" * 64, "c" * 64]])
def test_candidate_append_rejects_malformed_or_unrequested_lookup_ids(tmp_path, monkeypatch, ids):
    from chromadb.api.models.Collection import Collection

    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, _candidate().document_version, 2)
    monkeypatch.setattr(Collection, "get", lambda self, *args, **kwargs: {"ids": ids})
    with pytest.raises(VectorStoreWriteError):
        store.add_embedded(candidate, (_candidate_records()[0],))
    assert store._candidate_collection(candidate).count() == 0


def test_never_indexed_repository_has_explicit_inactive_state(tmp_path: Path) -> None:
    state = _store(tmp_path).inspect_active(NAMESPACE)

    assert state.indexed is False
    assert state.snapshot is None


def test_candidate_is_invisible_until_publish_and_active_survives_reopen(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )
    store.add_embedded(candidate, _candidate_records())
    store.validate_candidate(candidate)

    assert store.inspect_active(NAMESPACE).indexed is False

    published = store.publish(candidate)
    reopened_state = _store(tmp_path).inspect_active(NAMESPACE)

    assert published.repository_namespace == NAMESPACE
    assert reopened_state.indexed is True
    assert reopened_state.snapshot is not None
    assert reopened_state.snapshot.repository_namespace == NAMESPACE
    assert reopened_state.snapshot.identity == IDENTITY
    assert reopened_state.snapshot.document_version == "tracerag-embedding-document-v1"
    assert reopened_state.snapshot.schema_version == STORAGE_SCHEMA_VERSION
    assert reopened_state.snapshot.expected_chunk_count == 2


def test_empty_state_is_published_and_distinct_from_never_indexed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        0,
    )

    store.validate_candidate(candidate)
    store.publish(candidate)
    reopened = _store(tmp_path).inspect_active(NAMESPACE)

    assert reopened.indexed is True
    assert reopened.snapshot is not None
    assert reopened.snapshot.expected_chunk_count == 0


def test_delete_repository_index_removes_only_exact_namespace_and_survives_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    deleted_namespace = "tracerag-repository-v1:github:example/delete-me"
    retained_namespace = "tracerag-repository-v1:github:example/delete-me-too"
    first = _publish_records(store, deleted_namespace, ())
    old_collection = first._token

    monkeypatch.setattr(store, "_cleanup_obsolete_collection", lambda *args: None)
    current = _publish_records(store, deleted_namespace, _candidate_records())
    abandoned = store.begin_candidate(
        deleted_namespace,
        IDENTITY,
        "tracerag-embedding-document-v1",
        0,
    )
    retained = _publish_records(store, retained_namespace, _candidate_records())
    retained_before = store.search(retained, EmbeddingVector((1.0, 0.0, 0.0)), 2)
    deleted_collections = {
        old_collection,
        current._token,
        store._candidate_collection_name(abandoned),
    }
    retained_collection = retained._token
    events: list[tuple[str, str]] = []
    pointer_path = store._active_pointer_path(deleted_namespace)
    path_type = type(pointer_path)
    original_unlink = path_type.unlink
    original_delete_collection = store._client.delete_collection

    def record_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == pointer_path:
            events.append(("pointer", path.name))
        original_unlink(path, *args, **kwargs)

    def record_delete_collection(collection_name: str) -> None:
        events.append(("collection", collection_name))
        original_delete_collection(collection_name)

    monkeypatch.setattr(path_type, "unlink", record_unlink)
    monkeypatch.setattr(store._client, "delete_collection", record_delete_collection)

    store.delete_repository_index(deleted_namespace)

    assert events[0][0] == "pointer"
    assert {name for kind, name in events if kind == "collection"} == deleted_collections
    assert retained_collection not in {name for kind, name in events if kind == "collection"}
    reopened = _store(tmp_path)
    assert reopened.inspect_active(deleted_namespace).indexed is False
    retained_after = reopened.inspect_active(retained_namespace)
    assert retained_after.snapshot is not None
    assert retained_after.snapshot._token == retained_collection
    assert (
        reopened.search(
            retained_after.snapshot,
            EmbeddingVector((1.0, 0.0, 0.0)),
            2,
        )
        == retained_before
    )


@pytest.mark.parametrize("namespace", ("", None, 5))
def test_delete_repository_index_rejects_invalid_namespace(
    tmp_path: Path, namespace: object
) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        _store(tmp_path).delete_repository_index(namespace)  # type: ignore[arg-type]


def test_delete_repository_index_never_expands_substrings_display_names_or_patterns(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    full_namespace = "tracerag-repository-v1:github:example/project"
    snapshot = _publish_records(store, full_namespace, _candidate_records())

    store.delete_repository_index("project")
    store.delete_repository_index(full_namespace[:-4])
    store.delete_repository_index("*")

    active = store.inspect_active(full_namespace)
    assert active.snapshot is not None
    assert active.snapshot._token == snapshot._token


def test_delete_repository_index_rejects_digest_collision_before_any_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    first = "tracerag-repository-v1:github:example/first"
    colliding = "tracerag-repository-v1:github:example/second"
    monkeypatch.setattr(store, "_namespace_digest", lambda namespace: "f" * 64)
    snapshot = _publish_records(store, first, _candidate_records())
    pointer_path = store._active_pointer_path(first)
    collections_before = {item.name for item in store._client.list_collections()}

    with pytest.raises(VectorStoreCorruptionError):
        store.delete_repository_index(colliding)

    assert pointer_path.exists()
    assert {item.name for item in store._client.list_collections()} == collections_before
    assert store.inspect_active(first).snapshot == snapshot


def test_delete_repository_index_never_touches_source_repository_files(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source-repository"
    source_root.mkdir()
    source_file = source_root / "evidence.py"
    source_file.write_bytes(b"print('exact source')\r\n")
    namespace = str(source_root)
    store = _store(tmp_path)
    _publish_records(store, namespace, _candidate_records())

    store.delete_repository_index(namespace)

    assert source_root.is_dir()
    assert source_file.read_bytes() == b"print('exact source')\r\n"


def test_delete_repository_index_maps_pointer_removal_failure_to_typed_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    snapshot = _publish_records(store, NAMESPACE, _candidate_records())
    pointer_path = store._active_pointer_path(NAMESPACE)
    path_type = type(pointer_path)
    original_unlink = path_type.unlink

    def fail_pointer_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == pointer_path:
            raise OSError("injected pointer removal failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", fail_pointer_unlink)

    with pytest.raises(VectorStoreWriteError):
        store.delete_repository_index(NAMESPACE)

    assert pointer_path.exists()
    assert {item.name for item in store._client.list_collections()} >= {snapshot._token}


def test_delete_repository_index_maps_collection_failure_after_logical_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _publish_records(store, NAMESPACE, _candidate_records())

    def fail_collection_delete(collection_name: str) -> None:
        raise RuntimeError("injected collection removal failure")

    monkeypatch.setattr(store._client, "delete_collection", fail_collection_delete)

    with pytest.raises(VectorStoreWriteError):
        store.delete_repository_index(NAMESPACE)

    assert store.inspect_active(NAMESPACE).indexed is False


def test_add_reused_completes_candidate_without_core_vector_conversion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )
    store.add_embedded(first, _candidate_records())
    stored_records = store._read_candidate_manifest(first)
    store.publish(first)
    reused = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )

    store.add_reused(reused, stored_records)
    store.validate_candidate(reused)
    store.publish(reused)

    assert store._read_candidate_manifest(reused) == stored_records


def test_candidate_validation_rejects_incomplete_logical_generation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )
    store.add_embedded(candidate, (_candidate_records()[0],))

    with pytest.raises(VectorStoreCorruptionError):
        store.validate_candidate(candidate)


def test_publish_uses_canonical_checksummed_pointer_and_os_replace_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )
    store.add_embedded(old, _candidate_records())
    store.publish(old)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v2",
        0,
    )
    real_replace = os.replace
    real_fsync = os.fsync
    commit_events: list[str] = []
    commit_observations: list[tuple[Path, Path]] = []

    def observe_fsync(file_descriptor: int) -> None:
        commit_events.append("fsync")
        real_fsync(file_descriptor)

    def observe_commit(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        active_before_commit = store.inspect_active(NAMESPACE)
        assert active_before_commit.snapshot is not None
        assert active_before_commit.snapshot.document_version == (
            "tracerag-embedding-document-v1"
        )
        assert source_path.parent == destination_path.parent
        commit_observations.append((source_path, destination_path))
        commit_events.append("replace")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    monkeypatch.setattr(os, "replace", observe_commit)
    published = store.publish(candidate)
    pointer_path = store._active_pointer_path(NAMESPACE)
    pointer_bytes = pointer_path.read_bytes()
    envelope = json.loads(pointer_bytes)
    canonical_payload = json.dumps(
        envelope["payload"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert len(commit_observations) == 1
    assert commit_events == ["fsync", "replace"]
    assert commit_observations[0][1] == pointer_path
    assert set(envelope) == {"payload", "sha256"}
    assert set(envelope["payload"]) == {
        "schema_version",
        "repository_namespace",
        "active_collection",
    }
    assert envelope["sha256"] == sha256(canonical_payload).hexdigest()
    assert pointer_bytes == json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert published.document_version == "tracerag-embedding-document-v2"


def test_reopen_uses_published_pointer_not_newer_candidate_or_collection_order(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    published = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "published-document-version",
        0,
    )
    store.publish(published)
    abandoned = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "newer-unpublished-document-version",
        0,
    )
    store.validate_candidate(abandoned)

    reopened = _store(tmp_path).inspect_active(NAMESPACE)

    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "published-document-version"


def test_abort_removes_only_unpublished_candidate_and_never_active(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    active = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "active-document-version",
        0,
    )
    store.publish(active)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "candidate-document-version",
        0,
    )
    candidate_name = store._candidate_collection_name(candidate)

    store.abort(candidate)
    store.abort(active)

    reopened = _store(tmp_path).inspect_active(NAMESPACE)
    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "active-document-version"
    assert candidate_name not in {collection.name for collection in store._client.list_collections()}


def test_candidate_write_failure_keeps_old_active_snapshot_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 2)

    def fail_candidate_write(*args: object, **kwargs: object) -> None:
        raise VectorStoreWriteError("injected candidate write failure")

    monkeypatch.setattr(store, "_append_stored_candidate", fail_candidate_write)

    with pytest.raises(VectorStoreWriteError):
        store.add_embedded(candidate, _candidate_records())

    reopened = _store(tmp_path).inspect_active(NAMESPACE)
    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "old-document-version"


def test_candidate_validation_failure_keeps_old_active_snapshot_authoritative(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 2)
    store.add_embedded(candidate, (_candidate_records()[0],))

    with pytest.raises(VectorStoreCorruptionError):
        store.publish(candidate)

    reopened = _store(tmp_path).inspect_active(NAMESPACE)
    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "old-document-version"


def test_publication_failure_with_corrupt_pointer_fails_closed_as_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 0)
    pointer_path = store._active_pointer_path(NAMESPACE)

    def interrupt_before_commit(source: str | Path, destination: str | Path) -> None:
        pointer_path.write_bytes(b"not a pointer")
        raise OSError("injected interruption before commit")

    monkeypatch.setattr(os, "replace", interrupt_before_commit)

    with pytest.raises(VectorStoreCorruptionError):
        store.publish(candidate)


def test_boundary_interruption_returns_new_snapshot_only_after_pointer_proves_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 0)
    real_replace = os.replace

    def commit_then_interrupt(source: str | Path, destination: str | Path) -> None:
        real_replace(source, destination)
        raise OSError("injected interruption at commit boundary")

    monkeypatch.setattr(os, "replace", commit_then_interrupt)

    published = store.publish(candidate)
    reopened = _store(tmp_path).inspect_active(NAMESPACE)

    assert published.document_version == "new-document-version"
    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "new-document-version"


@pytest.mark.parametrize("replace_raises", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_committed_publication_survives_backend_read_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, replace_raises: bool, cleanup_fails: bool,
) -> None:
    store = _store(tmp_path)
    old = _publish_records(store, NAMESPACE, (_candidate_records()[0],))
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 2)
    store.add_embedded(candidate, _candidate_records())
    store.validate_candidate(candidate)
    real_replace = os.replace
    real_get = store._client.get_collection
    committed = False

    def commit_then_lose_backend(source: str | Path, destination: str | Path) -> None:
        nonlocal committed
        real_replace(source, destination)
        committed = True
        if replace_raises:
            raise OSError("private replacement failure")

    def read_during_outage(*args: object, **kwargs: object):
        if committed:
            raise RuntimeError("private backend outage")
        return real_get(*args, **kwargs)

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", commit_then_lose_backend)
        patch.setattr(store._client, "get_collection", read_during_outage)
        if cleanup_fails:
            patch.setattr(store._client, "delete_collection", fail_cleanup)
        published = store.publish(candidate)
        pointer = json.loads(store._active_pointer_path(NAMESPACE).read_bytes())
        assert pointer["payload"]["active_collection"] == published._token

    assert published.repository_namespace == NAMESPACE
    assert published.identity == IDENTITY
    assert published.document_version == "new-document-version"
    assert published.schema_version == STORAGE_SCHEMA_VERSION
    assert published.expected_chunk_count == 2
    reopened = _store(tmp_path).inspect_active(NAMESPACE).snapshot
    assert reopened == published
    assert reopened._token == published._token
    assert published._token != old._token
    assert tuple(record.content for record in store.read_manifest(published)) == (
        "def 計算():\r\n    return 'λ  ' \n", "class 資料:\n    値 = 'two'\n",
    )
    assert {collection.name for collection in store._client.list_collections()} == (
        {old._token, published._token} if cleanup_fails else {published._token}
    )
    assert caplog.messages == (["Vector store cleanup failed."] if cleanup_fails else [])


@pytest.mark.parametrize("replace_raises", [False, True])
@pytest.mark.parametrize("pointer_state", ["unreadable", "checksum", "third", "missing"])
def test_publication_requires_readable_unambiguous_pointer_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    replace_raises: bool, pointer_state: str,
) -> None:
    store = _store(tmp_path)
    old = _publish_records(store, NAMESPACE, (_candidate_records()[0],))
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 2)
    store.add_embedded(candidate, _candidate_records())
    third = store.begin_candidate(NAMESPACE, IDENTITY, "third-document-version", 0)
    pointer_path = store._active_pointer_path(NAMESPACE)
    real_replace = os.replace
    real_read = Path.read_bytes
    committed = False

    def replace_with_uncertain_pointer(source: str | Path, destination: str | Path) -> None:
        nonlocal committed
        real_replace(source, destination)
        committed = True
        if pointer_state == "checksum":
            envelope = json.loads(real_read(pointer_path))
            envelope["sha256"] = "0" * 64
            pointer_path.write_bytes(store._canonical_json(envelope))
        elif pointer_state == "third":
            pointer_path.write_bytes(store._active_pointer_bytes(
                NAMESPACE, store._candidate_collection_name(third),
            ))
        elif pointer_state == "missing":
            pointer_path.unlink()
        if replace_raises:
            raise OSError("private replacement failure")

    def read_during_outage(path: Path) -> bytes:
        if committed and pointer_state == "unreadable" and path == pointer_path:
            raise OSError("private pointer outage")
        return real_read(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace_with_uncertain_pointer)
        patch.setattr(Path, "read_bytes", read_during_outage)
        expected = (VectorStorePublicationError if pointer_state == "unreadable"
                    else VectorStoreCorruptionError)
        with pytest.raises(expected) as captured:
            store.publish(candidate)
        assert captured.value.__context__ is None
        assert captured.value.__cause__ is None
        assert "private" not in str(captured.value)

    # An unproven outcome must retain both generations; no rollback may replace
    # the durable pointer or claim that the old generation remains active.
    assert {collection.name for collection in store._client.list_collections()} == {
        old._token, store._candidate_collection_name(candidate),
        store._candidate_collection_name(third),
    }
    if pointer_state == "unreadable":
        assert store.inspect_active(NAMESPACE).snapshot.document_version == "new-document-version"
    elif pointer_state == "third":
        assert store.inspect_active(NAMESPACE).snapshot.document_version == "third-document-version"
    elif pointer_state == "missing":
        assert not pointer_path.exists()
    else:
        with pytest.raises(VectorStoreCorruptionError):
            store.inspect_active(NAMESPACE)


def test_restart_ignores_abandoned_candidate_after_precommit_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = _publish_records(store, NAMESPACE, (_candidate_records()[0],))
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "abandoned-document-version", 2)
    store.add_embedded(candidate, _candidate_records())
    old_pointer = store._active_pointer_path(NAMESPACE).read_bytes()

    def interrupt_before_commit(source: str | Path, destination: str | Path) -> None:
        raise OSError("injected interruption before commit")

    monkeypatch.setattr(os, "replace", interrupt_before_commit)

    with pytest.raises(VectorStorePublicationError):
        store.publish(candidate)

    reopened = _store(tmp_path).inspect_active(NAMESPACE)
    assert reopened.snapshot is not None
    assert reopened.snapshot._token == old._token
    assert store._active_pointer_path(NAMESPACE).read_bytes() == old_pointer
    assert tuple(record.content for record in store.read_manifest(old)) == (
        "def 計算():\r\n    return 'λ  ' \n",
    )


def test_cleanup_failure_after_commit_keeps_new_active_snapshot_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 0)
    cleanup_calls: list[tuple[str | None, str]] = []

    def fail_delete_collection(collection_name: str) -> None:
        cleanup_calls.append((store._candidate_collection_name(old), collection_name))
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(store._client, "delete_collection", fail_delete_collection)

    with caplog.at_level(logging.WARNING, logger="backend.embedding_vector_store.stores.chroma"):
        published = store.publish(candidate)
    reopened = _store(tmp_path).inspect_active(NAMESPACE)

    assert cleanup_calls == [
        (store._candidate_collection_name(old), store._candidate_collection_name(old))
    ]
    assert caplog.messages == ["Vector store cleanup failed."]
    assert published.document_version == "new-document-version"
    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "new-document-version"


def test_successful_replacement_retires_previously_active_collection(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    old_collection = store._candidate_collection_name(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 0)

    published = store.publish(candidate)
    collection_names = {collection.name for collection in store._client.list_collections()}

    assert old_collection not in collection_names
    assert store._candidate_collection_name(candidate) in collection_names
    assert published.document_version == "new-document-version"


def test_concurrent_reader_keeps_its_snapshot_until_search_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old_record = _vector_record("c" * 64, "old content", (1.0, 0.0, 0.0))
    old_snapshot = _publish_records(store, NAMESPACE, (old_record,))
    new_record = _vector_record("e" * 64, "new content", (1.0, 0.0, 0.0))
    new_candidate = store.begin_candidate(
        NAMESPACE, IDENTITY, "tracerag-embedding-document-v1", 1
    )
    store.add_embedded(new_candidate, (new_record,))
    entered = Event()
    release = Event()
    old_collection = store._read_collection(NAMESPACE, old_snapshot._token)
    original_query = old_collection.query
    original_read_collection = store._read_collection

    def blocking_query(**kwargs: object) -> object:
        entered.set()
        if not release.wait(5):
            raise AssertionError("test did not release the snapshot reader")
        return original_query(**kwargs)

    def read_collection(repository_namespace: str, collection_name: object) -> object:
        if collection_name == old_snapshot._token:
            return old_collection
        return original_read_collection(repository_namespace, collection_name)

    monkeypatch.setattr(old_collection, "query", blocking_query)
    monkeypatch.setattr(store, "_read_collection", read_collection)

    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(
            store.search, old_snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1
        )
        assert entered.wait(5)
        published = store.publish(new_candidate)
        names_during_read = {
            collection.name for collection in store._client.list_collections()
        }
        assert old_snapshot._token in names_during_read
        release.set()
        old_results = reader.result(timeout=5)

    names_after_read = {collection.name for collection in store._client.list_collections()}
    assert old_snapshot._token not in names_after_read
    assert old_results[0].content == "old content"
    assert store.search(published, EmbeddingVector((1.0, 0.0, 0.0)), 1)[0].content == (
        "new content"
    )


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (None, None),
        ("snapshot", VectorStoreCorruptionError),
        ("identity", EmbeddingSpaceMismatch),
        ("provider", RuntimeError),
        ("retry", EmbeddingTransientError),
        ("vector", EmbeddingInvalidResponseError),
        ("store", VectorStoreReadError),
        ("normalization", VectorStoreCorruptionError),
    ],
)
def test_concurrent_query_retains_snapshot_from_identity_through_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure, expected_error
) -> None:
    store = _store(tmp_path)
    old_record = _vector_record(
        "c" * 64, "old content", (1.0, 0.0, 0.0)
    )
    old_snapshot = _publish_records(store, NAMESPACE, (old_record,))
    new_record = _vector_record(
        "e" * 64, "new content", (1.0, 0.0, 0.0)
    )
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, old_snapshot.document_version, 1)
    store.add_embedded(candidate, (new_record,))
    entered, release = Event(), Event()

    def pause():
        entered.set()
        assert release.wait(10), "test did not release query"

    class Provider:
        max_batch_size = 1
        attempts = 0

        @property
        def identity(self):
            if failure == "identity":
                pause()
                return replace(IDENTITY, model="other")
            return IDENTITY

        def embed_documents(self, documents):
            raise AssertionError("query must not embed documents")

        def embed_query(self, text):
            self.attempts += 1
            if failure not in ("store", "normalization"):
                pause()
            if failure == "provider":
                raise RuntimeError("provider failed")
            if failure == "retry":
                raise EmbeddingTransientError("retry")
            if failure == "vector":
                return EmbeddingVector((1.0,))
            return EmbeddingVector((1.0, 0.0, 0.0))

    if failure == "snapshot":
        original_snapshot = store._snapshot_for_collection

        def failed_snapshot(namespace, collection_name):
            if collection_name == old_snapshot._token and not entered.is_set():
                pause()
                raise VectorStoreCorruptionError("invalid snapshot")
            return original_snapshot(namespace, collection_name)

        monkeypatch.setattr(store, "_snapshot_for_collection", failed_snapshot)
    if failure == "store":
        def failed_search(*args):
            pause()
            raise VectorStoreReadError("read failed")
        monkeypatch.setattr(store, "search", failed_search)
    if failure == "normalization":
        original_normalization = search_module.validate_and_normalize_search_results

        def failed_normalization(*args):
            pause()
            return original_normalization((object(),), NAMESPACE, 1)
        monkeypatch.setattr(search_module, "validate_and_normalize_search_results", failed_normalization)
    provider = Provider()
    searcher = SemanticSearcher(
        provider, store, retry_policy=RetryPolicy(max_attempts=2, sleeper=lambda _: None)
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(searcher.search, NAMESPACE, "query", 1)
        try:
            if not entered.wait(15):
                # Surface an early reader failure rather than hiding it behind
                # a barrier timeout (backend validation happens before embed).
                reader.result(timeout=5)
                pytest.fail("query did not reach the controlled handoff")
            store.publish(candidate)
            assert old_snapshot._token in {
                item.name for item in store._client.list_collections()
            }
        finally:
            release.set()
        if expected_error is None:
            assert reader.result(timeout=10)[0].content == "old content"
        else:
            with pytest.raises(expected_error):
                reader.result(timeout=10)
    assert old_snapshot._token not in {
        item.name for item in store._client.list_collections()
    }
    if failure == "retry":
        assert provider.attempts == 2
    if failure is None:
        assert searcher.search(NAMESPACE, "query", 1)[0].content == "new content"


def test_search_snapshot_context_releases_empty_and_never_indexed_states(tmp_path: Path) -> None:
    store = _store(tmp_path)

    class Provider:
        identity = IDENTITY
        max_batch_size = 1

        def embed_documents(self, documents):
            raise AssertionError("empty query must not embed documents")

        def embed_query(self, text):
            raise AssertionError("empty query must not call provider")

    searcher = SemanticSearcher(Provider(), store)
    with pytest.raises(RepositoryIndexNotFound):
        searcher.search(NAMESPACE, "query", 1)
    empty = _publish_records(store, NAMESPACE, ())
    assert searcher.search(NAMESPACE, "query", 1) == ()
    _publish_records(store, NAMESPACE, ())
    assert empty._token not in {item.name for item in store._client.list_collections()}


def test_reader_cannot_lease_snapshot_reserved_for_cleanup_and_can_retry_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old_record = _vector_record("c" * 64, "old content", (1.0, 0.0, 0.0))
    old_snapshot = _publish_records(store, NAMESPACE, (old_record,))
    delete_entered = Event()
    release_delete = Event()

    def fail_blocked_delete(collection_name: str) -> None:
        assert collection_name == old_snapshot._token
        delete_entered.set()
        if not release_delete.wait(5):
            raise AssertionError("test did not release collection cleanup")
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(store._client, "delete_collection", fail_blocked_delete)

    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(
            store._cleanup_obsolete_collection,
            old_snapshot._token,
            "replacement-collection",
        )
        try:
            assert delete_entered.wait(5)
            stale_reader = executor.submit(
                store.search,
                old_snapshot,
                EmbeddingVector((1.0, 0.0, 0.0)),
                1,
            )
            with pytest.raises(VectorStoreReadError):
                stale_reader.result(timeout=5)
        finally:
            release_delete.set()
        cleanup.result(timeout=5)

    assert store.search(
        old_snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1
    )[0].content == "old content"


def test_writer_mutation_rejects_use_from_a_non_owning_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    state = store._concurrency_state
    owner_process = os.getpid()
    with monkeypatch.context() as pid_patch:
        pid_patch.setattr(chroma_module.os, "getpid", lambda: owner_process + 1)

        with pytest.raises(VectorStoreConfigurationError):
            store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 0)

    del store
    gc.collect()
    assert state.ownership_file.closed


@pytest.mark.parametrize("operation", ("inspect", "acquire", "manifest", "search"))
def test_public_reads_reject_use_from_a_non_owning_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    store = _store(tmp_path)
    snapshot = _publish_records(store, NAMESPACE, ())
    state = store._concurrency_state
    owner_process = os.getpid()
    with monkeypatch.context() as pid_patch:
        pid_patch.setattr(chroma_module.os, "getpid", lambda: owner_process + 1)

        with pytest.raises(VectorStoreConfigurationError):
            if operation == "inspect":
                store.inspect_active(NAMESPACE)
            elif operation == "acquire":
                with store.acquire_active(NAMESPACE):
                    pytest.fail("non-owner acquired active snapshot")
            elif operation == "manifest":
                store.read_manifest(snapshot)
            else:
                store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)

    del store
    gc.collect()
    assert state.ownership_file.closed


_SUBPROCESS_WRITER = """
import json
import os
import sys
from backend.embedding_vector_store.exceptions import VectorStoreConfigurationError
from backend.embedding_vector_store.models import EmbeddingModelIdentity
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore

try:
    store = ChromaVectorStore(sys.argv[1])
    candidate = store.begin_candidate(
        "subprocess-repository", EmbeddingModelIdentity("test", "test", 3, "v1"),
        "subprocess-document-v1", 0,
    )
    store.publish(candidate)
except VectorStoreConfigurationError as error:
    print(json.dumps({"outcome": "rejected", "message": str(error)}))
else:
    print(json.dumps({"outcome": "published"}), flush=True)
    if sys.argv[2] == "abrupt":
        os._exit(0)
"""


_SUBPROCESS_FORK_OWNERSHIP = """
import gc
import json
import os
import sys
import weakref
from threading import Event, Thread

from chromadb.api.client import SharedSystemClient

from backend.embedding_vector_store.exceptions import VectorStoreConfigurationError
from backend.embedding_vector_store.models import EmbeddingModelIdentity
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
import backend.embedding_vector_store.stores.chroma as chroma_module
import backend.embedding_vector_store.synchronization as synchronization
import backend.embedding_vector_store._concurrency as concurrency
from backend.code_chunker.models import CodeChunkInventory

root = sys.argv[1]
namespace = "fork-repository"
identity = EmbeddingModelIdentity("test", "test", 3, "v1")
store = ChromaVectorStore(root)
snapshot = store.publish(store.begin_candidate(namespace, identity, "parent-v1", 0))
inventory = CodeChunkInventory(".", namespace, 0, 0, 0, 0, 0, ())
class Provider:
    max_batch_size = 1
    def __init__(self):
        self.identity = identity
    def embed_documents(self, documents):
        raise AssertionError("empty sync must not embed")
    def embed_query(self, text):
        raise AssertionError("sync must not query")
entered, release = Event(), Event()
original_inspect = store.inspect_active
def blocked_inspect(namespace):
    entered.set()
    assert release.wait(15)
    return original_inspect(namespace)
store.inspect_active = blocked_inspect
thread = Thread(target=synchronization.SemanticIndexer(Provider(), store).synchronize, args=(inventory,))
thread.start()
assert entered.wait(10)
# Fork with both an active namespace entry and a lock owned by a vanished thread.
lock_held, unlock = Event(), Event()
def hold_guard_lock():
    with concurrency._WRITER_GUARDS_LOCK:
        lock_held.set()
        assert unlock.wait(15)
lock_thread = Thread(target=hold_guard_lock)
lock_thread.start()
assert lock_held.wait(10)
ready_read, ready_write = os.pipe()
parent_alive_read, parent_alive_write = os.pipe()
child_pid = os.fork()
if child_pid:
    unlock.set()
    lock_thread.join(10)
    release.set()
    thread.join(10)
    assert not thread.is_alive()
    os.close(ready_write)
    os.close(parent_alive_read)
    ready = b""
    while len(ready) < 2:
        chunk = os.read(ready_read, 2 - len(ready))
        assert chunk
        ready += chunk
    assert ready == b"12"
    assert store.inspect_active(namespace).snapshot.document_version == "tracerag-embedding-document-v1"
    # Keep the lifetime pipe open until exit also releases process ownership.
    os._exit(0)

os.close(ready_read)
os.close(parent_alive_write)
store.inspect_active = original_inspect
outcome = {
    "registry_empty": chroma_module._PERSISTENCE_STATES == {},
    "client_cache_empty": SharedSystemClient._identifier_to_system == {},
    "writer_guards_empty": concurrency._ACTIVE_WRITER_GUARDS == set(),
    "writer_guard_lock_available": concurrency._WRITER_GUARDS_LOCK.acquire(blocking=False),
}
if outcome["writer_guard_lock_available"]:
    concurrency._WRITER_GUARDS_LOCK.release()
try:
    store.inspect_active(namespace)
except VectorStoreConfigurationError:
    outcome["inherited_read"] = "rejected"
else:
    outcome["inherited_read"] = "accepted"
inherited_client = store._client
inherited_adapter = weakref.ref(store)
del store, original_inspect, thread
gc.collect()
outcome["inherited_adapter_collected"] = inherited_adapter() is None
outcome["inherited_client_not_closed"] = not inherited_client._closed
os.write(ready_write, b"1")
try:
    ChromaVectorStore(root)
except VectorStoreConfigurationError:
    outcome["fresh_while_parent_alive"] = "rejected"
else:
    outcome["fresh_while_parent_alive"] = "accepted"
os.write(ready_write, b"2")
os.close(ready_write)
assert os.read(parent_alive_read, 1) == b""
reopened = ChromaVectorStore(root)
child_provider = Provider()
child_provider.identity = EmbeddingModelIdentity("test", "child", 3, "v2")
result = synchronization.SemanticIndexer(child_provider, reopened).synchronize(inventory)
assert reopened.inspect_active(namespace).snapshot.identity == child_provider.identity
outcome["fresh_after_parent_exit"] = result.status.value
os.write(1, (json.dumps(outcome) + "\\n").encode())
os._exit(0)
"""


def _run_subprocess_writer(root: Path, exit_mode: str = "normal") -> dict[str, str]:
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_WRITER, str(root), exit_mode],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
def test_fork_discards_inherited_chroma_state_without_releasing_parent_ownership(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _SUBPROCESS_FORK_OWNERSHIP,
            str(tmp_path / "fork-vector-data"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert json.loads(result.stdout) == {
        "registry_empty": True,
        "client_cache_empty": True,
        "writer_guards_empty": True,
        "writer_guard_lock_available": True,
        "inherited_read": "rejected",
        "inherited_adapter_collected": True,
        "inherited_client_not_closed": True,
        "fresh_while_parent_alive": "rejected",
        "fresh_after_parent_exit": "success",
    }


def test_concurrent_subprocess_writer_rejected_for_owned_persistence_root(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    # A second same-process instance shares ownership and can publish normally.
    same_process_store = ChromaVectorStore(store._persistence_root / ".")
    snapshot = same_process_store.publish(
        same_process_store.begin_candidate(NAMESPACE, IDENTITY, "owner-document-v1", 0)
    )

    result = _run_subprocess_writer(store._persistence_root)

    assert result == {
        "outcome": "rejected", "message": "Vector store configuration is invalid."
    }
    assert store.inspect_active(NAMESPACE).snapshot == snapshot
    assert not store.inspect_active("subprocess-repository").indexed


@pytest.mark.parametrize("exit_mode", ("normal", "abrupt"))
def test_subprocess_writer_ownership_released_on_process_exit(
    tmp_path: Path, exit_mode: str,
) -> None:
    root = tmp_path / "external-vector-data"
    assert _run_subprocess_writer(root, exit_mode) == {"outcome": "published"}

    reopened = ChromaVectorStore(root)

    assert reopened.inspect_active("subprocess-repository").indexed
    snapshot = reopened.publish(
        reopened.begin_candidate(NAMESPACE, IDENTITY, "restarted-document-v1", 0)
    )
    assert reopened.inspect_active(NAMESPACE).snapshot == snapshot


def test_writer_ownership_released_after_client_initialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_client(**kwargs: object) -> None:
        raise RuntimeError("private initialization failure")

    monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", fail_client)
    root = tmp_path / "external-vector-data"
    with pytest.raises(VectorStoreConfigurationError):
        ChromaVectorStore(root)

    assert _run_subprocess_writer(root) == {"outcome": "published"}


def test_unused_persistence_roots_release_ownership_and_chroma_systems(
    tmp_path: Path,
) -> None:
    for index in range(12):
        root = tmp_path / str(index)
        store = ChromaVectorStore(root)
        state = store._concurrency_state
        client = store._client
        reference = weakref.ref(store)
        # A cycle must not require __del__ or keep an otherwise unused root alive.
        store._test_cycle = store

        del store
        gc.collect()

        assert reference() is None
        assert state.ownership_file.closed
        assert os.path.normcase(str(root.resolve())) not in chroma_module._PERSISTENCE_STATES
        assert client._identifier not in chroma_module.SharedSystemClient._identifier_to_system


def test_last_shared_adapter_releases_ownership_and_can_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store._persistence_root
    state = store._concurrency_state
    other = ChromaVectorStore(root / ".")
    assert other._concurrency_state is state
    assert other._client is store._client
    del store
    gc.collect()

    assert not state.ownership_file.closed
    snapshot = _publish_records(other, NAMESPACE, _candidate_records())
    assert _run_subprocess_writer(root)["outcome"] == "rejected"

    del other
    gc.collect()

    assert state.ownership_file.closed
    assert _run_subprocess_writer(root) == {"outcome": "published"}
    reopened = ChromaVectorStore(root)
    assert reopened._concurrency_state is not state
    assert reopened.inspect_active(NAMESPACE).snapshot == snapshot
    assert len(reopened.read_manifest(snapshot)) == 2


def test_active_snapshot_context_retains_last_adapter_ownership(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store._persistence_root
    snapshot = _publish_records(store, NAMESPACE, _candidate_records())
    state = store._concurrency_state
    reference = weakref.ref(store)
    reader = store.acquire_active(NAMESPACE)

    with reader as active:
        del store
        gc.collect()
        assert reference() is not None
        assert not state.ownership_file.closed
        assert state.readers == {snapshot._token: 1}
        assert active.snapshot == snapshot
        assert _run_subprocess_writer(root)["outcome"] == "rejected"

    gc.collect()
    assert reference() is None
    assert not state.readers
    assert state.ownership_file.closed


def test_active_writer_retains_last_adapter_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    state = store._concurrency_state
    reference = weakref.ref(store)
    entered, release = Event(), Event()
    create = store._client.create_collection

    def blocked_create(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return create(*args, **kwargs)

    monkeypatch.setattr(store._client, "create_collection", blocked_create)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.begin_candidate, NAMESPACE, IDENTITY, "document-v1", 0)
        try:
            assert entered.wait(10)
            del store
            gc.collect()
            assert reference() is not None
            assert not state.ownership_file.closed
        finally:
            release.set()
        assert future.result(timeout=10).expected_chunk_count == 0

    gc.collect()
    assert reference() is None
    assert state.ownership_file.closed


def test_adapter_setup_failure_releases_acquired_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = []
    acquire = chroma_module._persistence_concurrency_state

    def capture_state(root):
        state = acquire(root)
        states.append(state)
        return state

    class FailedAdapter(ChromaVectorStore):
        def __setattr__(self, name, value):
            if name == "_client":
                raise RuntimeError("injected adapter setup failure")
            super().__setattr__(name, value)

    monkeypatch.setattr(chroma_module, "_persistence_concurrency_state", capture_state)
    root = tmp_path / "failed-adapter"
    with pytest.raises(VectorStoreConfigurationError):
        FailedAdapter(root)

    assert states[0].ownership_file.closed
    assert _run_subprocess_writer(root) == {"outcome": "published"}


def test_finalization_during_client_construction_does_not_deadlock(tmp_path: Path) -> None:
    script = """
import gc
from pathlib import Path
import sys
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
import backend.embedding_vector_store.stores.chroma as module

gc.disable()
store = ChromaVectorStore(Path(sys.argv[1]) / "old")
state = store._concurrency_state
store.cycle = store
del store
original = module.chromadb.PersistentClient
def collect_during_construction(**kwargs):
    gc.collect()
    return original(**kwargs)
module.chromadb.PersistentClient = collect_during_construction
new = ChromaVectorStore(Path(sys.argv[1]) / "new")
assert state.ownership_file.closed
assert not new._concurrency_state.ownership_file.closed
print("released")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    assert result.stdout.strip() == "released"


def test_teardown_rejects_reentrant_acquisition_and_closes_before_unlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    root = store._persistence_root
    state = store._concurrency_state
    close = store._client.close
    observed = []

    def close_with_reentrant_acquisition():
        observed.append(not state.ownership_file.closed)
        try:
            ChromaVectorStore(root)
        except VectorStoreConfigurationError:
            observed.append("rejected")
        close()

    monkeypatch.setattr(store._client, "close", close_with_reentrant_acquisition)
    del store
    gc.collect()

    assert observed == [True, "rejected"]
    assert state.ownership_file.closed


def test_failed_client_teardown_keeps_ownership_and_rejects_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    store = _store(tmp_path)
    root = store._persistence_root
    state = store._concurrency_state
    close = store._client.close

    def fail_close():
        raise RuntimeError("private teardown detail")

    monkeypatch.setattr(store._client, "close", fail_close)
    try:
        del store
        gc.collect()
        assert not state.ownership_file.closed
        assert _run_subprocess_writer(root)["outcome"] == "rejected"
        with pytest.raises(VectorStoreConfigurationError):
            ChromaVectorStore(root)
        assert "Vector store cleanup failed." in caplog.text
        assert "private teardown detail" not in caplog.text
    finally:
        # This injected failure intentionally retains ownership until process
        # exit. Release the healthy real client so this test leaves no resources.
        close()
        state.ownership_file.close()
        chroma_module._PERSISTENCE_STATES.pop(os.path.normcase(str(root)), None)


def test_publication_failure_with_valid_third_pointer_is_ambiguous_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 0)
    third = store.begin_candidate(NAMESPACE, IDENTITY, "third-document-version", 0)
    pointer_path = store._active_pointer_path(NAMESPACE)

    def replace_with_third_then_fail(source: str | Path, destination: str | Path) -> None:
        pointer_path.write_bytes(
            store._active_pointer_bytes(
                NAMESPACE, store._candidate_collection_name(third)
            )
        )
        raise OSError("injected replacement failure after competing commit")

    monkeypatch.setattr(os, "replace", replace_with_third_then_fail)

    with pytest.raises(VectorStoreCorruptionError):
        store.publish(candidate)


def test_restart_uses_pointer_not_obsolete_generation_or_collection_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    old = store.begin_candidate(NAMESPACE, IDENTITY, "old-document-version", 0)
    store.publish(old)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "new-document-version", 0)

    def retain_obsolete(old_collection: str | None, new_collection: str) -> None:
        assert old_collection == store._candidate_collection_name(old)
        assert new_collection == store._candidate_collection_name(candidate)

    monkeypatch.setattr(store, "_cleanup_obsolete_collection", retain_obsolete)
    store.publish(candidate)
    monkeypatch.setattr(
        store._client,
        "list_collections",
        lambda: (_ for _ in ()).throw(AssertionError("collection ordering must not select active")),
    )

    reopened = _store(tmp_path).inspect_active(NAMESPACE)

    assert reopened.snapshot is not None
    assert reopened.snapshot.document_version == "new-document-version"


def test_active_pointer_requires_canonical_json_and_valid_checksum(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 0)
    store.publish(candidate)
    pointer_path = store._active_pointer_path(NAMESPACE)

    pointer_path.write_bytes(pointer_path.read_bytes() + b"\n")
    with pytest.raises(VectorStoreCorruptionError):
        store.inspect_active(NAMESPACE)

    pointer_path.write_bytes(
        store._active_pointer_bytes(NAMESPACE, store._candidate_collection_name(candidate))
    )
    envelope = json.loads(pointer_path.read_bytes())
    envelope["sha256"] = "0" * 64
    pointer_path.write_bytes(store._canonical_json(envelope))
    with pytest.raises(VectorStoreCorruptionError):
        store.inspect_active(NAMESPACE)


def test_active_pointer_missing_target_is_corruption_not_not_indexed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    missing = store._collection_identifier(NAMESPACE, "missinggeneration")
    store._active_pointer_path(NAMESPACE).write_bytes(
        store._active_pointer_bytes(NAMESPACE, missing)
    )

    with pytest.raises(VectorStoreCorruptionError):
        store.inspect_active(NAMESPACE)


def test_active_pointer_rejects_non_adapter_locator_even_with_valid_checksum(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store._active_pointer_path(NAMESPACE).write_bytes(
        store._active_pointer_bytes(NAMESPACE, "foreign-collection")
    )

    with pytest.raises(VectorStoreCorruptionError):
        store.inspect_active(NAMESPACE)


def test_inspect_active_validates_the_published_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 2)
    store.add_embedded(candidate, _candidate_records())
    store.publish(candidate)
    active = store.inspect_active(NAMESPACE)
    assert active.snapshot is not None
    collection = store._client.get_collection(
        name=active.snapshot._token, embedding_function=None
    )
    collection.update(
        ids=[_candidate_records()[0].chunk_id], metadatas=[{"unexpected": "x"}]
    )

    with pytest.raises(VectorStoreCorruptionError):
        store.inspect_active(NAMESPACE)


def test_add_embedded_prevalidates_all_records_before_candidate_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 2)

    with pytest.raises(VectorStoreWriteError):
        store.add_embedded(candidate, (_candidate_records()[0], object()))  # type: ignore[arg-type]

    assert store._candidate_collection(candidate).count() == 0


def test_add_reused_prevalidates_all_records_before_candidate_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 2)
    malformed = StoredRecord(
        NAMESPACE,
        "a" * 64,
        sha256(b"content").hexdigest(),
        "src/main.py",
        "python",
        "symbol",
        None,
        None,
        None,
        "content",
        EmbeddingVector((1.0,)),
        IDENTITY,
        "document-v1",
        STORAGE_SCHEMA_VERSION,
    )
    valid = StoredRecord(
        NAMESPACE,
        _candidate_records()[1].chunk_id,
        sha256(b"content").hexdigest(),
        "src/main.py",
        "python",
        "symbol",
        None,
        None,
        None,
        "content",
        EmbeddingVector((0.0, 1.0, 0.0)),
        IDENTITY,
        "document-v1",
        STORAGE_SCHEMA_VERSION,
    )

    with pytest.raises(VectorStoreWriteError):
        store.add_reused(candidate, (malformed, valid))

    assert store._candidate_collection(candidate).count() == 0


def test_read_manifest_uses_snapshot_token_without_consulting_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    first = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 2)
    store.add_embedded(first, _candidate_records())
    store.publish(first)
    first_snapshot = store.inspect_active(NAMESPACE).snapshot
    assert first_snapshot is not None

    monkeypatch.setattr(
        store,
        "_read_active_collection_name",
        lambda namespace: (_ for _ in ()).throw(AssertionError("pointer consulted")),
    )

    manifest = store.read_manifest(first_snapshot)

    assert tuple(record.chunk_id for record in manifest) == ("c" * 64, "e" * 64)
    assert all(record.document_version == "document-v1" for record in manifest)


def test_read_manifest_rejects_snapshot_control_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 2)
    store.add_embedded(candidate, _candidate_records())
    store.publish(candidate)
    snapshot = store.inspect_active(NAMESPACE).snapshot
    assert snapshot is not None

    with pytest.raises(VectorStoreCorruptionError):
        store.read_manifest(replace(snapshot, document_version="document-v2"))


def test_inspect_active_maps_backend_read_outage_to_typed_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 0)
    store.publish(candidate)

    def fail_get_collection(*args: object, **kwargs: object) -> object:
        raise RuntimeError("backend unavailable: secret detail")

    monkeypatch.setattr(store._client, "get_collection", fail_get_collection)

    with pytest.raises(VectorStoreReadError, match="^Vector store data could not be read\\.$"):
        store.inspect_active(NAMESPACE)


def test_read_manifest_maps_backend_chroma_outage_to_typed_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, "document-v1", 0)
    store.publish(candidate)
    snapshot = store.inspect_active(NAMESPACE).snapshot
    assert snapshot is not None

    def fail_get_collection(*args: object, **kwargs: object) -> object:
        from chromadb.errors import ChromaError

        raise ChromaError("backend unavailable: secret detail")

    monkeypatch.setattr(store._client, "get_collection", fail_get_collection)

    with pytest.raises(VectorStoreReadError, match="^Vector store data could not be read\\.$"):
        store.read_manifest(snapshot)


def test_persistence_root_is_mandatory_resolved_and_not_inventory_inferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameter = inspect.signature(ChromaVectorStore).parameters["persistence_root"]
    assert parameter.default is inspect.Parameter.empty
    assert tuple(inspect.signature(ChromaVectorStore).parameters) == ("persistence_root",)

    monkeypatch.chdir(tmp_path)
    store = ChromaVectorStore(Path("external") / "chroma")

    assert store._persistence_root == (tmp_path / "external" / "chroma").resolve()
    assert store._persistence_root.is_absolute()


@pytest.mark.parametrize("persistence_root", (None, "", 7))
def test_persistence_root_rejects_unsupported_values(persistence_root: object) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        ChromaVectorStore(persistence_root)  # type: ignore[arg-type]


def test_namespace_mapping_is_deterministic_safe_and_separates_near_namespaces(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = "tracerag-repository-v1:github:example/repo"
    second = "tracerag-repository-v1:github:example/repó"

    first_name = store._collection_identifier(first, "abc123")
    second_name = store._collection_identifier(second, "abc123")

    assert first_name == store._collection_identifier(first, "abc123")
    assert first_name != second_name
    assert re.fullmatch(r"tr5-[0-9a-f]{64}-[a-z0-9]+", first_name)
    assert 3 <= len(first_name) <= 512


@pytest.mark.parametrize("namespace", ("", None, 5))
def test_namespace_mapping_rejects_unsupported_namespaces(
    tmp_path: Path, namespace: object
) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        _store(tmp_path)._collection_identifier(namespace, "abc123")  # type: ignore[arg-type]


@pytest.mark.parametrize("generation_token", ("", "Upper", "bad-token", "λ", 7))
def test_namespace_mapping_rejects_unsafe_generation_tokens(
    tmp_path: Path, generation_token: object
) -> None:
    with pytest.raises(VectorStoreConfigurationError):
        _store(tmp_path)._collection_identifier(NAMESPACE, generation_token)  # type: ignore[arg-type]


def test_record_metadata_round_trip_preserves_every_field_and_exact_content(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _record()

    encoded = store._encode_record(original)
    decoded = store._decode_record(NAMESPACE, **encoded)

    assert decoded == original
    assert encoded["document"] == "def 計算():\r\n    return 'λ  ' \n"
    assert encoded["metadata"]["repository_namespace"] == NAMESPACE
    assert encoded["metadata"]["embedding_dimensions"] == 3


def test_record_metadata_omits_optional_none_keys_and_decodes_them_reversibly(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _record(symbol_kind=None, qualified_name=None, parent_qualified_name=None)

    encoded = store._encode_record(original)
    metadata = encoded["metadata"]
    decoded = store._decode_record(NAMESPACE, **encoded)

    assert "symbol_kind" not in metadata
    assert "qualified_name" not in metadata
    assert "parent_qualified_name" not in metadata
    assert decoded.symbol_kind is None
    assert decoded.qualified_name is None
    assert decoded.parent_qualified_name is None


def test_control_metadata_round_trip_preserves_identity_versions_count_and_dimensions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _snapshot()

    metadata = store._encode_control_metadata(original)
    decoded = store._decode_snapshot_metadata(NAMESPACE, metadata, object())

    assert decoded.repository_namespace == original.repository_namespace
    assert decoded.identity == original.identity
    assert decoded.identity.dimensions == 3
    assert decoded.document_version == original.document_version
    assert decoded.schema_version == STORAGE_SCHEMA_VERSION
    assert decoded.expected_chunk_count == 7


def test_forced_namespace_identifier_collision_still_fails_closed_on_full_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    first = "tracerag-repository-v1:github:example/repo-a"
    second = "tracerag-repository-v1:github:example/repo-b"
    monkeypatch.setattr(store, "_namespace_digest", lambda namespace: "f" * 64)

    assert store._collection_identifier(first, "abc123") == store._collection_identifier(
        second, "abc123"
    )
    encoded = store._encode_record(_record(repository_namespace=first))
    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(second, **encoded)


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("schema_version", "tracerag-chroma-schema-v2"),
        ("embedding_dimensions", 0),
        ("embedding_dimensions", True),
        ("expected_chunk_count", -1),
        ("repository_namespace", "other"),
        ("embedding_provider", ""),
        ("unsupported", {"nested": "value"}),
    ),
)
def test_control_metadata_rejects_unknown_schema_malformed_and_unsupported_values(
    tmp_path: Path, target: str, value: object
) -> None:
    store = _store(tmp_path)
    metadata = store._encode_control_metadata(_snapshot())
    metadata[target] = value

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_snapshot_metadata(NAMESPACE, metadata, object())


@pytest.mark.parametrize("empty", [True, False], ids=["empty", "nonempty"])
def test_acquire_active_rejects_tampered_storage_schema_before_yielding(
    tmp_path: Path, empty: bool
) -> None:
    store = _store(tmp_path)
    snapshot = _publish_records(store, NAMESPACE, () if empty else _candidate_records())
    collection = store._read_collection(NAMESPACE, snapshot._token)
    metadata = dict(collection.metadata)
    metadata["schema_version"] = "unsupported-schema-v2"
    collection.modify(metadata=metadata)

    with pytest.raises(VectorStoreCorruptionError):
        with store.acquire_active(NAMESPACE):
            pytest.fail("adapter yielded an unsupported schema")


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("schema_version", "tracerag-chroma-schema-v2"),
        ("repository_namespace", "other"),
        ("chunk_id", "short"),
        ("qualified_name", ""),
        ("embedding_dimensions", 2),
        ("unsupported", ["not", "a", "field"]),
    ),
)
def test_record_metadata_rejects_unknown_schema_malformed_and_namespace_mismatch(
    tmp_path: Path, target: str, value: object
) -> None:
    store = _store(tmp_path)
    encoded = store._encode_record(_record())
    encoded["metadata"][target] = value

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(NAMESPACE, **encoded)


@pytest.mark.parametrize(
    "embedding",
    (
        (0.25, -0.5),
        (0.25, float("nan"), 0.75),
        (0.25, True, 0.75),
        (0.25, "-0.5", 0.75),
        "bad",
    ),
)
def test_record_metadata_rejects_invalid_embedding_dimensions_and_values(
    tmp_path: Path, embedding: object
) -> None:
    store = _store(tmp_path)
    encoded = store._encode_record(_record())
    encoded["embedding"] = embedding

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(NAMESPACE, **encoded)


def test_external_vector_candidate_reopens_with_exact_evidence_and_no_auto_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailIfCalledEmbeddingFunction:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, input: object) -> object:
            self.calls += 1
            raise AssertionError("Chroma automatic embedding must not run")

    store = _store(tmp_path)
    sentinel = FailIfCalledEmbeddingFunction()
    create_collection = store._client.create_collection

    def create_with_external_embeddings(*args: object, **kwargs: object) -> object:
        assert kwargs["embedding_function"] is None
        collection = create_collection(*args, **kwargs)
        collection._embedding_function = sentinel
        return collection

    monkeypatch.setattr(store._client, "create_collection", create_with_external_embeddings)
    candidate = _candidate()
    records = _candidate_records()

    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)

    reopened = _store(tmp_path)
    manifest = reopened._read_candidate_manifest(candidate)

    assert sentinel.calls == 0
    assert manifest == (
        StoredRecord(
            NAMESPACE,
            records[0].chunk_id,
            records[0].content_hash,
            records[0].relative_path,
            records[0].language,
            records[0].chunk_kind,
            records[0].symbol_kind,
            records[0].qualified_name,
            records[0].parent_qualified_name,
            records[0].content,
            records[0].embedding,
            IDENTITY,
            "tracerag-embedding-document-v1",
            STORAGE_SCHEMA_VERSION,
        ),
        StoredRecord(
            NAMESPACE,
            records[1].chunk_id,
            records[1].content_hash,
            records[1].relative_path,
            records[1].language,
            records[1].chunk_kind,
            records[1].symbol_kind,
            records[1].qualified_name,
            records[1].parent_qualified_name,
            records[1].content,
            records[1].embedding,
            IDENTITY,
            "tracerag-embedding-document-v1",
            STORAGE_SCHEMA_VERSION,
        ),
    )


@pytest.mark.parametrize(
    "candidate,records",
    (
        (
            _candidate(),
            (
                _vector_record("1" * 64, "wrong dimensions", (1.0, 0.0)),
                _candidate_records()[1],
            ),
        ),
        (
            _candidate(),
            (_candidate_records()[0], _candidate_records()[0]),
        ),
        (
            replace(_candidate(), expected_chunk_count=1),
            _candidate_records(),
        ),
        (
            _candidate(),
            (object(), _candidate_records()[1]),
        ),
    ),
    ids=("wrong_dimensions", "duplicate_ids", "count_mismatch", "malformed_records"),
)
def test_external_vector_candidate_write_fails_closed_before_partial_persistence(
    tmp_path: Path, candidate: CandidateIndex, records: tuple[object, ...]
) -> None:
    store = _store(tmp_path)
    store._create_candidate_collection(candidate)

    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, records)  # type: ignore[arg-type]

    assert store._candidate_collection(candidate).count() == 0


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("embedding_provider", "other-provider"),
        ("document_version", "other-document-version"),
        ("schema_version", "tracerag-chroma-schema-v2"),
    ),
    ids=("identity", "document_version", "schema_version"),
)
def test_manifest_rejects_record_metadata_inconsistent_with_candidate_control(
    tmp_path: Path, target: str, value: str
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = _candidate_records()
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)
    store._candidate_collection(candidate).update(
        ids=[records[0].chunk_id], metadatas=[{target: value}]
    )

    with pytest.raises(VectorStoreCorruptionError):
        store._read_candidate_manifest(candidate)


def test_huge_integer_embedding_fails_closed_as_corruption_error(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    encoded = store._encode_record(_record())
    encoded["embedding"] = (10**10_000, 0.0, 0.0)

    with pytest.raises(VectorStoreCorruptionError):
        store._decode_record(NAMESPACE, **encoded)


def test_huge_integer_candidate_embedding_fails_closed_as_write_error(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    candidate = _candidate()
    store._create_candidate_collection(candidate)
    huge = _vector_record("1" * 64, "huge", (10**10_000, 0.0, 0.0))
    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, (huge, _candidate_records()[1]))


def test_non_unit_external_vectors_reopen_as_chroma_authoritative_values(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = (
        _vector_record("1" * 64, "first", (0.1, -0.2, 0.3)),
        _vector_record("3" * 64, "second", (0.4, 0.5, -0.6)),
    )
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)

    reopened = _store(tmp_path)
    manifest = reopened._read_candidate_manifest(candidate)
    physical = reopened._candidate_collection(candidate).get(
        include=["embeddings", "metadatas"]
    )

    assert "embedding_values" not in physical["metadatas"][0]
    assert tuple(record.embedding.values for record in manifest) == tuple(
        tuple(float(value) for value in row) for row in physical["embeddings"]
    )
    assert manifest[0].embedding != records[0].embedding


def test_finite_values_that_overflow_binary32_fail_before_candidate_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    store._create_candidate_collection(candidate)
    overflowing = _vector_record("1" * 64, "overflow", (3.5e38, 0.0, 0.0))

    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, (overflowing, _candidate_records()[1]))

    assert store._candidate_collection(candidate).count() == 0


@pytest.mark.parametrize(
    "values",
    (
        (0.5, -0.25, 0.125),
        (0.1, -0.2, 0.3),
        (-0.7, -1.1, 0.9),
        (1e-30, -3e-20, 2e-10),
        (0.5773502691896258, -0.5773502691896258, 0.5773502691896258),
    ),
    ids=("exact_f32", "ordinary", "negative", "small", "normalized_like"),
)
def test_projected_external_vectors_survive_fresh_child_process_reopen(
    tmp_path: Path, values: tuple[float, float, float]
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = (
        _vector_record("1" * 64, "first", values),
        _candidate_records()[1],
    )
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)
    collection_name = store._candidate_collection_name(candidate)
    parent_values = tuple(
        float(value)
        for value in store._candidate_collection(candidate).get(
            ids=[records[0].chunk_id], include=["embeddings"]
        )["embeddings"][0]
    )
    script = (
        "import chromadb,json,math,sys; from chromadb.config import Settings; "
        "c=chromadb.PersistentClient(path=sys.argv[1],settings=Settings(anonymized_telemetry=False)); "
        "r=c.get_collection(sys.argv[2],embedding_function=None).get(include=['embeddings']); "
        "v=[float(x) for x in r['embeddings'][0]]; assert len(v)==3 and all(math.isfinite(x) for x in v); print(json.dumps(v))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(store._persistence_root), collection_name],
        check=True,
        capture_output=True,
        text=True,
    )
    reopened_values = tuple(json.loads(result.stdout))

    assert len(reopened_values) == len(values)
    assert all(math.isfinite(value) for value in reopened_values)
    assert reopened_values == parent_values
    if values == (0.5, -0.25, 0.125):
        assert reopened_values == values


@pytest.mark.parametrize("invalid", (float("nan"), float("inf"), float("-inf")))
def test_nonfinite_provider_vectors_fail_before_candidate_mutation(
    tmp_path: Path, invalid: float
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    store._create_candidate_collection(candidate)
    invalid_record = _vector_record("1" * 64, "invalid", (invalid, 0.0, 0.0))

    with pytest.raises(VectorStoreWriteError):
        store._write_embedded_candidate(candidate, (invalid_record, _candidate_records()[1]))

    assert store._candidate_collection(candidate).count() == 0


def test_external_vector_records_have_no_vector_metadata_mirror_or_sidecar(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, _candidate_records())
    physical = store._candidate_collection(candidate).get(include=["metadatas"])
    required = {
        "schema_version", "repository_namespace", "chunk_id", "content_hash",
        "relative_path", "language", "chunk_kind", "embedding_provider",
        "embedding_model", "embedding_dimensions", "embedding_compatibility_version",
        "document_version", "symbol_kind", "qualified_name", "parent_qualified_name",
    }

    for metadata in physical["metadatas"]:
        assert set(metadata) == required
        assert all(not isinstance(value, list | dict) for value in metadata.values())
        assert "embedding_values" not in metadata
    assert not [path for path in store._persistence_root.rglob("*") if path.is_file() and path.suffix in {".json", ".npy", ".npz"}]


def test_direct_chroma_query_uses_persisted_external_embedding_field(tmp_path: Path) -> None:
    store = _store(tmp_path)
    candidate = _candidate()
    records = _candidate_records()
    store._create_candidate_collection(candidate)
    store._write_embedded_candidate(candidate, records)
    collection = store._candidate_collection(candidate)
    persisted = collection.get(ids=[records[0].chunk_id], include=["embeddings"])["embeddings"][0]
    result = collection.query(query_embeddings=[list(persisted)], n_results=2, include=["distances"])

    assert result["ids"][0][0] == records[0].chunk_id
    assert abs(result["distances"][0][0]) <= 1e-6


def test_search_converts_cosine_landmarks_and_decodes_exact_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        4,
    )
    records = (
        _vector_record("1" * 64, "identical\r\n", (1.0, 0.0, 0.0)),
        _vector_record("3" * 64, "intermediate λ\n", (1.0, 1.0, 0.0)),
        _vector_record("5" * 64, "orthogonal\n", (0.0, 1.0, 0.0)),
        _vector_record("7" * 64, "opposite\n", (-1.0, 0.0, 0.0)),
    )
    store.add_embedded(candidate, records)
    snapshot = store.publish(candidate)

    results = store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 4)

    by_id = {result.chunk_id: result for result in results}
    assert tuple(result.score for result in results) == tuple(
        sorted((result.score for result in results), reverse=True)
    )
    assert by_id["1" * 64].score == 1.0
    assert by_id["3" * 64].score == pytest.approx(
        0.8535533905932737, abs=1e-6
    )
    assert by_id["5" * 64].score == pytest.approx(0.5, abs=1e-6)
    assert by_id["7" * 64].score == 0.0
    assert by_id["1" * 64] == StoreSearchResult(
        repository_namespace=NAMESPACE,
        chunk_id="1" * 64,
        content_hash=sha256(b"identical\r\n").hexdigest(),
        relative_path="src/資料/δelta.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name="資料.計算",
        parent_qualified_name="資料",
        content="identical\r\n",
        score=1.0,
    )
    for private_name in (
        "distance",
        "embedding",
        "vector",
        "generation",
        "collection",
        "_token",
    ):
        assert not hasattr(by_id["1" * 64], private_name)


def test_search_uses_chroma_authoritative_persisted_binary32_vector(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )
    records = (
        _vector_record("1" * 64, "ordinary", (0.1, -0.2, 0.3)),
        _vector_record("3" * 64, "other", (-0.7, -1.1, 0.9)),
    )
    store.add_embedded(candidate, records)
    snapshot = store.publish(candidate)
    collection = store._read_collection(NAMESPACE, snapshot._token)
    persisted = tuple(
        float(value)
        for value in collection.get(
            ids=[records[0].chunk_id], include=["embeddings"]
        )["embeddings"][0]
    )

    results = store.search(snapshot, EmbeddingVector(persisted), 1)

    assert persisted != records[0].embedding.values
    assert tuple(result.chunk_id for result in results) == (records[0].chunk_id,)
    assert results[0].score == 1.0


def test_search_queries_only_supplied_snapshot_and_bounds_n_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        2,
    )
    store.add_embedded(candidate, _candidate_records())
    snapshot = store.publish(candidate)
    collection = store._read_collection(NAMESPACE, snapshot._token)
    query_calls: list[dict[str, object]] = []
    original_query = collection.query

    def recording_query(**kwargs: object) -> object:
        query_calls.append(kwargs)
        return original_query(**kwargs)

    monkeypatch.setattr(collection, "query", recording_query)

    def read_only_snapshot_collection(
        repository_namespace: str, collection_name: object
    ) -> object:
        assert repository_namespace == snapshot.repository_namespace
        assert collection_name == snapshot._token
        return collection

    monkeypatch.setattr(store, "_read_collection", read_only_snapshot_collection)
    monkeypatch.setattr(
        store,
        "_read_active_collection_name",
        lambda namespace: (_ for _ in ()).throw(AssertionError("pointer consulted")),
    )

    results = store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)

    assert len(results) == 1
    assert query_calls == [
        {
            "query_embeddings": [[1.0, 0.0, 0.0]],
            "n_results": 1,
            "include": ["documents", "metadatas", "distances"],
        }
    ]


@pytest.mark.parametrize(
    ("distance", "expected_score"),
    ((-0.0000005, 1.0), (2.0000005, 0.0)),
    ids=("near_zero", "near_two"),
)
def test_search_maps_only_in_tolerance_cosine_endpoint_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distance: float,
    expected_score: float,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        1,
    )
    record = _candidate_records()[0]
    store.add_embedded(candidate, (record,))
    snapshot = store.publish(candidate)
    collection = store._read_collection(NAMESPACE, snapshot._token)
    physical = collection.get(include=["documents", "metadatas"])

    class DistanceCollection:
        metadata = collection.metadata

        @staticmethod
        def count() -> int:
            return 1

        @staticmethod
        def query(**kwargs: object) -> dict[str, object]:
            return {
                "ids": [[record.chunk_id]],
                "documents": [[physical["documents"][0]]],
                "metadatas": [[physical["metadatas"][0]]],
                "distances": [[distance]],
            }

    monkeypatch.setattr(store, "_read_collection", lambda *args: DistanceCollection())

    result = store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)

    assert result[0].score == expected_score


@pytest.mark.parametrize(
    "distance",
    (-0.0000011, 2.0000011, float("nan"), float("inf"), float("-inf")),
    ids=("below_zero", "above_two", "nan", "positive_infinity", "negative_infinity"),
)
def test_search_rejects_out_of_domain_or_nonfinite_cosine_distance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distance: float,
) -> None:
    store = _store(tmp_path)
    candidate = store.begin_candidate(
        NAMESPACE,
        IDENTITY,
        "tracerag-embedding-document-v1",
        1,
    )
    record = _candidate_records()[0]
    store.add_embedded(candidate, (record,))
    snapshot = store.publish(candidate)
    collection = store._read_collection(NAMESPACE, snapshot._token)
    physical = collection.get(include=["documents", "metadatas"])

    class DistanceCollection:
        metadata = collection.metadata

        @staticmethod
        def count() -> int:
            return 1

        @staticmethod
        def query(**kwargs: object) -> dict[str, object]:
            return {
                "ids": [[record.chunk_id]],
                "documents": [[physical["documents"][0]]],
                "metadatas": [[physical["metadatas"][0]]],
                "distances": [[distance]],
            }

    monkeypatch.setattr(store, "_read_collection", lambda *args: DistanceCollection())

    with pytest.raises(VectorStoreCorruptionError):
        store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)


@pytest.mark.parametrize("top_k", (0, -1, True, 1.0, "1"))
def test_search_rejects_invalid_top_k_before_opening_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    top_k: object,
) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot(expected_chunk_count=1)
    monkeypatch.setattr(
        store,
        "_read_collection",
        lambda *args: (_ for _ in ()).throw(AssertionError("collection opened")),
    )

    with pytest.raises(VectorStoreReadError):
        store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), top_k)  # type: ignore[arg-type]


def test_search_rejects_invalid_snapshot_before_opening_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(
        store,
        "_read_collection",
        lambda *args: (_ for _ in ()).throw(AssertionError("collection opened")),
    )

    with pytest.raises(VectorStoreReadError):
        store.search(object(), EmbeddingVector((1.0, 0.0, 0.0)), 1)  # type: ignore[arg-type]
