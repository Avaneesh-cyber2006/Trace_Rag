"""Offline integration coverage from repository scanning through semantic search."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import struct

from backend.code_chunker import ChunkKind, ChunkerConfig, CodeChunker
from backend.code_parser import CodeParser, ParsedLanguage
from backend.embedding_vector_store.models import (
    EmbeddingDocument,
    EmbeddingModelIdentity,
    EmbeddingVector,
    IndexSyncStatus,
)
from backend.embedding_vector_store.search import SemanticSearcher
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.embedding_vector_store.synchronization import SemanticIndexer
from backend.file_scanner import FileScanner


IDENTITY = EmbeddingModelIdentity(
    "offline-test",
    "deterministic-three-dimensional",
    3,
    "integration-v1",
)
CHUNKER_CONFIG = ChunkerConfig(target_chunk_bytes=96, max_chunk_bytes=128)
MAX_TOP_K = 100


def _deterministic_vector(text: str) -> tuple[float, float, float]:
    """Derive stable, non-binary32 values from the exact embedding document."""
    digest = sha256(text.encode("utf-8")).digest()
    return tuple(
        (int.from_bytes(digest[offset : offset + 2], "big") + 1) / 65_537
        for offset in (0, 2, 4)
    )


def _binary32(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(struct.unpack("!f", struct.pack("!f", value))[0] for value in values)


def _positive_binary32_ulp_distance(left: float, right: float) -> int:
    """Return binary32 ULP distance for this fixture's positive components."""
    left_bits = struct.unpack("!I", struct.pack("!f", left))[0]
    right_bits = struct.unpack("!I", struct.pack("!f", right))[0]
    return abs(left_bits - right_bits)


class DeterministicProvider:
    """Small offline provider whose non-binary32 values expose store projection."""

    def __init__(self) -> None:
        self.document_calls: list[tuple[EmbeddingDocument, ...]] = []
        self.query_calls: list[str] = []

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return IDENTITY

    @property
    def max_batch_size(self) -> int:
        return 4

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]:
        self.document_calls.append(documents)
        return tuple(
            EmbeddingVector(_deterministic_vector(document.text))
            for document in documents
        )

    def embed_query(self, query_text: str) -> EmbeddingVector:
        self.query_calls.append(query_text)
        return EmbeddingVector(_deterministic_vector(query_text))


def _write(repository: Path, relative_path: str, content: bytes) -> None:
    destination = repository / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def _write_multilanguage_repository(repository: Path) -> dict[str, bytes]:
    sources = {
        "src/python_service.py": (
            b"# Python context: \xce\xbb\r\n"
            b"CONTEXT_VALUE = 1\r\n\r\n"
            b"def stable_target(value: int) -> int:\r\n"
            b"    return value + 10\r\n"
        ),
        "src/Service.java": (
            b"// Java context\n"
            b"package demo;\n"
            b"public class Service {\n"
            b"    public int compute(int value) {\n"
            b"        int total = value;\n"
            b"        total += 1;\n"
            b"        total += 2;\n"
            b"        total += 3;\n"
            b"        total += 4;\n"
            b"        total += 5;\n"
            b"        total += 6;\n"
            b"        total += 7;\n"
            b"        total += 8;\n"
            b"        return total;\n"
            b"    }\n"
            b"}\n"
        ),
        "src/removed.js": (
            b"// JavaScript context\n"
            b"export function removedTarget(value) {\n"
            b"  return value + 1;\n"
            b"}\n"
        ),
        "src/typed.ts": (
            b"// TypeScript context\n"
            b"export interface Runner { run(value: number): number; }\n"
            b"export function typedTarget(value: number): number {\n"
            b"  return value * 2;\n"
            b"}\n"
        ),
        "src/view.tsx": (
            b"// TSX context\n"
            b"export function View(props: {label: string}) {\n"
            b"  return <section>{props.label}</section>;\n"
            b"}\n"
        ),
        "src/fragmented.py": (
            b"# Fragment context\n"
            b"def long_running_total(value: int) -> int:\n"
            b"    total = value\n"
            b"    total += 1\n"
            b"    total += 2\n"
            b"    total += 3\n"
            b"    total += 4\n"
            b"    total += 5\n"
            b"    total += 6\n"
            b"    total += 7\n"
            b"    total += 8\n"
            b"    total += 9\n"
            b"    return total\n"
        ),
    }
    for relative_path, content in sources.items():
        _write(repository, relative_path, content)
    # These are supported source types, but the scanner must prune their directories.
    # They intentionally remain after the visible sources are removed for empty sync.
    _write(repository, "node_modules/ignored.ts", b"export const ignored = true;\n")
    _write(repository, ".git/ignored.py", b"IGNORED = True\n")
    return sources


