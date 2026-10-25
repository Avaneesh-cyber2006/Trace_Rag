"""TEXT_LOG adapter producing immutable parse drafts."""

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import (
    ArtifactParseResult,
    EventDraft,
    IssueDraft,
)
from backend.runtime_evidence.lines import (
    decode_physical_line,
    split_physical_lines,
)
from backend.runtime_evidence.models import RuntimeEventKind, RuntimeIssueCode
from backend.runtime_evidence.validation import truncate_utf8_text


def parse_text_log(
    content: bytes,
    config: RuntimeEvidenceConfig,
    max_new_events: int,
) -> ArtifactParseResult:
    """Parse one TEXT_LOG artifact into events and issues."""
    events: list[EventDraft] = []
    issues: list[IssueDraft] = []
    text_truncated = False
    empty_input = True
    event_limit_hit = False

    for line in split_physical_lines(content):
        if line.blank:
            continue
        empty_input = False
        decoded = decode_physical_line(line, config.max_line_bytes)
        if decoded.issue is not None:
            issues.append(IssueDraft(decoded.issue, None, line.number))
            continue
        if len(events) >= max_new_events:
            event_limit_hit = True
            break

        message, truncated = truncate_utf8_text(
            decoded.text, config.max_message_bytes
        )
        sequence = len(events)
        if truncated:
            text_truncated = True
            issues.append(
                IssueDraft(
                    RuntimeIssueCode.TEXT_TRUNCATED,
                    sequence,
                    line.number,
                )
            )
        events.append(
            EventDraft(
                sequence=sequence,
                source_line=line.number,
                kind=RuntimeEventKind.LOG,
                timestamp_text=None,
                level=None,
                message=message,
                logger=None,
                operation=None,
                trace_id=None,
                span_id=None,
                parent_span_id=None,
                duration_ms_text=None,
                status=None,
                error_type=None,
                error_message=None,
                tags=(),
                frames=(),
                text_truncated=truncated,
                frames_truncated=False,
            )
        )

    return ArtifactParseResult(
        events=tuple(events),
        issues=tuple(issues),
        text_truncated=text_truncated,
        frames_truncated=False,
        empty_input=empty_input,
        event_limit_hit=event_limit_hit,
    )
