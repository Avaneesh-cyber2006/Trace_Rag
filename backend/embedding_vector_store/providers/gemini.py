"""Gemini SDK adapter for the verified Module 5 embedding contract."""

from __future__ import annotations

from json import JSONDecodeError
import math

from google import genai
from google.genai import errors, types
import httpx
from pydantic import ValidationError

from ..exceptions import (
    EmbeddingAuthenticationError,
    EmbeddingDocumentTooLarge,
    EmbeddingInvalidRequestError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    EmbeddingVectorStoreError,
)
from ..models import EmbeddingDocument, EmbeddingModelIdentity, EmbeddingVector


_PROVIDER = "gemini"
_MODEL = "gemini-embedding-001"
_DIMENSIONS = 3_072
_COMPATIBILITY_VERSION = "gemini-embedding-001-retrieval-3072-v1"
_MAX_BATCH_SIZE = 1
_MAX_INPUT_BYTES = 1_536
_DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
_QUERY_TASK = "RETRIEVAL_QUERY"

_CONFIGURATION_MESSAGE = "Gemini embedding provider configuration is invalid."
_AUTHENTICATION_MESSAGE = "Gemini embedding authentication failed."
_RATE_LIMIT_MESSAGE = "Gemini embedding request was rate limited."
_TRANSIENT_MESSAGE = "Gemini embedding request failed transiently."
_INVALID_REQUEST_MESSAGE = "Gemini embedding request is invalid."
_TOO_LARGE_MESSAGE = "Gemini embedding input is too large."
_INVALID_RESPONSE_MESSAGE = "Gemini embedding response is invalid."
_PROVIDER_MESSAGE = "Gemini embedding request failed."


