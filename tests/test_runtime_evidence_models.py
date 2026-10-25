from dataclasses import FrozenInstanceError, fields

import pytest

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError, RuntimeEvidenceError,
    RuntimeEvidenceGraphError, RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError, RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.models import (
    CaptureCompleteness, FrameAssociation, GraphBinding, ISSUE_MESSAGES,
    PathMatchBasis, RuntimeArtifact, RuntimeArtifactFormat,
    RuntimeArtifactRecord, RuntimeArtifactStatus, RuntimeEvent,
    RuntimeEventKind, RuntimeEvidenceIssue, RuntimeEvidenceRequest,
    RuntimeFrameLanguage, RuntimeIncompleteReason, RuntimeIssueCode,
    RuntimeStackFrame, RuntimeTag, issue_message,
)


def test_all_fatal_errors_descend_from_runtime_evidence_error():
    assert RuntimeEvidenceError.__bases__ == (Exception,)
    for cls in (RuntimeEvidenceConfigurationError, RuntimeEvidenceValidationError,
                RuntimeEvidenceLimitError, RuntimeEvidenceGraphError,
                RuntimeEvidenceInvariantError, RuntimeEvidenceIngestionError):
        assert cls.__bases__ == (RuntimeEvidenceError,), cls.__name__


def test_exception_message_is_exact_and_not_rewritten():
    err = RuntimeEvidenceGraphError("runtime evidence graph is invalid")
    assert str(err) == "runtime evidence graph is invalid"
    assert err.args == ("runtime evidence graph is invalid",)


def test_config_defaults_are_exact():
    cfg = RuntimeEvidenceConfig()
    assert cfg.max_artifacts == 64
    assert cfg.max_namespace_bytes == 256
    assert cfg.max_artifact_bytes == 8_388_608
    assert cfg.max_submitted_input_bytes == 67_108_864
    assert cfg.max_total_input_bytes == 67_108_864
    assert cfg.max_events == 50_000
    assert cfg.max_json_nodes == 1_000_000
    assert cfg.max_json_number_bytes == 1_024
    assert cfg.max_frames_per_event == 128
    assert cfg.max_total_frames == 200_000
    assert cfg.max_issues == 10_000
    assert cfg.max_line_bytes == 65_536
    assert cfg.max_source_reference_bytes == 1_024
    assert cfg.max_message_bytes == 8_192
    assert cfg.max_error_message_bytes == 8_192
    assert cfg.max_stack_bytes == 65_536
    assert cfg.max_frame_function_bytes == 1_024
    assert cfg.max_tags == 32
    assert cfg.max_tag_key_bytes == 128
    assert cfg.max_tag_value_bytes == 512
    assert cfg.retain_event_text is True


def test_config_field_order_is_exact():
    assert [field.name for field in fields(RuntimeEvidenceConfig)] == [
        "max_artifacts", "max_namespace_bytes", "max_artifact_bytes",
        "max_submitted_input_bytes", "max_total_input_bytes", "max_events",
        "max_json_nodes", "max_json_number_bytes", "max_frames_per_event",
        "max_total_frames", "max_issues", "max_line_bytes",
        "max_source_reference_bytes", "max_message_bytes",
        "max_error_message_bytes", "max_stack_bytes", "max_frame_function_bytes",
        "max_tags", "max_tag_key_bytes", "max_tag_value_bytes",
        "retain_event_text"]


@pytest.mark.parametrize("field", [
    "max_artifacts", "max_namespace_bytes", "max_artifact_bytes",
    "max_submitted_input_bytes", "max_total_input_bytes", "max_events",
    "max_json_nodes", "max_json_number_bytes", "max_frames_per_event",
    "max_total_frames", "max_issues", "max_line_bytes",
    "max_source_reference_bytes", "max_message_bytes",
    "max_error_message_bytes", "max_stack_bytes", "max_frame_function_bytes",
    "max_tags", "max_tag_key_bytes", "max_tag_value_bytes",
])
def test_config_rejects_bool_and_nonpositive_integers(field):
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(**{field: True})
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(**{field: 0})
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(**{field: -1})


def test_config_rejects_cross_field_violations():
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(max_artifact_bytes=11, max_total_input_bytes=10,
                              max_submitted_input_bytes=10)
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(max_artifact_bytes=10,
                              max_total_input_bytes=11,
                              max_submitted_input_bytes=10)
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(max_frames_per_event=3, max_total_frames=2)