def _pipeline(repository: Path, namespace: str):
    scanned = FileScanner().scan(repository, repository_namespace=namespace)
    parsed = CodeParser().parse_inventory(scanned)
    return CodeChunker(CHUNKER_CONFIG).chunk_inventory(scanned, parsed)


def _expected_records(inventory) -> dict[str, dict[str, object]]:
    return {
        chunk.chunk_id: {
            "content_hash": chunk.content_hash,
            "relative_path": file.relative_path,
            "language": file.language.value,
            "chunk_kind": chunk.kind.value,
            "symbol_kind": (
                None if chunk.symbol_kind is None else chunk.symbol_kind.value
            ),
            "qualified_name": chunk.qualified_name,
            "parent_qualified_name": chunk.parent_qualified_name,
            "content": chunk.content,
        }
        for file in inventory.files
        for chunk in file.chunks
    }


def _assert_exact_records(records, inventory, namespace: str) -> None:
    expected = _expected_records(inventory)
    assert {record.chunk_id for record in records} == set(expected)
    assert len(records) == inventory.total_chunks
    for record in records:
        authoritative = expected[record.chunk_id]
        assert record.repository_namespace == namespace
        assert record.content_hash == authoritative["content_hash"]
        assert record.content_hash == sha256(record.content.encode("utf-8")).hexdigest()
        assert record.relative_path == authoritative["relative_path"]
        assert record.language == authoritative["language"]
        assert record.chunk_kind == authoritative["chunk_kind"]
        assert record.symbol_kind == authoritative["symbol_kind"]
        assert record.qualified_name == authoritative["qualified_name"]
        assert record.parent_qualified_name == authoritative["parent_qualified_name"]
        assert record.content == authoritative["content"]
        assert record.content.encode("utf-8") == authoritative["content"].encode(
            "utf-8"
        )


def _manifest(store: ChromaVectorStore, namespace: str):
    state = store.inspect_active(namespace)
    assert state.indexed is True
    assert state.snapshot is not None
    return state.snapshot, store.read_manifest(state.snapshot)


