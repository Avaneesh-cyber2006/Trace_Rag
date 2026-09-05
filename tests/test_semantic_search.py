from __future__ import annotations

from collections.abc import Callable
import re

import pytest

from backend.embedding_vector_store.exceptions import (
    EmbeddingVectorStoreConfigurationError,
    InvalidSearchRequest,
)
from backend.embedding_vector_store.models import (
    EmbeddingModelIdentity,
    EmbeddingVector,
)
from backend.embedding_vector_store.retry import RetryPolicy
from backend.embedding_vector_store.search import SemanticSearcher
from backend.embedding_vector_store.stores.base import RepositoryIndexState
from backend.embedding_vector_store.validation import validate_search_request


REQUEST_MESSAGE = "Semantic search request is invalid."
CONFIG_MESSAGE = "Semantic search configuration is invalid."
REQUEST_PATTERN = f"^{re.escape(REQUEST_MESSAGE)}$"
CONFIG_PATTERN = f"^{re.escape(CONFIG_MESSAGE)}$"


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self._identity = EmbeddingModelIdentity(
            provider="test",
            model="semantic-search",
            dimensions=2,
            compatibility_version="v1",
        )

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
        return EmbeddingVector((1.0, 0.0))


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _record(self, name: str, value: object = None) -> None:
        self.calls.append((name, value))

    def inspect_active(self, repository_namespace: str) -> RepositoryIndexState:
        self._record("inspect_active", repository_namespace)
        return RepositoryIndexState(indexed=False, snapshot=None)

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
    store = RecordingStore()
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
    assert store.calls == []


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
