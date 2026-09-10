"""Offline contract tests for the Gemini embedding adapter."""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import json
import math
from threading import Barrier
import traceback
from types import SimpleNamespace
from typing import Any

from google import genai
from google.genai import errors, types
import httpx
import pytest

from backend.embedding_vector_store.exceptions import (
    EmbeddingAuthenticationError,
    EmbeddingDocumentTooLarge,
    EmbeddingInvalidRequestError,
    EmbeddingInvalidResponseError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
)
from backend.embedding_vector_store.models import EmbeddingDocument
from backend.embedding_vector_store.providers import EmbeddingProvider
from backend.embedding_vector_store.providers.gemini import GeminiEmbeddingProvider


_MODEL = "gemini-embedding-001"
_DIMENSIONS = 3_072
_COMPATIBILITY = "gemini-embedding-001-retrieval-3072-v1"
_DOCUMENT = EmbeddingDocument("a" * 64, "document")


@dataclass
class _Call:
    model: str
    contents: object
    config: object


class _FakeModels:
    def __init__(self, response: object | None = None, failure: Exception | None = None):
        self.calls: list[_Call] = []
        self.response = response or _response([0.0] * _DIMENSIONS)
        self.failure = failure

    def embed_content(self, *, model: str, contents: object, config: object) -> object:
        self.calls.append(_Call(model, contents, config))
        if self.failure is not None:
            raise self.failure
        return self.response


class _FakeClient:
    def __init__(self, response: object | None = None, failure: Exception | None = None):
        self.models = _FakeModels(response, failure)


def _response(*vectors: list[object]) -> object:
    return SimpleNamespace(
        embeddings=[SimpleNamespace(values=values) for values in vectors]
    )


def _real_sdk_client_with_response(response: httpx.Response) -> genai.Client:
    transport = httpx.MockTransport(lambda request: response)
    http_client = httpx.Client(transport=transport)
    return genai.Client(
        api_key="offline-key",
        vertexai=False,
        http_options=types.HttpOptions(httpx_client=http_client),
    )


def _provider(client: object | None = None, **overrides: Any) -> GeminiEmbeddingProvider:
    return GeminiEmbeddingProvider(
        client=client or _FakeClient(),
        model=_MODEL,
        dimensions=_DIMENSIONS,
        compatibility_version=_COMPATIBILITY,
        max_batch_size=1,
        **overrides,
    )


def test_constructor_requires_exactly_one_external_credential_source() -> None:
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        GeminiEmbeddingProvider(
            model=_MODEL,
            dimensions=_DIMENSIONS,
            compatibility_version=_COMPATIBILITY,
            max_batch_size=1,
        )
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        GeminiEmbeddingProvider(
            api_key="external-key",
            client=_FakeClient(),
            model=_MODEL,
            dimensions=_DIMENSIONS,
            compatibility_version=_COMPATIBILITY,
            max_batch_size=1,
        )


@pytest.mark.parametrize("api_key", ["", " ", 7])
def test_constructor_rejects_malformed_external_api_key(api_key: object) -> None:
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        GeminiEmbeddingProvider(
            api_key=api_key,  # type: ignore[arg-type]
            model=_MODEL,
            dimensions=_DIMENSIONS,
            compatibility_version=_COMPATIBILITY,
            max_batch_size=1,
        )


def test_constructor_rejects_an_unusable_injected_client() -> None:
    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        GeminiEmbeddingProvider(
            client=object(),
            model=_MODEL,
            dimensions=_DIMENSIONS,
            compatibility_version=_COMPATIBILITY,
            max_batch_size=1,
        )


def test_constructor_sanitizes_verified_sdk_configuration_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_configuration(*, api_key: str, vertexai: bool) -> object:
        assert vertexai is False
        raise ValueError(f"raw-body:{api_key}")

    monkeypatch.setattr(
        "backend.embedding_vector_store.providers.gemini.genai.Client",
        reject_configuration,
    )

    with pytest.raises(EmbeddingVectorStoreConfigurationError) as captured:
        GeminiEmbeddingProvider(
            api_key="external-key",
            model=_MODEL,
            dimensions=_DIMENSIONS,
            compatibility_version=_COMPATIBILITY,
            max_batch_size=1,
        )

    assert "external-key" not in str(captured.value)
    assert captured.value.__context__ is None