def test_real_multilanguage_inventory_round_trips_through_reopen_and_search(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    sources = _write_multilanguage_repository(repository)
    inventory = _pipeline(repository, "integration-repository")

    assert inventory.success_files == 6
    assert inventory.failed_files == 0
    assert {file.relative_path for file in inventory.files} == set(sources)
    assert {file.language for file in inventory.files} == {
        ParsedLanguage.PYTHON,
        ParsedLanguage.JAVA,
        ParsedLanguage.JAVASCRIPT,
        ParsedLanguage.TYPESCRIPT,
        ParsedLanguage.TSX,
    }
    assert {chunk.kind for file in inventory.files for chunk in file.chunks} >= {
        ChunkKind.SYMBOL,
        ChunkKind.CONTEXT,
        ChunkKind.FRAGMENT,
    }

    provider = DeterministicProvider()
    persistence_root = tmp_path / "persistent-chroma"
    initial = SemanticIndexer(
        provider, ChromaVectorStore(persistence_root)
    ).synchronize(inventory)

    assert initial.status is IndexSyncStatus.SUCCESS
    assert initial.total_chunks == inventory.total_chunks
    assert initial.inserted_chunks == inventory.total_chunks
    assert initial.updated_chunks == 0
    assert initial.deleted_chunks == 0
    assert sum(len(batch) for batch in provider.document_calls) == inventory.total_chunks
    submitted_documents = tuple(
        document for batch in provider.document_calls for document in batch
    )
    assert tuple(document.chunk_id for document in submitted_documents) == tuple(
        chunk.chunk_id for file in inventory.files for chunk in file.chunks
    )
    expected = _expected_records(inventory)
    for document in submitted_documents:
        assert document.text.endswith(
            "\n---TRACERAG-SOURCE---\n" + expected[document.chunk_id]["content"]
        )
    submitted_vectors = {
        document.chunk_id: _deterministic_vector(document.text)
        for document in submitted_documents
    }

    reopened = ChromaVectorStore(persistence_root)
    snapshot, records = _manifest(reopened, "integration-repository")
    _assert_exact_records(records, inventory, "integration-repository")

    physical = reopened._client.get_collection(
        name=snapshot._token, embedding_function=None
    ).get(include=["embeddings"])
    physical_vectors = {
        chunk_id: tuple(float(value) for value in embedding)
        for chunk_id, embedding in zip(
            physical["ids"], physical["embeddings"], strict=True
        )
    }
    assert {
        record.chunk_id: record.embedding.values for record in records
    } == physical_vectors
    for record in records:
        submitted_projection = _binary32(submitted_vectors[record.chunk_id])
        assert all(
            _positive_binary32_ulp_distance(persisted, submitted) <= 4
            for persisted, submitted in zip(
                record.embedding.values, submitted_projection, strict=True
            )
        )
    assert all(
        record.embedding.values != submitted_vectors[record.chunk_id]
        for record in records
    )

    assert 0 < inventory.total_chunks <= MAX_TOP_K
    results = SemanticSearcher(provider, reopened).search(
        "integration-repository", "find every exact source chunk", inventory.total_chunks
    )
    assert {result.chunk_id for result in results} == set(expected)
    for result in results:
        authoritative = expected[result.chunk_id]
        assert result.content_hash == authoritative["content_hash"]
        assert result.relative_path == authoritative["relative_path"]
        assert result.language == authoritative["language"]
        assert result.chunk_kind == authoritative["chunk_kind"]
        assert result.symbol_kind == authoritative["symbol_kind"]
        assert result.qualified_name == authoritative["qualified_name"]
        assert result.parent_qualified_name == authoritative["parent_qualified_name"]
        assert result.content == authoritative["content"]
        assert result.content.encode("utf-8") == authoritative["content"].encode(
            "utf-8"
        )
    assert provider.query_calls == ["find every exact source chunk"]

    provider.document_calls.clear()
    unchanged = SemanticIndexer(provider, reopened).synchronize(inventory)
    unchanged_snapshot, unchanged_records = _manifest(reopened, "integration-repository")
    assert unchanged.status is IndexSyncStatus.UNCHANGED
    assert unchanged.reused_chunks == inventory.total_chunks
    assert unchanged.embedded_chunks == 0
    assert provider.document_calls == []
    assert unchanged_snapshot._token == snapshot._token
    assert unchanged_records == records


def test_real_pipeline_mutation_classifies_update_delete_add_then_empty(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    sources = _write_multilanguage_repository(repository)
    namespace = "mutable-repository"
    before = _pipeline(repository, namespace)
    before_expected = _expected_records(before)

    provider = DeterministicProvider()
    store = ChromaVectorStore(tmp_path / "persistent-chroma")
    SemanticIndexer(provider, store).synchronize(before)

    mutated_python = sources["src/python_service.py"].replace(b"value + 10", b"value + 20")
    assert len(mutated_python) == len(sources["src/python_service.py"])
    _write(repository, "src/python_service.py", mutated_python)
    (repository / "src/removed.js").unlink()
    _write(
        repository,
        "src/added.py",
        b"# Added context\ndef added_target(value: int) -> int:\n    return value - 1\n",
    )

    after = _pipeline(repository, namespace)
    after_expected = _expected_records(after)
    common_ids = set(before_expected) & set(after_expected)
    expected_updated = {
        chunk_id
        for chunk_id in common_ids
        if before_expected[chunk_id]["content_hash"]
        != after_expected[chunk_id]["content_hash"]
    }
    expected_reused = {
        chunk_id
        for chunk_id in common_ids
        if before_expected[chunk_id]["content_hash"]
        == after_expected[chunk_id]["content_hash"]
    }
    expected_inserted = set(after_expected) - set(before_expected)
    expected_deleted = set(before_expected) - set(after_expected)
    assert expected_updated
    assert expected_inserted
    assert expected_deleted

    provider.document_calls.clear()
    changed = SemanticIndexer(provider, store).synchronize(after)
    assert changed.status is IndexSyncStatus.SUCCESS
    assert changed.updated_chunks == len(expected_updated)
    assert changed.inserted_chunks == len(expected_inserted)
    assert changed.deleted_chunks == len(expected_deleted)
    assert changed.reused_chunks == len(expected_reused)
    assert changed.embedded_chunks == len(expected_updated | expected_inserted)
    assert sum(len(batch) for batch in provider.document_calls) == changed.embedded_chunks
    assert {
        document.chunk_id
        for batch in provider.document_calls
        for document in batch
    } == expected_updated | expected_inserted

    reopened = ChromaVectorStore(tmp_path / "persistent-chroma")
    _, changed_records = _manifest(reopened, namespace)
    _assert_exact_records(changed_records, after, namespace)

    for relative_path in (*sources, "src/added.py"):
        path = repository / relative_path
        if path.exists():
            path.unlink()
    empty_inventory = _pipeline(repository, namespace)
    assert empty_inventory.total_chunks == 0

    provider.document_calls.clear()
    emptied = SemanticIndexer(provider, reopened).synchronize(empty_inventory)
    assert emptied.status is IndexSyncStatus.SUCCESS
    assert emptied.total_chunks == 0
    assert emptied.deleted_chunks == after.total_chunks
    assert emptied.embedded_chunks == 0
    assert provider.document_calls == []

    empty_reopen = ChromaVectorStore(tmp_path / "persistent-chroma")
    empty_snapshot, empty_records = _manifest(empty_reopen, namespace)
    assert empty_snapshot.expected_chunk_count == 0
    assert empty_records == ()
    provider.query_calls.clear()
    assert SemanticSearcher(provider, empty_reopen).search(namespace, "anything", 1) == ()
    assert provider.query_calls == []


def test_two_real_repository_namespaces_remain_isolated_in_one_chroma_root(
    tmp_path: Path,
) -> None:
    first_repository = tmp_path / "first-repository"
    second_repository = tmp_path / "second-repository"
    first_repository.mkdir()
    second_repository.mkdir()
    shared_source = (
        b"# Shared source bytes\n"
        b"def repository_target(value: int) -> int:\n"
        b"    return value + 1\n"
    )
    _write(first_repository, "src/shared.py", shared_source)
    _write(second_repository, "src/shared.py", shared_source)

    first = _pipeline(first_repository, "repository-one")
    second = _pipeline(second_repository, "repository-two")
    provider = DeterministicProvider()
    persistence_root = tmp_path / "shared-persistent-chroma"
    store = ChromaVectorStore(persistence_root)
    SemanticIndexer(provider, store).synchronize(first)
    SemanticIndexer(provider, store).synchronize(second)

    reopened = ChromaVectorStore(persistence_root)
    first_snapshot, first_records = _manifest(reopened, "repository-one")
    second_snapshot, second_records = _manifest(reopened, "repository-two")
    _assert_exact_records(first_records, first, "repository-one")
    _assert_exact_records(second_records, second, "repository-two")
    assert first_snapshot._token != second_snapshot._token
    assert {record.chunk_id for record in first_records}.isdisjoint(
        record.chunk_id for record in second_records
    )

    assert 0 < first.total_chunks <= MAX_TOP_K
    assert 0 < second.total_chunks <= MAX_TOP_K
    first_results = SemanticSearcher(provider, reopened).search(
        "repository-one", "shared target", first.total_chunks
    )
    second_results = SemanticSearcher(provider, reopened).search(
        "repository-two", "shared target", second.total_chunks
    )
    assert {result.chunk_id for result in first_results} == set(
        _expected_records(first)
    )
    assert {result.chunk_id for result in second_results} == set(
        _expected_records(second)
    )
    assert {result.chunk_id for result in first_results}.isdisjoint(
        result.chunk_id for result in second_results
    )

    (first_repository / "src/shared.py").unlink()
    SemanticIndexer(provider, reopened).synchronize(
        _pipeline(first_repository, "repository-one")
    )
    preserved_second_snapshot, preserved_second_records = _manifest(
        reopened, "repository-two"
    )
    assert preserved_second_snapshot._token == second_snapshot._token
    assert preserved_second_records == second_records
    assert SemanticSearcher(provider, reopened).search(
        "repository-one", "shared target", 1
    ) == ()
