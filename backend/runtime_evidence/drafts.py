"""Internal immutable parse drafts for runtime evidence."""

from dataclasses import dataclass

from backend.runtime_evidence.models import (
    RuntimeEventKind,
    RuntimeFrameLanguage,
    RuntimeIssueCode,
    RuntimeTag,
)


@dataclass(frozen=True, slots=True)
class FrameDraft:
    language: RuntimeFrameLanguage
    function_text: str | None
    source_text: str | None
    line: int | None
    column: int | None


@dataclass(frozen=True, slots=True)
class IssueDraft:
    code: RuntimeIssueCode
    event_sequence: int | None
    source_line: int | None


@dataclass(frozen=True, slots=True)
class EventDraft:
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
    frames: tuple[FrameDraft, ...]
    text_truncated: bool
    frames_truncated: bool


@dataclass(frozen=True, slots=True)
class ArtifactParseResult:
    events: tuple[EventDraft, ...]
    issues: tuple[IssueDraft, ...]
    text_truncated: bool
    frames_truncated: bool
    empty_input: bool
    event_limit_hit: bool
