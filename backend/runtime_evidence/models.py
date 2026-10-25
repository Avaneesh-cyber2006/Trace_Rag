"""Public runtime evidence models."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceInvariantError,
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.validation import (
    require_exact_bool,
    require_nonnegative_int,
    require_positive_int,
    require_relative_posix_path,
    require_scalar_text,
    require_sha256_digest,
)

_MAX_SOURCE_NAME_BYTES = 128
_MAX_NAMESPACE_BYTES = 256
_MAX_REVISION_BYTES = 256
_MAX_CAPTURED_AT_BYTES = 128

_ASCII_WHITESPACE = frozenset(" \t\n\r\v\f")
_SOURCE_NAME_FORBIDDEN_CHARACTERS = frozenset("/\\:")

_DURATION_MS_TEXT_PATTERN = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")

_EVENT_OPTIONAL_TEXT_FIELDS = (
    "timestamp_text",
    "level",
    "message",
    "logger",
    "operation",
    "trace_id",
    "span_id",
    "parent_span_id",
    "status",
    "error_type",
    "error_message",
)

_ARTIFACT_COUNTER_FIELDS = (
    "event_count",
    "frame_count",
    "duplicate_count",
    "issue_count",
)


class RuntimeArtifactFormat(str, Enum):
    TEXT_LOG = "text_log"
    JSON_EVENTS = "json_events"
    STACK_TRACE_TEXT = "stack_trace_text"


class RuntimeArtifactStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class RuntimeEventKind(str, Enum):
    LOG = "log"
    ERROR = "error"
    SPAN = "span"


class RuntimeFrameLanguage(str, Enum):
    PYTHON = "python"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    UNKNOWN = "unknown"


class CaptureCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class GraphBinding(str, Enum):
    NOT_PROVIDED = "not_provided"
    NAMESPACE_MATCHED_UNVERSIONED = "namespace_matched_unversioned"


class PathMatchBasis(str, Enum):
    EXACT_RELATIVE_PATH = "exact_relative_path"
    UNVERIFIED_RELATIVE_PATH = "unverified_relative_path"
    UNIQUE_PATH_SUFFIX = "unique_path_suffix"
    UNIQUE_BASENAME = "unique_basename"
    EXTERNAL_OR_NON_FILE = "external_or_non_file"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


class FrameAssociation(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    UNASSOCIATED = "unassociated"
    FILE = "file"
    SYMBOL = "symbol"
    AMBIGUOUS_SYMBOL = "ambiguous_symbol"


class RuntimeIncompleteReason(str, Enum):
    ARTIFACT_FAILED = "artifact_failed"
    ARTIFACT_PARTIAL = "artifact_partial"
    ARTIFACT_SKIPPED = "artifact_skipped"
    EVENT_LIMIT_REACHED = "event_limit_reached"
    FRAME_LIMIT_REACHED = "frame_limit_reached"
    ISSUE_LIMIT_REACHED = "issue_limit_reached"
    TEXT_TRUNCATED = "text_truncated"


class RuntimeIssueCode(str, Enum):
    ARTIFACT_TOO_LARGE = "artifact_too_large"
    INVALID_UTF8 = "invalid_utf8"
    NUL_BYTE = "nul_byte"
    LINE_TOO_LONG = "line_too_long"
    MALFORMED_JSON = "malformed_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    INVALID_EVENT_SCHEMA = "invalid_event_schema"
    STACK_LANGUAGE_MISSING = "stack_language_missing"
    STACK_TOO_LARGE = "stack_too_large"
    STACK_UNPARSEABLE = "stack_unparseable"
    INVALID_SOURCE_LOCATION = "invalid_source_location"
    TEXT_TRUNCATED = "text_truncated"
    FRAME_LIMIT_REACHED = "frame_limit_reached"
    EVENT_LIMIT_REACHED = "event_limit_reached"
    ISSUE_LIMIT_REACHED = "issue_limit_reached"


_VERIFIED_PATH_BASES = frozenset({
    PathMatchBasis.EXACT_RELATIVE_PATH,
    PathMatchBasis.UNIQUE_PATH_SUFFIX,
    PathMatchBasis.UNIQUE_BASENAME,
})
_PATH_BASES_WITH_PATH = frozenset({
    *_VERIFIED_PATH_BASES,
    PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
    PathMatchBasis.AMBIGUOUS,
    PathMatchBasis.UNMATCHED,
})
_PATH_BASES_WITHOUT_PATH = frozenset({
    PathMatchBasis.EXTERNAL_OR_NON_FILE,
    PathMatchBasis.AMBIGUOUS,
    PathMatchBasis.UNMATCHED,
})

ISSUE_MESSAGES: Mapping[RuntimeIssueCode, str] = MappingProxyType({
    RuntimeIssueCode.ARTIFACT_TOO_LARGE:
        "artifact exceeds the configured byte limit",
    RuntimeIssueCode.INVALID_UTF8: "line is not valid UTF-8",
    RuntimeIssueCode.NUL_BYTE: "NUL byte is not allowed",
    RuntimeIssueCode.LINE_TOO_LONG:
        "line exceeds the configured byte limit",
    RuntimeIssueCode.MALFORMED_JSON:
        "line or document is not valid JSON",
    RuntimeIssueCode.DUPLICATE_JSON_KEY:
        "JSON object contains a duplicate key",
    RuntimeIssueCode.INVALID_EVENT_SCHEMA:
        "event does not match the supported schema",
    RuntimeIssueCode.STACK_LANGUAGE_MISSING:
        "stack cannot be parsed without a known language",
    RuntimeIssueCode.STACK_TOO_LARGE:
        "stack exceeds the configured byte limit",
    RuntimeIssueCode.STACK_UNPARSEABLE:
        "stack contains no recognized frame",
    RuntimeIssueCode.INVALID_SOURCE_LOCATION:
        "source location is invalid",
    RuntimeIssueCode.TEXT_TRUNCATED:
        "text was truncated to the configured limit",
    RuntimeIssueCode.FRAME_LIMIT_REACHED: "frame limit was reached",
    RuntimeIssueCode.EVENT_LIMIT_REACHED: "event limit was reached",
    RuntimeIssueCode.ISSUE_LIMIT_REACHED:
        "additional issues were omitted",
})


def _require_source_name(
    value: object,
    error_type: type[Exception] = RuntimeEvidenceValidationError,
) -> str:
    text = require_scalar_text(
        value,
        "source_name",
        error_type,
        allow_empty=False,
        max_bytes=_MAX_SOURCE_NAME_BYTES,
    )
    if any(
        character in _SOURCE_NAME_FORBIDDEN_CHARACTERS
        or ord(character) < 0x20
        for character in text
    ):
        raise error_type("source_name contains a forbidden character")
    if text[0] in _ASCII_WHITESPACE or text[-1] in _ASCII_WHITESPACE:
        raise error_type(
            "source_name must not have leading or trailing ASCII whitespace"
        )
    return text


def _require_namespace(value: object) -> str:
    return require_scalar_text(
        value,
        "repository_namespace",
        RuntimeEvidenceValidationError,
        allow_empty=False,
        max_bytes=_MAX_NAMESPACE_BYTES,
    )


def _require_optional_claim(
    value: object,
    field_name: str,
    max_bytes: int,
) -> None:
    if value is None:
        return
    require_scalar_text(
        value,
        field_name,
        RuntimeEvidenceValidationError,
        max_bytes=max_bytes,
    )


def _require_exact_enum(
    value: object,
    enum_type: type[Enum],
    field_name: str,
    error_type: type[Exception] = RuntimeEvidenceValidationError,
) -> None:
    if type(value) is not enum_type:
        raise error_type(
            f"{field_name} must be an exact {enum_type.__name__}"
        )


def _require_exact_tuple(value: object, field_name: str) -> tuple:
    if type(value) is not tuple:
        raise RuntimeEvidenceInvariantError(
            f"{field_name} must be an exact tuple"
        )
    return value


def _require_optional_text(value: object, field_name: str) -> None:
    if value is None:
        return
    require_scalar_text(
        value, field_name, RuntimeEvidenceInvariantError
    )


def _require_optional_positive_int(value: object, field_name: str) -> None:
    if value is None:
        return
    require_positive_int(
        value, field_name, RuntimeEvidenceInvariantError
    )


def _require_optional_nonnegative_int(
    value: object, field_name: str
) -> None:
    if value is None:
        return
    require_nonnegative_int(
        value, field_name, RuntimeEvidenceInvariantError
    )


def _require_nonempty_node_id(value: object, field_name: str) -> str:
    return require_scalar_text(
        value,
        field_name,
        RuntimeEvidenceInvariantError,
        allow_empty=False,
    )


def issue_message(code: RuntimeIssueCode) -> str:
    """Return the fixed issue message for ``code``."""
    _require_exact_enum(
        code,
        RuntimeIssueCode,
        "code",
        RuntimeEvidenceInvariantError,
    )
    return ISSUE_MESSAGES[code]


@dataclass(frozen=True, slots=True)
class RuntimeTag:
    key: str
    value: str

    def __post_init__(self) -> None:
        require_scalar_text(
            self.key, "key", RuntimeEvidenceValidationError
        )
        require_scalar_text(
            self.value, "value", RuntimeEvidenceValidationError
        )


@dataclass(frozen=True, slots=True)
class RuntimeArtifact:
    source_name: str
    format: RuntimeArtifactFormat
    content: bytes
    declared_language: RuntimeFrameLanguage | None = None

    def __post_init__(self) -> None:
        _require_source_name(self.source_name)
        _require_exact_enum(
            self.format, RuntimeArtifactFormat, "format"
        )
        if type(self.content) is not bytes:
            raise RuntimeEvidenceValidationError(
                "content must be exact bytes"
            )
        if self.declared_language is not None:
            _require_exact_enum(
                self.declared_language,
                RuntimeFrameLanguage,
                "declared_language",
            )
        if (
            self.format is RuntimeArtifactFormat.STACK_TRACE_TEXT
            and (
                self.declared_language is None
                or self.declared_language is RuntimeFrameLanguage.UNKNOWN
            )
        ):
            raise RuntimeEvidenceValidationError(
                "STACK_TRACE_TEXT requires a known declared_language"
            )


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceRequest:
    repository_namespace: str
    artifacts: tuple[RuntimeArtifact, ...]
    declared_revision: str | None = None
    declared_captured_at: str | None = None
    declared_capture_completeness: CaptureCompleteness = (
        CaptureCompleteness.UNKNOWN
    )

    def __post_init__(self) -> None:
        _require_namespace(self.repository_namespace)
        if type(self.artifacts) is not tuple:
            raise RuntimeEvidenceValidationError(
                "artifacts must be an exact tuple"
            )
        if any(
            type(artifact) is not RuntimeArtifact
            for artifact in self.artifacts
        ):
            raise RuntimeEvidenceValidationError(
                "artifacts must contain only exact RuntimeArtifact instances"
            )
        _require_optional_claim(
            self.declared_revision,
            "declared_revision",
            _MAX_REVISION_BYTES,
        )
        _require_optional_claim(
            self.declared_captured_at,
            "declared_captured_at",
            _MAX_CAPTURED_AT_BYTES,
        )
        _require_exact_enum(
            self.declared_capture_completeness,
            CaptureCompleteness,
            "declared_capture_completeness",
        )


@dataclass(frozen=True, slots=True)
class RuntimeStackFrame:
    frame_id: str
    event_id: str
    ordinal: int
    language: RuntimeFrameLanguage
    function_text: str | None
    relative_path: str | None
    line: int | None
    column: int | None
    source_reference_sha256: str | None
    path_match_basis: PathMatchBasis
    association: FrameAssociation
    primary_node_id: str | None
    candidate_node_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_sha256_digest(
            self.frame_id, "frame_id", RuntimeEvidenceInvariantError
        )
        require_sha256_digest(
            self.event_id, "event_id", RuntimeEvidenceInvariantError
        )
        require_nonnegative_int(
            self.ordinal, "ordinal", RuntimeEvidenceInvariantError
        )
        _require_exact_enum(
            self.language,
            RuntimeFrameLanguage,
            "language",
            RuntimeEvidenceInvariantError,
        )
        _require_optional_text(self.function_text, "function_text")
        if self.relative_path is not None:
            require_relative_posix_path(
                self.relative_path,
                "relative_path",
                RuntimeEvidenceInvariantError,
            )
        _require_optional_positive_int(self.line, "line")
        _require_optional_nonnegative_int(self.column, "column")
        self._validate_source_reference()
        _require_exact_enum(
            self.path_match_basis,
            PathMatchBasis,
            "path_match_basis",
            RuntimeEvidenceInvariantError,
        )
        _require_exact_enum(
            self.association,
            FrameAssociation,
            "association",
            RuntimeEvidenceInvariantError,
        )
        self._validate_path_basis()
        self._validate_candidates()
        self._validate_association_shape()

    def _validate_source_reference(self) -> None:
        if self.relative_path is None:
            if self.source_reference_sha256 is not None:
                raise RuntimeEvidenceInvariantError(
                    "source_reference_sha256 must be absent when "
                    "relative_path is absent"
                )
            return
        require_sha256_digest(
            self.source_reference_sha256,
            "source_reference_sha256",
            RuntimeEvidenceInvariantError,
        )

    def _validate_path_basis(self) -> None:
        has_path = self.relative_path is not None
        if has_path:
            if self.path_match_basis not in _PATH_BASES_WITH_PATH:
                raise RuntimeEvidenceInvariantError(
                    "path_match_basis is invalid when relative_path is present"
                )
        elif self.path_match_basis not in _PATH_BASES_WITHOUT_PATH:
            raise RuntimeEvidenceInvariantError(
                "path_match_basis is invalid when relative_path is absent"
            )
        if self.association is FrameAssociation.NOT_EVALUATED:
            allowed = (
                (
                    self.path_match_basis
                    is PathMatchBasis.UNVERIFIED_RELATIVE_PATH
                    and has_path
                )
                or (
                    self.path_match_basis is PathMatchBasis.UNMATCHED
                    and not has_path
                )
                or (
                    self.path_match_basis
                    is PathMatchBasis.EXTERNAL_OR_NON_FILE
                    and not has_path
                )
            )
        elif self.association is FrameAssociation.UNASSOCIATED:
            allowed = (
                self.path_match_basis
                in (PathMatchBasis.UNMATCHED, PathMatchBasis.AMBIGUOUS)
                or (
                    self.path_match_basis
                    is PathMatchBasis.EXTERNAL_OR_NON_FILE
                    and not has_path
                )
            )
        else:
            allowed = (
                has_path
                and self.path_match_basis in _VERIFIED_PATH_BASES
            )
        if not allowed:
            raise RuntimeEvidenceInvariantError(
                "association and path_match_basis are incompatible"
            )

    def _validate_candidates(self) -> None:
        candidates = _require_exact_tuple(
            self.candidate_node_ids, "candidate_node_ids"
        )
        previous: str | None = None
        for candidate in candidates:
            current = _require_nonempty_node_id(
                candidate, "candidate_node_ids"
            )
            if previous is not None and previous >= current:
                raise RuntimeEvidenceInvariantError(
                    "candidate_node_ids must be sorted and unique"
                )
            previous = current
        if self.primary_node_id is not None:
            _require_nonempty_node_id(
                self.primary_node_id, "primary_node_id"
            )
            if self.primary_node_id not in candidates:
                raise RuntimeEvidenceInvariantError(
                    "primary_node_id must be contained in "
                    "candidate_node_ids"
                )

    def _validate_association_shape(self) -> None:
        if self.association in (
            FrameAssociation.NOT_EVALUATED,
            FrameAssociation.UNASSOCIATED,
        ):
            if self.candidate_node_ids or self.primary_node_id is not None:
                raise RuntimeEvidenceInvariantError(
                    "association must not carry candidates or a primary node"
                )
        elif self.association is FrameAssociation.FILE:
            if (
                len(self.candidate_node_ids) != 1
                or self.primary_node_id != self.candidate_node_ids[0]
            ):
                raise RuntimeEvidenceInvariantError(
                    "FILE association requires exactly one candidate, "
                    "which must be the primary"
                )
        elif self.association is FrameAssociation.SYMBOL:
            if (
                len(self.candidate_node_ids) != 2
                or self.primary_node_id not in self.candidate_node_ids
            ):
                raise RuntimeEvidenceInvariantError(
                    "SYMBOL association requires exactly two candidates "
                    "and one primary"
                )
        elif (
            len(self.candidate_node_ids) < 3
            or self.primary_node_id is not None
        ):
            raise RuntimeEvidenceInvariantError(
                "AMBIGUOUS_SYMBOL association requires at least three "
                "candidates and no primary"
            )


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    event_fingerprint: str
    artifact_id: str
    sequence: int
    source_line: int | None
    kind: RuntimeEventKind
    timestamp_text: str | None
    level: str | None
    message: str | None
    logger: str | None
    operation: str | None
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    duration_ms_text: str | None
    status: str | None
    error_type: str | None
    error_message: str | None
    tags: tuple[RuntimeTag, ...]
    frames: tuple[RuntimeStackFrame, ...]
    text_truncated: bool
    frames_truncated: bool

    def __post_init__(self) -> None:
        require_sha256_digest(
            self.event_id, "event_id", RuntimeEvidenceInvariantError
        )
        require_sha256_digest(
            self.event_fingerprint,
            "event_fingerprint",
            RuntimeEvidenceInvariantError,
        )
        require_sha256_digest(
            self.artifact_id, "artifact_id", RuntimeEvidenceInvariantError
        )
        require_nonnegative_int(
            self.sequence, "sequence", RuntimeEvidenceInvariantError
        )
        _require_optional_positive_int(self.source_line, "source_line")
        _require_exact_enum(
            self.kind,
            RuntimeEventKind,
            "kind",
            RuntimeEvidenceInvariantError,
        )
        for field_name in _EVENT_OPTIONAL_TEXT_FIELDS:
            _require_optional_text(getattr(self, field_name), field_name)
        _require_optional_text(self.duration_ms_text, "duration_ms_text")
        if (
            self.duration_ms_text is not None
            and _DURATION_MS_TEXT_PATTERN.fullmatch(
                self.duration_ms_text
            ) is None
        ):
            raise RuntimeEvidenceInvariantError(
                "duration_ms_text must be a nonnegative decimal string"
            )
        self._validate_tags()
        self._validate_frames()
        require_exact_bool(
            self.text_truncated,
            "text_truncated",
            RuntimeEvidenceInvariantError,
        )
        require_exact_bool(
            self.frames_truncated,
            "frames_truncated",
            RuntimeEvidenceInvariantError,
        )

    def _validate_tags(self) -> None:
        tags = _require_exact_tuple(self.tags, "tags")
        previous_key: str | None = None
        for tag in tags:
            if type(tag) is not RuntimeTag:
                raise RuntimeEvidenceInvariantError(
                    "tags must contain only exact RuntimeTag instances"
                )
            require_scalar_text(
                tag.key, "tag.key", RuntimeEvidenceInvariantError
            )
            require_scalar_text(
                tag.value, "tag.value", RuntimeEvidenceInvariantError
            )
            if previous_key is not None and previous_key >= tag.key:
                raise RuntimeEvidenceInvariantError(
                    "tags must be sorted by key with unique keys"
                )
            previous_key = tag.key

    def _validate_frames(self) -> None:
        frames = _require_exact_tuple(self.frames, "frames")
        for position, frame in enumerate(frames):
            if type(frame) is not RuntimeStackFrame:
                raise RuntimeEvidenceInvariantError(
                    "frames must contain only exact RuntimeStackFrame "
                    "instances"
                )
            if frame.event_id != self.event_id:
                raise RuntimeEvidenceInvariantError(
                    "frame event_id must equal the containing event_id"
                )
            if frame.ordinal != position:
                raise RuntimeEvidenceInvariantError(
                    "frame ordinals must equal their positions"
                )


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceIssue:
    code: RuntimeIssueCode
    message: str
    artifact_id: str
    event_sequence: int | None
    source_line: int | None

    def __post_init__(self) -> None:
        _require_exact_enum(
            self.code,
            RuntimeIssueCode,
            "code",
            RuntimeEvidenceInvariantError,
        )
        if (
            type(self.message) is not str
            or self.message != ISSUE_MESSAGES[self.code]
        ):
            raise RuntimeEvidenceInvariantError(
                "message must match the fixed text for code"
            )
        require_sha256_digest(
            self.artifact_id, "artifact_id", RuntimeEvidenceInvariantError
        )
        _require_optional_nonnegative_int(
            self.event_sequence, "event_sequence"
        )
        _require_optional_positive_int(self.source_line, "source_line")


@dataclass(frozen=True, slots=True)
class RuntimeArtifactRecord:
    artifact_id: str
    source_names: tuple[str, ...]
    format: RuntimeArtifactFormat
    declared_language: RuntimeFrameLanguage | None
    content_sha256: str
    size_bytes: int
    status: RuntimeArtifactStatus
    event_count: int
    frame_count: int
    duplicate_count: int
    text_truncated: bool
    frames_truncated: bool
    issue_count: int

    def __post_init__(self) -> None:
        require_sha256_digest(
            self.artifact_id, "artifact_id", RuntimeEvidenceInvariantError
        )
        self._validate_source_names()
        _require_exact_enum(
            self.format,
            RuntimeArtifactFormat,
            "format",
            RuntimeEvidenceInvariantError,
        )
        if self.declared_language is not None:
            _require_exact_enum(
                self.declared_language,
                RuntimeFrameLanguage,
                "declared_language",
                RuntimeEvidenceInvariantError,
            )
        if (
            self.format is RuntimeArtifactFormat.STACK_TRACE_TEXT
            and (
                self.declared_language is None
                or self.declared_language is RuntimeFrameLanguage.UNKNOWN
            )
        ):
            raise RuntimeEvidenceInvariantError(
                "STACK_TRACE_TEXT requires a known declared_language"
            )
        require_sha256_digest(
            self.content_sha256,
            "content_sha256",
            RuntimeEvidenceInvariantError,
        )
        require_nonnegative_int(
            self.size_bytes, "size_bytes", RuntimeEvidenceInvariantError
        )
        _require_exact_enum(
            self.status,
            RuntimeArtifactStatus,
            "status",
            RuntimeEvidenceInvariantError,
        )
        for field_name in _ARTIFACT_COUNTER_FIELDS:
            require_nonnegative_int(
                getattr(self, field_name),
                field_name,
                RuntimeEvidenceInvariantError,
            )
        require_exact_bool(
            self.text_truncated,
            "text_truncated",
            RuntimeEvidenceInvariantError,
        )
        require_exact_bool(
            self.frames_truncated,
            "frames_truncated",
            RuntimeEvidenceInvariantError,
        )

    def _validate_source_names(self) -> None:
        source_names = _require_exact_tuple(
            self.source_names, "source_names"
        )
        if not source_names:
            raise RuntimeEvidenceInvariantError(
                "source_names must not be empty"
            )
        previous_key: tuple[str, str] | None = None
        for source_name in source_names:
            _require_source_name(
                source_name, RuntimeEvidenceInvariantError
            )
            current_key = (source_name.casefold(), source_name)
            if previous_key is not None and previous_key >= current_key:
                raise RuntimeEvidenceInvariantError(
                    "source_names must be sorted by casefolded value "
                    "and contain no duplicates"
                )
            previous_key = current_key