def test_constructor_uses_verified_sdk_client_path_for_external_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    constructed: list[dict[str, object]] = []
    fake = _FakeClient()

    def construct(**kwargs: object) -> _FakeClient:
        constructed.append(kwargs)
        return fake

    monkeypatch.setattr(
        "backend.embedding_vector_store.providers.gemini.genai.Client", construct
    )

    provider = GeminiEmbeddingProvider(
        api_key="external-key",
        model=_MODEL,
        dimensions=_DIMENSIONS,
        compatibility_version=_COMPATIBILITY,
        max_batch_size=1,
    )

    assert constructed == [{"api_key": "external-key", "vertexai": False}]
    assert isinstance(provider, EmbeddingProvider)
    assert provider.identity.provider == "gemini"
    assert provider.identity.model == _MODEL
    assert provider.identity.dimensions == _DIMENSIONS
    assert provider.identity.compatibility_version == _COMPATIBILITY


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("model", ""),
        ("model", "wrong-model"),
        ("dimensions", 0),
        ("dimensions", True),
        ("dimensions", 768),
        ("compatibility_version", ""),
        ("compatibility_version", "wrong-version"),
        ("max_batch_size", 0),
        ("max_batch_size", True),
        ("max_batch_size", 2),
    ],
)
def test_constructor_rejects_invalid_or_unverified_configuration(
    override: str, value: object
) -> None:
    kwargs = {
        "client": _FakeClient(),
        "model": _MODEL,
        "dimensions": _DIMENSIONS,
        "compatibility_version": _COMPATIBILITY,
        "max_batch_size": 1,
    }
    kwargs[override] = value

    with pytest.raises(EmbeddingVectorStoreConfigurationError):
        GeminiEmbeddingProvider(**kwargs)


def test_identity_and_capacity_are_the_verified_v1_contract() -> None:
    provider = _provider()

    assert provider.identity.provider == "gemini"
    assert provider.identity.model == _MODEL
    assert provider.identity.dimensions == _DIMENSIONS
    assert provider.identity.compatibility_version == _COMPATIBILITY
    assert provider.max_batch_size == 1


def test_document_and_query_calls_use_distinct_exact_retrieval_modes() -> None:
    client = _FakeClient()
    provider = _provider(client)

    provider.embed_documents((_DOCUMENT,))
    provider.embed_query("query")

    document_call, query_call = client.models.calls
    assert document_call.model == query_call.model == _MODEL
    assert document_call.contents == ["document"]
    assert query_call.contents == ["query"]
    assert document_call.config.task_type == "RETRIEVAL_DOCUMENT"
    assert query_call.config.task_type == "RETRIEVAL_QUERY"
    assert document_call.config.output_dimensionality == _DIMENSIONS
    assert query_call.config.output_dimensionality == _DIMENSIONS
    assert document_call.config.auto_truncate is None
    assert query_call.config.auto_truncate is None


def test_response_is_converted_positionally_without_changing_values() -> None:
    values = [float(index) / _DIMENSIONS for index in range(_DIMENSIONS)]
    provider = _provider(_FakeClient(_response(values)))

    result = provider.embed_documents((_DOCUMENT,))

    assert len(result) == 1
    assert result[0].values == tuple(values)


@pytest.mark.parametrize("documents", [(), (_DOCUMENT, _DOCUMENT)])
def test_invalid_batch_is_rejected_before_sdk_call(
    documents: tuple[EmbeddingDocument, ...]
) -> None:
    client = _FakeClient()
    provider = _provider(client)

    with pytest.raises(EmbeddingInvalidRequestError):
        provider.embed_documents(documents)

    assert client.models.calls == []


def test_oversize_document_and_query_are_rejected_before_sdk_call() -> None:
    client = _FakeClient()
    provider = _provider(client)
    exact_limit = "é" * 768
    oversize = exact_limit + "x"

    provider.embed_documents((EmbeddingDocument("b" * 64, exact_limit),))
    with pytest.raises(EmbeddingDocumentTooLarge):
        provider.embed_documents((EmbeddingDocument("c" * 64, oversize),))
    with pytest.raises(EmbeddingInvalidRequestError):
        provider.embed_query(oversize)

    assert len(client.models.calls) == 1
    assert client.models.calls[0].contents == [exact_limit]


