"""Provider boundary contracts that remain independent of any SDK."""

from inspect import signature

from backend.embedding_vector_store.models import (
    EmbeddingDocument,
    EmbeddingModelIdentity,
    EmbeddingVector,
)
from backend.embedding_vector_store.providers import EmbeddingProvider


_IDENTITY = EmbeddingModelIdentity("fake", "deterministic", 3, "v1")
_DOCUMENT = EmbeddingDocument("a" * 64, "deterministic document")
_VECTOR = EmbeddingVector((0.1, 0.2, 0.3))


class _DeterministicProvider:
    @property
    def identity(self) -> EmbeddingModelIdentity:
        return _IDENTITY

    @property
    def max_batch_size(self) -> int:
        return 2

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return tuple(_VECTOR for _ in documents)

    def embed_query(self, query_text: str) -> EmbeddingVector:
        return _VECTOR


class _MissingQueryProvider:
    identity = _IDENTITY
    max_batch_size = 1

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]:
        return tuple(_VECTOR for _ in documents)


def test_provider_protocol_accepts_a_deterministic_provider_with_separate_methods() -> None:
    provider = _DeterministicProvider()

    assert isinstance(provider, EmbeddingProvider)
    assert provider.identity == _IDENTITY
    assert provider.max_batch_size > 0
    assert provider.embed_documents((_DOCUMENT,)) == (_VECTOR,)
    assert provider.embed_query("query") == _VECTOR


def test_provider_protocol_requires_each_canonical_member() -> None:
    assert not isinstance(_MissingQueryProvider(), EmbeddingProvider)


def test_provider_protocol_uses_a_positional_document_tuple_and_separate_query_text() -> None:
    document_parameters = tuple(signature(EmbeddingProvider.embed_documents).parameters)
    query_parameters = tuple(signature(EmbeddingProvider.embed_query).parameters)

    assert document_parameters == ("self", "documents")
    assert query_parameters == ("self", "query_text")
