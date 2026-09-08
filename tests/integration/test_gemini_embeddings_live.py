"""Explicitly gated live check for the verified Gemini embedding space.

Task 1 requires explicit ``api_key=`` construction but did not name the
external variable. Task 28 resolves that boundary as
``TRACERAG_GEMINI_API_KEY``; SDK environment discovery remains forbidden.
"""

from __future__ import annotations

from contextlib import contextmanager
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


@contextmanager
def _private_log_capture():
    """Isolate all handler delivery for this synchronous, single-threaded probe.

    This temporarily changes process-global logging: do not run this probe
    concurrently with other work in the same process. Intercepting delivery also
    covers loggers/handlers created by SDK construction, including nonpropagating
    loggers. Neither pytest's report handler nor stream/file handlers see records.
    """
    records = []
    root = logging.getLogger()
    loggers = [root] + [
        logger for logger in root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    states = [
        (logger, logger.handlers, logger.level, logger.propagate, logger.disabled)
        for logger in loggers
    ]
    original_dispatch = logging.Logger.callHandlers
    original_disable = root.manager.disable

    def capture(_logger, record):
        records.append(record)

    try:
        logging.Logger.callHandlers = capture
        logging.disable(logging.NOTSET)
        for logger in loggers:
            logger.handlers = []
            logger.setLevel(logging.DEBUG)
            logger.propagate = True
            logger.disabled = False
        yield records
    finally:
        for logger, handlers, level, propagate, disabled in states:
            logger.handlers = handlers
            logger.setLevel(level)
            logger.propagate = propagate
            logger.disabled = disabled
        logging.disable(original_disable)
        logging.Logger.callHandlers = original_dispatch


def test_verified_document_and_query_modes_return_safe_vectors_without_leaks() -> None:
    api_key = os.environ["TRACERAG_GEMINI_API_KEY"]
    request_failed = False
    vectors = []
    with _private_log_capture() as records:
        try:
            provider = GeminiEmbeddingProvider(
                api_key=api_key,
                model=_MODEL,
                dimensions=_DIMENSIONS,
                compatibility_version=_COMPATIBILITY_VERSION,
                max_batch_size=1,
            )
            document = EmbeddingDocument("0" * 64, _PROBE_TEXT)
            (document_vector,) = provider.embed_documents((document,))
            vectors.append(document_vector)
            query_vector = provider.embed_query(_PROBE_TEXT)
            vectors.append(query_vector)
        except Exception:
            # Provider/SDK exceptions can contain credentials and responses.
            # Fail outside this exception context, with no traceback or locals.
            request_failed = True

    try:
        # Format exception/stack metadata too; getMessage alone misses it.
        formatter = logging.Formatter()
        log_text = "\n".join(formatter.format(record) for record in records)
        sensitive_values = [api_key, _PROBE_TEXT]
        for vector in vectors:
            sensitive_values.append(repr(vector.values))
            if vector.values:
                sensitive_values.append(repr(vector.values[0]))
        leaked = any(value in log_text for value in sensitive_values) or any(
            token in log_text.lower() for token in ("embeddings", "values")
        )
    except Exception:
        # Even a malformed log record must not expose its contents in a report.
        leaked = True
    finally:
        records.clear()
    if leaked:
        pytest.fail(
            "live Gemini logging exposed sensitive request or response data",
            pytrace=False,
        )
    if request_failed:
        pytest.fail("live Gemini request failed", pytrace=False)

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
