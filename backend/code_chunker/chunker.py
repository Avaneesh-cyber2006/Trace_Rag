"""Deterministic, snapshot-safe code chunking orchestration."""

from __future__ import annotations

from hashlib import sha256
import logging

from backend.code_parser.exceptions import RepositoryParseError
from backend.code_parser.models import ParseIssueKind, ParseStatus
from backend.code_parser.reader import SafeSourceReader, SourceReadError

from .exceptions import InvalidChunkInventory, RepositoryChunkError
from .fragmenter import FragmentationFailure, fragment_candidate
from .identity import make_chunk_id, make_content_hash
from .intervals import InvalidSymbolIntervals, build_symbol_intervals, select_candidates
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
from .locations import build_line_starts, location_from_offsets, validate_parsed_locations
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


def _log_path(relative_path: str) -> str:
    sanitized = "".join(character if character.isprintable() else "?" for character in relative_path)
    return sanitized or "<invalid>"


def _log_file(file: ChunkedFile) -> None:
    path = _log_path(file.relative_path)
    fragment_count = sum(chunk.kind is ChunkKind.FRAGMENT for chunk in file.chunks)
    logger.debug(
        "Code chunk file complete: %s status=%s chunks=%d fragments=%d",
        path,
        file.status.value,
        len(file.chunks),
        fragment_count,
    )
    for issue in file.issues:
        logger.warning("Code chunk recoverable issue: %s (%s)", path, issue.kind.value)


class CodeChunker:
    """Convert matching scanner/parser snapshots into immutable chunks."""

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self.config = ChunkerConfig() if config is None else config
        if not isinstance(self.config, ChunkerConfig):
            from .exceptions import ChunkerConfigurationError

            raise ChunkerConfigurationError("Invalid chunker configuration.")

    def chunk_inventory(self, file_inventory, parse_inventory) -> CodeChunkInventory:
        try:
            validated = validate_inputs(file_inventory, parse_inventory)
        except InvalidChunkInventory:
            logger.error("Code chunk inventory failed: invalid inventory")
            raise
        logger.info("Code chunk inventory start: %d files requested", len(validated.pairs))
        try:
            reader = SafeSourceReader(validated.repository_path)
        except RepositoryParseError as error:
            logger.error("Code chunk inventory failed: repository root unavailable")
            raise RepositoryChunkError(_REPOSITORY_ERROR) from error

        files: list[ChunkedFile] = []
        for scanned, parsed in validated.pairs:
            if parsed.status is ParseStatus.FAILED:
                outcome = _failed_file(parsed, ChunkIssueKind.PARSE_UNAVAILABLE)
                files.append(outcome)
                _log_file(outcome)
                continue
            try:
                source = reader.read(scanned)
            except SourceReadError as error:
                kind = _READ_KIND_MAP.get(error.kind, ChunkIssueKind.READ_ERROR)
                outcome = _failed_file(parsed, kind)
                files.append(outcome)
                _log_file(outcome)
                continue
            if sha256(source.original_bytes).hexdigest() != parsed.source_sha256:
                outcome = _failed_file(parsed, ChunkIssueKind.SOURCE_CHANGED)
                files.append(outcome)
                _log_file(outcome)
                del source
                continue
            line_starts = build_line_starts(source.original_bytes)
            if not validate_parsed_locations(parsed, source.original_bytes, line_starts):
                outcome = _failed_file(parsed, ChunkIssueKind.LOCATION_INVALID)
                files.append(outcome)
                _log_file(outcome)
                del source
                continue

            try:
                intervals = build_symbol_intervals(
                    parsed.symbols, len(source.original_bytes)
                )
                candidates = select_candidates(parsed, source.original_bytes, intervals)
            except (InvalidSymbolIntervals, UnicodeDecodeError):
                outcome = _failed_file(parsed, ChunkIssueKind.LOCATION_INVALID)
                files.append(outcome)
                _log_file(outcome)
                del source
                continue

            try:
                chunks = _construct_chunks(
                    validated.repository_namespace,
                    parsed.relative_path,
                    candidates,
                    source.original_bytes,
                    line_starts,
                    self.config,
                )
            except (FragmentationFailure, UnicodeDecodeError, ValueError):
                outcome = _failed_file(parsed, ChunkIssueKind.FRAGMENTATION_ERROR)
                files.append(outcome)
                _log_file(outcome)
                del source
                continue

            status = (
                ChunkFileStatus.SUCCESS
                if parsed.status is ParseStatus.SUCCESS
                else ChunkFileStatus.PARTIAL
            )
            outcome = ChunkedFile(
                    parsed.relative_path,
                    parsed.language,
                    parsed.status,
                    status,
                    parsed.source_sha256,
                    parsed.imports,
                    chunks,
                    (),
                )
            files.append(outcome)
            _log_file(outcome)
            del source

        result_files = tuple(files)
        result = CodeChunkInventory(
            validated.repository_path,
            validated.repository_namespace,
            len(result_files),
            sum(file.status is ChunkFileStatus.SUCCESS for file in result_files),
            sum(file.status is ChunkFileStatus.PARTIAL for file in result_files),
            sum(file.status is ChunkFileStatus.FAILED for file in result_files),
            sum(len(file.chunks) for file in result_files),
            result_files,
        )
        logger.info(
            "Code chunk inventory complete: %d success, %d partial, %d failed, %d chunks",
            result.success_files,
            result.partial_files,
            result.failed_files,
            result.total_chunks,
        )
        return result


