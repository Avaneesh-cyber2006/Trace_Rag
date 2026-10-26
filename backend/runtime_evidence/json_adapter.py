"""Bounded JSON event schema normalization for runtime evidence."""

from dataclasses import dataclass, replace

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import EventDraft, FrameDraft, IssueDraft
from backend.runtime_evidence.models import (
    RuntimeEventKind,
    RuntimeFrameLanguage,
    RuntimeIssueCode,
    RuntimeTag,
)
from backend.runtime_evidence.stack_parser import parse_stack_source
from backend.runtime_evidence.validation import truncate_utf8_text, utf8_size

_MISSING = object()

_MAX_TIMESTAMP_BYTES = 128
_MAX_LEVEL_BYTES = 32
_MAX_LOGGER_BYTES = 256
_MAX_OPERATION_BYTES = 256
_MAX_IDENTIFIER_BYTES = 256
_MAX_DURATION_BYTES = 64
_MAX_STATUS_BYTES = 32
_MAX_ERROR_TYPE_BYTES = 256

_KIND_BY_TEXT = {
    "log": RuntimeEventKind.LOG,
    "error": RuntimeEventKind.ERROR,
    "span": RuntimeEventKind.SPAN,
}


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    draft: EventDraft | None
    issues: tuple[IssueDraft, ...]


class _SchemaViolation(Exception):
    """Internal rejection marker carrying no diagnostic text."""


def _issue(
    code: RuntimeIssueCode,
    sequence: int | None,
    source_line: int | None,
) -> IssueDraft:
    return IssueDraft(
        code=code, event_sequence=sequence, source_line=source_line
    )


def _normalize_scalars(value: str) -> str | None:
    """Combine valid surrogate pairs and reject unpaired surrogates."""
    if not any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return value
    characters: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        code = ord(value[index])
        if 0xD800 <= code <= 0xDBFF:
            if index + 1 < length:
                low = ord(value[index + 1])
                if 0xDC00 <= low <= 0xDFFF:
                    scalar = 0x10000 + (
                        ((code - 0xD800) << 10) + (low - 0xDC00)
                    )
                    characters.append(chr(scalar))
                    index += 2
                    continue
            return None
        if 0xDC00 <= code <= 0xDFFF:
            return None
        characters.append(value[index])
        index += 1
    return "".join(characters)


def _validated_text(value: object) -> str:
    if type(value) is not str:
        raise _SchemaViolation
    text = _normalize_scalars(value)
    if text is None or "\x00" in text:
        raise _SchemaViolation
    return text


def _bounded_text(value: object, max_bytes: int) -> str:
    text = _validated_text(value)
    if utf8_size(text) > max_bytes:
        raise _SchemaViolation
    return text


def _truncatable_text(
    value: object, max_bytes: int, *, retain: bool
) -> tuple[str | None, bool]:
    text = _validated_text(value)
    if not retain:
        return (None, False)
    return truncate_utf8_text(text, max_bytes)


def _optional_text(
    obj: dict, key: str, max_bytes: int, *, retain: bool
) -> str | None:
    value = obj.get(key, _MISSING)
    if value is _MISSING:
        return None
    text = _bounded_text(value, max_bytes)
    return text if retain else None


def _optional_truncatable(
    obj: dict, key: str, max_bytes: int, *, retain: bool
) -> tuple[str | None, bool]:
    value = obj.get(key, _MISSING)
    if value is _MISSING:
        return (None, False)
    return _truncatable_text(value, max_bytes, retain=retain)


def _is_duration_text(text: str) -> bool:
    if not text:
        return False
    integer_part, dot, fraction = text.partition(".")
    if not integer_part or any(
        character < "0" or character > "9" for character in integer_part
    ):
        return False
    if integer_part != "0" and integer_part[0] == "0":
        return False
    if dot and (
        not fraction
        or any(character < "0" or character > "9" for character in fraction)
    ):
        return False
    return True