def test_config_is_frozen_slotted_and_requires_exact_bool():
    cfg = RuntimeEvidenceConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.max_events = 1
    assert not hasattr(cfg, "__dict__")
    with pytest.raises(RuntimeEvidenceConfigurationError):
        RuntimeEvidenceConfig(retain_event_text=1)


def test_enum_members_and_values_are_exact():
    assert [x.value for x in RuntimeArtifactFormat] == [
        "text_log", "json_events", "stack_trace_text"]
    assert [x.value for x in RuntimeArtifactStatus] == [
        "complete", "partial", "failed", "skipped"]
    assert [x.value for x in RuntimeEventKind] == ["log", "error", "span"]
    assert [x.value for x in RuntimeFrameLanguage] == [
        "python", "java", "javascript", "typescript", "tsx", "unknown"]
    assert [x.value for x in CaptureCompleteness] == [
        "complete", "partial", "unknown"]
    assert [x.value for x in GraphBinding] == [
        "not_provided", "namespace_matched_unversioned"]
    assert [x.value for x in PathMatchBasis] == [
        "exact_relative_path", "unverified_relative_path", "unique_path_suffix",
        "unique_basename", "external_or_non_file", "ambiguous", "unmatched"]
    assert [x.value for x in FrameAssociation] == [
        "not_evaluated", "unassociated", "file", "symbol", "ambiguous_symbol"]
    assert [x.value for x in RuntimeIncompleteReason] == [
        "artifact_failed", "artifact_partial", "artifact_skipped",
        "event_limit_reached", "frame_limit_reached", "issue_limit_reached",
        "text_truncated"]
    assert [x.value for x in RuntimeIssueCode] == [
        "artifact_too_large", "invalid_utf8", "nul_byte", "line_too_long",
        "malformed_json", "duplicate_json_key", "invalid_event_schema",
        "stack_language_missing", "stack_too_large", "stack_unparseable",
        "invalid_source_location", "text_truncated", "frame_limit_reached",
        "event_limit_reached", "issue_limit_reached"]


def _artifact(**kw):
    values = dict(source_name="ci.log", format=RuntimeArtifactFormat.TEXT_LOG,
                  content=b"hello\n", declared_language=None)
    values.update(kw)
    return RuntimeArtifact(**values)


def _request(**kw):
    values = dict(repository_namespace="repo", artifacts=(_artifact(),))
    values.update(kw)
    return RuntimeEvidenceRequest(**values)


def test_input_models_are_frozen_and_slotted():
    artifact = _artifact()
    with pytest.raises(FrozenInstanceError):
        artifact.source_name = "x"
    assert not hasattr(artifact, "__dict__")
    assert not hasattr(_request(), "__dict__")


def test_input_model_field_order_is_exact():
    assert [field.name for field in fields(RuntimeTag)] == ["key", "value"]
    assert [field.name for field in fields(RuntimeArtifact)] == [
        "source_name", "format", "content", "declared_language"]
    assert [field.name for field in fields(RuntimeEvidenceRequest)] == [
        "repository_namespace", "artifacts", "declared_revision",
        "declared_captured_at", "declared_capture_completeness"]


@pytest.mark.parametrize("name", ["", " ci.log", "ci.log ", "a/b", r"a\b",
                                  "a:b", "a\x00b", "a\x01b", "a\tb",
                                  "a\ud800b"])
def test_source_name_rules(name):
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(source_name=name)