class GeminiEmbeddingProvider:
    """Embed documents and queries in the verified Gemini retrieval space."""

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        compatibility_version: str,
        max_batch_size: int,
        api_key: str | None = None,
        client: object | None = None,
    ) -> None:
        if (
            model != _MODEL
            or type(dimensions) is not int
            or dimensions != _DIMENSIONS
            or compatibility_version != _COMPATIBILITY_VERSION
            or type(max_batch_size) is not int
            or max_batch_size != _MAX_BATCH_SIZE
            or (client is None) == (api_key is None)
            or (
                api_key is not None
                and (not isinstance(api_key, str) or not api_key.strip())
            )
        ):
            raise EmbeddingVectorStoreConfigurationError(_CONFIGURATION_MESSAGE)

        if client is None:
            constructed_client: object | None = None
            try:
                constructed_client = genai.Client(api_key=api_key, vertexai=False)
            except Exception:
                pass
            if constructed_client is None:
                raise EmbeddingVectorStoreConfigurationError(_CONFIGURATION_MESSAGE)
            client = constructed_client

        embed_content: object | None = None
        configuration_failure: EmbeddingVectorStoreConfigurationError | None = None
        try:
            embed_content = client.models.embed_content  # type: ignore[attr-defined]
        except Exception:
            configuration_failure = EmbeddingVectorStoreConfigurationError(
                _CONFIGURATION_MESSAGE
            )
        if configuration_failure is not None:
            raise configuration_failure
        if not callable(embed_content):
            raise EmbeddingVectorStoreConfigurationError(_CONFIGURATION_MESSAGE)

        self._client = client
        self._identity = EmbeddingModelIdentity(
            provider=_PROVIDER,
            model=model,
            dimensions=dimensions,
            compatibility_version=compatibility_version,
        )
        self._max_batch_size = max_batch_size

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def max_batch_size(self) -> int:
        return self._max_batch_size

    def embed_documents(
        self, documents: tuple[EmbeddingDocument, ...]
    ) -> tuple[EmbeddingVector, ...]:
        if (
            not isinstance(documents, tuple)
            or not documents
            or len(documents) > self._max_batch_size
            or not all(isinstance(document, EmbeddingDocument) for document in documents)
        ):
            raise EmbeddingInvalidRequestError(_INVALID_REQUEST_MESSAGE)
        for document in documents:
            if self._encoded_size(document.text) > _MAX_INPUT_BYTES:
                raise EmbeddingDocumentTooLarge(_TOO_LARGE_MESSAGE)

        return self._request(
            [document.text for document in documents],
            task_type=_DOCUMENT_TASK,
            document_request=True,
        )

    def embed_query(self, query_text: str) -> EmbeddingVector:
        if not isinstance(query_text, str) or not query_text:
            raise EmbeddingInvalidRequestError(_INVALID_REQUEST_MESSAGE)
        if self._encoded_size(query_text) > _MAX_INPUT_BYTES:
            raise EmbeddingInvalidRequestError(_TOO_LARGE_MESSAGE)

        return self._request(
            [query_text], task_type=_QUERY_TASK, document_request=False
        )[0]

    @staticmethod
    def _encoded_size(text: str) -> int:
        try:
            return len(text.encode("utf-8"))
        except UnicodeError:
            raise EmbeddingInvalidRequestError(_INVALID_REQUEST_MESSAGE) from None

    def _request(
        self,
        contents: list[str],
        *,
        task_type: str,
        document_request: bool,
    ) -> tuple[EmbeddingVector, ...]:
        request_config: object | None = None
        configuration_failure: EmbeddingProviderError | None = None
        try:
            request_config = types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self._identity.dimensions,
            )
        except ValueError:
            configuration_failure = EmbeddingInvalidRequestError(
                _INVALID_REQUEST_MESSAGE
            )
        except Exception:
            configuration_failure = EmbeddingProviderError(_PROVIDER_MESSAGE)
        if configuration_failure is not None:
            raise configuration_failure

        response: object | None = None
        mapped_failure: EmbeddingVectorStoreError | None = None
        try:
            response = self._client.models.embed_content(  # type: ignore[attr-defined]
                model=self._identity.model,
                contents=contents,
                config=request_config,
            )
        except errors.UnknownApiResponseError:
            mapped_failure = EmbeddingInvalidResponseError(_INVALID_RESPONSE_MESSAGE)
        except (JSONDecodeError, ValidationError):
            mapped_failure = EmbeddingInvalidResponseError(_INVALID_RESPONSE_MESSAGE)
        except (httpx.TimeoutException, httpx.ConnectError):
            mapped_failure = EmbeddingTransientError(_TRANSIENT_MESSAGE)
        except errors.APIError as failure:
            mapped_failure = self._map_api_failure(
                failure.code, document_request=document_request
            )
        except ValueError:
            mapped_failure = EmbeddingInvalidRequestError(_INVALID_REQUEST_MESSAGE)
        except Exception:
            mapped_failure = EmbeddingProviderError(_PROVIDER_MESSAGE)

        if mapped_failure is not None:
            raise mapped_failure
        return self._convert_response(response, expected_count=len(contents))

    @staticmethod
    def _map_api_failure(
        code: object, *, document_request: bool
    ) -> EmbeddingVectorStoreError:
        if code in (401, 403):
            return EmbeddingAuthenticationError(_AUTHENTICATION_MESSAGE)
        if code == 408:
            return EmbeddingTransientError(_TRANSIENT_MESSAGE)
        if code == 413:
            if document_request:
                return EmbeddingDocumentTooLarge(_TOO_LARGE_MESSAGE)
            return EmbeddingInvalidRequestError(_TOO_LARGE_MESSAGE)
        if code == 429:
            return EmbeddingRateLimitError(_RATE_LIMIT_MESSAGE)
        if code in (500, 502, 503, 504):
            return EmbeddingTransientError(_TRANSIENT_MESSAGE)
        if type(code) is int and 400 <= code < 500:
            return EmbeddingInvalidRequestError(_INVALID_REQUEST_MESSAGE)
        return EmbeddingProviderError(_PROVIDER_MESSAGE)

    def _convert_response(
        self, response: object | None, *, expected_count: int
    ) -> tuple[EmbeddingVector, ...]:
        try:
            embeddings = response.embeddings  # type: ignore[union-attr]
        except AttributeError:
            raise EmbeddingInvalidResponseError(_INVALID_RESPONSE_MESSAGE) from None
        if not isinstance(embeddings, list) or len(embeddings) != expected_count:
            raise EmbeddingInvalidResponseError(_INVALID_RESPONSE_MESSAGE)

        vectors: list[EmbeddingVector] = []
        for embedding in embeddings:
            try:
                values = embedding.values
            except AttributeError:
                raise EmbeddingInvalidResponseError(_INVALID_RESPONSE_MESSAGE) from None
            if (
                not isinstance(values, list)
                or len(values) != self._identity.dimensions
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in values
                )
            ):
                raise EmbeddingInvalidResponseError(_INVALID_RESPONSE_MESSAGE)
            vectors.append(EmbeddingVector(tuple(float(value) for value in values)))
        return tuple(vectors)


__all__ = ["GeminiEmbeddingProvider"]