def _duration_text(obj: dict) -> str | None:
    value = obj.get("duration_ms", _MISSING)
    if value is _MISSING:
        return None
    if type(value) is not str:
        raise _SchemaViolation
    text = _normalize_scalars(value)
    if text is None or "\x00" in text:
        raise _SchemaViolation
    if utf8_size(text) > _MAX_DURATION_BYTES:
        raise _SchemaViolation
    if not _is_duration_text(text):
        raise _SchemaViolation
    return text


def _tags(obj: dict, config: RuntimeEvidenceConfig) -> tuple[RuntimeTag, ...]:
    value = obj.get("tags", _MISSING)
    if value is _MISSING:
        return ()
    if type(value) is not dict:
        raise _SchemaViolation
    if len(value) > config.max_tags:
        raise _SchemaViolation
    pairs: list[tuple[str, str]] = []
    for key, raw_value in value.items():
        if type(key) is not str or type(raw_value) is not str:
            raise _SchemaViolation
        tag_key = _bounded_text(key, config.max_tag_key_bytes)
        tag_value = _bounded_text(raw_value, config.max_tag_value_bytes)
        pairs.append((tag_key, tag_value))
    pairs.sort(key=lambda pair: pair[0])
    for previous, current in zip(pairs, pairs[1:]):
        if previous[0] == current[0]:
            raise _SchemaViolation
    if not config.retain_event_text:
        return ()
    return tuple(
        RuntimeTag(key=key, value=value) for key, value in pairs
    )


def _event_kind(obj: dict) -> RuntimeEventKind:
    value = obj.get("kind", _MISSING)
    if type(value) is not str:
        raise _SchemaViolation
    kind = _KIND_BY_TEXT.get(value)
    if kind is None:
        raise _SchemaViolation
    return kind


def _error_object(obj: dict, kind: RuntimeEventKind) -> dict | None:
    if "error" not in obj:
        return None
    if kind is not RuntimeEventKind.ERROR:
        raise _SchemaViolation
    error = obj["error"]
    if type(error) is not dict:
        raise _SchemaViolation
    return error


def _known_language(
    declared_language: RuntimeFrameLanguage | None,
) -> RuntimeFrameLanguage | None:
    if (
        declared_language is None
        or declared_language is RuntimeFrameLanguage.UNKNOWN
    ):
        return None
    return declared_language


def _explicit_language(
    declared_language: RuntimeFrameLanguage | None,
) -> RuntimeFrameLanguage:
    known = _known_language(declared_language)
    if known is None:
        return RuntimeFrameLanguage.UNKNOWN
    return known


def _frame_position(obj: dict, key: str) -> int | None:
    value = obj.get(key, _MISSING)
    if value is _MISSING:
        return None
    if type(value) is not int:
        raise _SchemaViolation
    return value


def _frame_fields(
    raw_frame: object,
) -> tuple[str | None, str | None, int | None, int | None]:
    if type(raw_frame) is not dict:
        raise _SchemaViolation
    function_value = raw_frame.get("function", _MISSING)
    if function_value is _MISSING:
        function_text = None
    else:
        function_text = _validated_text(function_value)
    source_value = raw_frame.get("source", _MISSING)
    if source_value is _MISSING:
        source_text = None
    elif type(source_value) is not str:
        raise _SchemaViolation
    else:
        source_text = source_value
    return (
        function_text,
        source_text,
        _frame_position(raw_frame, "line"),
        _frame_position(raw_frame, "column"),
    )