def test_input_scalar_fields_require_exact_strings():
    for bad in (None, 1, b"x", object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _artifact(source_name=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(repository_namespace=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            RuntimeTag(bad, "v")
        with pytest.raises(RuntimeEvidenceValidationError):
            RuntimeTag("k", bad)
    for bad in (1, b"x", object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_revision=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_captured_at=bad)


def test_exact_bytes_and_stack_language_rules():
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(content=bytearray(b"x"))
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(content=memoryview(b"x"))
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  declared_language=None)
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  declared_language=RuntimeFrameLanguage.UNKNOWN)
    assert _artifact(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                     declared_language=RuntimeFrameLanguage.PYTHON)


def test_format_and_declared_language_require_exact_enums():
    for bad in ("text_log", "python", 1, object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _artifact(format=bad)
    for bad in ("python", "text_log", 1, object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _artifact(declared_language=bad)
    assert _artifact(format=RuntimeArtifactFormat.TEXT_LOG,
                     declared_language=RuntimeFrameLanguage.PYTHON
                     ).declared_language is RuntimeFrameLanguage.PYTHON


def test_request_namespace_exact_multibyte_limit_and_surrogates():
    assert _request(repository_namespace="é" * 128).repository_namespace == "é" * 128
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(repository_namespace="é" * 128 + "a")
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(repository_namespace="")
    for bad in ("\ud800", "\udfff", "\ud83d\ude00"):
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(repository_namespace=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_revision=bad)
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_captured_at=bad)


def test_request_claims_are_opaque_and_not_trimmed():
    request = _request(declared_revision="  rev  ", declared_captured_at="when\x01")
    assert request.declared_revision == "  rev  "
    assert request.declared_captured_at == "when\x01"
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(declared_captured_at="a\x00b")


def test_source_name_and_claim_byte_boundaries():
    assert _artifact(source_name="a" * 128).source_name == "a" * 128
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(source_name="a" * 129)
    assert _artifact(source_name="é" * 64).source_name == "é" * 64
    with pytest.raises(RuntimeEvidenceValidationError):
        _artifact(source_name="é" * 64 + "a")
    assert _request(declared_revision="é" * 128).declared_revision == "é" * 128
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(declared_revision="é" * 128 + "a")
    assert _request(declared_captured_at="é" * 64).declared_captured_at == "é" * 64
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(declared_captured_at="é" * 64 + "a")


def test_capture_completeness_requires_exact_enum():
    assert _request(declared_capture_completeness=CaptureCompleteness.PARTIAL
                    ).declared_capture_completeness is CaptureCompleteness.PARTIAL
    for bad in ("partial", 1, object()):
        with pytest.raises(RuntimeEvidenceValidationError):
            _request(declared_capture_completeness=bad)


def test_request_requires_exact_tuple_and_artifacts():
    class _TupleSubclass(tuple):
        pass
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(artifacts=[_artifact()])
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(artifacts=_TupleSubclass((_artifact(),)))
    with pytest.raises(RuntimeEvidenceValidationError):
        _request(artifacts=(object(),))
    assert _request(artifacts=()).artifacts == ()


def test_tag_is_scalar_only():
    assert RuntimeTag("k", "v").value == "v"
    for key, value in (("k\ud800", "v"), ("k", "v\ud800"),
                       ("k\x00", "v"), ("k", "v\x00")):
        with pytest.raises(RuntimeEvidenceValidationError):
            RuntimeTag(key, value)


A = "a" * 64

_BAD_SHA256 = (None, 1, "A" * 64, "a" * 63, "a" * 64 + "\x00", "\ud800")
_BAD_POSITIVE_INT = (0, -1, True, 1.0, "\ud800", "a\x00b")
_BAD_NONNEGATIVE_INT = (-1, True, 0.0, "\ud800", "a\x00b")
_BAD_REQUIRED_NONNEGATIVE_INT = (None, *_BAD_NONNEGATIVE_INT)
_BAD_TEXT = (1, b"x", "a\ud800", "a\x00b")


class _TupleSubclass(tuple):
    pass


def _frame(**kw):
    values = dict(frame_id=A, event_id="b" * 64, ordinal=0,
                  language=RuntimeFrameLanguage.UNKNOWN, function_text=None,
                  relative_path=None, line=None, column=None,
                  source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.UNASSOCIATED,
                  primary_node_id=None, candidate_node_ids=())
    values.update(kw)
    return RuntimeStackFrame(**values)


def _verified_frame(**kw):
    values = dict(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH)
    values.update(kw)
    return _frame(**values)


def _file_frame(**kw):
    values = dict(association=FrameAssociation.FILE,
                  primary_node_id="f", candidate_node_ids=("f",))
    values.update(kw)
    return _verified_frame(**values)


def _event(**kw):
    values = dict(event_id=A, event_fingerprint="c" * 64, artifact_id="d" * 64,
                  sequence=0, source_line=1, kind=RuntimeEventKind.LOG,
                  timestamp_text=None, level="INFO", message="hello",
                  logger=None, operation=None, trace_id=None, span_id=None,
                  parent_span_id=None, duration_ms_text=None, status=None,
                  error_type=None, error_message=None, tags=(), frames=(),
                  text_truncated=False, frames_truncated=False)
    values.update(kw)
    return RuntimeEvent(**values)


def _issue(**kw):
    values = dict(code=RuntimeIssueCode.INVALID_UTF8,
                  message=ISSUE_MESSAGES[RuntimeIssueCode.INVALID_UTF8],
                  artifact_id=A, event_sequence=None, source_line=1)
    values.update(kw)
    return RuntimeEvidenceIssue(**values)


def _artifact_record(**kw):
    values = dict(artifact_id=A, source_names=("ci.log",),
                  format=RuntimeArtifactFormat.TEXT_LOG,
                  declared_language=None, content_sha256="e" * 64,
                  size_bytes=6, status=RuntimeArtifactStatus.COMPLETE,
                  event_count=0, frame_count=0, duplicate_count=0,
                  text_truncated=False, frames_truncated=False, issue_count=0)
    values.update(kw)
    return RuntimeArtifactRecord(**values)


def test_output_model_field_order_and_frozen_slotted():
    assert [field.name for field in fields(RuntimeStackFrame)] == [
        "frame_id", "event_id", "ordinal", "language", "function_text",
        "relative_path", "line", "column", "source_reference_sha256",
        "path_match_basis", "association", "primary_node_id",
        "candidate_node_ids"]
    assert [field.name for field in fields(RuntimeEvent)] == [
        "event_id", "event_fingerprint", "artifact_id", "sequence",
        "source_line", "kind", "timestamp_text", "level", "message", "logger",
        "operation", "trace_id", "span_id", "parent_span_id",
        "duration_ms_text", "status", "error_type", "error_message", "tags",
        "frames", "text_truncated", "frames_truncated"]
    assert [field.name for field in fields(RuntimeEvidenceIssue)] == [
        "code", "message", "artifact_id", "event_sequence", "source_line"]
    assert [field.name for field in fields(RuntimeArtifactRecord)] == [
        "artifact_id", "source_names", "format", "declared_language",
        "content_sha256", "size_bytes", "status", "event_count",
        "frame_count", "duplicate_count", "text_truncated",
        "frames_truncated", "issue_count"]
    issue = RuntimeEvidenceIssue(
        RuntimeIssueCode.INVALID_UTF8,
        ISSUE_MESSAGES[RuntimeIssueCode.INVALID_UTF8], A, None, 1)
    for record, field_name in ((_frame(), "frame_id"), (_event(), "event_id"),
                               (issue, "code"),
                               (_artifact_record(), "artifact_id")):
        assert not hasattr(record, "__dict__")
        with pytest.raises(FrozenInstanceError):
            setattr(record, field_name, None)


def test_issue_messages_are_exact_and_issue_validates_them():
    expected = {
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
    }
    assert dict(ISSUE_MESSAGES) == expected
    with pytest.raises(RuntimeEvidenceInvariantError):
        RuntimeEvidenceIssue(RuntimeIssueCode.INVALID_UTF8, "wrong", A, None, 1)


def test_issue_message_helper_matches_the_fixed_mapping():
    for code in RuntimeIssueCode:
        assert issue_message(code) == ISSUE_MESSAGES[code]
        assert _issue(code=code, message=ISSUE_MESSAGES[code]).message == (
            ISSUE_MESSAGES[code]
        )
    for bad in (None, "invalid_utf8", 1):
        with pytest.raises(RuntimeEvidenceInvariantError):
            issue_message(bad)


def test_optional_output_values_accept_valid_boundaries():
    frame = _frame(line=1, column=0)
    assert frame.line == 1
    assert frame.column == 0
    assert _event(source_line=None).source_line is None
    issue = _issue(event_sequence=0, source_line=None)
    assert issue.event_sequence == 0
    assert issue.source_line is None
    assert _artifact_record(size_bytes=0).size_bytes == 0


def test_output_text_fields_are_retained_verbatim():
    assert _event(message="  hi\x01  ").message == "  hi\x01  "
    assert _frame(function_text="  fn\x01  ").function_text == "  fn\x01  "
    assert _artifact_record(source_names=("A.log",)).source_names == (
        "A.log",
    )


def test_frame_shape_and_path_hash_pairing():
    assert _verified_frame(association=FrameAssociation.FILE,
                           candidate_node_ids=("f",), primary_node_id="f")
    assert _frame(line=1, column=0)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(line=0)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(column=-1)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE,
                        candidate_node_ids=("",), primary_node_id="")
    assert _frame(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.AMBIGUOUS,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.AMBIGUOUS,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
                  association=FrameAssociation.UNASSOCIATED)
    assert _frame(relative_path="pkg/a.py", source_reference_sha256=A,
                  path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
                  association=FrameAssociation.NOT_EVALUATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.UNMATCHED,
                  association=FrameAssociation.NOT_EVALUATED)
    assert _frame(relative_path=None, source_reference_sha256=None,
                  path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
                  association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE, candidate_node_ids=(),
                        primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(association=FrameAssociation.FILE,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(association=FrameAssociation.SYMBOL,
               candidate_node_ids=("a", "f"), primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
               candidate_node_ids=("a", "b", "f"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.FILE,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNIQUE_PATH_SUFFIX,
               association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=None,
               path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path=None, source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path=None, source_reference_sha256=None,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.UNASSOCIATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.AMBIGUOUS,
               association=FrameAssociation.NOT_EVALUATED)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path=None, source_reference_sha256=None,
               path_match_basis=PathMatchBasis.AMBIGUOUS,
               association=FrameAssociation.NOT_EVALUATED)


def test_frame_candidate_order_and_primary_containment():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("z", "a"), primary_node_id="z")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("a", "z"), primary_node_id="x")
    assert _verified_frame(association=FrameAssociation.SYMBOL,
                           candidate_node_ids=("a", "z"), primary_node_id="z")


def test_verified_association_candidate_shapes():
    assert _verified_frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
                           candidate_node_ids=("a", "b", "f"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
                        candidate_node_ids=("a", "f"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.AMBIGUOUS_SYMBOL,
                        candidate_node_ids=("a", "b", "f"),
                        primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE,
                        candidate_node_ids=("a", "f"), primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.FILE,
                        candidate_node_ids=("f",), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("a", "b", "f"), primary_node_id="a")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _verified_frame(association=FrameAssociation.SYMBOL,
                        candidate_node_ids=("a", "z"), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.NOT_EVALUATED,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.NOT_EVALUATED,
               candidate_node_ids=("f",), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
               association=FrameAssociation.NOT_EVALUATED,
               candidate_node_ids=(), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED,
               candidate_node_ids=("f",), primary_node_id="f")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED,
               candidate_node_ids=("f",), primary_node_id=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(relative_path="pkg/a.py", source_reference_sha256=A,
               path_match_basis=PathMatchBasis.UNMATCHED,
               association=FrameAssociation.UNASSOCIATED,
               candidate_node_ids=(), primary_node_id="f")


def test_frame_association_path_basis_matrix():
    verified_bases = (
        PathMatchBasis.EXACT_RELATIVE_PATH,
        PathMatchBasis.UNIQUE_PATH_SUFFIX,
        PathMatchBasis.UNIQUE_BASENAME,
    )
    shapes = {
        FrameAssociation.NOT_EVALUATED: ((), None),
        FrameAssociation.UNASSOCIATED: ((), None),
        FrameAssociation.FILE: (("f",), "f"),
        FrameAssociation.SYMBOL: (("a", "z"), "z"),
        FrameAssociation.AMBIGUOUS_SYMBOL: (("a", "b", "f"), None),
    }
    allowed = {
        (FrameAssociation.NOT_EVALUATED,
         PathMatchBasis.UNVERIFIED_RELATIVE_PATH, True),
        (FrameAssociation.NOT_EVALUATED, PathMatchBasis.UNMATCHED, False),
        (FrameAssociation.NOT_EVALUATED,
         PathMatchBasis.EXTERNAL_OR_NON_FILE, False),
        (FrameAssociation.UNASSOCIATED, PathMatchBasis.UNMATCHED, True),
        (FrameAssociation.UNASSOCIATED, PathMatchBasis.UNMATCHED, False),
        (FrameAssociation.UNASSOCIATED, PathMatchBasis.AMBIGUOUS, True),
        (FrameAssociation.UNASSOCIATED, PathMatchBasis.AMBIGUOUS, False),
        (FrameAssociation.UNASSOCIATED,
         PathMatchBasis.EXTERNAL_OR_NON_FILE, False),
    }
    for association in (
        FrameAssociation.FILE,
        FrameAssociation.SYMBOL,
        FrameAssociation.AMBIGUOUS_SYMBOL,
    ):
        for basis in verified_bases:
            allowed.add((association, basis, True))
    for association in FrameAssociation:
        for basis in PathMatchBasis:
            for has_path in (False, True):
                candidates, primary = shapes[association]
                values = dict(
                    association=association,
                    path_match_basis=basis,
                    candidate_node_ids=candidates,
                    primary_node_id=primary,
                    relative_path="pkg/a.py" if has_path else None,
                    source_reference_sha256=A if has_path else None,
                )
                if (association, basis, has_path) in allowed:
                    assert _frame(**values)
                else:
                    with pytest.raises(RuntimeEvidenceInvariantError):
                        _frame(**values)


def test_event_forbids_duplicate_or_unsorted_tags_and_bad_frame_ordinals():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=(RuntimeTag("b", "1"), RuntimeTag("a", "2")))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=(RuntimeTag("a", "1"), RuntimeTag("a", "2")))
    bad = _frame(ordinal=1, event_id=A)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=(bad,))


