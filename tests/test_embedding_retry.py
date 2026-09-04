import math

import pytest

from backend.embedding_vector_store.exceptions import (
    EmbeddingAuthenticationError,
    EmbeddingDocumentTooLarge,
    EmbeddingInvalidRequestError,
    EmbeddingInvalidResponseError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
    EmbeddingVectorStoreConfigurationError,
    InvalidCodeChunkInventory,
    VectorStoreError,
)
from backend.embedding_vector_store.retry import RetryPolicy, run_with_embedding_retries


def test_retries_rate_limits_with_exponential_delays_and_returns_result() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise EmbeddingRateLimitError("provider secret")
        return "ok"

    assert run_with_embedding_retries(operation, RetryPolicy(sleeper=sleeps.append)) == "ok"
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


def test_retry_delay_is_capped() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise EmbeddingTransientError("secret")
        return "ok"

    policy = RetryPolicy(max_attempts=4, initial_delay_seconds=1.5, multiplier=3, max_delay_seconds=2, sleeper=sleeps.append)
    assert run_with_embedding_retries(operation, policy) == "ok"
    assert sleeps == [1.5, 2.0, 2.0]


def test_maximum_attempts_and_final_error_are_bounded_and_sanitized() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise EmbeddingTransientError("token=super-secret")

    with pytest.raises(EmbeddingTransientError) as raised:
        run_with_embedding_retries(operation, RetryPolicy(max_attempts=2, sleeper=lambda _: None))
    assert attempts == 2
    assert str(raised.value) == "Embedding operation failed after maximum retry attempts."
    assert "super-secret" not in str(raised.value)


@pytest.mark.parametrize(
    "error_type",
    [
        EmbeddingAuthenticationError,
        EmbeddingVectorStoreConfigurationError,
        EmbeddingInvalidRequestError,
        EmbeddingDocumentTooLarge,
        EmbeddingInvalidResponseError,
        InvalidCodeChunkInventory,
        VectorStoreError,
        ValueError,
    ],
)
def test_non_retryable_errors_execute_once_and_never_sleep(error_type: type[Exception]) -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise error_type("sensitive detail")

    with pytest.raises(error_type):
        run_with_embedding_retries(operation, RetryPolicy(sleeper=sleeps.append))
    assert attempts == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_attempts", 0),
        ("max_attempts", True),
        ("initial_delay_seconds", 0),
        ("initial_delay_seconds", math.inf),
        ("multiplier", 0),
        ("multiplier", math.nan),
        ("max_delay_seconds", -1),
    ],
)
def test_policy_rejects_invalid_numeric_settings(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**{field: value})
