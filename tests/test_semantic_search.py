from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import fields
from hashlib import sha256
import re

import pytest

import backend.embedding_vector_store.validation as embedding_validation
from backend.embedding_vector_store.exceptions import (
    EmbeddingInvalidResponseError,
    EmbeddingSpaceMismatch,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    InvalidSearchRequest,
    RepositoryIndexNotFound,
    VectorStoreCorruptionError,
)
from backend.embedding_vector_store.documents import EMBEDDING_DOCUMENT_VERSION
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity,
    EmbeddingVector,
    VectorSearchResult,
)
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.search import SemanticSearcher
from backend.embedding_vector_store.stores.base import (
    RepositoryIndexSnapshot,
    RepositoryIndexState,
    StoreSearchResult,
)
from backend.embedding_vector_store.validation import validate_search_request


REQUEST_MESSAGE = "Semantic search request is invalid."
CONFIG_MESSAGE = "Semantic search configuration is invalid."
REQUEST_PATTERN = f"^{re.escape(REQUEST_MESSAGE)}$"
CONFIG_PATTERN = f"^{re.escape(CONFIG_MESSAGE)}$"


class RecordingProvider:
    def __init__(
        self,
        *,
        identity: EmbeddingModelIdentity | None = None,
        query_outcomes: tuple[object, ...] = (),
        on_query: Callable[[], None] | None = None,
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self._identity = identity or EmbeddingModelIdentity(
            provider="test",
            model="semantic-search",
            dimensions=2,
            compatibility_version="v1",
        )
        self._query_outcomes = list(query_outcomes)
        self._on_query = on_query

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def max_batch_size(self) -> int:
        return 8

    def embed_documents(
        self, documents: tuple[object, ...]
    ) -> tuple[EmbeddingVector, ...]:
        self.calls.append(("embed_documents", documents))
        return ()

    def embed_query(self, query_text: str) -> EmbeddingVector:
        self.calls.append(("embed_query", query_text))
        if self._on_query is not None:
            self._on_query()
        if self._query_outcomes:
            outcome = self._query_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome  # type: ignore[return-value]
        return EmbeddingVector((1.0, 0.0))


class RecordingStore:
    def __init__(
        self,
        state: RepositoryIndexState | None = None,
        search_results: tuple[object, ...] = (),
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = state or RepositoryIndexState(indexed=False, snapshot=None)
        self.search_results = search_results

    def _record(self, name: str, value: object = None) -> None:
        self.calls.append((name, value))

    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState:
        self._record("inspect_active", repository_namespace)
        return self.state

    @contextmanager
    def acquire_active(self, repository_namespace: str):
        yield self.inspect_active(repository_namespace)

    def read_manifest(self, snapshot: object) -> tuple[object, ...]:
        self._record("read_manifest", snapshot)
        return ()

    def begin_candidate(self, *args: object) -> object:
        self._record("begin_candidate", args)
        return object()

    def add_reused(self, candidate: object, records: tuple[object, ...]) -> None:
        self._record("add_reused", (candidate, records))

    def add_embedded(self, candidate: object, records: tuple[object, ...]) -> None:
        self._record("add_embedded", (candidate, records))

    def validate_candidate(self, candidate: object) -> None:
        self._record("validate_candidate", candidate)

    def publish(self, candidate: object) -> object:
        self._record("publish", candidate)
        return object()

    def abort(self, candidate: object) -> None:
        self._record("abort", candidate)

    def search(
        self, snapshot: object, query: EmbeddingVector, top_k: int
    ) -> tuple[object, ...]:
        self._record("search", (snapshot, query, top_k))
        return self.search_results

    def delete_repository_index(self, repository_namespace: str) -> None:
        self._record("delete_repository_index", repository_namespace)


class ExplosiveDependency:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError(f"dependency inspected before bounds validation: {name}")


def _identity(model: str = "semantic-search") -> EmbeddingModelIdentity:
    return EmbeddingModelIdentity(
        provider="test",
        model=model,
        dimensions=2,
        compatibility_version="v1",
    )


def _snapshot(
    *,
    identity: EmbeddingModelIdentity | None = None,
    expected_chunk_count: int = 1,
    token: object | None = None,
) -> RepositoryIndexSnapshot:
    return RepositoryIndexSnapshot(
        repository_namespace="repo",
        identity=identity or _identity(),
        document_version=EMBEDDING_DOCUMENT_VERSION,
        schema_version="tracerag-chroma-schema-v1",
        expected_chunk_count=expected_chunk_count,
        _token=token or object(),
    )


def _active(snapshot: RepositoryIndexSnapshot) -> RepositoryIndexState:
    return RepositoryIndexState(indexed=True, snapshot=snapshot)


def _store_result(
    *,
    repository_namespace: str = "repo",
    chunk_id: str = "a" * 64,
    content_hash: str | None = None,
    relative_path: str = "src/example.py",
    language: str = "python",
    chunk_kind: str = "symbol",
    symbol_kind: str | None = "function",
    qualified_name: str | None = "example",
    parent_qualified_name: str | None = None,
    content: str = "def example():\r\n\treturn 'exact source'\n",
    score: float = 0.75,
) -> StoreSearchResult:
    if content_hash is None:
        content_hash = sha256(content.encode("utf-8")).hexdigest()
    return StoreSearchResult(
        repository_namespace=repository_namespace,
        chunk_id=chunk_id,
        content_hash=content_hash,
        relative_path=relative_path,
        language=language,
        chunk_kind=chunk_kind,
        symbol_kind=symbol_kind,
        qualified_name=qualified_name,
        parent_qualified_name=parent_qualified_name,
        content=content,
        score=score,
    )


def _corrupt_result(attribute: str, invalid_value: object) -> StoreSearchResult:
    result = _store_result()
    object.__setattr__(result, attribute, invalid_value)
    return result


class _StringSubclass(str):
    pass


class _HostileEqualityString(str):
    def __eq__(self, other: object) -> bool:
        raise RuntimeError("hostile equality")


class _HostileIsspaceString(str):
    def isspace(self) -> bool:
        raise RuntimeError("hostile whitespace check")


class _HostileHashString(str):
    def __hash__(self) -> int:
        raise RuntimeError("hostile hash")


class _HostileCasefoldString(str):
    def casefold(self) -> str:
        raise RuntimeError("hostile ordering")


@pytest.mark.parametrize("repository_namespace", [None, 0, False, ""])
def test_request_rejects_invalid_repository_namespace(
    repository_namespace: object,
) -> None:
    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        validate_search_request(  # type: ignore[arg-type]
            repository_namespace, "query", 1, 16_384, 100
        )


@pytest.mark.parametrize(
    "query_text",
    [None, 0, False, "", " ", "\t\r\n", "x" * 16_385],
)
def test_request_rejects_invalid_query_text(query_text: object) -> None:
    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        validate_search_request(  # type: ignore[arg-type]
            "repo", query_text, 1, 16_384, 100
        )


@pytest.mark.parametrize("top_k", [None, True, False, 0, -1, 1.0, "1", 101])
def test_request_rejects_invalid_top_k(top_k: object) -> None:
    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        validate_search_request(  # type: ignore[arg-type]
            "repo", "query", top_k, 16_384, 100
        )


@pytest.mark.parametrize("top_k", [1, 100])
def test_request_accepts_exact_query_and_top_k_bounds(top_k: int) -> None:
    validate_search_request("repo", "x" * 16_384, top_k, 16_384, 100)


def test_request_uses_configured_bounds() -> None:
    validate_search_request("repo", "x" * 3, 2, 3, 2)

    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        validate_search_request("repo", "x" * 4, 2, 3, 2)
    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        validate_search_request("repo", "x" * 3, 3, 3, 2)


@pytest.mark.parametrize(
    ("repository_namespace", "query_text", "top_k"),
    [
        ("", "query", 1),
        ("repo", "   ", 1),
        ("repo", "query", True),
        ("repo", "query", 101),
    ],
)
def test_request_validation_precedes_all_provider_and_store_calls(
    repository_namespace: object,
    query_text: object,
    top_k: object,
) -> None:
    provider = RecordingProvider()
    store = RecordingStore()
    searcher = SemanticSearcher(provider, store)

    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        searcher.search(repository_namespace, query_text, top_k)  # type: ignore[arg-type]

    assert provider.calls == []
    assert store.calls == []


@pytest.mark.parametrize(
    ("repository_namespace", "query_text"),
    [
        (_HostileEqualityString("repo"), "query"),
        ("repo", _HostileIsspaceString("query")),
    ],
)
def test_request_rejects_hostile_string_subclasses_before_all_dependency_calls(
    repository_namespace: str,
    query_text: str,
) -> None:
    provider = RecordingProvider()
    store = RecordingStore(_active(_snapshot()))

    with pytest.raises(InvalidSearchRequest, match=REQUEST_PATTERN):
        SemanticSearcher(provider, store).search(
            repository_namespace,
            query_text,
            1,
        )

    assert provider.calls == []
    assert store.calls == []


def test_request_query_text_is_forwarded_to_validation_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider()
    store = RecordingStore(_active(_snapshot(expected_chunk_count=0)))
    searcher = SemanticSearcher(provider, store)
    received: list[tuple[object, ...]] = []

    def record_validation(*args: object) -> None:
        received.append(args)

    monkeypatch.setattr(
        "backend.embedding_vector_store.search.validate_search_request",
        record_validation,
    )
    query_text = "  preserve\r\nthis query\t"

    assert searcher.search("repo", query_text, 7) == ()
    assert received == [("repo", query_text, 7, 16_384, 100)]
    assert provider.calls == []
    assert store.calls == [("inspect_active", "repo")]


def test_not_indexed_raises_before_query_embedding_or_store_search() -> None:
    provider = RecordingProvider()
    store = RecordingStore()

    with pytest.raises(RepositoryIndexNotFound):
        SemanticSearcher(provider, store).search("repo", "query", 3)

    assert provider.calls == []
    assert store.calls == [("inspect_active", "repo")]


def test_active_empty_returns_without_query_embedding_or_store_search() -> None:
    snapshot = _snapshot(expected_chunk_count=0)
    provider = RecordingProvider(identity=snapshot.identity)
    store = RecordingStore(_active(snapshot))

    assert SemanticSearcher(provider, store).search("repo", "query", 3) == ()

    assert provider.calls == []
    assert store.calls == [("inspect_active", "repo")]


def test_identity_mismatch_precedes_query_embedding_and_store_search() -> None:
    snapshot = _snapshot(identity=_identity("indexed-model"))
    provider = RecordingProvider(identity=_identity("configured-model"))
    store = RecordingStore(_active(snapshot))

    with pytest.raises(EmbeddingSpaceMismatch):
        SemanticSearcher(provider, store).search("repo", "query", 3)

    assert provider.calls == []
    assert store.calls == [("inspect_active", "repo")]


@pytest.mark.parametrize(
    ("attribute", "invalid_value"),
    [
        ("repository_namespace", "other-repo"),
        ("document_version", "other-document-version"),
        ("schema_version", "other-schema-version"),
        ("expected_chunk_count", True),
        ("expected_chunk_count", -1),
    ],
)
def test_snapshot_metadata_is_validated_before_query_embedding(
    attribute: str,
    invalid_value: object,
) -> None:
    snapshot = _snapshot()
    object.__setattr__(snapshot, attribute, invalid_value)
    provider = RecordingProvider(identity=snapshot.identity)
    store = RecordingStore(_active(snapshot))

    with pytest.raises(VectorStoreCorruptionError):
        SemanticSearcher(provider, store).search("repo", "query", 3)

    assert provider.calls == []
    assert store.calls == [("inspect_active", "repo")]


def test_query_embedding_is_retried_and_passed_to_store_search_unchanged() -> None:
    snapshot = _snapshot()
    query_vector = EmbeddingVector((0.25, -0.5))
    provider = RecordingProvider(
        identity=snapshot.identity,
        query_outcomes=(EmbeddingTransientError("provider detail"), query_vector),
    )
    store = RecordingStore(_active(snapshot))
    delays: list[float] = []
    retry_policy = RetryPolicy(
        max_attempts=2,
        initial_delay_seconds=0.125,
        multiplier=2.0,
        max_delay_seconds=1.0,
        sleeper=delays.append,
    )

    assert (
        SemanticSearcher(
            provider,
            store,
            retry_policy=retry_policy,
        ).search("repo", "  unchanged query\r\n", 3)
        == ()
    )

    assert provider.calls == [
        ("embed_query", "  unchanged query\r\n"),
        ("embed_query", "  unchanged query\r\n"),
    ]
    assert delays == [0.125]
    assert store.calls == [
        ("inspect_active", "repo"),
        ("search", (snapshot, query_vector, 3)),
    ]


@pytest.mark.parametrize(
    "invalid_vector",
    [
        EmbeddingVector((1.0,)),
        EmbeddingVector((float("nan"), 0.0)),
        EmbeddingVector((0.0, float("inf"))),
    ],
)
def test_query_embedding_response_is_validated_before_store_search(
    invalid_vector: EmbeddingVector,
) -> None:
    snapshot = _snapshot()
    provider = RecordingProvider(
        identity=snapshot.identity,
        query_outcomes=(invalid_vector,),
    )
    store = RecordingStore(_active(snapshot))

    with pytest.raises(EmbeddingInvalidResponseError):
        SemanticSearcher(provider, store).search("repo", "query", 3)

    assert provider.calls == [("embed_query", "query")]
    assert store.calls == [("inspect_active", "repo")]


def test_snapshot_is_resolved_once_and_remains_pinned_during_publication() -> None:
    old_snapshot = _snapshot(token=object())
    new_snapshot = _snapshot(token=object())
    store = RecordingStore(_active(old_snapshot))

    def publish_new_snapshot() -> None:
        store.state = _active(new_snapshot)

    provider = RecordingProvider(
        identity=old_snapshot.identity,
        on_query=publish_new_snapshot,
    )

    assert SemanticSearcher(provider, store).search("repo", "query", 3) == ()

    assert provider.calls == [("embed_query", "query")]
    assert [name for name, _ in store.calls] == ["inspect_active", "search"]
    search_arguments = store.calls[1][1]
    assert isinstance(search_arguments, tuple)
    searched_snapshot, searched_vector, searched_top_k = search_arguments
    assert searched_snapshot is old_snapshot
    assert searched_snapshot is not new_snapshot
    assert searched_vector == EmbeddingVector((1.0, 0.0))
    assert searched_top_k == 3


def test_result_normalization_rejects_more_records_than_top_k() -> None:
    results = (
        _store_result(chunk_id="a" * 64),
        _store_result(chunk_id="b" * 64),
    )

    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(results, "repo", 1)


def test_result_normalization_rejects_duplicate_chunk_ids() -> None:
    results = (
        _store_result(relative_path="src/first.py"),
        _store_result(relative_path="src/second.py"),
    )

    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(results, "repo", 2)


def test_result_normalization_rejects_wrong_complete_namespace() -> None:
    results = (_store_result(repository_namespace="repo/subtree"),)

    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(results, "repo", 1)


@pytest.mark.parametrize(
    ("attribute", "invalid_value"),
    [
        ("chunk_id", "A" * 64),
        ("chunk_id", "a" * 63),
        ("content_hash", "not-a-content-hash"),
        ("relative_path", ""),
        ("relative_path", "../escape.py"),
        ("relative_path", "src\\example.py"),
        ("language", ""),
        ("chunk_kind", ""),
        ("symbol_kind", ""),
        ("qualified_name", ""),
        ("parent_qualified_name", ""),
        ("content", ""),
        ("score", True),
        ("score", float("nan")),
        ("score", float("inf")),
        ("score", 10**1_000),
        ("score", -0.000_001),
        ("score", 1.000_001),
    ],
)
def test_corrupt_result_metadata_content_hashes_and_scores_fail_closed(
    attribute: str,
    invalid_value: object,
) -> None:
    valid_result = _store_result(chunk_id="c" * 64)
    corrupt_result = _corrupt_result(attribute, invalid_value)

    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(
            (valid_result, corrupt_result),
            "repo",
            2,
        )


def test_result_normalization_rejects_valid_looking_mismatched_content_hash_without_partial_results() -> None:
    results = (
        _store_result(chunk_id="a" * 64, content="first exact source"),
        _store_result(
            chunk_id="b" * 64,
            content_hash="0" * 64,
            content="second exact source",
        ),
    )

    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(
            results,
            "repo",
            2,
        )


@pytest.mark.parametrize(
    "attribute",
    [
        "repository_namespace",
        "chunk_id",
        "content_hash",
        "relative_path",
        "language",
        "chunk_kind",
        "symbol_kind",
        "qualified_name",
        "parent_qualified_name",
        "content",
    ],
)
def test_result_normalization_rejects_string_subclasses(attribute: str) -> None:
    result = _store_result()
    original_value = getattr(result, attribute)
    if original_value is None:
        original_value = "parent"
    object.__setattr__(result, attribute, _StringSubclass(original_value))

    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(
            (result,), "repo", 1
        )


@pytest.mark.parametrize(
    ("attribute", "invalid_value"),
    [
        ("repository_namespace", _HostileEqualityString("repo")),
        ("chunk_id", _HostileHashString("a" * 64)),
        ("relative_path", _HostileCasefoldString("src/example.py")),
    ],
)
def test_hostile_primitive_subclasses_fail_as_store_corruption(
    attribute: str,
    invalid_value: object,
) -> None:
    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(
            (_corrupt_result(attribute, invalid_value),), "repo", 1
        )


@pytest.mark.parametrize("results", [None, [], (object(),)])
def test_corrupt_result_container_or_missing_record_fields_fail_closed(
    results: object,
) -> None:
    with pytest.raises(VectorStoreCorruptionError):
        embedding_validation.validate_and_normalize_search_results(  # type: ignore[attr-defined,arg-type]
            results, "repo", 1
        )


def test_result_ordering_uses_exact_score_path_case_and_chunk_id_key() -> None:
    results = (
        _store_result(
            chunk_id="e" * 64,
            relative_path="z.py",
            score=0.9,
        ),
        _store_result(
            chunk_id="d" * 64,
            relative_path="a.py",
            score=0.9,
        ),
        _store_result(
            chunk_id="c" * 64,
            relative_path="A.py",
            score=0.9,
        ),
        _store_result(
            chunk_id="b" * 64,
            relative_path="A.py",
            score=0.9,
        ),
        _store_result(
            chunk_id="a" * 64,
            relative_path="0.py",
            score=0.8,
        ),
    )

    normalized = embedding_validation.validate_and_normalize_search_results(
        results, "repo", 5
    )

    assert [result.chunk_id for result in normalized] == [
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "a" * 64,
    ]


def test_search_returns_exact_source_in_public_results_without_private_fields() -> None:
    snapshot = _snapshot()
    exact_source = "  # Unicode: λ\r\n\tprint('unchanged')\n\x00suffix  "
    store_result = _store_result(content=exact_source)
    store = RecordingStore(_active(snapshot), (store_result,))

    results = SemanticSearcher(
        RecordingProvider(identity=snapshot.identity),
        store,
    ).search("repo", "query", 1)

    assert results == (
        VectorSearchResult(
            chunk_id="a" * 64,
            content_hash=sha256(exact_source.encode("utf-8")).hexdigest(),
            relative_path="src/example.py",
            language="python",
            chunk_kind="symbol",
            symbol_kind="function",
            qualified_name="example",
            parent_qualified_name=None,
            content=exact_source,
            score=0.75,
        ),
    )
    assert results[0].content == exact_source
    assert [field.name for field in fields(results[0])] == [
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
    ]
    for forbidden in (
        "embedding_text",
        "text",
        "embedding",
        "vector",
        "distance",
        "repository_namespace",
        "generation",
        "collection",
        "locator",
        "_token",
    ):
        assert not hasattr(results[0], forbidden)


def test_search_rejects_entire_corrupt_result_tuple_without_filtering() -> None:
    snapshot = _snapshot()
    store = RecordingStore(
        _active(snapshot),
        (
            _store_result(chunk_id="a" * 64),
            _corrupt_result("repository_namespace", "other-repo"),
        ),
    )

    with pytest.raises(VectorStoreCorruptionError):
        SemanticSearcher(
            RecordingProvider(identity=snapshot.identity),
            store,
        ).search("repo", "query", 2)


@pytest.mark.parametrize("invalid_bound", [None, True, False, 0, -1, 1.0, "1"])
@pytest.mark.parametrize("bound_name", ["max_query_chars", "max_top_k"])
def test_config_rejects_invalid_constructor_bounds(
    bound_name: str,
    invalid_bound: object,
) -> None:
    kwargs = {bound_name: invalid_bound}

    with pytest.raises(
        EmbeddingVectorStoreConfigurationError,
        match=CONFIG_PATTERN,
    ):
        SemanticSearcher(RecordingProvider(), RecordingStore(), **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider_factory", "store_factory"),
    [
        (ExplosiveDependency, RecordingStore),
        (RecordingProvider, ExplosiveDependency),
    ],
)
def test_config_validates_bounds_before_inspecting_dependencies(
    provider_factory: Callable[[], object],
    store_factory: Callable[[], object],
) -> None:
    with pytest.raises(
        EmbeddingVectorStoreConfigurationError,
        match=CONFIG_PATTERN,
    ):
        SemanticSearcher(  # type: ignore[arg-type]
            provider_factory(),
            store_factory(),
            max_query_chars=0,
        )


def test_config_accepts_positive_bounds_and_stores_only_contract_state() -> None:
    provider = RecordingProvider()
    store = RecordingStore()
    retry_policy = RetryPolicy(max_attempts=1, sleeper=lambda _: None)

    searcher = SemanticSearcher(
        provider,
        store,
        max_query_chars=1,
        max_top_k=1,
        retry_policy=retry_policy,
    )

    assert vars(searcher) == {
        "_provider": provider,
        "_store": store,
        "_max_query_chars": 1,
        "_max_top_k": 1,
        "_retry_policy": retry_policy,
    }


def test_config_applies_default_bounds_and_validated_retry_policy() -> None:
    searcher = SemanticSearcher(RecordingProvider(), RecordingStore())

    state = vars(searcher)
    assert state["_max_query_chars"] == 16_384
    assert state["_max_top_k"] == 100
    assert isinstance(state["_retry_policy"], RetryPolicy)


@pytest.mark.parametrize(
    ("provider_factory", "store_factory", "retry_policy"),
    [
        (object, RecordingStore, None),
        (RecordingProvider, object, None),
        (RecordingProvider, RecordingStore, object()),
    ],
)
def test_config_rejects_invalid_dependencies_and_retry_policy(
    provider_factory: Callable[[], object],
    store_factory: Callable[[], object],
    retry_policy: object,
) -> None:
    with pytest.raises(
        EmbeddingVectorStoreConfigurationError,
        match=CONFIG_PATTERN,
    ):
        SemanticSearcher(
            provider_factory(),  # type: ignore[arg-type]
            store_factory(),  # type: ignore[arg-type]
            retry_policy=retry_policy,
        )