def test_event_frames_must_match_event_and_positions():
    assert _event(frames=(_frame(event_id=A, ordinal=0),
                          _frame(event_id=A, ordinal=1)))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=(_frame(event_id="b" * 64, ordinal=0),))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=(_frame(event_id=A, ordinal=1),))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=(_frame(event_id=A, ordinal=0),
                       _frame(event_id=A, ordinal=2)))


def test_event_tags_require_sorted_unique_keys():
    assert _event(tags=(RuntimeTag("a", "1"), RuntimeTag("b", "2")))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=(RuntimeTag("b", "1"), RuntimeTag("a", "2")))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=(RuntimeTag("a", "1"), RuntimeTag("a", "2")))


def test_output_tuple_fields_require_exact_tuples():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(tags=list(_event().tags))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(frames=list(_event().frames))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=list(_artifact_record().source_names))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _frame(candidate_node_ids=list(_frame().candidate_node_ids))


@pytest.mark.parametrize("duration", ["-1", "1.", ".1", "01", "1e3", " 1", "1 "])
def test_event_duration_is_a_nonnegative_decimal_string(duration):
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(duration_ms_text=duration)


@pytest.mark.parametrize("duration", ["0", "0.0", "1", "10.25", "999"])
def test_event_duration_accepts_valid_decimal_strings(duration):
    assert _event(duration_ms_text=duration).duration_ms_text == duration


