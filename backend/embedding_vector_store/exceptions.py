"""Typed failures at the embedding vector-store boundary."""


class EmbeddingVectorStoreError(Exception):
    """Base class for embedding vector-store failures."""


class EmbeddingVectorStoreConfigurationError(EmbeddingVectorStoreError):
    """Raised when embedding vector-store configuration is invalid."""


class InvalidCodeChunkInventory(EmbeddingVectorStoreError):
    """Raised when a code-chunk inventory violates the embedding contract."""


class EmbeddingDocumentError(EmbeddingVectorStoreError):
    """Raised when an embedding document is invalid."""


class EmbeddingDocumentTooLarge(EmbeddingDocumentError):
    """Raised when a complete embedding document exceeds a supported limit."""


class EmbeddingProviderError(EmbeddingVectorStoreError):
    """Base class for embedding provider failures."""


class EmbeddingAuthenticationError(EmbeddingProviderError):
    """Raised when provider authentication fails."""


class EmbeddingRateLimitError(EmbeddingProviderError):
    """Raised when a provider rate limit is encountered."""


class EmbeddingTransientError(EmbeddingProviderError):
    """Raised when a retryable provider failure is encountered."""


class EmbeddingInvalidRequestError(EmbeddingProviderError):
    """Raised when a provider request is invalid."""


class EmbeddingInvalidResponseError(EmbeddingProviderError):
    """Raised when a provider response violates the contract."""


class VectorStoreError(EmbeddingVectorStoreError):
    """Base class for vector-store failures."""


class VectorStoreConfigurationError(VectorStoreError):
    """Raised when a vector-store configuration is invalid."""


class VectorStoreReadError(VectorStoreError):
    """Raised when a vector-store read fails."""


class VectorStoreWriteError(VectorStoreError):
    """Raised when a vector-store write fails."""


class VectorStorePublicationError(VectorStoreError):
    """Raised when candidate publication fails or is indeterminate."""


class VectorStoreCorruptionError(VectorStoreError):
    """Raised when vector-store data is incompatible or corrupt."""


class SemanticSearchError(EmbeddingVectorStoreError):
    """Base class for semantic-search failures."""


class InvalidSearchRequest(SemanticSearchError):
    """Raised when a semantic-search request or result is invalid."""


class RepositoryIndexNotFound(SemanticSearchError):
    """Raised when no published index exists for a repository."""


class EmbeddingSpaceMismatch(SemanticSearchError):
    """Raised when query and index embedding spaces are incompatible."""
