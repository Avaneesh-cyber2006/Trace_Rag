from dataclasses import FrozenInstanceError, fields

import pytest

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import (
    ArtifactParseResult, EventDraft, FrameDraft, IssueDraft,
)
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor
from backend.runtime_evidence.lines import (
    LineDecodeResult, PhysicalLine, decode_physical_line,
    split_physical_lines,
)
from backend.runtime_evidence.models import (
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeArtifactStatus,
    RuntimeEventKind, RuntimeEvidenceRequest, RuntimeFrameLanguage,
    RuntimeIssueCode, RuntimeTag,
)
from backend.runtime_evidence.text_adapter import parse_text_log


def _config(**kw):
    values = dict(max_message_bytes=32, max_line_bytes=32)
    values.update(kw)
    return RuntimeEvidenceConfig(**values)


def test_split_removes_one_bom_from_first_line_only():
    lines = split_physical_lines(
        b"\xef\xbb\xbfone\n\xef\xbb\xbftwo\n")
    assert [(line.number, line.raw, line.blank) for line in lines] == [
        (1, b"one", False),
        (2, b"\xef\xbb\xbftwo", False),
    ]
    (double_bom,) = split_physical_lines(b"\xef\xbb\xbf\xef\xbb\xbfx")
    assert double_bom.raw == b"\xef\xbb\xbfx"


def test_split_lf_crlf_blank_and_trailing_cr_exactly():
    lines = split_physical_lines(
        b"one\r\n\r\n \t\r\ntwo\r\r\nlone\rcr\r\n")
    assert [(line.number, line.raw, line.blank) for line in lines] == [
        (1, b"one", False),
        (2, b"", True),
        (3, b" \t", True),
        (4, b"two\r", False),
        (5, b"lone\rcr", False),
    ]


def test_split_empty_and_trailing_lf_do_not_add_lines():
    assert split_physical_lines(b"") == ()
    assert [(line.number, line.raw) for line in split_physical_lines(b"\n")] \
        == [(1, b"")]
    assert [(line.number, line.raw) for line in split_physical_lines(b"a\n\n")] \
        == [(1, b"a"), (2, b"")]


def test_split_does_not_trim_and_blankness_is_only_space_and_tab():
    (line,) = split_physical_lines(b"  a  \n")
    assert (line.raw, line.blank) == (b"  a  ", False)
    assert [line.blank for line in split_physical_lines(b"\v\n\xc2\xa0\n")] \
        == [False, False]


def test_decode_precedence_blank_then_length_then_nul_then_utf8():
    blank = PhysicalLine(1, b" " * 10, True)
    assert decode_physical_line(blank, 4) == LineDecodeResult(None, None)
    overlong = PhysicalLine(1, b"\x00" + b"x" * 10, False)
    assert decode_physical_line(overlong, 4) == LineDecodeResult(
        None, RuntimeIssueCode.LINE_TOO_LONG)
    assert decode_physical_line(
        PhysicalLine(1, b"\x00\xff", False), 4
    ) == LineDecodeResult(None, RuntimeIssueCode.NUL_BYTE)
    assert decode_physical_line(
        PhysicalLine(1, b"\xff", False), 4
    ) == LineDecodeResult(None, RuntimeIssueCode.INVALID_UTF8)


def test_decode_length_boundary_and_retained_controls():
    assert decode_physical_line(
        PhysicalLine(1, b"abcd", False), 4
    ) == LineDecodeResult("abcd", None)
    assert decode_physical_line(
        PhysicalLine(1, b"abcde", False), 4
    ) == LineDecodeResult(None, RuntimeIssueCode.LINE_TOO_LONG)
    assert decode_physical_line(
        PhysicalLine(1, b"a\rb\x01c", False), 5
    ) == LineDecodeResult("a\rb\x01c", None)


