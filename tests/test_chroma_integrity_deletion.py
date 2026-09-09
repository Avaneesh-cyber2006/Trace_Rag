"""Regressions for authoritative evidence and exact-namespace deletion."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Event

import pytest

from backend.code_chunker.models import (
    ChunkFileStatus, ChunkKind, ChunkedFile, CodeChunk, CodeChunkInventory,
)
from backend.code_parser.models import ParsedLanguage, ParseStatus, SourceLocation
from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import (
    EmbeddingVectorStoreConfigurationError, VectorStoreConfigurationError,
    VectorStoreCorruptionError, VectorStoreReadError, VectorStoreWriteError,
)
from backend.embedding_vector_store.models import EmbeddingModelIdentity, EmbeddingVector
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.embedding_vector_store.synchronization import SemanticIndexer


NAMESPACE = "repository:exact"
IDENTITY = EmbeddingModelIdentity("offline", "test", 3, "v1")


class Provider:
    identity = IDENTITY
    max_batch_size = 2

    def __init__(self):
        self.calls = 0

    def embed_documents(self, documents):
        self.calls += 1
        return tuple(EmbeddingVector((1.0, 0.0, 0.0)) for _ in documents)

    def embed_query(self, text):
        return EmbeddingVector((1.0, 0.0, 0.0))


def inventory(contents=("# exact λ\r\nprint('one')  \n",), namespace=NAMESPACE):
    chunks = tuple(
        CodeChunk(
            f"{index + 1:064x}", ChunkKind.CONTEXT,
            SourceLocation(index * 100, index * 100 + len(content.encode("utf-8")),
                           index + 1, 0, index + 1, len(content)),
            content, sha256(content.encode("utf-8")).hexdigest(),
            None, None, None, None, (), None, (), (), (), 0, 1,
        )
        for index, content in enumerate(contents)
    )
    file = ChunkedFile("src/main.py", ParsedLanguage.PYTHON, ParseStatus.SUCCESS,
                       ChunkFileStatus.SUCCESS, "0" * 64, (), chunks, ())
    return CodeChunkInventory(".", namespace, 1, 1, 0, 0, len(chunks), (file,))


def indexed_store(tmp_path):
    store = ChromaVectorStore(tmp_path / "external")
    provider = Provider()
    SemanticIndexer(provider, store).synchronize(inventory())
    snapshot = store.inspect_active(NAMESPACE).snapshot
    assert snapshot is not None
    return store, provider, snapshot


@pytest.mark.parametrize("operation", ["inspect", "manifest", "sync_noop", "sync_reuse"])
def test_tampered_document_with_old_valid_hash_fails_before_reuse_or_publication(
    tmp_path, operation,
):
    store, provider, snapshot = indexed_store(tmp_path)
    collection = store._client.get_collection(snapshot._token, embedding_function=None)
    collection.update(ids=["1".zfill(64)], documents=["tampered content"],
                      embeddings=[[1.0, 0.0, 0.0]])
    pointer_before = store._active_pointer_path(NAMESPACE).read_bytes()
    names_before = {item.name for item in store._client.list_collections()}
    calls_before = provider.calls

    with pytest.raises(VectorStoreCorruptionError):
        if operation == "inspect":
            store.inspect_active(NAMESPACE)
        elif operation == "manifest":
            store.read_manifest(snapshot)
        else:
            current = inventory() if operation == "sync_noop" else inventory(
                ("# exact λ\r\nprint('one')  \n", "print('new')\n")
            )
            SemanticIndexer(provider, store).synchronize(current)

    assert provider.calls == calls_before
    assert store._active_pointer_path(NAMESPACE).read_bytes() == pointer_before
    assert {item.name for item in store._client.list_collections()} == names_before


@pytest.mark.parametrize("written", [0, 1])
@pytest.mark.parametrize("has_active", [False, True])
def test_delete_repository_index_removes_incomplete_abandoned_candidates(
    tmp_path, written, has_active,
):
    store, provider, snapshot = indexed_store(tmp_path)
    records = store.read_manifest(snapshot)
    if not has_active:
        store.delete_repository_index(NAMESPACE)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 2)
    store.add_reused(candidate, records[:written])
    other_inventory = inventory(namespace=NAMESPACE + "-other")
    SemanticIndexer(provider, store).synchronize(other_inventory)
    other_before = store.inspect_active(other_inventory.repository_namespace)

    store.delete_repository_index(NAMESPACE)

    assert store.inspect_active(NAMESPACE).indexed is False
    assert store.inspect_active(other_inventory.repository_namespace) == other_before
    assert {item.name for item in store._client.list_collections()} == {
        other_before.snapshot._token,
    }


@pytest.mark.parametrize("evidence", ["control", "record"])
def test_delete_repository_index_rejects_foreign_namespace_in_incomplete_candidate(
    tmp_path, evidence,
):
    store, _, snapshot = indexed_store(tmp_path)
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 2)
    store.add_reused(candidate, store.read_manifest(snapshot))
    collection = store._candidate_collection(candidate)
    if evidence == "control":
        collection.modify(metadata={**collection.metadata, "repository_namespace": "foreign"})
    else:
        collection.update(ids=["1".zfill(64)], metadatas=[{"repository_namespace": "foreign"}])
    names_before = {item.name for item in store._client.list_collections()}
    pointer_before = store._active_pointer_path(NAMESPACE).read_bytes()

    with pytest.raises(VectorStoreCorruptionError):
        store.delete_repository_index(NAMESPACE)

    assert {item.name for item in store._client.list_collections()} == names_before
    assert store._active_pointer_path(NAMESPACE).read_bytes() == pointer_before


def test_delete_repository_index_rejects_incomplete_active_before_any_mutation(tmp_path):
    store, _, snapshot = indexed_store(tmp_path)
    collection = store._client.get_collection(snapshot._token, embedding_function=None)
    collection.delete(ids=["1".zfill(64)])
    pointer_before = store._active_pointer_path(NAMESPACE).read_bytes()

    with pytest.raises(VectorStoreCorruptionError):
        store.delete_repository_index(NAMESPACE)

    assert store._active_pointer_path(NAMESPACE).read_bytes() == pointer_before
    assert {item.name for item in store._client.list_collections()} == {snapshot._token}


def test_delete_repository_index_is_busy_while_active_reader_is_leased(tmp_path):
    store, _, snapshot = indexed_store(tmp_path)
    deleting_store = ChromaVectorStore(tmp_path / "external")
    entered, release = Event(), Event()

    def read():
        with store.acquire_active(NAMESPACE) as state:
            entered.set()
            assert release.wait(10)
            return store.search(state.snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(read)
        try:
            assert entered.wait(10)
            with pytest.raises(VectorStoreConfigurationError):
                deleting_store.delete_repository_index(NAMESPACE)
            assert store.inspect_active(NAMESPACE).snapshot == snapshot
        finally:
            release.set()
        assert reader.result(timeout=10)[0].content == inventory().files[0].chunks[0].content
    deleting_store.delete_repository_index(NAMESPACE)
    assert store.inspect_active(NAMESPACE).indexed is False


def test_delete_repository_index_is_busy_while_obsolete_reader_is_leased(tmp_path):
    store, provider, old_snapshot = indexed_store(tmp_path)
    with store.acquire_active(NAMESPACE) as state:
        SemanticIndexer(provider, store).synchronize(inventory(("print('new')\n",)))
        new_snapshot = store.inspect_active(NAMESPACE).snapshot
        with pytest.raises(VectorStoreConfigurationError):
            store.delete_repository_index(NAMESPACE)
        assert store.inspect_active(NAMESPACE).snapshot == new_snapshot
        assert store.search(state.snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)
    assert old_snapshot._token not in {item.name for item in store._client.list_collections()}
    store.delete_repository_index(NAMESPACE)
    assert store._client.list_collections() == []


def test_delete_repository_index_shares_sync_writer_guard_across_instances(tmp_path, monkeypatch):
    store, provider, snapshot = indexed_store(tmp_path)
    deleting_store = ChromaVectorStore(tmp_path / "external")
    entered, release = Event(), Event()
    original_inspect = store.inspect_active

    def paused_inspect(namespace):
        entered.set()
        assert release.wait(10)
        return original_inspect(namespace)

    monkeypatch.setattr(store, "inspect_active", paused_inspect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        writer = executor.submit(SemanticIndexer(provider, store).synchronize, inventory())
        try:
            assert entered.wait(10)
            with pytest.raises(EmbeddingVectorStoreConfigurationError):
                deleting_store.delete_repository_index(NAMESPACE)
            assert deleting_store.inspect_active(NAMESPACE).snapshot == snapshot
        finally:
            release.set()
        writer.result(timeout=10)
    deleting_store.delete_repository_index(NAMESPACE)
    assert deleting_store.inspect_active(NAMESPACE).indexed is False


def test_delete_repository_index_holds_writer_guard_until_cleanup_finishes(tmp_path, monkeypatch):
    store, provider, snapshot = indexed_store(tmp_path)
    competing_store = ChromaVectorStore(tmp_path / "external")
    entered, release = Event(), Event()
    original_delete = store._client.delete_collection

    def paused_delete(name):
        if name == snapshot._token:
            entered.set()
            assert release.wait(10)
        original_delete(name)

    monkeypatch.setattr(store._client, "delete_collection", paused_delete)
    with ThreadPoolExecutor(max_workers=1) as executor:
        deletion = executor.submit(store.delete_repository_index, NAMESPACE)
        try:
            assert entered.wait(10)
            with pytest.raises(EmbeddingVectorStoreConfigurationError):
                SemanticIndexer(provider, competing_store).synchronize(inventory())
            with pytest.raises(EmbeddingVectorStoreConfigurationError):
                competing_store.delete_repository_index(NAMESPACE)
            with pytest.raises(VectorStoreReadError):
                competing_store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)
            SemanticIndexer(provider, competing_store).synchronize(
                inventory(namespace=NAMESPACE + "-other")
            )
        finally:
            release.set()
        deletion.result(timeout=10)
    SemanticIndexer(provider, competing_store).synchronize(inventory())
    assert competing_store.inspect_active(NAMESPACE).indexed is True


def test_delete_repository_index_rechecks_readers_after_collection_validation(tmp_path, monkeypatch):
    store, _, snapshot = indexed_store(tmp_path)
    entered, release = Event(), Event()
    original_names = store._repository_collection_names

    def paused_validation(*args):
        names = original_names(*args)
        entered.set()
        assert release.wait(10)
        return names

    monkeypatch.setattr(store, "_repository_collection_names", paused_validation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        deletion = executor.submit(store.delete_repository_index, NAMESPACE)
        try:
            assert entered.wait(10)
            with store.acquire_active(NAMESPACE) as state:
                release.set()
                with pytest.raises(VectorStoreConfigurationError):
                    deletion.result(timeout=10)
                assert store.search(state.snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)
        finally:
            release.set()
    assert store.inspect_active(NAMESPACE).snapshot == snapshot


@pytest.mark.parametrize("boundary", ["pointer", "collection"])
def test_delete_repository_index_releases_guard_and_reservations_after_failure(
    tmp_path, monkeypatch, boundary,
):
    store, _, snapshot = indexed_store(tmp_path)
    pointer_path = store._active_pointer_path(NAMESPACE)
    path_type = type(pointer_path)
    original_unlink = path_type.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == pointer_path:
            raise OSError("injected failure")
        return original_unlink(path, *args, **kwargs)

    def fail_delete(name):
        raise RuntimeError("injected failure")

    with monkeypatch.context() as patch:
        if boundary == "pointer":
            patch.setattr(path_type, "unlink", fail_unlink)
        else:
            patch.setattr(store._client, "delete_collection", fail_delete)
        with pytest.raises(VectorStoreWriteError):
            store.delete_repository_index(NAMESPACE)

    # The failed backend removal leaves the collection available. The private
    # handle must be usable again and a subsequent administrative delete succeeds.
    assert store.search(snapshot, EmbeddingVector((1.0, 0.0, 0.0)), 1)
    store.delete_repository_index(NAMESPACE)
    assert store.inspect_active(NAMESPACE).indexed is False
    assert store._client.list_collections() == []


def test_reused_record_with_changed_content_is_rejected_before_candidate_mutation(tmp_path):
    store, _, snapshot = indexed_store(tmp_path)
    record = store.read_manifest(snapshot)[0]
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 1)

    with pytest.raises(VectorStoreWriteError):
        store.add_reused(candidate, (replace(record, content=record.content.replace("\r\n", "\n")),))

    assert store._candidate_collection(candidate).count() == 0
    assert store.inspect_active(NAMESPACE).snapshot == snapshot
