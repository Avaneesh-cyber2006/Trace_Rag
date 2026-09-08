"""Explicitly gated live check for the verified Gemini embedding space.

Task 1 requires explicit ``api_key=`` construction but did not name the
external variable. Task 28 resolves that boundary as
``TRACERAG_GEMINI_API_KEY``; SDK environment discovery remains forbidden.
"""

from __future__ import annotations

import logging
import math
import os

import pytest

from backend.embedding_vector_store.models import EmbeddingDocument
from backend.embedding_vector_store.providers.gemini import GeminiEmbeddingProvider


pytestmark = pytest.mark.gemini_live

_MODEL = "gemini-embedding-001"
_DIMENSIONS = 3_072
_COMPATIBILITY_VERSION = "gemini-embedding-001-retrieval-3072-v1"
_PROBE_TEXT = "tracerag-live-probe"


def setup_module() -> None:
    if os.environ.get("TRACERAG_RUN_GEMINI_LIVE") != "1":
        pytest.skip("set TRACERAG_RUN_GEMINI_LIVE=1 to enable the live Gemini check")
    if not os.environ.get("TRACERAG_GEMINI_API_KEY"):
        pytest.skip(
            "set externally injected TRACERAG_GEMINI_API_KEY to enable the live "
            "Gemini check"
        )


def test_verified_document_and_query_modes_return_safe_vectors_without_leaks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    api_key = os.environ["TRACERAG_GEMINI_API_KEY"]
    provider = GeminiEmbeddingProvider(
        api_key=api_key,
        model=_MODEL,
        dimensions=_DIMENSIONS,
        compatibility_version=_COMPATIBILITY_VERSION,
        max_batch_size=1,
    )
    document = EmbeddingDocument("0" * 64, _PROBE_TEXT)

    (document_vector,) = provider.embed_documents((document,))
    query_vector = provider.embed_query(_PROBE_TEXT)

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    log_text_lower = log_text.lower()
    leaked = any(
        sensitive in log_text
        for sensitive in (
            api_key,
            _PROBE_TEXT,
            repr(document_vector.values),
            repr(query_vector.values),
            repr(document_vector.values[0]),
            repr(query_vector.values[0]),
        )
    ) or any(
        raw_response_token in log_text_lower
        for raw_response_token in ("embeddings", "values")
    )
    caplog.clear()
    if leaked:
        pytest.fail(
            "live Gemini logging exposed sensitive request or response data",
            pytrace=False,
        )

    if provider.identity.dimensions != _DIMENSIONS:
        pytest.fail(
            "live Gemini provider identity has an unexpected dimension", pytrace=False
        )
    if (
        len(document_vector.values) != _DIMENSIONS
        or len(query_vector.values) != _DIMENSIONS
    ):
        pytest.fail(
            "live Gemini returned a vector with an unexpected dimension",
            pytrace=False,
        )
    if not all(
        math.isfinite(value)
        for vector in (document_vector, query_vector)
        for value in vector.values
    ):
        pytest.fail("live Gemini returned a non-finite vector", pytrace=False)
    if document_vector.values == query_vector.values:
        pytest.fail(
            "live Gemini document and query task modes were not distinct",
            pytrace=False,
        )