def test_lf_crlf_blank_bom_and_source_lines():
    result = parse_text_log(b"\xef\xbb\xbfone\r\n\r\n \t\r\ntwo\n",
                            _config(), 100)
    assert [(event.sequence, event.source_line, event.message)
            for event in result.events] == [(0, 1, "one"), (1, 4, "two")]
    assert result.empty_input is False
    assert result.issues == ()
    assert result.text_truncated is False
    assert result.frames_truncated is False
    assert result.event_limit_hit is False


def test_blank_only_and_empty_are_complete_shaped_results():
    for content in (b"", b"\n\t\n  \r\n"):
        result = parse_text_log(content, _config(), 10)
        assert result.events == ()
        assert result.empty_input is True
        assert result.issues == ()
        assert result.text_truncated is False
        assert result.frames_truncated is False
        assert result.event_limit_hit is False


def test_overlong_blank_line_is_ignored_without_issue():
    result = parse_text_log(b" " * 40 + b"\n",
                            _config(max_line_bytes=8), 10)
    assert result.events == ()
    assert result.issues == ()
    assert result.empty_input is True


def test_invalid_utf8_nul_and_overlong_lines_are_skipped_with_one_based_lines():
    result = parse_text_log(
        b"ok\n\xff\nno\x00pe\n" + b"x" * 40 + b"\nend\n",
        _config(max_line_bytes=32), 10)
    assert [event.source_line for event in result.events] == [1, 5]
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in result.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 2, None),
        (RuntimeIssueCode.NUL_BYTE, 3, None),
        (RuntimeIssueCode.LINE_TOO_LONG, 4, None),
    ]
    assert result.empty_input is False


def test_line_failure_precedence():
    overlong_nul = parse_text_log(
        b"\x00" + b"x" * 40 + b"\n", _config(max_line_bytes=32), 10)
    assert [issue.code for issue in overlong_nul.issues] == [
        RuntimeIssueCode.LINE_TOO_LONG]
    assert overlong_nul.events == ()
    assert overlong_nul.empty_input is False

    nul_and_invalid_utf8 = parse_text_log(
        b"\x00\xff\n", _config(max_line_bytes=32), 10)
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in nul_and_invalid_utf8.issues] == [
        (RuntimeIssueCode.NUL_BYTE, 1, None)]

    invalid_utf8 = parse_text_log(
        b"\xff\n", _config(max_line_bytes=32), 10)
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in invalid_utf8.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 1, None)]


def test_controls_and_lone_cr_are_retained_but_nul_is_not():
    result = parse_text_log(b"a\rb\x01c\nno\x00pe\n", _config(), 10)
    assert [event.message for event in result.events] == ["a\rb\x01c"]
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in result.issues] == [
        (RuntimeIssueCode.NUL_BYTE, 2, None)]


def test_message_truncation_is_codepoint_safe():
    result = parse_text_log(
        "abc\U0001F600\n".encode("utf-8"),
        _config(max_message_bytes=5), 10)
    assert [event.message for event in result.events] == ["ab\u2026"]
    assert result.events[0].sequence == 0
    assert result.events[0].source_line == 1
    assert result.events[0].text_truncated is True
    assert result.text_truncated is True
    assert [(issue.code, issue.event_sequence, issue.source_line)
            for issue in result.issues] == [
        (RuntimeIssueCode.TEXT_TRUNCATED, 0, 1)]

    exact = parse_text_log(b"abcd\n", _config(max_message_bytes=4), 10)
    assert [event.message for event in exact.events] == ["abcd"]
    assert exact.events[0].text_truncated is False
    assert exact.text_truncated is False
    assert exact.issues == ()


def test_text_log_never_guesses_fields_or_frames():
    message = "2026-01-01 INFO Traceback (most recent call last):"
    result = parse_text_log(
        message.encode("utf-8") + b"\n",
        _config(max_message_bytes=256, max_line_bytes=256), 10)
    (event,) = result.events
    assert event.kind is RuntimeEventKind.LOG
    assert event.message == message
    assert event.source_line == 1
    assert event.sequence == 0
    assert event.timestamp_text is None
    assert event.level is None
    assert event.logger is None
    assert event.operation is None
    assert event.trace_id is None
    assert event.span_id is None
    assert event.parent_span_id is None
    assert event.duration_ms_text is None
    assert event.status is None
    assert event.error_type is None
    assert event.error_message is None
    assert event.tags == ()
    assert event.frames == ()
    assert event.text_truncated is False
    assert event.frames_truncated is False
    assert result.frames_truncated is False


