"""Provider-independent semantic-search request orchestration."""

from .documents import EMBEDDING_DOCUMENT_VERSION
from .exceptions import (
    EmbeddingSpaceMismatch,
    EmbeddingVectorStoreConfigurationError,
    RepositoryIndexNotFound,
    VectorStoreCorruptionError,
)
from .models import EmbeddingModelIdentity, VectorSearchResult
from .providers.base import EmbeddingProvider
from .retry import RetryPolicy, run_with_embedding_retries
from .stores.base import RepositoryIndexSnapshot, RepositoryIndexState, VectorStore
from .validation import validate_embedding_vector, validate_search_request


_INVALID_SEARCH_CONFIGURATION_MESSAGE = "Semantic search configuration is invalid."
_INDEX_NOT_FOUND_MESSAGE = "Repository semantic index is not found."
_EMBEDDING_SPACE_MISMATCH_MESSAGE = "Semantic index embedding space is incompatible."
_STORE_CORRUPTION_MESSAGE = "Vector store data is incompatible or corrupt."
_STORAGE_SCHEMA_VERSION = "tracerag-chroma-schema-v1"


def _resolve_snapshot(
    state: RepositoryIndexState,
    repository_namespace: str,
) -> RepositoryIndexSnapshot:
    if not isinstance(state, RepositoryIndexState):
        raise VectorStoreCorruptionError(_STORE_CORRUPTION_MESSAGE)
    if state.indexed is False and state.snapshot is None:
        raise RepositoryIndexNotFound(_INDEX_NOT_FOUND_MESSAGE)
    snapshot = state.snapshot
    if (
        state.indexed is not True
        or not isinstance(snapshot, RepositoryIndexSnapshot)
        or snapshot.repository_namespace != repository_namespace
        or not isinstance(snapshot.identity, EmbeddingModelIdentity)
        or snapshot.document_version != EMBEDDING_DOCUMENT_VERSION
        or snapshot.schema_version != _STORAGE_SCHEMA_VERSION
        or type(snapshot.expected_chunk_count) is not int
        or snapshot.expected_chunk_count < 0
    ):
        raise VectorStoreCorruptionError(_STORE_CORRUPTION_MESSAGE)
    return snapshot


class SemanticSearcher:
    """Validate bounded semantic-search requests before using collaborators."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        store: VectorStore,
        *,
        max_query_chars: int = 16_384,
        max_top_k: int = 100,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if (
            type(max_query_chars) is not int
            or max_query_chars <= 0
            or type(max_top_k) is not int
            or max_top_k <= 0
            or (retry_policy is not None and not isinstance(retry_policy, RetryPolicy))
        ):
            raise EmbeddingVectorStoreConfigurationError(
                _INVALID_SEARCH_CONFIGURATION_MESSAGE
            )
        if not isinstance(provider, EmbeddingProvider) or not isinstance(
            store, VectorStore
        ):
            raise EmbeddingVectorStoreConfigurationError(
                _INVALID_SEARCH_CONFIGURATION_MESSAGE
            )
        self._provider = provider
        self._store = store
        self._max_query_chars = max_query_chars
        self._max_top_k = max_top_k
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()

    def search(
        self,
        repository_namespace: str,
        query_text: str,
        top_k: int,
    ) -> tuple[VectorSearchResult, ...]:
        """Query one immutable active repository-index snapshot."""
        validate_search_request(
            repository_namespace,
            query_text,
            top_k,
            self._max_query_chars,
            self._max_top_k,
        )
        snapshot = _resolve_snapshot(
            self._store.inspect_active(repository_namespace),
            repository_namespace,
        )
        if snapshot.identity != self._provider.identity:
            raise EmbeddingSpaceMismatch(_EMBEDDING_SPACE_MISMATCH_MESSAGE)
        if snapshot.expected_chunk_count == 0:
            return ()
        query_vector = run_with_embedding_retries(
            lambda: self._provider.embed_query(query_text),
            self._retry_policy,
        )
        validate_embedding_vector(query_vector, snapshot.identity)
        self._store.search(snapshot, query_vector, top_k)
        return ()