def test_artifact_record_source_name_order_and_language_rule():
    record = _artifact_record(source_names=("A.log", "a.log"))
    assert record.source_names == ("A.log", "a.log")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("b.log", "a.log"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("a.log", "A.log"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("ci.log", "ci.log"))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=())
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                         declared_language=None)
    assert _artifact_record(source_names=("a" * 128,))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("a" * 129,))
    assert _artifact_record(source_names=("é" * 64,))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(source_names=("é" * 64 + "a",))
    for bad in ("", " ci.log", "ci.log ", "a/b", r"a\b", "a:b",
                "a\x00b", "a\x01b", "a\tb", "a\ud800b"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _artifact_record(source_names=(bad,))


def test_artifact_record_language_compatibility():
    assert _artifact_record(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                            declared_language=RuntimeFrameLanguage.PYTHON)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                         declared_language=RuntimeFrameLanguage.UNKNOWN)
    assert _artifact_record(format=RuntimeArtifactFormat.JSON_EVENTS,
                            declared_language=RuntimeFrameLanguage.UNKNOWN)


@pytest.mark.parametrize("field,bad", [
    (field, bad)
    for field, values in {
        "frame_id": _BAD_SHA256,
        "event_id": _BAD_SHA256,
        "ordinal": _BAD_REQUIRED_NONNEGATIVE_INT,
        "language": (None, "unknown", 1),
        "function_text": _BAD_TEXT,
        "relative_path": (None, 1, "/abs/a.py", r"pkg\a.py", "pkg/../a.py",
                          "pkg//a.py", "pkg/", ".", "a/./b", "C:/x",
                          "pkg/\x00a.py", "pkg/\ud800.py"),
        "line": _BAD_POSITIVE_INT,
        "column": _BAD_NONNEGATIVE_INT,
        "source_reference_sha256": _BAD_SHA256,
        "path_match_basis": (None, "unmatched", 1),
        "association": (None, "unassociated", 1),
        "primary_node_id": (None, 1, "", "x\x00", "x\ud800"),
        "candidate_node_ids": (None, ["f"], _TupleSubclass(("f",)),
                               ("f", 1), ("",), ("f", "f"), ("z", "a"),
                               ("a\x00b",), ("a\ud800b",)),
    }.items()
    for bad in values
])
def test_frame_fields_are_validated_locally(field, bad):
    with pytest.raises(RuntimeEvidenceInvariantError):
        _file_frame(**{field: bad})


