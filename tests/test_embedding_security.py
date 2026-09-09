"""Security boundaries for credentials, source evidence, and vector storage."""

from __future__ import annotations

import builtins
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
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
from chromadb.errors import ChromaError, NotFoundError

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
    EmbeddingProviderError,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    VectorStoreConfigurationError,
    VectorStoreCorruptionError,
    VectorStorePublicationError,
    VectorStoreReadError,
    VectorStoreWriteError,
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
import backend.embedding_vector_store.stores.chroma as chroma_module
from backend.embedding_vector_store.synchronization import SemanticIndexer


SECRET = "TRACERAG_TEST_SECRET_DO_NOT_LEAK"
SOURCE_MARKER = "TRACERAG_TEST_SOURCE_DO_NOT_LEAK"
SOURCE = f"# {SOURCE_MARKER}\nprint('exact evidence')\n"
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
CONTENT_HASH = sha256(SOURCE.encode("utf-8")).hexdigest()

GEMINI_MODEL = "gemini-embedding-001"
GEMINI_DIMENSIONS = 3_072
GEMINI_COMPATIBILITY = "gemini-embedding-001-retrieval-3072-v1"


def _assert_sensitive_text_absent(value: object, *, include_source: bool = True) -> None:
    rendered = str(value)
    sentinels = [SECRET, AUTH_HEADER, RAW_RESPONSE]
    if include_source:
        sentinels.append(SOURCE_MARKER)
    for sentinel in sentinels:
        assert sentinel not in rendered


def _assert_sanitized_exception(error: BaseException) -> None:
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    _assert_sensitive_text_absent(str(error))
    _assert_sensitive_text_absent(repr(error))
    _assert_sensitive_text_absent(rendered)
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("render", [str, repr, json.dumps])
def test_source_detector_rejects_raw_and_escaped_source(render) -> None:
    with pytest.raises(AssertionError):
        _assert_sensitive_text_absent(render(SOURCE))


def test_public_result_allows_source_only_in_exact_content() -> None:
    result = VectorSearchResult(
        CHUNK_ID, CONTENT_HASH, "src/security.py", "python", "symbol",
        "function", "security", None, SOURCE, 0.75,
    )
    public_fields = asdict(result)
    assert public_fields.pop("content") == SOURCE
    _assert_sensitive_text_absent(json.dumps(public_fields))
    _assert_sensitive_text_absent(repr(result))


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
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure

    def embed_content(self, **_: object) -> object:
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.0] * GEMINI_DIMENSIONS)]
        )


class _GeminiClient:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.models = _GeminiModels(failure)

    def __repr__(self) -> str:
        return SENSITIVE_TEXT


class _GeminiDiscoveryFailureClient:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    @property
    def models(self) -> object:
        raise self.failure


class _GeminiProcessControlFailure(BaseException):
    pass


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


def test_unknown_gemini_request_failure_maps_to_fixed_sanitized_provider_error() -> None:
    provider = _gemini_provider(_GeminiClient(RuntimeError(SENSITIVE_TEXT)))

    with pytest.raises(EmbeddingProviderError) as captured:
        provider.embed_query("offline query")

    assert type(captured.value) is EmbeddingProviderError
    assert str(captured.value) == "Gemini embedding request failed."
    _assert_sanitized_exception(captured.value)


def test_unknown_gemini_request_configuration_failure_maps_to_fixed_sanitized_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_request_configuration(**_: object) -> object:
        raise RuntimeError(SENSITIVE_TEXT)

    monkeypatch.setattr(
        gemini_module.types, "EmbedContentConfig", fail_request_configuration
    )
    provider = _gemini_provider(_GeminiClient())

    with pytest.raises(EmbeddingProviderError) as captured:
        provider.embed_query("offline query")

    assert type(captured.value) is EmbeddingProviderError
    assert str(captured.value) == "Gemini embedding request failed."
    _assert_sanitized_exception(captured.value)


