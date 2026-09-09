"""Public embedding-provider contract and Gemini adapter."""

from .base import EmbeddingProvider
from .gemini import GeminiEmbeddingProvider

__all__ = ("EmbeddingProvider", "GeminiEmbeddingProvider")
