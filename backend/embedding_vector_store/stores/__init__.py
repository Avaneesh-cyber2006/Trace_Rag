"""Public vector-store contract and Chroma adapter."""

from .base import VectorStore
from .chroma import ChromaVectorStore

__all__ = ("VectorStore", "ChromaVectorStore")