def _explicit_frames(
    error: dict,
    config: RuntimeEvidenceConfig,
    declared_language: RuntimeFrameLanguage | None,
) -> tuple[tuple[FrameDraft, ...], bool, bool, bool, None, int]:
    raw_frames = error["frames"]
    if type(raw_frames) is not list:
        raise _SchemaViolation
    language = _explicit_language(declared_language)
    frames: list[FrameDraft] = []
    frames_truncated = False
    text_truncated = False
    location_issues = 0
    for position, raw_frame in enumerate(raw_frames):
        function_text, source_text, line, column = _frame_fields(raw_frame)
        if position >= config.max_frames_per_event:
            frames_truncated = True
            continue
        location_invalid = False
        if line is not None and line < 1:
            line = None
            location_invalid = True
        if column is not None and column < 0:
            column = None
            location_invalid = True
        if location_invalid:
            location_issues += 1
        if function_text is not None:
            if config.retain_event_text:
                function_text, truncated = truncate_utf8_text(
                    function_text, config.max_frame_function_bytes
                )
                text_truncated = text_truncated or truncated
            else:
                function_text = None
        frames.append(
            FrameDraft(
                language=language,
                function_text=function_text,
                source_text=source_text,
                line=line,
                column=column,
            )
        )
    return (
        tuple(frames),
        frames_truncated,
        text_truncated,
        frames_truncated,
        None,
        location_issues,
    )


def _parsed_stack(
    stack_text: str,
    config: RuntimeEvidenceConfig,
    declared_language: RuntimeFrameLanguage | None,
) -> tuple[
    tuple[FrameDraft, ...],
    bool,
    bool,
    bool,
    RuntimeIssueCode | None,
    int,
]:
    language = _known_language(declared_language)
    if language is None:
        return (
            (),
            False,
            False,
            False,
            RuntimeIssueCode.STACK_LANGUAGE_MISSING,
            0,
        )
    normalized = _normalize_scalars(stack_text)
    if normalized is None:
        return (
            (),
            False,
            False,
            False,
            RuntimeIssueCode.STACK_UNPARSEABLE,
            0,
        )
    if utf8_size(normalized) > config.max_stack_bytes:
        return (
            (),
            True,
            True,
            False,
            RuntimeIssueCode.STACK_TOO_LARGE,
            0,
        )
    if "\x00" in normalized:
        return (
            (),
            False,
            False,
            False,
            RuntimeIssueCode.STACK_UNPARSEABLE,
            0,
        )
    parsed = parse_stack_source(
        normalized,
        language,
        max_frames=config.max_frames_per_event,
        max_function_bytes=config.max_frame_function_bytes,
    )
    if parsed.recognized_frame_count == 0:
        return (
            (),
            False,
            False,
            False,
            RuntimeIssueCode.STACK_UNPARSEABLE,
            0,
        )
    frames = parsed.frames
    if config.retain_event_text:
        text_truncated = parsed.function_truncated
    else:
        frames = tuple(
            replace(frame, function_text=None) for frame in frames
        )
        text_truncated = False
    return (
        frames,
        parsed.frame_limit_hit,
        text_truncated,
        parsed.frame_limit_hit,
        None,
        0,
    )


def _frames_and_stack(
    error: dict | None,
    config: RuntimeEvidenceConfig,
    declared_language: RuntimeFrameLanguage | None,
) -> tuple[
    tuple[FrameDraft, ...],
    bool,
    bool,
    bool,
    RuntimeIssueCode | None,
    int,
]:
    if error is None:
        return ((), False, False, False, None, 0)
    if "stack" in error and type(error["stack"]) is not str:
        raise _SchemaViolation
    if "frames" in error:
        return _explicit_frames(error, config, declared_language)
    if "stack" in error:
        return _parsed_stack(error["stack"], config, declared_language)
    return ((), False, False, False, None, 0)


