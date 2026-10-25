"""Public runtime evidence models."""

from dataclasses import dataclass
from enum import Enum

from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.validation import require_scalar_text

_MAX_SOURCE_NAME_BYTES = 128
_MAX_NAMESPACE_BYTES = 256
_MAX_REVISION_BYTES = 256
_MAX_CAPTURED_AT_BYTES = 128

_ASCII_WHITESPACE = frozenset(" \t\n\r\v\f")
_SOURCE_NAME_FORBIDDEN_CHARACTERS = frozenset("/\\:")


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


def _require_source_name(value: object) -> str:
    text = require_scalar_text(
        value,
        "source_name",
        RuntimeEvidenceValidationError,
        allow_empty=False,
        max_bytes=_MAX_SOURCE_NAME_BYTES,
    )
    if any(
        character in _SOURCE_NAME_FORBIDDEN_CHARACTERS
        or ord(character) < 0x20
        for character in text
    ):
        raise RuntimeEvidenceValidationError(
            "source_name contains a forbidden character"
        )
    if text[0] in _ASCII_WHITESPACE or text[-1] in _ASCII_WHITESPACE:
        raise RuntimeEvidenceValidationError(
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
) -> None:
    if type(value) is not enum_type:
        raise RuntimeEvidenceValidationError(
            f"{field_name} must be an exact {enum_type.__name__}"
        )


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
