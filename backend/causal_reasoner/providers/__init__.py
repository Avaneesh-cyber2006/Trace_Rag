"""Provider-neutral request contract; production SDK is lazy and opt-in."""

from .base import (
    GenerationBoundaryError, GenerationRequest, ReasoningProvider,
    prepare_generation_request,
)

__all__ = (
    "GenerationBoundaryError", "GenerationRequest", "ReasoningProvider",
    "prepare_generation_request",
)
