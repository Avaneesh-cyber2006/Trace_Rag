"""Canonical identities and fingerprints for runtime evidence."""

import hashlib
import json

from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceInvariantError,
)
from backend.runtime_evidence.models import (
    RuntimeArtifactFormat,
    RuntimeEventKind,
    RuntimeFrameLanguage,
    RuntimeStackFrame,
    RuntimeTag,
)

_INVALID_MATERIAL = "invalid runtime evidence identity material"

_ARTIFACT_VERSION = "tracerag-runtime-artifact-v1"
_EVENT_VERSION = "tracerag-runtime-event-v1"
_FRAME_VERSION = "tracerag-runtime-frame-v1"
_SOURCE_REFERENCE_VERSION = "tracerag-runtime-source-reference-v1"
_FINGERPRINT_VERSION = "tracerag-runtime-event-fingerprint-v1"

_MISSING = object()


def _read_field(record: object, field_name: str) -> object:
    value = getattr(record, field_name, _MISSING)
    if value is _MISSING:
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
    return value


def _require_scalar_text(value: object) -> str:
    if type(value) is not str:
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
    return value


def _require_optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _require_scalar_text(value)


def _require_exact_enum(value: object, enum_type: type) -> None:
    if type(value) is not enum_type:
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)


def _require_nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
    return value


def _require_optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
    return value


def _require_optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    return _require_nonnegative_int(value)


def _require_exact_tuple(value: object) -> tuple:
    if type(value) is not tuple:
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
    return value


def _canonical_digest(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (
        TypeError, UnicodeEncodeError, ValueError, OverflowError,
    ):
        raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL) from None
    return hashlib.sha256(encoded).hexdigest()


def _validate_tags(tags: object) -> list[RuntimeTag]:
    values = _require_exact_tuple(tags)
    seen_keys: set[str] = set()
    for tag in values:
        if type(tag) is not RuntimeTag:
            raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
        key = _require_scalar_text(_read_field(tag, "key"))
        _require_scalar_text(_read_field(tag, "value"))
        if key in seen_keys:
            raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
        seen_keys.add(key)
    return sorted(values, key=lambda tag: tag.key)


def _validate_frames(frames: object) -> list[RuntimeStackFrame]:
    values = _require_exact_tuple(frames)
    seen_ordinals: set[int] = set()
    for frame in values:
        if type(frame) is not RuntimeStackFrame:
            raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
        ordinal = _require_nonnegative_int(
            _read_field(frame, "ordinal")
        )
        _require_exact_enum(
            _read_field(frame, "language"), RuntimeFrameLanguage
        )
        _require_optional_text(_read_field(frame, "function_text"))
        _require_optional_text(_read_field(frame, "relative_path"))
        _require_optional_positive_int(_read_field(frame, "line"))
        _require_optional_nonnegative_int(_read_field(frame, "column"))
        _require_optional_text(
            _read_field(frame, "source_reference_sha256")
        )
        if ordinal in seen_ordinals:
            raise RuntimeEvidenceInvariantError(_INVALID_MATERIAL)
        seen_ordinals.add(ordinal)
    return sorted(values, key=lambda frame: frame.ordinal)


def artifact_id(
    repository_namespace: str,
    artifact_format: RuntimeArtifactFormat,
    declared_language: RuntimeFrameLanguage | None,
    content_sha256: str,
) -> str:
    """Return the canonical identifier for one artifact."""
    _require_scalar_text(repository_namespace)
    _require_exact_enum(artifact_format, RuntimeArtifactFormat)
    if declared_language is not None:
        _require_exact_enum(declared_language, RuntimeFrameLanguage)
    _require_scalar_text(content_sha256)
    return _canonical_digest([
        _ARTIFACT_VERSION,
        repository_namespace,
        artifact_format.value,
        (
            None
            if declared_language is None
            else declared_language.value
        ),
        content_sha256,
    ])


def event_id(
    repository_namespace: str,
    artifact_id: str,
    sequence: int,
) -> str:
    """Return the canonical identifier for one event occurrence."""
    _require_scalar_text(repository_namespace)
    _require_scalar_text(artifact_id)
    _require_nonnegative_int(sequence)
    return _canonical_digest([
        _EVENT_VERSION,
        repository_namespace,
        artifact_id,
        sequence,
    ])


def frame_id(
    repository_namespace: str,
    event_id: str,
    ordinal: int,
) -> str:
    """Return the canonical identifier for one frame occurrence."""
    _require_scalar_text(repository_namespace)
    _require_scalar_text(event_id)
    _require_nonnegative_int(ordinal)
    return _canonical_digest([
        _FRAME_VERSION,
        repository_namespace,
        event_id,
        ordinal,
    ])


def source_reference_sha256(relative_path: str) -> str:
    """Return the privacy-preserving hash of a source reference."""
    _require_scalar_text(relative_path)
    return _canonical_digest([
        _SOURCE_REFERENCE_VERSION,
        relative_path,
    ])


def event_fingerprint(
    *,
    repository_namespace: str,
    kind: RuntimeEventKind,
    timestamp_text: str | None,
    level: str | None,
    message: str | None,
    logger: str | None,
    operation: str | None,
    trace_id: str | None,
    span_id: str | None,
    parent_span_id: str | None,
    duration_ms_text: str | None,
    status: str | None,
    error_type: str | None,
    error_message: str | None,
    tags: tuple[RuntimeTag, ...],
    frames: tuple[RuntimeStackFrame, ...],
) -> str:
    """Return the canonical grouping fingerprint for an event."""
    namespace = _require_scalar_text(repository_namespace)
    _require_exact_enum(kind, RuntimeEventKind)
    optional_text = [
        _require_optional_text(timestamp_text),
        _require_optional_text(level),
        _require_optional_text(message),
        _require_optional_text(logger),
        _require_optional_text(operation),
        _require_optional_text(trace_id),
        _require_optional_text(span_id),
        _require_optional_text(parent_span_id),
        _require_optional_text(duration_ms_text),
        _require_optional_text(status),
        _require_optional_text(error_type),
        _require_optional_text(error_message),
    ]
    sorted_tags = _validate_tags(tags)
    sorted_frames = _validate_frames(frames)
    return _canonical_digest([
        _FINGERPRINT_VERSION,
        namespace,
        kind.value,
        *optional_text,
        [[tag.key, tag.value] for tag in sorted_tags],
        [
            [
                frame.ordinal,
                frame.language.value,
                frame.function_text,
                frame.relative_path,
                frame.line,
                frame.column,
                frame.source_reference_sha256,
            ]
            for frame in sorted_frames
        ],
    ])