@pytest.mark.parametrize("boundary", ["construction", "client discovery"])
def test_unknown_gemini_setup_failure_maps_to_fixed_sanitized_configuration_error(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    failure = RuntimeError(SENSITIVE_TEXT)

    if boundary == "construction":
        def fail_construction(**_: object) -> object:
            raise failure

        monkeypatch.setattr(gemini_module.genai, "Client", fail_construction)
        operation = lambda: GeminiEmbeddingProvider(
            api_key=SECRET,
            model=GEMINI_MODEL,
            dimensions=GEMINI_DIMENSIONS,
            compatibility_version=GEMINI_COMPATIBILITY,
            max_batch_size=1,
        )
    else:
        operation = lambda: _gemini_provider(_GeminiDiscoveryFailureClient(failure))

    with pytest.raises(EmbeddingVectorStoreConfigurationError) as captured:
        operation()

    assert str(captured.value) == "Gemini embedding provider configuration is invalid."
    _assert_sanitized_exception(captured.value)


@pytest.mark.parametrize(
    "failure_type", [KeyboardInterrupt, SystemExit, _GeminiProcessControlFailure]
)
@pytest.mark.parametrize(
    "boundary", ["construction", "client discovery", "request configuration", "request"]
)
def test_gemini_process_control_failures_are_not_mapped(
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[BaseException],
    boundary: str,
) -> None:
    failure = failure_type("process control")

    if boundary == "construction":
        def fail_construction(**_: object) -> object:
            raise failure

        monkeypatch.setattr(gemini_module.genai, "Client", fail_construction)
        operation = lambda: GeminiEmbeddingProvider(
            api_key=SECRET,
            model=GEMINI_MODEL,
            dimensions=GEMINI_DIMENSIONS,
            compatibility_version=GEMINI_COMPATIBILITY,
            max_batch_size=1,
        )
    elif boundary == "client discovery":
        operation = lambda: _gemini_provider(_GeminiDiscoveryFailureClient(failure))
    elif boundary == "request configuration":
        def fail_request_configuration(**_: object) -> object:
            raise failure

        monkeypatch.setattr(
            gemini_module.types, "EmbedContentConfig", fail_request_configuration
        )
        operation = lambda: _gemini_provider(_GeminiClient()).embed_query(
            "offline query"
        )
    else:
        operation = lambda: _gemini_provider(_GeminiClient(failure)).embed_query(
            "offline query"
        )

    with pytest.raises(failure_type) as captured:
        operation()

    assert captured.value is failure


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


class _FaultBoundary:
    """Inject at a single SDK call/property, forwarding every other access."""

    def __init__(self, wrapped: object, boundary: str, failure: Exception) -> None:
        self.wrapped = wrapped
        self.boundary = boundary
        self.failure = failure

    def __getattr__(self, name: str) -> object:
        if name == self.boundary:
            raise self.failure
        return getattr(self.wrapped, name)


@pytest.fixture(scope="module")
def fault_store(tmp_path_factory):
    store = ChromaVectorStore(tmp_path_factory.mktemp("security-boundaries"))
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 1)
    store.add_embedded(candidate, (_vector_record(),))
    snapshot = store.publish(candidate)
    return store, snapshot


_READ_OPERATIONS = (
    "inspect_active", "acquire_active", "read_manifest", "search",
    "validate_candidate", "publish", "abort", "delete_repository_index",
)
_READ_BOUNDARIES = [
    (operation, boundary)
    for operation in _READ_OPERATIONS
    for boundary in ("get_collection", "metadata", "count", "get")
    if (operation, boundary) != ("search", "get")
] + [("search", "query"), ("delete_repository_index", "list_collections"),
     ("delete_repository_index", "name")]
_WRITE_BOUNDARIES = [
    (operation, boundary)
    for operation in ("add_reused", "add_embedded")
    for boundary in ("get_collection", "metadata", "get", "add")
] + [("begin_candidate", "create_collection"), ("abort", "delete_collection"),
     ("delete_repository_index", "delete_collection")]


