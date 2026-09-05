"""Provider-independent semantic-search request orchestration."""

from .exceptions import EmbeddingVectorStoreConfigurationError
from .models import VectorSearchResult
from .providers.base import EmbeddingProvider
from .retry import RetryPolicy
from .stores.base import VectorStore
from .validation import validate_search_request


_INVALID_SEARCH_CONFIGURATION_MESSAGE = "Semantic search configuration is invalid."


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
        """Validate a request before semantic-search flow is resolved."""
        validate_search_request(
            repository_namespace,
            query_text,
            top_k,
            self._max_query_chars,
            self._max_top_k,
        )
        return ()