@pytest.mark.parametrize("field,bad", [
    (field, bad)
    for field, values in {
        "event_id": _BAD_SHA256,
        "event_fingerprint": _BAD_SHA256,
        "artifact_id": _BAD_SHA256,
        "sequence": _BAD_REQUIRED_NONNEGATIVE_INT,
        "source_line": _BAD_POSITIVE_INT,
        "kind": (None, "log", 1),
        "duration_ms_text": ("-1", "1.", ".1", "01", "1e3", " 1", "1 ",
                             1, "a\ud800", "a\x00b"),
        "tags": (None, [], ["a"], ("a",),
                 (RuntimeTag("a", "1"), object()),
                 _TupleSubclass((RuntimeTag("a", "1"),))),
        "frames": (None, [], ("a",), (object(),), _TupleSubclass(())),
        "text_truncated": (None, 0, 1, "false"),
        "frames_truncated": (None, 0, 1, "false"),
    }.items()
    for bad in values
] + [
    (field, bad)
    for field in (
        "timestamp_text", "level", "message", "logger", "operation",
        "trace_id", "span_id", "parent_span_id", "status", "error_type",
        "error_message",
    )
    for bad in _BAD_TEXT
])
def test_event_fields_are_validated_locally(field, bad):
    with pytest.raises(RuntimeEvidenceInvariantError):
        _event(**{field: bad})