@pytest.mark.parametrize("failure_type", [
    OSError, RuntimeError, ChromaError, LookupError,
    NotFoundError, TypeError, ValueError,
    VectorStoreReadError, VectorStoreWriteError, VectorStoreCorruptionError,
])
@pytest.mark.parametrize("operation,boundary", _READ_BOUNDARIES + _WRITE_BOUNDARIES)
def test_chroma_public_operations_sanitize_each_backend_boundary(
    fault_store, monkeypatch, caplog, operation, boundary, failure_type,
) -> None:
    store, snapshot = fault_store
    candidate = CandidateIndex(
        NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION,
        "tracerag-chroma-schema-v1", 1, "securitycandidate",
    )
    # All calls still execute real adapter validation, decoding, and mapping.
    # The SDK surface alone is substituted to make failures deterministic.
    encoded = store._encode_record(_stored_record())
    page = {
        "ids": [CHUNK_ID], "documents": [SOURCE],
        "embeddings": [list(VECTOR_VALUES)], "metadatas": [encoded["metadata"]],
    }
    candidate_count = 0 if operation in ("add_reused", "add_embedded") else 1
    collection = SimpleNamespace(
        metadata=store._encode_control_metadata(snapshot),
        count=lambda: candidate_count,
        get=lambda **kwargs: {"ids": []} if kwargs.get("include") == [] else page,
        add=lambda **kwargs: None,
        query=lambda **kwargs: {
            "ids": [[CHUNK_ID]], "documents": [[SOURCE]],
            "metadatas": [[encoded["metadata"]]], "distances": [[0.0]],
        },
    )
    typed_failure = failure_type in (
        VectorStoreReadError, VectorStoreWriteError, VectorStoreCorruptionError,
    )
    failure = failure_type(
        "Already classified adapter failure." if typed_failure
        else SENSITIVE_TEXT + PRIVATE_COLLECTION
    )
    collection = _FaultBoundary(collection, boundary, failure)
    listed = _FaultBoundary(SimpleNamespace(name=snapshot._token), boundary, failure)
    client = SimpleNamespace(
        get_collection=lambda **kwargs: collection,
        create_collection=lambda **kwargs: collection,
        list_collections=lambda: [listed], delete_collection=lambda *args: None,
    )
    monkeypatch.setattr(store, "_client", _FaultBoundary(client, boundary, failure))
    store._active_pointer_path(NAMESPACE).write_bytes(
        store._active_pointer_bytes(NAMESPACE, snapshot._token)
    )
    calls = {
        "inspect_active": lambda: store.inspect_active(NAMESPACE),
        "read_manifest": lambda: store.read_manifest(snapshot),
        "search": lambda: store.search(snapshot, EmbeddingVector(VECTOR_VALUES), 1),
        "validate_candidate": lambda: store.validate_candidate(candidate),
        "publish": lambda: store.publish(candidate),
        "abort": lambda: store.abort(candidate),
        "delete_repository_index": lambda: store.delete_repository_index(NAMESPACE),
        "begin_candidate": lambda: store.begin_candidate(
            NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 1
        ),
        "add_reused": lambda: store.add_reused(candidate, (_stored_record(),)),
        "add_embedded": lambda: store.add_embedded(candidate, (_vector_record(),)),
    }
    expected = VectorStoreReadError
    if (operation, boundary) in _WRITE_BOUNDARIES and boundary not in (
        "get_collection", "metadata",
    ):
        expected = VectorStoreWriteError
    elif failure_type in (TypeError, ValueError) or (
        failure_type is NotFoundError and boundary not in ("list_collections", "name")
    ):
        expected = VectorStoreCorruptionError
    if typed_failure:
        expected = failure_type
    with pytest.raises(expected) as captured:
        if operation == "acquire_active":
            with store.acquire_active(NAMESPACE):
                pytest.fail("injected failure was not reached")
        else:
            calls[operation]()
    assert type(captured.value) is expected
    if typed_failure:
        assert captured.value is failure
    _assert_sanitized_exception(captured.value)
    _assert_sensitive_text_absent(caplog.text)
    assert PRIVATE_COLLECTION not in repr(captured.value)


@pytest.mark.parametrize("failure_type", [OSError, RuntimeError, ChromaError, LookupError])
def test_chroma_constructor_sanitizes_backend_failures(tmp_path, monkeypatch, failure_type):
    def fail(**kwargs):
        raise failure_type(SENSITIVE_TEXT)

    monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", fail)
    with pytest.raises(VectorStoreConfigurationError) as captured:
        ChromaVectorStore(tmp_path / "constructor-failure")
    _assert_sanitized_exception(captured.value)


@pytest.mark.parametrize("failure_type", [OSError, RuntimeError, ChromaError, LookupError])
def test_cleanup_sanitizes_ordinary_backend_failures(fault_store, monkeypatch, caplog, failure_type):
    store, _ = fault_store
    monkeypatch.setattr(store, "_client", _FaultBoundary(
        object(), "delete_collection", failure_type(SENSITIVE_TEXT + PRIVATE_COLLECTION)
    ))
    store._delete_reserved_collection(PRIVATE_COLLECTION)
    assert caplog.messages == ["Vector store cleanup failed."]
    _assert_sensitive_text_absent(caplog.text)
    assert PRIVATE_COLLECTION not in caplog.text