@pytest.mark.parametrize("query", ["", 7, None])
def test_invalid_query_is_rejected_before_sdk_call(query: object) -> None:
    client = _FakeClient()
    provider = _provider(client)

    with pytest.raises(EmbeddingInvalidRequestError):
        provider.embed_query(query)  # type: ignore[arg-type]

    assert client.models.calls == []


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (errors.ClientError(401, {"secret": "raw-body"}), EmbeddingAuthenticationError),
        (errors.ClientError(403, {"secret": "raw-body"}), EmbeddingAuthenticationError),
        (errors.ClientError(408, {"secret": "raw-body"}), EmbeddingTransientError),
        (errors.ClientError(429, {"secret": "raw-body"}), EmbeddingRateLimitError),
        (errors.ClientError(400, {"secret": "raw-body"}), EmbeddingInvalidRequestError),
        (errors.ClientError(404, {"secret": "raw-body"}), EmbeddingInvalidRequestError),
        (errors.ServerError(500, {"secret": "raw-body"}), EmbeddingTransientError),
        (errors.ServerError(502, {"secret": "raw-body"}), EmbeddingTransientError),
        (errors.ServerError(503, {"secret": "raw-body"}), EmbeddingTransientError),
        (errors.ServerError(504, {"secret": "raw-body"}), EmbeddingTransientError),
        (errors.ServerError(501, {"secret": "raw-body"}), EmbeddingProviderError),
        (ValueError("raw-body"), EmbeddingInvalidRequestError),
        (errors.UnknownApiResponseError("raw-body"), EmbeddingInvalidResponseError),
        (httpx.ReadTimeout("raw-body"), EmbeddingTransientError),
        (httpx.ConnectError("raw-body"), EmbeddingTransientError),
    ],
)
def test_verified_failures_map_to_fixed_sanitized_types(
    failure: Exception, expected: type[Exception]
) -> None:
    provider = _provider(_FakeClient(failure=failure))

    with pytest.raises(expected) as captured:
        provider.embed_query("query")

    assert "raw-body" not in str(captured.value)


def test_413_maps_document_size_and_query_request_without_leaking_body() -> None:
    document_provider = _provider(
        _FakeClient(failure=errors.ClientError(413, {"secret": "raw-body"}))
    )
    query_provider = _provider(
        _FakeClient(failure=errors.ClientError(413, {"secret": "raw-body"}))
    )

    with pytest.raises(EmbeddingDocumentTooLarge) as document_error:
        document_provider.embed_documents((_DOCUMENT,))
    with pytest.raises(EmbeddingInvalidRequestError) as query_error:
        query_provider.embed_query("query")

    assert "raw-body" not in str(document_error.value)
    assert "raw-body" not in str(query_error.value)


def test_classification_never_uses_exception_message_substrings() -> None:
    provider = _provider(
        _FakeClient(failure=ValueError("401 429 timeout authentication rate limit"))
    )

    with pytest.raises(EmbeddingInvalidRequestError):
        provider.embed_query("query")


def test_sdk_request_config_validation_remains_an_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    provider = _provider(client)
    real_constructor = types.EmbedContentConfig

    def reject_request_config(**kwargs: object) -> object:
        return real_constructor(output_dimensionality="raw-body")  # type: ignore[arg-type]

    monkeypatch.setattr(
        "backend.embedding_vector_store.providers.gemini.types.EmbedContentConfig",
        reject_request_config,
    )

    with pytest.raises(EmbeddingInvalidRequestError) as captured:
        provider.embed_query("query")

    assert client.models.calls == []
    assert "raw-body" not in str(captured.value)
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            content=b"raw-body-not-json",
            headers={"content-type": "application/json"},
        ),
        httpx.Response(
            200,
            json={"embeddings": [{"values": ["raw-body-not-a-number"]}]},
        ),
    ],
)
def test_real_sdk_response_decode_and_validation_failures_are_invalid_responses(
    response: httpx.Response,
) -> None:
    client = _real_sdk_client_with_response(response)
    provider = _provider(client)

    try:
        with pytest.raises(EmbeddingInvalidResponseError) as captured:
            provider.embed_query("query")
    finally:
        client.close()

    assert "raw-body" not in str(captured.value)
    assert captured.value.__context__ is None