def test_event_limit_stops_at_capacity():
    content = b"one\ntwo\nthree\n"
    capped = parse_text_log(content, _config(), 1)
    assert [(event.sequence, event.source_line, event.message)
            for event in capped.events] == [(0, 1, "one")]
    assert capped.event_limit_hit is True
    assert capped.empty_input is False

    exact = parse_text_log(content, _config(), 3)
    assert [event.message for event in exact.events] == [
        "one", "two", "three"]
    assert exact.event_limit_hit is False

    zero = parse_text_log(content, _config(), 0)
    assert zero.events == ()
    assert zero.event_limit_hit is True
    assert zero.empty_input is False


def test_chunked_utf8_is_decoded_only_as_complete_lines():
    result = parse_text_log(
        "\u00e9\n".encode("utf-8") + b"\xa9\nok\n", _config(), 10)
    assert [(event.sequence, event.source_line, event.message)
            for event in result.events] == [(0, 1, "\u00e9"), (1, 3, "ok")]
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in result.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 2, None)]


def test_utf16_forms_are_rejected_by_line_rules():
    bom_only = parse_text_log(b"\xff\xfe", _config(), 10)
    assert bom_only.events == ()
    assert bom_only.empty_input is False
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in bom_only.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 1, None)]

    utf16_payload = parse_text_log(
        "abc".encode("utf-16-le"), _config(), 10)
    assert utf16_payload.events == ()
    assert utf16_payload.empty_input is False
    assert [(issue.code, issue.source_line, issue.event_sequence)
            for issue in utf16_payload.issues] == [
        (RuntimeIssueCode.NUL_BYTE, 1, None)]


def test_trailing_cr_and_empty_line_at_eof_are_terminators():
    result = parse_text_log(b"one\r\n\r\n", _config(), 10)
    assert [(event.sequence, event.source_line, event.message)
            for event in result.events] == [(0, 1, "one")]
    assert result.empty_input is False
    assert result.issues == ()

    single_cr = parse_text_log(b"one\r", _config(), 10)
    assert [event.message for event in single_cr.events] == ["one"]
    assert single_cr.empty_input is False
    assert single_cr.issues == ()


def test_all_invalid_nonblank_input_is_not_empty_input():
    result = parse_text_log(b"\xff\n", _config(), 10)
    assert result.events == ()
    assert result.empty_input is False
    assert [(issue.code, issue.source_line) for issue in result.issues] == [
        (RuntimeIssueCode.INVALID_UTF8, 1)]


def test_draft_interfaces_are_exact_frozen_slotted_and_tuple_typed():
    assert [field.name for field in fields(FrameDraft)] == [
        "language", "function_text", "source_text", "line", "column"]
    assert [field.name for field in fields(IssueDraft)] == [
        "code", "event_sequence", "source_line"]
    assert [field.name for field in fields(EventDraft)] == [
        "sequence", "source_line", "kind", "timestamp_text", "level",
        "message", "logger", "operation", "trace_id", "span_id",
        "parent_span_id", "duration_ms_text", "status", "error_type",
        "error_message", "tags", "frames", "text_truncated",
        "frames_truncated"]
    assert [field.name for field in fields(ArtifactParseResult)] == [
        "events", "issues", "text_truncated", "frames_truncated",
        "empty_input", "event_limit_hit"]

    frame = FrameDraft(
        language=RuntimeFrameLanguage.UNKNOWN, function_text=None,
        source_text=None, line=None, column=None)
    issue = IssueDraft(
        code=RuntimeIssueCode.INVALID_UTF8, event_sequence=None,
        source_line=1)
    event = EventDraft(
        sequence=0, source_line=1, kind=RuntimeEventKind.LOG,
        timestamp_text=None, level=None, message="x", logger=None,
        operation=None, trace_id=None, span_id=None, parent_span_id=None,
        duration_ms_text=None, status=None, error_type=None,
        error_message=None, tags=(RuntimeTag(key="k", value="v"),),
        frames=(frame,), text_truncated=False, frames_truncated=False)
    result = parse_text_log(b"x\n", _config(), 10)

    assert not hasattr(event, "__dict__")
    assert not hasattr(result, "__dict__")
    assert isinstance(result.events, tuple)
    assert isinstance(result.issues, tuple)
    with pytest.raises(FrozenInstanceError):
        event.message = "y"
    with pytest.raises(FrozenInstanceError):
        issue.source_line = 2
    with pytest.raises(FrozenInstanceError):
        result.empty_input = True