@pytest.mark.parametrize("boundary", ["open", "write", "flush", "fsync", "replace"])
@pytest.mark.parametrize("recovery", ["old", "read_failure", "corrupt"])
def test_publication_io_and_recovery_never_retain_sensitive_context(
    tmp_path, monkeypatch, boundary, recovery,
):
    store = ChromaVectorStore(tmp_path / "publication")
    candidate = store.begin_candidate(NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 0)
    real_open = Path.open
    real_inspect = store.inspect_active
    interrupted = False

    def fail(*args, **kwargs):
        nonlocal interrupted
        interrupted = True
        raise OSError(SENSITIVE_TEXT)

    class InterruptedStream:
        def __enter__(self):
            self.stream = real_open(
                store._active_pointer_path(NAMESPACE).with_suffix(".candidate"), "wb"
            )
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            return fail() if boundary == "write" else self.stream.write(data)

        def flush(self):
            return fail() if boundary == "flush" else self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    def interrupted_open(path, *args, **kwargs):
        if path.suffix == ".candidate":
            return fail() if boundary == "open" else InterruptedStream()
        return real_open(path, *args, **kwargs)

    def inspect(namespace):
        if interrupted and recovery != "old":
            error_type = VectorStoreReadError if recovery == "read_failure" else VectorStoreCorruptionError
            raise error_type("Classified recovery failure.")
        return real_inspect(namespace)

    monkeypatch.setattr(store, "inspect_active", inspect)
    if boundary in ("open", "write", "flush"):
        monkeypatch.setattr(Path, "open", interrupted_open)
    else:
        monkeypatch.setattr(chroma_module.os, boundary, fail)
    expected = VectorStoreCorruptionError if recovery == "corrupt" else VectorStorePublicationError
    with pytest.raises(expected) as captured:
        store.publish(candidate)
    _assert_sanitized_exception(captured.value)
    assert real_inspect(NAMESPACE).indexed is False


@pytest.mark.parametrize("operation", ["inspect_active", "delete_repository_index"])
def test_pointer_filesystem_failures_have_no_sensitive_context(
    fault_store, monkeypatch, operation,
):
    store, snapshot = fault_store
    pointer = store._active_pointer_path(NAMESPACE)
    pointer.write_bytes(store._active_pointer_bytes(NAMESPACE, snapshot._token))

    def fail(*args, **kwargs):
        raise OSError(SENSITIVE_TEXT)

    boundary = "read_bytes" if operation == "inspect_active" else "unlink"
    expected = VectorStoreReadError if operation == "inspect_active" else VectorStoreWriteError
    monkeypatch.setattr(Path, boundary, fail)
    with pytest.raises(expected) as captured:
        getattr(store, operation)(NAMESPACE)
    _assert_sanitized_exception(captured.value)


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("operation", ["constructor", "begin_candidate", "inspect_active", "cleanup"])
def test_process_control_exceptions_are_not_mapped(
    tmp_path, fault_store, monkeypatch, failure_type, operation,
):
    failure = failure_type("process control")

    def fail(*args, **kwargs):
        raise failure

    store, snapshot = fault_store
    if operation == "constructor":
        monkeypatch.setattr(chroma_module.chromadb, "PersistentClient", fail)
        call = lambda: ChromaVectorStore(tmp_path / "interrupt")
    else:
        store._active_pointer_path(NAMESPACE).write_bytes(
            store._active_pointer_bytes(NAMESPACE, snapshot._token)
        )
        monkeypatch.setattr(store, "_client", SimpleNamespace(
            create_collection=fail, get_collection=fail, delete_collection=fail,
        ))
        call = {
            "begin_candidate": lambda: store.begin_candidate(
                NAMESPACE, IDENTITY, EMBEDDING_DOCUMENT_VERSION, 0
            ),
            "inspect_active": lambda: store.inspect_active(NAMESPACE),
            "cleanup": lambda: store._delete_reserved_collection(PRIVATE_COLLECTION),
        }[operation]
    with pytest.raises(failure_type) as captured:
        call()
    assert captured.value is failure


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
