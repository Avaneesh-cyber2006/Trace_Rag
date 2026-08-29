"""Immutable public values produced by the Code Chunker."""

from dataclasses import dataclass
from enum import Enum

from backend.code_parser.models import (
    ImportInfo,
    ParameterInfo,
    ParseStatus,
    ParsedLanguage,
    SourceLocation,
    SymbolKind,
)

from .exceptions import ChunkerConfigurationError


class ChunkKind(str, Enum):
    SYMBOL = "symbol"
    CONTEXT = "context"
    FRAGMENT = "fragment"


class ChunkFileStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ChunkIssueKind(str, Enum):
    SOURCE_CHANGED = "source_changed"
    READ_ERROR = "read_error"
    PATH_INVALID = "path_invalid"
    LINK_UNSAFE = "link_unsafe"
    DECODING_ERROR = "decoding_error"
    LOCATION_INVALID = "location_invalid"
    PARSE_UNAVAILABLE = "parse_unavailable"
    FRAGMENTATION_ERROR = "fragmentation_error"


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    target_chunk_bytes: int = 4_096
    max_chunk_bytes: int = 8_192

    def __post_init__(self) -> None:
        if (
            type(self.target_chunk_bytes) is not int
            or type(self.max_chunk_bytes) is not int
            or self.target_chunk_bytes <= 0
            or self.max_chunk_bytes <= 0
            or self.target_chunk_bytes > self.max_chunk_bytes
        ):
            raise ChunkerConfigurationError(
                "target_chunk_bytes and max_chunk_bytes must be positive integers "
                "with target_chunk_bytes <= max_chunk_bytes"
            )


@dataclass(frozen=True, slots=True)
class CodeChunk:
    chunk_id: str
    kind: ChunkKind
    location: SourceLocation
    content: str
    content_hash: str
    symbol_kind: SymbolKind | None
    symbol_name: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    parameters: tuple[ParameterInfo, ...]
    return_type: str | None
    modifiers: tuple[str, ...]
    base_types: tuple[str, ...]
    implemented_types: tuple[str, ...]
    fragment_index: int
    fragment_count: int


@dataclass(frozen=True, slots=True)
class ChunkIssue:
    kind: ChunkIssueKind
    message: str
    location: SourceLocation | None


@dataclass(frozen=True, slots=True)
class ChunkedFile:
    relative_path: str
    language: ParsedLanguage
    parse_status: ParseStatus
    status: ChunkFileStatus
    source_sha256: str | None
    imports: tuple[ImportInfo, ...]
    chunks: tuple[CodeChunk, ...]
    issues: tuple[ChunkIssue, ...]


@dataclass(frozen=True, slots=True)
class CodeChunkInventory:
    repository_path: str
    repository_namespace: str
    total_files_requested: int
    success_files: int
    partial_files: int
    failed_files: int
    total_chunks: int
    files: tuple[ChunkedFile, ...]
