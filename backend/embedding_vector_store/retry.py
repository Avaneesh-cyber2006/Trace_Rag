"""Bounded retries for transient embedding provider failures."""

from dataclasses import dataclass, field
from math import isfinite
from typing import Callable, TypeVar
import time

from .exceptions import EmbeddingRateLimitError, EmbeddingTransientError


T = TypeVar("T")
_RETRYABLE_ERRORS = (EmbeddingRateLimitError, EmbeddingTransientError)
_SANITIZED_RETRY_FAILURE = "Embedding operation failed after maximum retry attempts."


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Configuration for bounded embedding retries."""

    max_attempts: int = 3
    initial_delay_seconds: float = 0.25
    multiplier: float = 2.0
    max_delay_seconds: float = 2.0
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False, compare=False)

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise ValueError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        for name in ("initial_delay_seconds", "multiplier", "max_delay_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} must be a finite positive number")
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if not callable(self.sleeper):
            raise ValueError("sleeper must be callable")


def run_with_embedding_retries(
    operation: Callable[[], T], policy: RetryPolicy
) -> T:
    """Run an embedding operation, retrying only typed transient failures."""
    delay = policy.initial_delay_seconds
    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except _RETRYABLE_ERRORS as error:
            if attempt == policy.max_attempts - 1:
                raise type(error)(_SANITIZED_RETRY_FAILURE) from None
            policy.sleeper(min(delay, policy.max_delay_seconds))
            delay = min(delay * policy.multiplier, policy.max_delay_seconds)
    raise AssertionError("unreachable")
