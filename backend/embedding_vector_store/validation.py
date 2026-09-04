"""Validation at the immutable Module 4 to Module 5 boundary."""

from dataclasses import dataclass
import math
import re

from backend.code_chunker.models import (
    ChunkFileStatus,
    ChunkKind,
    ChunkedFile,
    CodeChunk,
    CodeChunkInventory,
)
from backend.code_parser.models import ParsedLanguage, SourceLocation, SymbolKind

from .exceptions import EmbeddingInvalidResponseError, InvalidCodeChunkInventory
from .models import EmbeddingDocument, EmbeddingModelIdentity, EmbeddingVector


_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_INVALID_INVENTORY_MESSAGE = "Code chunk inventory contract is invalid."
_INVALID_EMBEDDING_RESPONSE_MESSAGE = "Embedding provider response is invalid."


@dataclass(frozen=True, slots=True)
class ChunkInput:
    """A chunk with the file context needed by Module 5."""

    chunk: CodeChunk
    relative_path: str
    language: ParsedLanguage


def _invalid() -> None:
    raise InvalidCodeChunkInventory(_INVALID_INVENTORY_MESSAGE)


def _is_lowercase_sha256(value: object) -> bool:
    return isinstance(value, str) and _LOWERCASE_SHA256.fullmatch(value) is not None


def _is_optional_nonempty_string(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(value))


def _is_posix_relative_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
    ):
        return False
    parts = value.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def validate_and_flatten_inventory(
    inventory: CodeChunkInventory,
) -> tuple[str, tuple[ChunkInput, ...]]:
    """Validate Module 5's consumed contracts and attach file context to chunks."""
    if type(inventory) is not CodeChunkInventory:
        _invalid()

    if not isinstance(inventory.repository_namespace, str) or not inventory.repository_namespace:
        _invalid()
    if type(inventory.files) is not tuple:
        _invalid()
    counters = (
        inventory.total_files_requested,
        inventory.success_files,
        inventory.partial_files,
        inventory.failed_files,
        inventory.total_chunks,
    )
    if any(type(counter) is not int or counter < 0 for counter in counters):
        _invalid()
    if inventory.total_files_requested != len(inventory.files):
        _invalid()

    chunk_ids: set[str] = set()
    inputs: list[ChunkInput] = []
    success_files = 0
    partial_files = 0
    failed_files = 0
    total_chunks = 0
    previous_path_key: tuple[str, str] | None = None

    for file in inventory.files:
        if type(file) is not ChunkedFile:
            _invalid()
        if not _is_posix_relative_path(file.relative_path):
            _invalid()
        path_key = (file.relative_path.casefold(), file.relative_path)
        if previous_path_key is not None and path_key <= previous_path_key:
            _invalid()
        previous_path_key = path_key
        if type(file.language) is not ParsedLanguage:
            _invalid()
        if type(file.status) is not ChunkFileStatus or type(file.chunks) is not tuple:
            _invalid()

        if file.status is ChunkFileStatus.SUCCESS:
            success_files += 1
        elif file.status is ChunkFileStatus.PARTIAL:
            partial_files += 1
        elif file.status is ChunkFileStatus.FAILED:
            failed_files += 1
            if file.chunks:
                _invalid()
        else:
            _invalid()

        previous_chunk_key: tuple[int, int, str, str, str, int, str] | None = None
        for chunk in file.chunks:
            if type(chunk) is not CodeChunk:
                _invalid()
            if (
                not _is_lowercase_sha256(chunk.chunk_id)
                or not _is_lowercase_sha256(chunk.content_hash)
                or type(chunk.kind) is not ChunkKind
                or type(chunk.location) is not SourceLocation
                or not isinstance(chunk.content, str)
                or not chunk.content
                or not (
                    chunk.symbol_kind is None
                    or type(chunk.symbol_kind) is SymbolKind
                )
                or not _is_optional_nonempty_string(chunk.qualified_name)
                or not _is_optional_nonempty_string(chunk.parent_qualified_name)
                or type(chunk.fragment_index) is not int
            ):
                _invalid()
            if chunk.chunk_id in chunk_ids:
                _invalid()
            chunk_ids.add(chunk.chunk_id)

            chunk_key = (
                chunk.location.start_byte,
                chunk.location.end_byte,
                chunk.kind.value,
                "" if chunk.symbol_kind is None else chunk.symbol_kind.value,
                "" if chunk.qualified_name is None else chunk.qualified_name,
                chunk.fragment_index,
                chunk.chunk_id,
            )
            if previous_chunk_key is not None and chunk_key < previous_chunk_key:
                _invalid()
            previous_chunk_key = chunk_key
            inputs.append(ChunkInput(chunk, file.relative_path, file.language))
            total_chunks += 1

    if (
        inventory.success_files != success_files
        or inventory.partial_files != partial_files
        or inventory.failed_files != failed_files
        or inventory.total_chunks != total_chunks
    ):
        _invalid()

    return inventory.repository_namespace, tuple(inputs)


def _invalid_embedding_response() -> None:
    raise EmbeddingInvalidResponseError(_INVALID_EMBEDDING_RESPONSE_MESSAGE)


def validate_embedding_vector(
    vector: EmbeddingVector, identity: EmbeddingModelIdentity
) -> EmbeddingVector:
    """Return a vector only when it belongs to the declared embedding space."""
    if not isinstance(vector, EmbeddingVector):
        _invalid_embedding_response()
    values = getattr(vector, "values", None)
    if not isinstance(values, tuple):
        _invalid_embedding_response()
    if len(values) != identity.dimensions:
        _invalid_embedding_response()
    for value in values:
        if type(value) not in (int, float) or not math.isfinite(value):
            _invalid_embedding_response()
    return vector


def validate_embedding_batch(
    documents: tuple[EmbeddingDocument, ...],
    vectors: tuple[EmbeddingVector, ...],
    identity: EmbeddingModelIdentity,
) -> tuple[EmbeddingVector, ...]:
    """Validate every response vector before preserving request/result association."""
    if type(documents) is not tuple or type(vectors) is not tuple:
        _invalid_embedding_response()
    if len(documents) != len(vectors):
        _invalid_embedding_response()
    for vector in vectors:
        validate_embedding_vector(vector, identity)
    return vectors