@pytest.mark.parametrize("value", [True, False, "0.125"], ids=["true", "false", "numeric-string"])
@pytest.mark.parametrize("document_request", [False, True], ids=["query", "document"])
def test_real_sdk_rejects_embedding_values_that_would_be_coerced(
    value: object, document_request: bool, caplog: pytest.LogCaptureFixture,
) -> None:
    client = _real_sdk_client_with_response(httpx.Response(
        200,
        json={"embeddings": [{"values": [value] * _DIMENSIONS}],
              "sensitive": "raw-body-secret"},
    ))
    provider = _provider(client)

    try:
        with pytest.raises(EmbeddingInvalidResponseError) as captured:
            if document_request:
                provider.embed_documents((_DOCUMENT,))
            else:
                provider.embed_query("query")
        # The caller's original SDK client must retain its normal parsing behavior.
        direct = client.models.embed_content(model=_MODEL, contents=["query"])
        assert direct.embeddings[0].values == [float(value)] * _DIMENSIONS
    finally:
        client.close()

    assert "raw-body" not in str(captured.value)
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None
    assert "raw-body-secret" not in "".join(traceback.format_exception(captured.value))
    assert "raw-body-secret" not in caplog.text
    assert "raw-body-secret" not in repr(provider)


@pytest.mark.parametrize("missing", [False, True], ids=["changed", "absent"])
def test_real_sdk_private_request_seam_changes_fail_closed(
    monkeypatch: pytest.MonkeyPatch, missing: bool,
) -> None:
    client = _real_sdk_client_with_response(httpx.Response(200, json={}))
    with monkeypatch.context() as patch:
        if missing:
            patch.delattr(client.models, "_api_client")
        else:
            patch.setattr(client.models, "_api_client", SimpleNamespace(request=None))
        try:
            with pytest.raises(EmbeddingVectorStoreConfigurationError) as captured:
                _provider(client)
        finally:
            client.close()

    assert str(captured.value) == "Gemini embedding provider configuration is invalid."
    assert captured.value.__context__ is None