def _ingest_text(content, config=None):
    return RuntimeEvidenceIngestor(config).ingest(RuntimeEvidenceRequest(
        "repo", (
            RuntimeArtifact("ci.log", RuntimeArtifactFormat.TEXT_LOG,
                            content),
        )))


def test_pipeline_text_keeps_zero_based_sequences_and_one_based_lines():
    inv = _ingest_text(b"one\n\ntwo\n")
    assert [(event.sequence, event.source_line, event.message)
            for event in inv.events] == [
        (0, 1, "one"), (1, 3, "two")]
    artifact = inv.artifacts[0]
    assert artifact.status is RuntimeArtifactStatus.COMPLETE
    assert artifact.event_count == 2
    assert artifact.frame_count == 0
    assert artifact.issue_count == 0
    assert artifact.text_truncated is False
    assert inv.total_events == 2
    assert inv.frames == ()
    assert inv.issues == ()
    assert inv.ingestion_complete is True


def test_pipeline_text_truncation_is_partial_with_one_issue():
    inv = _ingest_text(
        b"abcdef\n", RuntimeEvidenceConfig(
            max_message_bytes=4, max_line_bytes=32))
    assert len(inv.artifacts) == 1
    assert len(inv.events) == 1
    artifact = inv.artifacts[0]
    assert artifact.status is RuntimeArtifactStatus.PARTIAL
    assert artifact.text_truncated is True
    assert artifact.frames_truncated is False
    assert inv.text_truncated is True
    assert inv.events[0].message == "a\u2026"
    assert inv.events[0].text_truncated is True
    assert [(issue.code, issue.event_sequence, issue.source_line)
            for issue in inv.issues] == [
        (RuntimeIssueCode.TEXT_TRUNCATED, 0, 1)]
    assert artifact.issue_count == 1


def test_pipeline_text_metadata_only_omits_messages_and_truncation():
    content = b"ok\nthis message is too long\n"
    config = RuntimeEvidenceConfig(max_message_bytes=8)
    retained = _ingest_text(content, config)
    metadata = _ingest_text(
        content, RuntimeEvidenceConfig(
            max_message_bytes=8, retain_event_text=False))

    assert [event.message for event in retained.events] == [
        "ok", "this \u2026"]
    assert [event.message for event in metadata.events] == [None, None]
    assert [event.event_id for event in metadata.events] == [
        event.event_id for event in retained.events]
    assert all(
        metadata_event.event_fingerprint != retained_event.event_fingerprint
        for metadata_event, retained_event in zip(
            metadata.events, retained.events)
    )
    assert [event.text_truncated for event in retained.events] == [
        False, True]
    assert [event.text_truncated for event in metadata.events] == [
        False, False]
    assert [(issue.code, issue.event_sequence, issue.source_line)
            for issue in retained.issues] == [
        (RuntimeIssueCode.TEXT_TRUNCATED, 1, 2)]
    assert metadata.issues == ()
    assert metadata.artifacts[0].status is RuntimeArtifactStatus.COMPLETE
    assert metadata.artifacts[0].issue_count == 0
    assert metadata.artifacts[0].text_truncated is False
    assert metadata.text_truncated is False
    assert metadata.incomplete_reasons == ()
