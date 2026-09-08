"""Security boundaries for credentials, source evidence, and vector storage."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
from dataclasses import asdict
import importlib
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
from types import SimpleNamespace
import traceback

import httpx
import pytest

from backend.code_chunker.models import (
    ChunkFileStatus,
    ChunkKind,
    ChunkedFile,
    CodeChunk,
    CodeChunkInventory,
)
from backend.code_parser.models import ParseStatus, ParsedLanguage, SourceLocation, SymbolKind
from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.exceptions import (
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    VectorStoreReadError,
)
from backend.embedding_vector_store.models import (
    EmbeddingDocument,
    EmbeddingModelIdentity,
    EmbeddingVector,
    VectorRecord,
    VectorSearchResult,
)
from backend.embedding_vector_store.providers.gemini import GeminiEmbeddingProvider
import backend.embedding_vector_store.providers.gemini as gemini_module
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.stores.base import (
    CandidateIndex,
    RepositoryIndexSnapshot,
    RepositoryIndexState,
    StoreSearchResult,
    StoredRecord,
)
from backend.embedding_vector_store.stores.chroma import ChromaVectorStore
from backend.embedding_vector_store.synchronization import SemanticIndexer


SECRET = "TRACERAG_TEST_SECRET_DO_NOT_LEAK"
SOURCE = "# TRACERAG_TEST_SOURCE_DO_NOT_LEAK\nprint('exact evidence')\n"
AUTH_HEADER = "Authorization: Bearer TRACERAG_TEST_AUTH_HEADER_DO_NOT_LEAK"
RAW_RESPONSE = "TRACERAG_TEST_RAW_RESPONSE_DO_NOT_LEAK"
PRIVATE_COLLECTION = "TRACERAG_TEST_PRIVATE_COLLECTION_DO_NOT_LEAK"
SENSITIVE_TEXT = " | ".join((SECRET, SOURCE, AUTH_HEADER, RAW_RESPONSE))
VECTOR_VALUES = (0.5, -0.25, 0.125)
REPLACEMENT_VECTOR_VALUES = (0.25, 0.5, -0.125)
VECTOR_TEXT = tuple(str(value) for value in VECTOR_VALUES)

IDENTITY = EmbeddingModelIdentity("security-provider", "security-model", 3, "security-v1")
NAMESPACE = "security-repository"
CHUNK_ID = "a" * 64
CONTENT_HASH = "b" * 64

GEMINI_MODEL = "gemini-embedding-001"
GEMINI_DIMENSIONS = 3_072
GEMINI_COMPATIBILITY = "gemini-embedding-001-retrieval-3072-v1"


def _assert_sensitive_text_absent(value: object, *, include_source: bool = True) -> None:
    rendered = str(value)
    sentinels = [SECRET, AUTH_HEADER, RAW_RESPONSE]
    if include_source:
        sentinels.append(SOURCE)
    for sentinel in sentinels:
        assert sentinel not in rendered


def _assert_sanitized_exception(error: BaseException) -> None:
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    _assert_sensitive_text_absent(str(error))
    _assert_sensitive_text_absent(repr(error))
    _assert_sensitive_text_absent(rendered)
    assert error.__cause__ is None


def _vector_record(values: tuple[float, ...] = VECTOR_VALUES) -> VectorRecord:
    return VectorRecord(
        chunk_id=CHUNK_ID,
        content_hash=CONTENT_HASH,
        relative_path="src/security.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name="security",
        parent_qualified_name=None,
        content=SOURCE,
        embedding=EmbeddingVector(values),
    )


def _stored_record(values: tuple[float, ...] = VECTOR_VALUES) -> StoredRecord:
    record = _vector_record(values)
    return StoredRecord(
        repository_namespace=NAMESPACE,
        chunk_id=record.chunk_id,
        content_hash=record.content_hash,
        relative_path=record.relative_path,
        language=record.language,
        chunk_kind=record.chunk_kind,
        symbol_kind=record.symbol_kind,
        qualified_name=record.qualified_name,
        parent_qualified_name=record.parent_qualified_name,
        content=record.content,
        embedding=record.embedding,
        embedding_identity=IDENTITY,
        document_version=EMBEDDING_DOCUMENT_VERSION,
        schema_version="tracerag-chroma-schema-v1",
    )


class _GeminiModels:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    def embed_content(self, **_: object) -> object:
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.0] * GEMINI_DIMENSIONS)]
        )


class _GeminiClient:
    def __init__(self, failure: Exception | None = None) -> None:
        self.models = _GeminiModels(failure)

    def __repr__(self) -> str:
        return SENSITIVE_TEXT


class _LeakySleeper:
    def __call__(self, _: float) -> None:
        return None

    def __repr__(self) -> str:
        return SENSITIVE_TEXT


def _gemini_provider(client: object) -> GeminiEmbeddingProvider:
    return GeminiEmbeddingProvider(
        client=client,
        model=GEMINI_MODEL,
        dimensions=GEMINI_DIMENSIONS,
        compatibility_version=GEMINI_COMPATIBILITY,
        max_batch_size=1,
    )


class _ClientBoundarySpy:
    def __init__(self, client: object) -> None:
        self._client = client
        self.embedding_function_arguments: list[object] = []

    def create_collection(self, **kwargs: object) -> object:
        self.embedding_function_arguments.append(kwargs.get("embedding_function", object()))
        return self._client.create_collection(**kwargs)  # type: ignore[attr-defined]

    def get_collection(self, **kwargs: object) -> object:
        self.embedding_function_arguments.append(kwargs.get("embedding_function", object()))
        return self._client.get_collection(**kwargs)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


def test_credentials_enter_only_mocked_trusted_client_construction_and_never_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[dict[str, object]] = []

    def construct_client(**kwargs: object) -> object:
        constructed.append(kwargs)
        return _GeminiClient()

    monkeypatch.setattr(gemini_module.genai, "Client", construct_client)
    provider = GeminiEmbeddingProvider(
        api_key=SECRET,
        model=GEMINI_MODEL,
        dimensions=GEMINI_DIMENSIONS,
        compatibility_version=GEMINI_COMPATIBILITY,
        max_batch_size=1,
    )

    assert constructed == [{"api_key": SECRET, "vertexai": False}]
    assert asdict(provider.identity) == {
        "provider": "gemini",
        "model": GEMINI_MODEL,
        "dimensions": GEMINI_DIMENSIONS,
        "compatibility_version": GEMINI_COMPATIBILITY,
    }
    _assert_sensitive_text_absent(repr(provider))
    _assert_sensitive_text_absent(repr(provider.identity))


def test_missing_credentials_and_injected_client_never_construct_a_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_real_client(**_: object) -> object:
        raise AssertionError("ordinary tests must not construct a real Gemini client")

    monkeypatch.setattr(gemini_module.genai, "Client", reject_real_client)
    with pytest.raises(EmbeddingVectorStoreConfigurationError) as captured:
        GeminiEmbeddingProvider(
            model=GEMINI_MODEL,
            dimensions=GEMINI_DIMENSIONS,
            compatibility_version=GEMINI_COMPATIBILITY,
            max_batch_size=1,
        )
    _assert_sanitized_exception(captured.value)

    provider = _gemini_provider(_GeminiClient())
    assert len(provider.embed_query("offline query").values) == GEMINI_DIMENSIONS


def test_raw_provider_failure_is_absent_from_exception_chain_and_traceback() -> None:
    provider = _gemini_provider(_GeminiClient(httpx.ReadTimeout(SENSITIVE_TEXT)))

    with pytest.raises(EmbeddingTransientError) as captured:
        provider.embed_query("offline query")

    _assert_sanitized_exception(captured.value)


def test_source_and_vectors_are_available_as_values_but_hidden_from_repr() -> None:
    document = EmbeddingDocument(CHUNK_ID, SOURCE)
    vector = EmbeddingVector(VECTOR_VALUES)
    vector_record = _vector_record()
    stored_record = _stored_record()
    public_result = VectorSearchResult(
        chunk_id=CHUNK_ID,
        content_hash=CONTENT_HASH,
        relative_path="src/security.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name="security",
        parent_qualified_name=None,
        content=SOURCE,
        score=0.75,
    )
    store_result = StoreSearchResult(
        repository_namespace=NAMESPACE,
        chunk_id=CHUNK_ID,
        content_hash=CONTENT_HASH,
        relative_path="src/security.py",
        language="python",
        chunk_kind="symbol",
        symbol_kind="function",
        qualified_name="security",
        parent_qualified_name=None,
        content=SOURCE,
        score=0.75,
    )
    retry_policy = RetryPolicy(sleeper=_LeakySleeper())

    assert document.text == SOURCE
    assert vector.values == VECTOR_VALUES
    assert vector_record.content == SOURCE
    assert vector_record.embedding.values == VECTOR_VALUES
    assert stored_record.content == SOURCE
    assert stored_record.embedding.values == VECTOR_VALUES
    assert public_result.content == SOURCE
    for value in (
        document,
        vector,
        vector_record,
        stored_record,
        public_result,
        store_result,
        retry_policy,
    ):
        representation = repr(value)
        _assert_sensitive_text_absent(representation)
        for vector_text in VECTOR_TEXT:
            assert vector_text not in representation


def test_chroma_uses_only_document_and_embedding_fields_for_sensitive_payloads(
    tmp_path: Path,
) -> None:
    store = ChromaVectorStore(tmp_path / "vector-data")
    client_spy = _ClientBoundarySpy(store._client)
    store._client = client_spy
    candidate = store.begin_candidate(
        NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 1
    )
    store.add_embedded(candidate, (_vector_record(),))
    collection = store._candidate_collection(candidate)
    physical = collection.get(include=["documents", "embeddings", "metadatas"])

    assert physical["documents"] == [SOURCE]
    persisted_vector = tuple(float(value) for value in physical["embeddings"][0])
    assert persisted_vector == VECTOR_VALUES
    metadata_text = json.dumps(physical["metadatas"], sort_keys=True)
    _assert_sensitive_text_absent(metadata_text)
    for vector_text in VECTOR_TEXT:
        assert vector_text not in metadata_text

    snapshot = store.publish(candidate)
    pointer_text = store._active_pointer_path(NAMESPACE).read_text(encoding="utf-8")
    _assert_sensitive_text_absent(pointer_text)
    for vector_text in VECTOR_TEXT:
        assert vector_text not in pointer_text

    manifest = store.read_manifest(snapshot)
    assert manifest[0].content == SOURCE
    assert manifest[0].embedding.values == persisted_vector

    active_collection = store._client.get_collection(
        name=snapshot._token, embedding_function=None
    )
    active_collection.update(ids=[CHUNK_ID], embeddings=[list(REPLACEMENT_VECTOR_VALUES)])
    assert store.read_manifest(snapshot)[0].embedding.values == REPLACEMENT_VECTOR_VALUES
    assert client_spy.embedding_function_arguments
    assert all(argument is None for argument in client_spy.embedding_function_arguments)


class _RawStorageFailureClient:
    def get_collection(self, **_: object) -> object:
        raise RuntimeError(SENSITIVE_TEXT)


def test_raw_chroma_failure_is_absent_from_public_exception_chain_and_traceback(
    tmp_path: Path,
) -> None:
    store = ChromaVectorStore(tmp_path / "vector-data")
    collection_name = store._collection_identifier(NAMESPACE, "rawfailure")
    store._active_pointer_path(NAMESPACE).write_bytes(
        store._active_pointer_bytes(NAMESPACE, collection_name)
    )
    store._client = _RawStorageFailureClient()

    with pytest.raises(VectorStoreReadError) as captured:
        store.inspect_active(NAMESPACE)

    _assert_sanitized_exception(captured.value)
    assert collection_name not in str(captured.value)
    assert collection_name not in repr(captured.value)


class _CleanupFailureClient:
    def delete_collection(self, _: str) -> None:
        raise RuntimeError(SENSITIVE_TEXT + PRIVATE_COLLECTION)


def test_cleanup_logging_omits_sensitive_and_private_storage_details(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = ChromaVectorStore(tmp_path / "vector-data")
    store._client = _CleanupFailureClient()

    with caplog.at_level("WARNING", logger="backend.embedding_vector_store.stores.chroma"):
        store._delete_reserved_collection(PRIVATE_COLLECTION)

    assert caplog.messages == ["Vector store cleanup failed."]
    _assert_sensitive_text_absent(caplog.text)
    assert PRIVATE_COLLECTION not in caplog.text


class _OfflineProvider:
    identity = IDENTITY
    max_batch_size = 1

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return tuple(EmbeddingVector(VECTOR_VALUES) for _ in documents)

    def embed_query(self, _: str) -> EmbeddingVector:
        return EmbeddingVector(VECTOR_VALUES)


class _MemoryStore:
    def __init__(self) -> None:
        self.records: tuple[VectorRecord, ...] = ()

    def inspect_active(self, _: str) -> RepositoryIndexState:
        return RepositoryIndexState(False, None)

    @contextmanager
    def acquire_active(self, _: str):
        yield RepositoryIndexState(False, None)

    def read_manifest(self, _: RepositoryIndexSnapshot) -> tuple[StoredRecord, ...]:
        return ()

    def begin_candidate(
        self,
        repository_namespace: str,
        identity: EmbeddingModelIdentity,
        document_version: str,
        expected_chunk_count: int,
    ) -> CandidateIndex:
        return CandidateIndex(
            repository_namespace,
            identity,
            document_version,
            "tracerag-chroma-schema-v1",
            expected_chunk_count,
            object(),
        )

    def add_reused(
        self, _: CandidateIndex, records: tuple[StoredRecord, ...]
    ) -> None:
        assert records == ()

    def add_embedded(
        self, _: CandidateIndex, records: tuple[VectorRecord, ...]
    ) -> None:
        self.records = records

    def validate_candidate(self, _: CandidateIndex) -> None:
        return None

    def publish(self, candidate: CandidateIndex) -> RepositoryIndexSnapshot:
        return RepositoryIndexSnapshot(
            candidate.repository_namespace,
            candidate.identity,
            candidate.document_version,
            candidate.schema_version,
            candidate.expected_chunk_count,
            object(),
        )

    def abort(self, _: CandidateIndex) -> None:
        return None

    def search(self, *_: object) -> tuple[object, ...]:
        return ()

    def delete_repository_index(self, _: str) -> None:
        return None


def _inventory(repository_path: Path) -> CodeChunkInventory:
    chunk = CodeChunk(
        CHUNK_ID,
        ChunkKind.SYMBOL,
        SourceLocation(0, len(SOURCE), 1, 0, 2, 0),
        SOURCE,
        CONTENT_HASH,
        SymbolKind.FUNCTION,
        "security",
        "security",
        None,
        (),
        None,
        (),
        (),
        (),
        0,
        1,
    )
    chunked_file = ChunkedFile(
        "src/security.py",
        ParsedLanguage.PYTHON,
        ParseStatus.SUCCESS,
        ChunkFileStatus.SUCCESS,
        "c" * 64,
        (),
        (chunk,),
        (),
    )
    return CodeChunkInventory(
        str(repository_path), NAMESPACE, 1, 1, 0, 0, 1, (chunked_file,)
    )


def test_core_never_reads_or_executes_repository_and_uses_no_process_or_network_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_path = tmp_path / "untrusted-repository"
    repository_path.mkdir()
    (repository_path / ".env").write_text(SECRET, encoding="utf-8")
    (repository_path / "config.json").write_text(AUTH_HEADER, encoding="utf-8")
    real_path_open = Path.open
    real_builtin_open = builtins.open
    repository_root = repository_path.resolve()

    def is_repository_path(value: object) -> bool:
        try:
            return Path(value).resolve().is_relative_to(repository_root)  # type: ignore[arg-type]
        except (OSError, TypeError, ValueError):
            return False

    def guarded_path_open(path: Path, *args: object, **kwargs: object):
        if is_repository_path(path):
            raise AssertionError("Module 5 must not read repository files")
        return real_path_open(path, *args, **kwargs)

    def guarded_builtin_open(file: object, *args: object, **kwargs: object):
        if is_repository_path(file):
            raise AssertionError("Module 5 must not read repository files")
        return real_builtin_open(file, *args, **kwargs)

    def forbidden(*_: object, **__: object) -> object:
        raise AssertionError("forbidden execution or external I/O boundary reached")

    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(runpy, "run_path", forbidden)
    monkeypatch.setattr(runpy, "run_module", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)
    monkeypatch.setattr(builtins, "eval", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "call", forbidden)
    monkeypatch.setattr(subprocess, "check_call", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    store = _MemoryStore()
    result = SemanticIndexer(_OfflineProvider(), store).synchronize(
        _inventory(repository_path)
    )
    assert result.total_chunks == 1
    assert store.records[0].content == SOURCE

    offline_gemini = _gemini_provider(_GeminiClient())
    assert len(offline_gemini.embed_query("offline query").values) == GEMINI_DIMENSIONS
