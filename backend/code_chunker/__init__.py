"""Public interface for TraceRAG's Code Chunker."""

from .chunker import CodeChunker, chunk_code_inventory
from .exceptions import (
    ChunkerConfigurationError,
    CodeChunkerError,
    InvalidChunkInventory,
    RepositoryChunkError,
)
from .models import (
    ChunkFileStatus,
    ChunkIssue,
    ChunkIssueKind,
    ChunkKind,
    ChunkedFile,
    ChunkerConfig,
    CodeChunk,
    CodeChunkInventory,
)

__all__ = [
    "ChunkFileStatus",
    "ChunkIssue",
    "ChunkIssueKind",
    "ChunkKind",
    "ChunkedFile",
    "ChunkerConfig",
    "ChunkerConfigurationError",
    "CodeChunk",
    "CodeChunkInventory",
    "CodeChunker",
    "CodeChunkerError",
    "InvalidChunkInventory",
    "RepositoryChunkError",
    "chunk_code_inventory",
]