def _construct_chunks(
    repository_namespace,
    relative_path,
    candidates,
    source,
    line_starts,
    config,
) -> tuple[CodeChunk, ...]:
    chunks: list[CodeChunk] = []
    for candidate in candidates:
        pieces = fragment_candidate(candidate, source, config)
        fragment_count = len(pieces)
        for fragment_index, (start, end) in enumerate(pieces):
            kind = candidate.kind if fragment_count == 1 else ChunkKind.FRAGMENT
            owner = candidate.owner
            if (
                fragment_count == 1
                and kind is ChunkKind.SYMBOL
                and owner is not None
                and owner.location.start_byte == start
                and owner.location.end_byte == end
            ):
                location = owner.location
            else:
                location = location_from_offsets(source, line_starts, start, end)
            content_bytes = source[start:end]
            content = content_bytes.decode("utf-8", errors="strict")
            chunks.append(
                CodeChunk(
                    chunk_id=make_chunk_id(
                        repository_namespace,
                        relative_path,
                        kind,
                        owner,
                        start,
                        end,
                        fragment_index,
                    ),
                    kind=kind,
                    location=location,
                    content=content,
                    content_hash=make_content_hash(content_bytes),
                    symbol_kind=None if owner is None else owner.kind,
                    symbol_name=None if owner is None else owner.name,
                    qualified_name=None if owner is None else owner.qualified_name,
                    parent_qualified_name=(
                        None if owner is None else owner.parent_qualified_name
                    ),
                    parameters=() if owner is None else owner.parameters,
                    return_type=None if owner is None else owner.return_type,
                    modifiers=() if owner is None else owner.modifiers,
                    base_types=() if owner is None else owner.base_types,
                    implemented_types=() if owner is None else owner.implemented_types,
                    fragment_index=fragment_index,
                    fragment_count=fragment_count,
                )
            )
    return tuple(
        sorted(
            chunks,
            key=lambda chunk: (
                chunk.location.start_byte,
                chunk.location.end_byte,
                chunk.kind.value,
                "" if chunk.symbol_kind is None else chunk.symbol_kind.value,
                "" if chunk.qualified_name is None else chunk.qualified_name,
                chunk.fragment_index,
                chunk.chunk_id,
            ),
        )
    )


def chunk_code_inventory(file_inventory, parse_inventory) -> CodeChunkInventory:
    """Chunk matching scanner/parser inventories with an isolated default instance."""
    return CodeChunker().chunk_inventory(file_inventory, parse_inventory)
