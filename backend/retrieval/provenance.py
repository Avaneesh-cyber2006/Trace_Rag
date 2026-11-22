"""Revision-bound Module 6 graph from the same source inputs as Module 5 chunks.

The binding is created by re-building Module 6's graph from validated Module 2/3
inventories, after checking Module 3 and 4's per-file original-byte digests.
At retrieval, a digest of *every* Module 5 published chunk identity is checked
under the active-generation lease. Neither matching names nor a caller-supplied
unverified graph is sufficient to enable automatic expansion.

This in-process evidence is not an authentication signature. The production
indexing pipeline must supply genuine scanner/parser/chunker inventories from
the same captured revision; an adversarial caller can forge Python dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re

from backend.code_chunker.models import ChunkFileStatus, CodeChunkInventory
from backend.code_graph.builder import CodeGraphBuilder
from backend.code_graph.models import CodeGraphInventory
from backend.code_graph.validation import validate_inputs as validate_graph_inputs
from backend.code_parser.models import ParseStatus
from backend.embedding_vector_store.validation import validate_and_flatten_inventory
from backend.embedding_vector_store.source_revision import (
    InvalidSourceRevision, SourceRevision, build_source_revision,
)
from backend.file_scanner.models import FileInventory
from backend.code_parser.models import CodeParseInventory

from .errors import InvalidHybridEvidence
from .lexical import LexicalIndex

_EVIDENCE_ERROR = "Graph retrieval revision evidence is invalid or inconsistent."
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FILES = 8_192
_MAX_CHUNKS = 8_192


def _fingerprint(rows: tuple[tuple[object, ...], ...]) -> str:
    try:
        raw = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return sha256(raw.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise InvalidHybridEvidence(_EVIDENCE_ERROR) from None


def _chunk_row(item) -> tuple[object, ...]:
    chunk = item.chunk
    return (
        chunk.chunk_id, chunk.content_hash, item.relative_path, item.language.value,
        chunk.kind.value, None if chunk.symbol_kind is None else chunk.symbol_kind.value,
        chunk.qualified_name, chunk.parent_qualified_name,
    )


def _lexical_row(item) -> tuple[object, ...]:
    return (
        item.chunk_id, item.content_hash, item.relative_path, item.language,
        item.chunk_kind, item.symbol_kind, item.qualified_name, item.parent_qualified_name,
    )


@dataclass(frozen=True, slots=True, init=False)
class GraphRevision:
    """A source-validated graph bound to the exact complete chunk manifest.

    Construct only via :func:`build_graph_revision`. The published manifest is
    compared at search time; no graph is silently joined by path or namespace.
    """
    repository_namespace: str
    manifest_fingerprint: str
    source_files: tuple[tuple[str, str], ...] = field(repr=False)
    graph: CodeGraphInventory = field(repr=False)
    published_source_revision: str | None = field(default=None, repr=False)

    def validate(self, index: LexicalIndex, pinned_source_revision: str | None = None) -> None:
        if (
            type(index) is not LexicalIndex
            or self.repository_namespace != index.repository_namespace
            or _DIGEST.fullmatch(self.manifest_fingerprint) is None
            or self.graph.repository_namespace != self.repository_namespace
            or len(index.documents) > _MAX_CHUNKS
        ):
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)
        for document in index.documents:
            try:
                if sha256(document.content.encode("utf-8")).hexdigest() != document.content_hash:
                    raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            except UnicodeError:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR) from None
        rows = tuple(_lexical_row(doc) for doc in index.documents)
        if _fingerprint(rows) != self.manifest_fingerprint:
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)
        if pinned_source_revision is not None:
            try:
                proof = SourceRevision.decode(pinned_source_revision)
                if (
                    proof.repository_namespace != self.repository_namespace
                    or proof.manifest_fingerprint != self.manifest_fingerprint
                    or proof.source_files != self.source_files
                ):
                    raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            except InvalidSourceRevision:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR) from None
        if self.published_source_revision is not None:
            if pinned_source_revision != self.published_source_revision:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)


def build_graph_revision(
    files: FileInventory,
    parsed: CodeParseInventory,
    chunks: CodeChunkInventory,
    *,
    require_published_source: bool = False,
) -> GraphRevision:
    """Rebuild graph from validated parse evidence matching chunked source bytes.

    Must run with the matched Module 2/3/4 inventories *before* they are
    discarded by the indexing pipeline. Failed or unverified source files
    cannot certify graph provenance and fail closed.
    """
    if type(require_published_source) is not bool:
        raise InvalidHybridEvidence(_EVIDENCE_ERROR)
    try:
        source_revision = build_source_revision(files, parsed, chunks)
        pairs = validate_graph_inputs(files, parsed).pairs
        namespace, flat = validate_and_flatten_inventory(chunks)
        if (
            namespace != files.repository_namespace
            or files.repository_path != parsed.repository_path
            or files.repository_path != chunks.repository_path
            or len(pairs) > _MAX_FILES
            or len(flat) > _MAX_CHUNKS
            or len(chunks.files) != len(pairs)
        ):
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)

        parse_by_path = {item.relative_path: item for _, item in pairs}
        chunk_by_path = {item.relative_path: item for item in chunks.files}
        if set(parse_by_path) != set(chunk_by_path):
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)
        source_files: list[tuple[str, str]] = []
        scanned_by_path = {scanned.relative_path: scanned for scanned, _ in pairs}
        for path, source in sorted(parse_by_path.items(), key=lambda pair: (pair[0].casefold(), pair[0])):
            chunked = chunk_by_path[path]
            if (
                source.status not in (ParseStatus.SUCCESS, ParseStatus.PARTIAL)
                or chunked.status not in (ChunkFileStatus.SUCCESS, ChunkFileStatus.PARTIAL)
                or source.status.value != chunked.parse_status.value
                or source.language is not chunked.language
                or type(source.source_sha256) is not str
                or _DIGEST.fullmatch(source.source_sha256) is None
                or source.source_sha256 != chunked.source_sha256
            ):
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            # Legacy graph revisions still require complete source byte coverage.
            # With persisted source evidence, omitted whitespace/context gaps
            # are allowed: the pinned generation carries the original digest.
            if not require_published_source:
                file_digest = sha256()
                cursor = 0
                scanned = scanned_by_path[path]
                for piece in chunked.chunks:
                    location = piece.location
                    data = piece.content.encode("utf-8")
                    if (
                        type(location.start_byte) is not int
                        or type(location.end_byte) is not int
                        or location.start_byte != cursor
                        or location.end_byte - location.start_byte != len(data)
                        or sha256(data).hexdigest() != piece.content_hash
                    ):
                        raise InvalidHybridEvidence(_EVIDENCE_ERROR)
                    file_digest.update(data)
                    cursor = location.end_byte
                if cursor != scanned.size_bytes or file_digest.hexdigest() != source.source_sha256:
                    raise InvalidHybridEvidence(_EVIDENCE_ERROR)
            source_files.append((path, source.source_sha256))

        for item in flat:
            if sha256(item.chunk.content.encode("utf-8")).hexdigest() != item.chunk.content_hash:
                raise InvalidHybridEvidence(_EVIDENCE_ERROR)

        # Do not accept a graph supplied by the caller. Construct it from the
        # same validated parser evidence whose byte hashes match the chunker.
        graph = CodeGraphBuilder().build(files, parsed)
        if graph.repository_namespace != namespace:
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)
        if (
            source_revision.manifest_fingerprint != _fingerprint(tuple(_chunk_row(x) for x in flat))
            or source_revision.source_files != tuple(source_files)
        ):
            raise InvalidHybridEvidence(_EVIDENCE_ERROR)
        binding = object.__new__(GraphRevision)
        object.__setattr__(binding, "repository_namespace", namespace)
        object.__setattr__(binding, "manifest_fingerprint", _fingerprint(tuple(_chunk_row(x) for x in flat)))
        object.__setattr__(binding, "source_files", tuple(source_files))
        object.__setattr__(binding, "graph", graph)
        object.__setattr__(binding, "published_source_revision", (
            source_revision.encode() if require_published_source else None
        ))
        return binding
    except InvalidHybridEvidence:
        raise
    except Exception:
        raise InvalidHybridEvidence(_EVIDENCE_ERROR) from None