@pytest.mark.parametrize("field,bad", [
    (field, bad)
    for field, values in {
        "code": (None, "invalid_utf8", 1),
        "message": (None, 1, "wrong", "line is not valid UTF-8\x00"),
        "artifact_id": _BAD_SHA256,
        "event_sequence": _BAD_NONNEGATIVE_INT,
        "source_line": _BAD_POSITIVE_INT,
    }.items()
    for bad in values
])
def test_issue_fields_are_validated_locally(field, bad):
    with pytest.raises(RuntimeEvidenceInvariantError):
        _issue(**{field: bad})


@pytest.mark.parametrize("field,bad", [
    (field, bad)
    for field, values in {
        "artifact_id": _BAD_SHA256,
        "source_names": (None, [], _TupleSubclass(()), (),
                         ("ci.log", "ci.log"), ("b.log", "a.log"),
                         (1,), ("",), ("a/b",), ("a\x00b",), ("a\ud800b",)),
        "format": (None, "text_log", 1),
        "declared_language": ("python", 1, object()),
        "content_sha256": _BAD_SHA256,
        "size_bytes": _BAD_REQUIRED_NONNEGATIVE_INT,
        "status": (None, "complete", 1),
        "event_count": _BAD_REQUIRED_NONNEGATIVE_INT,
        "frame_count": _BAD_REQUIRED_NONNEGATIVE_INT,
        "duplicate_count": _BAD_REQUIRED_NONNEGATIVE_INT,
        "issue_count": _BAD_REQUIRED_NONNEGATIVE_INT,
        "text_truncated": (None, 0, 1, "false"),
        "frames_truncated": (None, 0, 1, "false"),
    }.items()
    for bad in values
])
def test_artifact_record_fields_are_validated_locally(field, bad):
    with pytest.raises(RuntimeEvidenceInvariantError):
        _artifact_record(**{field: bad})


def test_output_models_do_not_apply_config_dependent_maxima():
    long_text = "x" * 20_000
    assert _event(message=long_text).message == long_text
    assert _frame(function_text=long_text).function_text == long_text
    assert _event(tags=(RuntimeTag("k", "v" * 600),)).tags[0].value == "v" * 600


def test_invariant_messages_never_echo_rejected_values():
    secret = "SECRET_VALUE"
    cases = (
        lambda: _frame(function_text=secret + "\ud800"),
        lambda: _event(message=secret + "\x00"),
        lambda: _artifact_record(source_names=(secret + "/x",)),
        lambda: _issue(message=secret),
        lambda: _frame(frame_id=secret.upper() * 5),
    )
    for build in cases:
        with pytest.raises(RuntimeEvidenceInvariantError) as excinfo:
            build()
        assert secret not in str(excinfo.value)
