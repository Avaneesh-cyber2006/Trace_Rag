"""Bounded, canonical source-revision evidence for one immutable Module 5 generation.

File SHA-256 values originate from matching, validated Module 3 and 4
inventories, not from rereading an indexing-time or search-time working tree.
The manifest fingerprint is computed over the complete Module 5 chunk identity
projection (including individual content hashes). This is a consistency proof
for trusted in-process inventories, NOT caller authentication or a signature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re

from backend.code_chunker.models import ChunkFileStatus, CodeChunkInventory
from backend.code_parser.models import CodeParseInventory, ParseStatus, ParsedLanguage

from .validation import validate_and_flatten_inventory


_VERSION = "tracerag-source-revision-v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FILES = 8_192
_MAX_CHUNKS = 8_192
_MAX_JSON_BYTES = 2_097_152
_ERROR = "Source revision evidence is invalid or inconsistent."


class InvalidSourceRevision(ValueError):
    """The source manifest does not prove its claimed revision."""


def _invalid() -> None:
    raise InvalidSourceRevision(_ERROR)


def _valid_path(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 4_096
        and "\\" not in value
        and "\x00" not in value
        and not value.startswith("/")
        and not (len(value) > 1 and value[0].isalpha() and value[1] == ":")
        and all(part and part not in (".", "..") for part in value.split("/"))
    )


def _fingerprint(rows: tuple[tuple[object, ...], ...]) -> str:
    try:
        encoded = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return sha256(encoded.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        _invalid()


def _row(item: object) -> tuple[object, ...]:
    """The one identity projection shared by chunk, stored and lexical records."""
    return (
        item.chunk_id, item.content_hash, item.relative_path, item.language,
        item.chunk_kind, item.symbol_kind, item.qualified_name,
        item.parent_qualified_name,
    )


def fingerprint_records(records: tuple[object, ...]) -> str:
    if type(records) is not tuple or len(records) > _MAX_CHUNKS:
        _invalid()
    try:
        return _fingerprint(tuple(_row(item) for item in records))
    except (AttributeError, TypeError, ValueError):
        _invalid()


@dataclass(frozen=True, slots=True)
class SourceRevision:
    repository_namespace: str
    manifest_fingerprint: str
    source_files: tuple[tuple[str, str], ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.repository_namespace) is not str
            or not self.repository_namespace
            or "\x00" in self.repository_namespace
            or type(self.manifest_fingerprint) is not str
            or _DIGEST.fullmatch(self.manifest_fingerprint) is None
            or type(self.source_files) is not tuple
            or len(self.source_files) > _MAX_FILES
        ):
            _invalid()
        previous = None
        for row in self.source_files:
            if (
                type(row) is not tuple
                or len(row) != 2
                or not _valid_path(row[0])
                or type(row[1]) is not str
                or _DIGEST.fullmatch(row[1]) is None
                or (previous is not None and (row[0].casefold(), row[0]) <= previous)
            ):
                _invalid()
            previous = (row[0].casefold(), row[0])

    def encode(self) -> str:
        payload = {
            "version": _VERSION,
            "namespace": self.repository_namespace,
            "chunks_sha256": self.manifest_fingerprint,
            "files": self.source_files,
        }
        try:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(raw.encode("utf-8")) > _MAX_JSON_BYTES:
                _invalid()
            return raw
        except (UnicodeError, TypeError, ValueError):
            _invalid()

    @classmethod
    def decode(cls, encoded: object) -> SourceRevision:
        if type(encoded) is not str:
            _invalid()
        try:
            if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
                _invalid()
            payload = json.loads(encoded)
            if (
                type(payload) is not dict
                or set(payload) != {"version", "namespace", "chunks_sha256", "files"}
                or payload["version"] != _VERSION
                or type(payload["files"]) is not list
            ):
                _invalid()
            files = tuple(
                tuple(row) if type(row) is list else _invalid()
                for row in payload["files"]
            )
            revision = cls(payload["namespace"], payload["chunks_sha256"], files)
            if revision.encode() != encoded:
                _invalid()
            return revision
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            _invalid()

    def validate_records(self, records: tuple[object, ...]) -> None:
        if fingerprint_records(records) != self.manifest_fingerprint:
            _invalid()


# Module 5 cannot import Modules 2 or 6: its canonical dependency boundary
# permits only Module 3/4 models. Validate the scanner input structurally,
# then pair it with strongly typed Module 3/4 inventories. Graph construction
# separately invokes Module 6's complete deep validator.
_SCANNER_LANGUAGES = {
    ("python", ".py"): ParsedLanguage.PYTHON,
    ("java", ".java"): ParsedLanguage.JAVA,
    ("javascript", ".js"): ParsedLanguage.JAVASCRIPT,
    ("javascript", ".jsx"): ParsedLanguage.JAVASCRIPT,
    ("typescript", ".ts"): ParsedLanguage.TYPESCRIPT,
    ("typescript", ".tsx"): ParsedLanguage.TSX,
}


def _source_pairs(files: object, parsed: CodeParseInventory):
    """Check consistent scanner/parser paths without Module 2/6 imports."""
    if (
        type(parsed) is not CodeParseInventory
        or type(getattr(files, "repository_path", None)) is not str
        or not files.repository_path
        or type(getattr(files, "repository_namespace", None)) is not str
        or not files.repository_namespace
        or files.repository_namespace != parsed.repository_namespace
        or files.repository_path != parsed.repository_path
        or type(getattr(files, "files", None)) is not tuple
        or type(getattr(files, "ignored", None)) is not tuple
        or type(getattr(files, "skipped_directories", None)) is not tuple
        or type(getattr(files, "included_files", None)) is not int
        or files.included_files != len(files.files)
        or type(getattr(files, "ignored_files", None)) is not int
        or files.ignored_files != len(files.ignored)
        or type(getattr(files, "total_files_seen", None)) is not int
        or files.total_files_seen != files.included_files + files.ignored_files
        or type(parsed.files) is not tuple
        or type(parsed.skipped) is not tuple
        or parsed.total_files_requested != len(parsed.files) + len(parsed.skipped)
        or parsed.success_files != sum(
            x.status is ParseStatus.SUCCESS for x in parsed.files
        )
        or parsed.partial_files != sum(
            x.status is ParseStatus.PARTIAL for x in parsed.files
        )
        or parsed.failed_files != sum(
            x.status is ParseStatus.FAILED for x in parsed.files
        )
        or parsed.skipped_files != len(parsed.skipped)
    ):
        _invalid()
    scanned_by_path = {}
    seen_paths = set()
    for scanned in files.files:
        path = scanned.relative_path
        if (
            not _valid_path(path)
            or path in seen_paths
            or type(scanned.size_bytes) is not int
            or scanned.size_bytes < 0
            or type(scanned.language) not in (str, type(None))
            or type(scanned.extension) is not str
            or type(scanned.filename) is not str
            or getattr(scanned.category, "value", None) not in {
                "source", "test", "config", "database",
                "documentation", "build", "other_text",
            }
        ):
            _invalid()
        seen_paths.add(path)
        if scanned.category.value in ("source", "test"):
            scanned_by_path[path] = scanned
    for other in (*files.ignored, *files.skipped_directories):
        path = other.relative_path
        if not _valid_path(path) or path in seen_paths:
            _invalid()
        seen_paths.add(path)
    parsed_by_path = {}
    for p in parsed.files:
        if not _valid_path(p.relative_path) or p.relative_path in parsed_by_path:
            _invalid()
        parsed_by_path[p.relative_path] = p
    skipped = set()
    for p in parsed.skipped:
        if (
            not _valid_path(p.relative_path)
            or p.relative_path in parsed_by_path
            or p.relative_path in skipped
        ):
            _invalid()
        skipped.add(p.relative_path)
    if set(scanned_by_path) != set(parsed_by_path) | skipped:
        _invalid()
    for path in skipped:
        scanned = scanned_by_path[path]
        if _SCANNER_LANGUAGES.get(
            (scanned.language, scanned.extension)
        ) is not None:
            _invalid()
    pairs = []
    for path, p in parsed_by_path.items():
        scanned = scanned_by_path[path]
        if _SCANNER_LANGUAGES.get(
            (scanned.language, scanned.extension)
        ) is not p.language:
            _invalid()
        pairs.append((scanned, p))
    return tuple(sorted(pairs, key=lambda pair: pair[0].relative_path))

def build_source_revision(
    files: object, parsed: CodeParseInventory, chunks: CodeChunkInventory
) -> SourceRevision:
    """Prove matching original digests, bounded chunk spans and complete file set.

    Chunk gaps are allowed, because the original-byte digest is persisted and
    compared at retrieval. This intentionally does not permit claiming the
    source digest from chunk content alone. No disk read is performed.
    """
    try:
        pairs = _source_pairs(files, parsed)
        namespace, flat = validate_and_flatten_inventory(chunks)
        if (
            namespace != files.repository_namespace
            or files.repository_path != parsed.repository_path
            or files.repository_path != chunks.repository_path
            or len(pairs) > _MAX_FILES
            or len(flat) > _MAX_CHUNKS
            or len(chunks.files) != len(pairs)
        ):
            _invalid()
        parse_by_path = {p.relative_path: (scanned, p) for scanned, p in pairs}
        chunk_by_path = {item.relative_path: item for item in chunks.files}
        if set(parse_by_path) != set(chunk_by_path):
            _invalid()

        original_files = []
        for path, (scanned, source) in sorted(parse_by_path.items(), key=lambda p: (p[0].casefold(), p[0])):
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
                _invalid()
            for piece in chunked.chunks:
                location = piece.location
                data = piece.content.encode("utf-8")
                if (
                    type(location.start_byte) is not int
                    or type(location.end_byte) is not int
                    or location.start_byte < 0
                    or location.end_byte > scanned.size_bytes
                    or location.end_byte - location.start_byte != len(data)
                    or sha256(data).hexdigest() != piece.content_hash
                ):
                    _invalid()
            original_files.append((path, source.source_sha256))

        rows = []
        for item in flat:
            chunk = item.chunk
            if sha256(chunk.content.encode("utf-8")).hexdigest() != chunk.content_hash:
                _invalid()
            rows.append((
                chunk.chunk_id, chunk.content_hash, item.relative_path,
                item.language.value, chunk.kind.value,
                None if chunk.symbol_kind is None else chunk.symbol_kind.value,
                chunk.qualified_name, chunk.parent_qualified_name,
            ))
        proof = SourceRevision(namespace, _fingerprint(tuple(rows)), tuple(original_files))
        proof.encode()  # enforce serialized persistence bound before publication
        return proof
    except InvalidSourceRevision:
        raise
    except Exception:
        raise InvalidSourceRevision(_ERROR) from None