def test_real_sdk_preserves_valid_numeric_embedding_values() -> None:
    values = [0, 1, -2, 0.125] * (_DIMENSIONS // 4)
    client = _real_sdk_client_with_response(httpx.Response(
        200, json={"embeddings": [{"values": values}]},
    ))

    try:
        provider = _provider(client)
        assert provider.embed_query("query").values == tuple(values)
        assert provider.embed_documents((_DOCUMENT,))[0].values == tuple(values)
    finally:
        client.close()


@pytest.mark.parametrize(
    "body",
    [
        "{}", "null", "[]", '{"embeddings": null}', '{"embeddings": []}',
        '{"embeddings": [null]}', '{"embeddings": [{}]}',
        '{"embeddings": [{"values": null}]}',
        '{"embeddings": [{"values": []}]}',
        json.dumps({"embeddings": [{"values": [0.0] * _DIMENSIONS}] * 2}),
        json.dumps({"embeddings": [{"values": [0.0] * (_DIMENSIONS - 1)}]}),
        *('{"embeddings": [{"values": [' + ','.join([value] * _DIMENSIONS)
          + ']}]}' for value in ("NaN", "Infinity", "-Infinity", "1e400")),
    ],
    ids=["missing", "null-root", "array-root", "null-embeddings", "empty-embeddings",
         "null-embedding", "missing-values", "null-values", "empty-values",
         "excess-embeddings", "wrong-dimension", "nan", "inf", "negative-inf", "overflow"],
)
def test_real_sdk_malformed_embedding_shape_is_invalid_response(body: str) -> None:
    client = _real_sdk_client_with_response(httpx.Response(200, text=body))
    try:
        with pytest.raises(EmbeddingInvalidResponseError):
            _provider(client).embed_query("query")
    finally:
        client.close()


@pytest.mark.parametrize("body", ["[" * 10000 + "]" * 10000,
                                  '{"embeddings": [{"values": [' + "1" * 5000 + ']}]}'],
                         ids=["nesting-limit", "integer-limit"])
def test_real_sdk_json_resource_limit_is_invalid_response(body: str) -> None:
    client = _real_sdk_client_with_response(httpx.Response(200, text=body))
    try:
        with pytest.raises(EmbeddingInvalidResponseError) as captured:
            _provider(client).embed_query("query")
    finally:
        client.close()
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None


@pytest.mark.parametrize("body", [None, b"{}", {}], ids=["absent", "bytes", "dict"])
def test_real_sdk_changed_response_body_type_fails_closed(
    monkeypatch: pytest.MonkeyPatch, body: object,
) -> None:
    client = _real_sdk_client_with_response(httpx.Response(200, json={}))
    monkeypatch.setattr(client.models._api_client, "request",
                        lambda *args, **kwargs: SimpleNamespace(body=body))
    try:
        with pytest.raises(EmbeddingInvalidResponseError):
            _provider(client).embed_query("query")
    finally:
        client.close()


def test_real_sdk_request_seam_bypass_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _real_sdk_client_with_response(httpx.Response(200, json={}))
    monkeypatch.setattr(type(client.models), "embed_content",
                        lambda *args, **kwargs: _response([1.0] * _DIMENSIONS))
    try:
        with pytest.raises(EmbeddingInvalidResponseError):
            _provider(client).embed_query("query")
    finally:
        client.close()


@pytest.mark.parametrize(("status", "expected"), [
    (401, EmbeddingAuthenticationError), (403, EmbeddingAuthenticationError),
    (408, EmbeddingTransientError), (429, EmbeddingRateLimitError),
    (400, EmbeddingInvalidRequestError), (413, EmbeddingInvalidRequestError),
    (500, EmbeddingTransientError), (503, EmbeddingTransientError),
    (501, EmbeddingProviderError),
])
def test_real_sdk_http_failure_precedes_raw_embedding_validation(
    status: int, expected: type[Exception],
) -> None:
    client = _real_sdk_client_with_response(httpx.Response(
        status, json={"error": {"code": status, "message": "raw-body-secret"}},
    ))
    try:
        with pytest.raises(expected) as captured:
            _provider(client).embed_query("query")
    finally:
        client.close()
    assert "raw-body-secret" not in str(captured.value)
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None


def test_real_sdk_concurrent_calls_do_not_share_validation_or_mutate_client() -> None:
    barrier = Barrier(3, timeout=10)

    def respond(request: httpx.Request) -> httpx.Response:
        # All calls overlap while inside the shared transport.
        barrier.wait()
        payload = json.loads(request.content)
        query = payload["requests"][0]["content"]["parts"][0]["text"]
        value = 0.125 if query == "valid" else True
        return httpx.Response(200, json={"embeddings": [{"values": [value] * _DIMENSIONS}]})

    client = genai.Client(api_key="offline-key", vertexai=False,
                         http_options=types.HttpOptions(
                             httpx_client=httpx.Client(transport=httpx.MockTransport(respond))))
    provider = _provider(client)
    original_models = client.models
    original_api = original_models._api_client
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            valid = executor.submit(provider.embed_query, "valid")
            invalid = executor.submit(provider.embed_query, "invalid")
            direct = executor.submit(client.models.embed_content, model=_MODEL, contents=["direct"])
            assert valid.result(timeout=15).values == (0.125,) * _DIMENSIONS
            with pytest.raises(EmbeddingInvalidResponseError):
                invalid.result(timeout=15)
            assert direct.result(timeout=15).embeddings[0].values == [1.0] * _DIMENSIONS
        assert client.models is original_models
        assert client.models._api_client is original_api
    finally:
        client.close()


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(embeddings=None),
        SimpleNamespace(),
        _response(),
        _response([0.0] * _DIMENSIONS, [1.0] * _DIMENSIONS),
        _response([0.0] * (_DIMENSIONS - 1)),
        _response([0.0] * (_DIMENSIONS - 1) + [math.nan]),
        _response([0.0] * (_DIMENSIONS - 1) + [math.inf]),
        _response([0.0] * (_DIMENSIONS - 1) + [True]),
        _response([0.0] * (_DIMENSIONS - 1) + ["0"]),
        _response(None),
    ],
)
def test_malformed_responses_are_permanent_invalid_response_failures(
    response: object,
) -> None:
    provider = _provider(_FakeClient(response))

    with pytest.raises(EmbeddingInvalidResponseError):
        provider.embed_query("query")
