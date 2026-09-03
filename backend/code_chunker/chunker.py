"""Deterministic, snapshot-safe code chunking orchestration."""

from __future__ import annotations

from hashlib import sha256
import logging

from backend.code_parser.exceptions import RepositoryParseError
from backend.code_parser.models import ParseIssueKind, ParseStatus
from backend.code_parser.reader import SafeSourceReader, SourceReadError

from .exceptions import RepositoryChunkError
from .models import (
    ChunkFileStatus,
    ChunkIssue,
    ChunkIssueKind,
    ChunkedFile,
    ChunkerConfig,
    CodeChunkInventory,
)
from .validation import validate_inputs


logger = logging.getLogger(__name__)

_REPOSITORY_ERROR = "Unable to establish the repository root."
_ISSUE_MESSAGES = {
    ChunkIssueKind.SOURCE_CHANGED: "Source changed after parsing.",
    ChunkIssueKind.READ_ERROR: "Unable to read source file.",
    ChunkIssueKind.PATH_INVALID: "Invalid source path.",
    ChunkIssueKind.LINK_UNSAFE: "Source path cannot be opened safely.",
    ChunkIssueKind.DECODING_ERROR: "Source file is not valid UTF-8.",
    ChunkIssueKind.PARSE_UNAVAILABLE: "Parsed source is unavailable for chunking.",
    ChunkIssueKind.LOCATION_INVALID: "Parsed source locations are invalid.",
    ChunkIssueKind.FRAGMENTATION_ERROR: "Source range could not be fragmented safely.",
}
_READ_KIND_MAP = {
    ParseIssueKind.FILE_CHANGED: ChunkIssueKind.SOURCE_CHANGED,
    ParseIssueKind.READ_ERROR: ChunkIssueKind.READ_ERROR,
    ParseIssueKind.PATH_INVALID: ChunkIssueKind.PATH_INVALID,
    ParseIssueKind.LINK_UNSAFE: ChunkIssueKind.LINK_UNSAFE,
    ParseIssueKind.DECODING_ERROR: ChunkIssueKind.DECODING_ERROR,
}


def _failed_file(parsed, kind: ChunkIssueKind) -> ChunkedFile:
    return ChunkedFile(
        relative_path=parsed.relative_path,
        language=parsed.language,
        parse_status=parsed.status,
        status=ChunkFileStatus.FAILED,
        source_sha256=parsed.source_sha256,
        imports=parsed.imports,
        chunks=(),
        issues=(ChunkIssue(kind, _ISSUE_MESSAGES[kind], None),),
    )


class CodeChunker:
    """Convert matching scanner/parser snapshots into immutable chunks."""

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self.config = ChunkerConfig() if config is None else config
        if not isinstance(self.config, ChunkerConfig):
            from .exceptions import ChunkerConfigurationError

            raise ChunkerConfigurationError("Invalid chunker configuration.")

    def chunk_inventory(self, file_inventory, parse_inventory) -> CodeChunkInventory:
        validated = validate_inputs(file_inventory, parse_inventory)
        try:
            reader = SafeSourceReader(validated.repository_path)
        except RepositoryParseError as error:
            raise RepositoryChunkError(_REPOSITORY_ERROR) from error

        files: list[ChunkedFile] = []
        for scanned, parsed in validated.pairs:
            if parsed.status is ParseStatus.FAILED:
                files.append(_failed_file(parsed, ChunkIssueKind.PARSE_UNAVAILABLE))
                continue
            try:
                source = reader.read(scanned)
            except SourceReadError as error:
                kind = _READ_KIND_MAP.get(error.kind, ChunkIssueKind.READ_ERROR)
                files.append(_failed_file(parsed, kind))
                continue
            if sha256(source.original_bytes).hexdigest() != parsed.source_sha256:
                files.append(_failed_file(parsed, ChunkIssueKind.SOURCE_CHANGED))
                continue

            status = (
                ChunkFileStatus.SUCCESS
                if parsed.status is ParseStatus.SUCCESS
                else ChunkFileStatus.PARTIAL
            )
            files.append(
                ChunkedFile(
                    parsed.relative_path,
                    parsed.language,
                    parsed.status,
                    status,
                    parsed.source_sha256,
                    parsed.imports,
                    (),
                    (),
                )
            )

        result_files = tuple(files)
        return CodeChunkInventory(
            validated.repository_path,
            validated.repository_namespace,
            len(result_files),
            sum(file.status is ChunkFileStatus.SUCCESS for file in result_files),
            sum(file.status is ChunkFileStatus.PARTIAL for file in result_files),
            sum(file.status is ChunkFileStatus.FAILED for file in result_files),
            sum(len(file.chunks) for file in result_files),
            result_files,
        )