def _normalize_event(
    value: object,
    sequence: int,
    source_line: int | None,
    config: RuntimeEvidenceConfig,
    declared_language: RuntimeFrameLanguage | None,
) -> NormalizedEvent:
    if type(value) is not dict:
        raise _SchemaViolation
    obj = value
    kind = _event_kind(obj)
    error = _error_object(obj, kind)

    if kind is RuntimeEventKind.LOG and "message" not in obj:
        raise _SchemaViolation
    if (
        kind is RuntimeEventKind.ERROR
        and "message" not in obj
        and error is None
    ):
        raise _SchemaViolation
    if kind is RuntimeEventKind.SPAN and "operation" not in obj:
        raise _SchemaViolation

    retain = config.retain_event_text
    timestamp_text = _optional_text(
        obj, "timestamp", _MAX_TIMESTAMP_BYTES, retain=True
    )
    level = _optional_text(obj, "level", _MAX_LEVEL_BYTES, retain=True)
    logger = _optional_text(
        obj, "logger", _MAX_LOGGER_BYTES, retain=retain
    )
    operation = _optional_text(
        obj, "operation", _MAX_OPERATION_BYTES, retain=retain
    )
    trace_id = _optional_text(
        obj, "trace_id", _MAX_IDENTIFIER_BYTES, retain=True
    )
    span_id = _optional_text(
        obj, "span_id", _MAX_IDENTIFIER_BYTES, retain=True
    )
    parent_span_id = _optional_text(
        obj, "parent_span_id", _MAX_IDENTIFIER_BYTES, retain=True
    )
    duration_ms_text = _duration_text(obj)
    status = _optional_text(obj, "status", _MAX_STATUS_BYTES, retain=True)
    message, message_truncated = _optional_truncatable(
        obj, "message", config.max_message_bytes, retain=retain
    )
    tags = _tags(obj, config)

    error_type: str | None = None
    error_message: str | None = None
    error_message_truncated = False
    if error is not None:
        error_type = _optional_text(
            error, "type", _MAX_ERROR_TYPE_BYTES, retain=retain
        )
        error_message, error_message_truncated = _optional_truncatable(
            error,
            "message",
            config.max_error_message_bytes,
            retain=retain,
        )

    (
        frames,
        frames_truncated,
        stack_text_truncated,
        frame_limit_hit,
        stack_issue,
        location_issues,
    ) = _frames_and_stack(error, config, declared_language)

    text_truncated = (
        message_truncated
        or error_message_truncated
        or stack_text_truncated
    )

    issues: list[IssueDraft] = []
    if stack_issue is not None:
        issues.append(_issue(stack_issue, sequence, source_line))
    for _ in range(location_issues):
        issues.append(
            _issue(
                RuntimeIssueCode.INVALID_SOURCE_LOCATION,
                sequence,
                source_line,
            )
        )
    if frame_limit_hit:
        issues.append(
            _issue(
                RuntimeIssueCode.FRAME_LIMIT_REACHED,
                sequence,
                source_line,
            )
        )
    if text_truncated:
        issues.append(
            _issue(
                RuntimeIssueCode.TEXT_TRUNCATED,
                sequence,
                source_line,
            )
        )

    return NormalizedEvent(
        draft=EventDraft(
            sequence=sequence,
            source_line=source_line,
            kind=kind,
            timestamp_text=timestamp_text,
            level=level,
            message=message,
            logger=logger,
            operation=operation,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            duration_ms_text=duration_ms_text,
            status=status,
            error_type=error_type,
            error_message=error_message,
            tags=tags,
            frames=frames,
            text_truncated=text_truncated,
            frames_truncated=frames_truncated,
        ),
        issues=tuple(issues),
    )


def normalize_event_object(
    value: object,
    *,
    sequence: int,
    source_line: int | None,
    config: RuntimeEvidenceConfig,
    declared_language: RuntimeFrameLanguage | None,
) -> NormalizedEvent:
    """Normalize one decoded JSON event object against the schema."""
    try:
        return _normalize_event(
            value, sequence, source_line, config, declared_language
        )
    except _SchemaViolation:
        return NormalizedEvent(
            draft=None,
            issues=(
                _issue(
                    RuntimeIssueCode.INVALID_EVENT_SCHEMA,
                    None,
                    source_line,
                ),
            ),
        )
