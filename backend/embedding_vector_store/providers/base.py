"""The provider boundary used by embedding orchestration."""

from typing import Protocol, runtime_checkable

from ..models import EmbeddingDocument, EmbeddingModelIdentity, EmbeddingVector


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produce document and query vectors in one declared embedding space."""

    @property
    def identity(self) -> EmbeddingModelIdentity: ...

    @property
    def max_batch_size(self) -> int: ...

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]: ...

    def embed_query(self, query_text: str) -> EmbeddingVector: ...
