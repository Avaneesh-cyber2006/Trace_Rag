from __future__ import annotations

from collections.abc import Callable
import re

import pytest

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
)
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.search import SemanticSearcher
from backend.embedding_vector_store.stores.base import (
    RepositoryIndexSnapshot,
    RepositoryIndexState,
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
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = state or RepositoryIndexState(indexed=False, snapshot=None)

    def _record(self, name: str, value: object = None) -> None:
        self.calls.append((name, value))

    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState:
        self._record("inspect_active", repository_namespace)
        return self.state

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
        return ()

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
