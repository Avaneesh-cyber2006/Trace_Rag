import logging
from dataclasses import FrozenInstanceError, fields
from types import MappingProxyType

import pytest

import backend.runtime_evidence as runtime_evidence
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError, RuntimeEvidenceError,
    RuntimeEvidenceGraphError, RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError, RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.indexes import (
    InventoryIndexes, build_inventory_indexes, validate_inventory,
)
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor
from backend.runtime_evidence.models import (
    CaptureCompleteness, FrameAssociation, GraphBinding, ISSUE_MESSAGES,
    PathMatchBasis, RuntimeArtifact, RuntimeArtifactFormat,
    RuntimeArtifactRecord, RuntimeArtifactStatus, RuntimeEvent,
    RuntimeEventKind, RuntimeEvidenceInventory, RuntimeEvidenceIssue,
    RuntimeEvidenceRequest, RuntimeFrameLanguage, RuntimeIncompleteReason,
    RuntimeIssueCode, RuntimeStackFrame, RuntimeTag, issue_message,
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


_INVENTORY_INDEX_FIELDS = (
    "events_by_artifact",
    "events_by_trace_id",
    "events_by_fingerprint",
    "frames_by_file",
    "frames_by_node",
)


def _default_inventory_parts():
    frame = _file_frame(event_id=A, ordinal=0)
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        trace_id="trace-1",
        frames=(frame,),
    )
    artifact = _artifact_record(event_count=1, frame_count=1)
    return artifact, event, frame


def _empty_indexes():
    return InventoryIndexes(
        events_by_artifact=MappingProxyType({}),
        events_by_trace_id=MappingProxyType({}),
        events_by_fingerprint=MappingProxyType({}),
        frames_by_file=MappingProxyType({}),
        frames_by_node=MappingProxyType({}),
    )


def _indexes_or_empty(artifacts, events, frames):
    if (
        type(artifacts) is tuple
        and type(events) is tuple
        and type(frames) is tuple
        and all(type(item) is RuntimeArtifactRecord for item in artifacts)
        and all(type(item) is RuntimeEvent for item in events)
        and all(type(item) is RuntimeStackFrame for item in frames)
    ):
        return build_inventory_indexes(artifacts, events, frames)
    return _empty_indexes()


def _inventory(**kw):
    default_artifact, default_event, default_frame = _default_inventory_parts()
    artifacts = kw.get("artifacts", (default_artifact,))
    events = kw.get("events", (default_event,))
    frames = kw.get("frames", (default_frame,))
    indexes = _indexes_or_empty(artifacts, events, frames)
    values = dict(
        repository_namespace="repo",
        declared_revision=None,
        declared_captured_at=None,
        declared_capture_completeness=CaptureCompleteness.UNKNOWN,
        graph_binding=GraphBinding.NOT_PROVIDED,
        event_text_retained=True,
        artifacts=artifacts,
        events=events,
        frames=frames,
        issues=(),
        submitted_artifacts=1,
        duplicate_artifacts=0,
        total_input_bytes=6,
        total_events=1,
        total_frames=1,
        text_truncated=False,
        events_truncated=False,
        frames_truncated=False,
        issues_truncated=False,
        ingestion_complete=True,
        incomplete_reasons=(),
    )
    for name in _INVENTORY_INDEX_FIELDS:
        values[name] = getattr(indexes, name)
    values.update(kw)
    if "submitted_artifacts" not in kw:
        values["submitted_artifacts"] = (
            len(artifacts) + values["duplicate_artifacts"]
        )
    if "total_input_bytes" not in kw:
        values["total_input_bytes"] = sum(
            artifact.size_bytes for artifact in artifacts
        )
    if "total_events" not in kw:
        values["total_events"] = len(events)
    if "total_frames" not in kw:
        values["total_frames"] = len(frames)
    return RuntimeEvidenceInventory(**values)


def _inventory_with_forged_frame(**overrides):
    base = _inventory()
    valid_frame = base.frames[0]
    forged = object.__new__(RuntimeStackFrame)
    for field in fields(RuntimeStackFrame):
        object.__setattr__(
            forged, field.name, getattr(valid_frame, field.name)
        )
    for name, value in overrides.items():
        object.__setattr__(forged, name, value)
    forged_event = object.__new__(RuntimeEvent)
    for field in fields(RuntimeEvent):
        object.__setattr__(
            forged_event,
            field.name,
            getattr(base.events[0], field.name),
        )
    object.__setattr__(forged_event, "frames", (forged,))
    indexes = build_inventory_indexes(
        base.artifacts, (forged_event,), (forged,)
    )
    inventory = object.__new__(RuntimeEvidenceInventory)
    for field in fields(RuntimeEvidenceInventory):
        value = getattr(base, field.name)
        if field.name == "events":
            value = (forged_event,)
        elif field.name == "frames":
            value = (forged,)
        elif field.name in _INVENTORY_INDEX_FIELDS:
            value = getattr(indexes, field.name)
        object.__setattr__(inventory, field.name, value)
    return inventory


def _inventory_with_bypassed_frame(
    association,
    candidates,
    primary,
    *,
    basis=PathMatchBasis.EXACT_RELATIVE_PATH,
):
    return _inventory_with_forged_frame(
        event_id=A,
        relative_path="pkg/a.py",
        source_reference_sha256=A,
        path_match_basis=basis,
        line=1,
        association=association,
        candidate_node_ids=candidates,
        primary_node_id=primary,
    )


def _forged_event(**overrides):
    base = _inventory()
    forged = object.__new__(RuntimeEvent)
    for field in fields(RuntimeEvent):
        object.__setattr__(
            forged, field.name, getattr(base.events[0], field.name)
        )
    for name, value in overrides.items():
        object.__setattr__(forged, name, value)
    return forged


def test_inventory_field_order_schema_and_frozen_slotted():
    inventory = _inventory()
    inventory_type = type(inventory)
    assert inventory_type.SCHEMA_VERSION == "tracerag-runtime-evidence-v1"
    assert [field.name for field in fields(inventory_type)] == [
        "repository_namespace", "declared_revision", "declared_captured_at",
        "declared_capture_completeness", "graph_binding", "event_text_retained",
        "artifacts", "events", "frames", "issues", "submitted_artifacts",
        "duplicate_artifacts", "total_input_bytes", "total_events",
        "total_frames", "text_truncated", "events_truncated",
        "frames_truncated", "issues_truncated", "ingestion_complete",
        "incomplete_reasons", "events_by_artifact", "events_by_trace_id",
        "events_by_fingerprint", "frames_by_file", "frames_by_node"]
    assert not hasattr(inventory, "__dict__")
    with pytest.raises(FrozenInstanceError):
        inventory.repository_namespace = "x"


def test_inventory_index_fields_are_exact_and_immutable():
    assert [field.name for field in fields(InventoryIndexes)] == list(
        _INVENTORY_INDEX_FIELDS
    )
    artifact, event, frame = _default_inventory_parts()
    indexes = build_inventory_indexes((artifact,), (event,), (frame,))
    for name in _INVENTORY_INDEX_FIELDS:
        mapping = getattr(indexes, name)
        assert isinstance(mapping, MappingProxyType)
        with pytest.raises(TypeError):
            mapping["x"] = ()
    with pytest.raises(FrozenInstanceError):
        indexes.events_by_artifact = MappingProxyType({})


def test_inventory_indexes_are_exact_filters_and_read_only():
    inv = _inventory()
    assert isinstance(inv.events_by_artifact, MappingProxyType)
    assert inv.events_by_artifact == {
        _artifact_record().artifact_id: (inv.events[0],)
    }
    assert inv.events_by_trace_id == {"trace-1": (inv.events[0],)}
    assert inv.events_by_fingerprint == {"c" * 64: (inv.events[0],)}
    assert inv.frames_by_file == {"pkg/a.py": (inv.frames[0],)}
    assert inv.frames_by_node == {"f": (inv.frames[0],)}
    for name in _INVENTORY_INDEX_FIELDS:
        mapping = getattr(inv, name)
        assert isinstance(mapping, MappingProxyType)
        assert list(mapping) == sorted(mapping)
        for value in mapping.values():
            assert type(value) is tuple
        with pytest.raises(TypeError):
            mapping["x"] = ()
    assert validate_inventory(inv) is None


def test_inventory_copies_caller_supplied_index_dicts():
    artifact, event, frame = _default_inventory_parts()
    expected = build_inventory_indexes((artifact,), (event,), (frame,))
    caller_mappings = {
        name: dict(getattr(expected, name))
        for name in _INVENTORY_INDEX_FIELDS
    }
    inv = _inventory(
        artifacts=(artifact,),
        events=(event,),
        frames=(frame,),
        **caller_mappings,
    )
    for name in _INVENTORY_INDEX_FIELDS:
        mapping = getattr(inv, name)
        assert isinstance(mapping, MappingProxyType)
        assert dict(mapping) == dict(getattr(expected, name))
        assert mapping is not caller_mappings[name]
    for mapping in caller_mappings.values():
        mapping.clear()
    for name in _INVENTORY_INDEX_FIELDS:
        mapping = getattr(inv, name)
        assert isinstance(mapping, MappingProxyType)
        assert dict(mapping) == dict(getattr(expected, name))
        assert mapping is not caller_mappings[name]
        assert caller_mappings[name] == {}


def test_inventory_indexes_preserve_repeats_and_empty_artifact_keys():
    first_artifact = _artifact_record(
        event_count=2, frame_count=1, source_names=("a.log",)
    )
    second_artifact = _artifact_record(
        artifact_id="b" * 64,
        source_names=("b.log",),
        event_count=0,
        frame_count=0,
    )
    frame = _file_frame(event_id=A, ordinal=0)
    first_event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="1" * 64,
        trace_id="t",
        frames=(frame,),
    )
    second_event = _event(
        event_id="2" * 64,
        artifact_id=A,
        sequence=1,
        event_fingerprint="1" * 64,
        trace_id="t",
        frames=(),
    )
    inv = _inventory(
        artifacts=(first_artifact, second_artifact),
        events=(first_event, second_event),
        frames=(frame,),
        total_input_bytes=12,
    )
    assert inv.events_by_artifact == {
        A: (first_event, second_event),
        "b" * 64: (),
    }
    assert inv.events_by_trace_id == {"t": (first_event, second_event)}
    assert inv.events_by_fingerprint == {
        "1" * 64: (first_event, second_event)
    }
    assert inv.frames_by_file == {"pkg/a.py": (frame,)}
    assert inv.frames_by_node == {"f": (frame,)}


def test_inventory_frames_by_file_and_node_filters():
    verified_suffix = _verified_frame(
        event_id=A,
        ordinal=0,
        relative_path="pkg/a.py",
        path_match_basis=PathMatchBasis.UNIQUE_PATH_SUFFIX,
        association=FrameAssociation.FILE,
        candidate_node_ids=("f",),
        primary_node_id="f",
    )
    unverified = _frame(
        frame_id="2" * 64,
        event_id=A,
        ordinal=1,
        relative_path="pkg/b.py",
        source_reference_sha256=A,
        path_match_basis=PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
        association=FrameAssociation.NOT_EVALUATED,
    )
    ambiguous = _frame(
        frame_id="3" * 64,
        event_id=A,
        ordinal=2,
        relative_path="pkg/c.py",
        source_reference_sha256=A,
        path_match_basis=PathMatchBasis.AMBIGUOUS,
        association=FrameAssociation.UNASSOCIATED,
    )
    unmatched = _frame(
        frame_id="4" * 64,
        event_id=A,
        ordinal=3,
        relative_path="pkg/d.py",
        source_reference_sha256=A,
        path_match_basis=PathMatchBasis.UNMATCHED,
        association=FrameAssociation.UNASSOCIATED,
    )
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(verified_suffix, unverified, ambiguous, unmatched),
    )
    inv = _inventory(
        artifacts=(_artifact_record(event_count=1, frame_count=4),),
        events=(event,),
        frames=(verified_suffix, unverified, ambiguous, unmatched),
    )
    assert list(inv.frames_by_file) == [
        "pkg/a.py", "pkg/b.py", "pkg/c.py", "pkg/d.py"
    ]
    assert inv.frames_by_file == {
        "pkg/a.py": (verified_suffix,),
        "pkg/b.py": (unverified,),
        "pkg/c.py": (ambiguous,),
        "pkg/d.py": (unmatched,),
    }
    assert inv.frames_by_node == {"f": (verified_suffix,)}


def test_inventory_symbol_frame_is_indexed_only_by_primary_node():
    symbol = _verified_frame(
        event_id=A,
        ordinal=0,
        association=FrameAssociation.SYMBOL,
        candidate_node_ids=("a", "z"),
        primary_node_id="z",
    )
    ambiguous = _verified_frame(
        frame_id="2" * 64,
        event_id=A,
        ordinal=1,
        association=FrameAssociation.AMBIGUOUS_SYMBOL,
        candidate_node_ids=("a", "b", "z"),
        primary_node_id=None,
    )
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(symbol, ambiguous),
    )
    inv = _inventory(
        artifacts=(_artifact_record(event_count=1, frame_count=2),),
        events=(event,),
        frames=(symbol, ambiguous),
    )
    assert inv.frames_by_file == {
        "pkg/a.py": (symbol, ambiguous)
    }
    assert inv.frames_by_node == {"z": (symbol,)}
    assert "a" not in inv.frames_by_node


def test_inventory_counter_formulas_and_derived_completeness():
    inv = _inventory()
    assert inv.submitted_artifacts == (
        len(inv.artifacts) + inv.duplicate_artifacts
    )
    assert inv.total_input_bytes == sum(
        artifact.size_bytes for artifact in inv.artifacts
    )
    assert inv.total_events == len(inv.events) == sum(
        artifact.event_count for artifact in inv.artifacts
    )
    assert inv.total_frames == len(inv.frames) == sum(
        artifact.frame_count for artifact in inv.artifacts
    ) == sum(len(event.frames) for event in inv.events)
    assert sum(artifact.issue_count for artifact in inv.artifacts) == len(
        inv.issues
    )
    assert inv.ingestion_complete == (inv.incomplete_reasons == ())


def test_inventory_rejects_wrong_count_and_index_snapshot():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(total_events=2)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_artifact={})


@pytest.mark.parametrize(
    "field,value",
    [
        ("submitted_artifacts", 2),
        ("duplicate_artifacts", 1),
        ("total_input_bytes", 7),
        ("total_events", 2),
        ("total_frames", 2),
    ],
)
def test_inventory_rejects_each_wrong_counter(field, value):
    extra = {}
    if field == "duplicate_artifacts":
        extra["submitted_artifacts"] = 1
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(**{field: value, **extra})


def test_inventory_authoritative_tuples_are_exact_tuples():
    inventory = _inventory()
    for field in ("artifacts", "events", "frames", "issues",
                  "incomplete_reasons"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: list(getattr(inventory, field))})


def test_inventory_authoritative_tuples_require_exact_element_types():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(object(),),
            events=(),
            frames=(),
            submitted_artifacts=1,
            total_input_bytes=0,
            total_events=0,
            total_frames=0,
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events=(object(),), total_events=1)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(frames=(object(),), total_frames=1)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(issues=(object(),))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(incomplete_reasons=(object(),))


def test_inventory_rejects_surrogate_index_key_or_value():
    event = _event()
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={"\ud800": (event,)})
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={"trace\x00id": (event,)})
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events=(_forged_event(message="secret\ud800"),),
                   total_events=1)


def test_inventory_requires_derived_incomplete_reasons_and_flags():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            incomplete_reasons=(RuntimeIncompleteReason.TEXT_TRUNCATED,)
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(text_truncated=True, incomplete_reasons=())


def test_inventory_rejects_invalid_top_level_scalars_enums_bools_and_counts():
    for bad in ("", "\ud800"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(repository_namespace=bad)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(repository_namespace=None)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_revision="rev\x00")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_revision=1)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_captured_at="\ud800")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_captured_at=object())
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(declared_capture_completeness="unknown")
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(graph_binding="not_provided")
    for field in ("event_text_retained", "text_truncated", "events_truncated",
                  "frames_truncated", "issues_truncated", "ingestion_complete"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: 1})
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: "false"})
    for field in ("submitted_artifacts", "duplicate_artifacts",
                  "total_input_bytes", "total_events", "total_frames"):
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: True})
        with pytest.raises(RuntimeEvidenceInvariantError):
            _inventory(**{field: -1})


def test_inventory_rejects_unsorted_duplicate_or_wrong_reasons():
    partial = _artifact_record(
        event_count=1, frame_count=1,
        status=RuntimeArtifactStatus.PARTIAL,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(artifacts=(partial,))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(partial,),
            events_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(
                RuntimeIncompleteReason.EVENT_LIMIT_REACHED,
                RuntimeIncompleteReason.ARTIFACT_PARTIAL,
            ),
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(partial,),
            events_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(
                RuntimeIncompleteReason.ARTIFACT_PARTIAL,
                RuntimeIncompleteReason.ARTIFACT_PARTIAL,
                RuntimeIncompleteReason.EVENT_LIMIT_REACHED,
            ),
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(partial,),
            ingestion_complete=False,
            incomplete_reasons=("artifact_partial",),
        )


def test_inventory_accepts_sorted_derived_incomplete_reasons():
    partial = _artifact_record(
        event_count=1,
        frame_count=1,
        status=RuntimeArtifactStatus.PARTIAL,
        text_truncated=True,
        frames_truncated=True,
        issue_count=1,
    )
    frame = _file_frame(event_id=A, ordinal=0)
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(frame,),
        text_truncated=True,
        frames_truncated=True,
    )
    sentinel = _issue(
        code=RuntimeIssueCode.ISSUE_LIMIT_REACHED,
        message=ISSUE_MESSAGES[RuntimeIssueCode.ISSUE_LIMIT_REACHED],
        artifact_id=A,
        event_sequence=None,
        source_line=None,
    )
    reasons = (
        RuntimeIncompleteReason.ARTIFACT_PARTIAL,
        RuntimeIncompleteReason.EVENT_LIMIT_REACHED,
        RuntimeIncompleteReason.FRAME_LIMIT_REACHED,
        RuntimeIncompleteReason.ISSUE_LIMIT_REACHED,
        RuntimeIncompleteReason.TEXT_TRUNCATED,
    )
    inv = _inventory(
        artifacts=(partial,),
        events=(event,),
        frames=(frame,),
        issues=(sentinel,),
        text_truncated=True,
        events_truncated=True,
        frames_truncated=True,
        issues_truncated=True,
        ingestion_complete=False,
        incomplete_reasons=reasons,
    )
    assert inv.incomplete_reasons == reasons
    assert inv.ingestion_complete is False


def test_inventory_requires_ingestion_complete_to_match_reasons():
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(ingestion_complete=False)
    partial = _artifact_record(
        event_count=1, frame_count=1,
        status=RuntimeArtifactStatus.PARTIAL,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(partial,),
            ingestion_complete=True,
            incomplete_reasons=(RuntimeIncompleteReason.ARTIFACT_PARTIAL,),
        )


def test_inventory_truncation_flags_must_match_event_and_artifact_flags():
    frame = _file_frame(event_id=A, ordinal=0)
    truncated_event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(frame,),
        text_truncated=True,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(event_count=1, frame_count=1),),
            events=(truncated_event,),
            frames=(frame,),
            text_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(RuntimeIncompleteReason.TEXT_TRUNCATED,),
        )
    truncated_artifact = _artifact_record(
        event_count=1,
        frame_count=1,
        text_truncated=True,
        status=RuntimeArtifactStatus.COMPLETE,
    )
    with pytest.raises(
        RuntimeEvidenceInvariantError,
        match="artifact text_truncated must match its events",
    ):
        _inventory(
            artifacts=(truncated_artifact,),
            events=(_event(
                event_id=A,
                artifact_id=A,
                sequence=0,
                event_fingerprint="c" * 64,
                frames=(frame,),
                text_truncated=False,
                frames_truncated=False,
            ),),
            frames=(frame,),
            text_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(RuntimeIncompleteReason.TEXT_TRUNCATED,),
        )
    frames_truncated_artifact = _artifact_record(
        event_count=1,
        frame_count=1,
        frames_truncated=True,
        status=RuntimeArtifactStatus.COMPLETE,
    )
    with pytest.raises(
        RuntimeEvidenceInvariantError,
        match="artifact frames_truncated must match its events",
    ):
        _inventory(
            artifacts=(frames_truncated_artifact,),
            events=(_event(
                event_id=A,
                artifact_id=A,
                sequence=0,
                event_fingerprint="c" * 64,
                frames=(frame,),
                text_truncated=False,
                frames_truncated=False,
            ),),
            frames=(frame,),
            frames_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(
                RuntimeIncompleteReason.FRAME_LIMIT_REACHED,
            ),
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            text_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(RuntimeIncompleteReason.TEXT_TRUNCATED,),
        )
    frame_truncated_event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(frame,),
        frames_truncated=True,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(event_count=1, frame_count=1),),
            events=(frame_truncated_event,),
            frames=(frame,),
            frames_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(RuntimeIncompleteReason.FRAME_LIMIT_REACHED,),
        )


def test_inventory_rejects_bad_artifact_order_and_conflicts():
    first = _artifact_record(
        artifact_id="a" * 64,
        source_names=("b.log",),
        event_count=0,
        frame_count=0,
    )
    second = _artifact_record(
        artifact_id="b" * 64,
        source_names=("a.log",),
        event_count=0,
        frame_count=0,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(first, second),
            events=(),
            frames=(),
            total_events=0,
            total_frames=0,
            total_input_bytes=12,
        )
    duplicate_id = _artifact_record(
        artifact_id="a" * 64,
        source_names=("c.log",),
        event_count=0,
        frame_count=0,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(first, duplicate_id),
            events=(),
            frames=(),
            total_events=0,
            total_frames=0,
            total_input_bytes=12,
        )
    conflicting = _artifact_record(
        artifact_id="b" * 64,
        source_names=("b.log",),
        event_count=0,
        frame_count=0,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(first, conflicting),
            events=(),
            frames=(),
            total_events=0,
            total_frames=0,
            total_input_bytes=12,
        )


def test_inventory_rejects_event_order_gaps_and_duplicate_ids():
    artifact = _artifact_record(event_count=2, frame_count=0)
    first = _event(
        event_id=A,
        artifact_id=A,
        sequence=1,
        event_fingerprint="c" * 64,
        frames=(),
    )
    second = _event(
        event_id="2" * 64,
        artifact_id=A,
        sequence=2,
        event_fingerprint="c" * 64,
        frames=(),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(first, second),
            frames=(),
            total_events=2,
            total_frames=0,
        )
    duplicate_id = _event(
        event_id=A,
        artifact_id=A,
        sequence=1,
        event_fingerprint="c" * 64,
        frames=(),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(_event(
                event_id=A,
                artifact_id=A,
                sequence=0,
                event_fingerprint="c" * 64,
                frames=(),
            ), duplicate_id),
            frames=(),
            total_events=2,
            total_frames=0,
        )


def test_inventory_rejects_unknown_event_artifact_and_per_artifact_counts():
    artifact = _artifact_record(event_count=0, frame_count=0)
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(_event(
                event_id=A,
                artifact_id="b" * 64,
                sequence=0,
                event_fingerprint="c" * 64,
                frames=(),
            ),),
            frames=(),
            total_events=1,
            total_frames=0,
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(artifacts=(_artifact_record(event_count=2),))
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(artifacts=(_artifact_record(frame_count=2),))


def test_inventory_rejects_frame_flattening_and_mismatches():
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(_inventory_with_forged_frame(ordinal=1))
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(_inventory_with_forged_frame(event_id="b" * 64))
    frame = _file_frame(event_id=A, ordinal=0)
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(frame,),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(event_count=1, frame_count=1),),
            events=(event,),
            frames=(),
            total_frames=0,
        )


def test_inventory_rejects_duplicate_frame_ids():
    artifact = _artifact_record(event_count=1, frame_count=2)
    first = _file_frame(event_id=A, ordinal=0)
    second = _file_frame(event_id=A, ordinal=1)
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(first, second),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(event,),
            frames=(first, second),
            total_frames=2,
        )


def test_inventory_rejects_issue_order_duplicates_and_artifact_counts():
    artifact = _artifact_record(
        event_count=0, frame_count=0, issue_count=2
    )
    absent = _issue(
        code=RuntimeIssueCode.INVALID_UTF8,
        message=ISSUE_MESSAGES[RuntimeIssueCode.INVALID_UTF8],
        artifact_id=A,
        event_sequence=None,
        source_line=1,
    )
    present = _issue(
        code=RuntimeIssueCode.NUL_BYTE,
        message=ISSUE_MESSAGES[RuntimeIssueCode.NUL_BYTE],
        artifact_id=A,
        event_sequence=0,
        source_line=1,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(),
            frames=(),
            issues=(absent, present),
            total_events=0,
            total_frames=0,
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(),
            frames=(),
            issues=(present, present),
            total_events=0,
            total_frames=0,
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(
                event_count=0, frame_count=0, issue_count=2
            ),),
            events=(),
            frames=(),
            issues=(present,),
            total_events=0,
            total_frames=0,
        )


def test_inventory_accepts_canonical_issue_order():
    first_artifact = _artifact_record(
        source_names=("a.log",),
        event_count=0,
        frame_count=0,
        issue_count=2,
    )
    second_artifact = _artifact_record(
        artifact_id="b" * 64,
        source_names=("b.log",),
        event_count=0,
        frame_count=0,
        issue_count=1,
    )
    issues = (
        _issue(
            code=RuntimeIssueCode.INVALID_UTF8,
            message=ISSUE_MESSAGES[RuntimeIssueCode.INVALID_UTF8],
            artifact_id=A,
            event_sequence=0,
            source_line=1,
        ),
        _issue(
            code=RuntimeIssueCode.NUL_BYTE,
            message=ISSUE_MESSAGES[RuntimeIssueCode.NUL_BYTE],
            artifact_id=A,
            event_sequence=None,
            source_line=2,
        ),
        _issue(
            code=RuntimeIssueCode.INVALID_UTF8,
            message=ISSUE_MESSAGES[RuntimeIssueCode.INVALID_UTF8],
            artifact_id="b" * 64,
            event_sequence=None,
            source_line=None,
        ),
    )
    inv = _inventory(
        artifacts=(first_artifact, second_artifact),
        events=(),
        frames=(),
        issues=issues,
        total_events=0,
        total_frames=0,
        total_input_bytes=12,
    )
    assert inv.issues == issues


def test_inventory_requires_exactly_one_sentinel_when_truncated():
    sentinel = _issue(
        code=RuntimeIssueCode.ISSUE_LIMIT_REACHED,
        message=ISSUE_MESSAGES[RuntimeIssueCode.ISSUE_LIMIT_REACHED],
        artifact_id=A,
        event_sequence=None,
        source_line=None,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            issues_truncated=True,
            ingestion_complete=False,
            incomplete_reasons=(
                RuntimeIncompleteReason.ISSUE_LIMIT_REACHED,
            ),
        )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(
                event_count=1, frame_count=1, issue_count=1
            ),),
            issues=(sentinel,),
        )


def test_inventory_rejects_invalid_index_snapshots():
    event = _event()
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_artifact=())
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={1: (event,)})
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={"trace-1": (event,)},
                   events_by_artifact={A: [event]})
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={"extraneous": (event,)})
    extra_event = _event(
        event_id="9" * 64,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        trace_id="other",
        frames=(),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={
            "other": (extra_event,),
            "trace-1": (event,),
        })


def test_inventory_rejects_non_tuple_index_values_and_reordering():
    event = _event()
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={"trace-1": [event]})
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(events_by_trace_id={
            "trace-1": _TupleSubclass((event,))
        })
    artifact = _artifact_record(event_count=2, frame_count=0)
    first = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        trace_id="t",
        frames=(),
    )
    second = _event(
        event_id="2" * 64,
        artifact_id=A,
        sequence=1,
        event_fingerprint="c" * 64,
        trace_id="t",
        frames=(),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(artifact,),
            events=(first, second),
            frames=(),
            total_events=2,
            total_frames=0,
            events_by_trace_id={"t": (second, first)},
        )


def test_inventory_rejects_unsorted_index_keys():
    first = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        trace_id="b",
        frames=(),
    )
    second = _event(
        event_id="2" * 64,
        artifact_id=A,
        sequence=1,
        event_fingerprint="c" * 64,
        trace_id="a",
        frames=(),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(event_count=2, frame_count=0),),
            events=(first, second),
            frames=(),
            total_events=2,
            total_frames=0,
            events_by_trace_id={
                "b": (first,),
                "a": (second,),
            },
        )


def test_inventory_frames_by_node_must_not_use_symbol_file_candidate():
    symbol = _verified_frame(
        event_id=A,
        ordinal=0,
        association=FrameAssociation.SYMBOL,
        candidate_node_ids=("a", "z"),
        primary_node_id="z",
    )
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint="c" * 64,
        frames=(symbol,),
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        _inventory(
            artifacts=(_artifact_record(event_count=1, frame_count=1),),
            events=(event,),
            frames=(symbol,),
            frames_by_node={"a": (symbol,)},
        )


def test_inventory_rejects_duplicate_candidates():
    inventory = _inventory_with_forged_frame(
        association=FrameAssociation.AMBIGUOUS_SYMBOL,
        candidate_node_ids=("a", "a", "b"),
        primary_node_id=None,
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(inventory)


def test_validate_inventory_rejects_bypassed_frame_shapes():
    for association, candidates, primary in (
        (FrameAssociation.AMBIGUOUS_SYMBOL, ("a", "f"), None),
        (FrameAssociation.FILE, ("a", "f"), "a"),
        (FrameAssociation.SYMBOL, ("a", "z"), "x"),
    ):
        inventory = _inventory_with_bypassed_frame(
            association, candidates, primary
        )
        with pytest.raises(RuntimeEvidenceInvariantError):
            validate_inventory(inventory)
    for association, basis in (
        (FrameAssociation.UNASSOCIATED,
         PathMatchBasis.UNVERIFIED_RELATIVE_PATH),
        (FrameAssociation.NOT_EVALUATED, PathMatchBasis.UNMATCHED),
    ):
        inventory = _inventory_with_bypassed_frame(
            association, (), None, basis=basis
        )
        with pytest.raises(RuntimeEvidenceInvariantError):
            validate_inventory(inventory)


def test_inventory_is_immutable_after_attempted_mutation():
    inv = _inventory()
    for name in _INVENTORY_INDEX_FIELDS:
        mapping = getattr(inv, name)
        with pytest.raises(TypeError):
            mapping["x"] = ()
    with pytest.raises(FrozenInstanceError):
        inv.frames = ()
    with pytest.raises(TypeError):
        inv.frames[0].candidate_node_ids[0] = "x"
    with pytest.raises(FrozenInstanceError):
        inv.events[0].message = "x"
    with pytest.raises(FrozenInstanceError):
        inv.frames[0].function_text = "x"
    with pytest.raises(FrozenInstanceError):
        inv.issues = ()


def test_inventory_retains_no_graph_reference_or_content():
    inventory = _inventory()
    field_names = {field.name for field in fields(RuntimeEvidenceInventory)}
    for forbidden in ("graph", "code_graph", "graph_inventory",
                      "graph_nodes", "nodes"):
        assert forbidden not in field_names
    assert not hasattr(inventory, "graph")
    assert inventory.graph_binding is GraphBinding.NOT_PROVIDED


def test_validate_inventory_requires_an_inventory_instance():
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(object())


def test_validate_inventory_rejects_incomplete_bypassed_inventory():
    inventory = object.__new__(RuntimeEvidenceInventory)
    with pytest.raises(RuntimeEvidenceInvariantError) as excinfo:
        validate_inventory(inventory)
    assert str(excinfo.value) == (
        "RuntimeEvidenceInventory is missing required fields"
    )
    object.__setattr__(inventory, "repository_namespace", "repo")
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(inventory)


def _bypassed_inventory_from(base):
    inventory = object.__new__(RuntimeEvidenceInventory)
    for field in fields(RuntimeEvidenceInventory):
        object.__setattr__(
            inventory, field.name, getattr(base, field.name)
        )
    return inventory


def test_validate_inventory_rejects_forged_records_only_in_indexes():
    base = _inventory()
    forged_event = object.__new__(RuntimeEvent)
    for field in fields(RuntimeEvent):
        object.__setattr__(
            forged_event, field.name, getattr(base.events[0], field.name)
        )
    object.__delattr__(forged_event, "message")
    event_inventory = _bypassed_inventory_from(base)
    object.__setattr__(
        event_inventory, "events_by_artifact", {A: (forged_event,)}
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(event_inventory)

    forged_frame = object.__new__(RuntimeStackFrame)
    for field in fields(RuntimeStackFrame):
        object.__setattr__(
            forged_frame, field.name, getattr(base.frames[0], field.name)
        )
    object.__delattr__(forged_frame, "function_text")
    frame_inventory = _bypassed_inventory_from(base)
    object.__setattr__(
        frame_inventory, "frames_by_file", {"pkg/a.py": (forged_frame,)}
    )
    with pytest.raises(RuntimeEvidenceInvariantError):
        validate_inventory(frame_inventory)


_PRIVACY_SENTINEL = "CP1-PRIVACY"
_PRIVACY_CONTENT = b"CP1-PRIVACY-ARTIFACT-CONTENT\x00\xff"
_PRIVACY_SOURCE = "CP1-PRIVACY-SOURCE-NAME.log"
_PRIVACY_NAMESPACE = "CP1-PRIVACY-NAMESPACE"
_PRIVACY_REVISION = "CP1-PRIVACY-REVISION"
_PRIVACY_CAPTURED = "CP1-PRIVACY-CAPTURED"
_PRIVACY_TAG_KEY = "CP1-PRIVACY-TAG-KEY"
_PRIVACY_TAG_VALUE = "CP1-PRIVACY-TAG-VALUE"
_PRIVACY_FUNCTION = "CP1-PRIVACY-FUNCTION"
_PRIVACY_PATH = "CP1-PRIVACY-PATH"
_PRIVACY_NODE = "CP1-PRIVACY-NODE"
_PRIVACY_TRACE = "CP1-PRIVACY-TRACE"
_PRIVACY_SPAN = "CP1-PRIVACY-SPAN"
_PRIVACY_TIMESTAMP = "CP1-PRIVACY-TIMESTAMP"
_PRIVACY_LEVEL = "CP1-PRIVACY-LEVEL"
_PRIVACY_MESSAGE = "CP1-PRIVACY-MESSAGE"
_PRIVACY_LOGGER = "CP1-PRIVACY-LOGGER"
_PRIVACY_OPERATION = "CP1-PRIVACY-OPERATION"
_PRIVACY_STATUS = "CP1-PRIVACY-STATUS"
_PRIVACY_ERROR = "CP1-PRIVACY-ERROR"
_PRIVACY_FINGERPRINT = "7" * 64
_PRIVACY_CONTENT_SHA = "8" * 64
_PRIVACY_ISSUE_MESSAGE = "line is not valid UTF-8"


def _privacy_models():
    content = _PRIVACY_CONTENT
    artifact = RuntimeArtifact(
        _PRIVACY_SOURCE,
        RuntimeArtifactFormat.TEXT_LOG,
        content,
        RuntimeFrameLanguage.PYTHON,
    )
    request = RuntimeEvidenceRequest(
        _PRIVACY_NAMESPACE,
        (artifact,),
        declared_revision=_PRIVACY_REVISION,
        declared_captured_at=_PRIVACY_CAPTURED,
        declared_capture_completeness=CaptureCompleteness.PARTIAL,
    )
    tag = RuntimeTag(_PRIVACY_TAG_KEY, _PRIVACY_TAG_VALUE)
    frame = _file_frame(
        event_id=A,
        ordinal=0,
        function_text=_PRIVACY_FUNCTION,
        relative_path=f"pkg/{_PRIVACY_PATH}.py",
        line=44,
        column=7,
        primary_node_id=f"{_PRIVACY_NODE}-PRIMARY",
        candidate_node_ids=(f"{_PRIVACY_NODE}-PRIMARY",),
    )
    event = _event(
        event_id=A,
        artifact_id=A,
        sequence=0,
        event_fingerprint=_PRIVACY_FINGERPRINT,
        source_line=12,
        kind=RuntimeEventKind.ERROR,
        timestamp_text=_PRIVACY_TIMESTAMP,
        level=_PRIVACY_LEVEL,
        message=_PRIVACY_MESSAGE,
        logger=_PRIVACY_LOGGER,
        operation=_PRIVACY_OPERATION,
        trace_id=_PRIVACY_TRACE,
        span_id=_PRIVACY_SPAN,
        parent_span_id=f"{_PRIVACY_SPAN}-PARENT",
        duration_ms_text="12.5",
        status=_PRIVACY_STATUS,
        error_type=f"{_PRIVACY_ERROR}-TYPE",
        error_message=f"{_PRIVACY_ERROR}-MESSAGE",
        tags=(tag,),
        frames=(frame,),
    )
    issue = _issue(
        code=RuntimeIssueCode.INVALID_UTF8,
        message=_PRIVACY_ISSUE_MESSAGE,
        artifact_id=A,
        event_sequence=0,
        source_line=12,
    )
    record = _artifact_record(
        artifact_id=A,
        source_names=(_PRIVACY_SOURCE,),
        content_sha256=_PRIVACY_CONTENT_SHA,
        size_bytes=len(content),
        event_count=1,
        frame_count=1,
        issue_count=1,
    )
    inventory = _inventory(
        artifacts=(record,),
        events=(event,),
        frames=(frame,),
        issues=(issue,),
        repository_namespace=_PRIVACY_NAMESPACE,
        declared_revision=_PRIVACY_REVISION,
        declared_captured_at=_PRIVACY_CAPTURED,
        declared_capture_completeness=CaptureCompleteness.PARTIAL,
    )
    return artifact, request, tag, frame, event, issue, record, inventory


def _privacy_forbidden_values():
    return (
        _PRIVACY_SENTINEL,
        _PRIVACY_CONTENT.decode("latin-1"),
        _PRIVACY_SOURCE,
        _PRIVACY_NAMESPACE,
        _PRIVACY_REVISION,
        _PRIVACY_CAPTURED,
        _PRIVACY_TAG_KEY,
        _PRIVACY_TAG_VALUE,
        _PRIVACY_FUNCTION,
        _PRIVACY_PATH,
        _PRIVACY_NODE,
        _PRIVACY_TRACE,
        _PRIVACY_SPAN,
        _PRIVACY_TIMESTAMP,
        _PRIVACY_LEVEL,
        _PRIVACY_MESSAGE,
        _PRIVACY_LOGGER,
        _PRIVACY_OPERATION,
        _PRIVACY_STATUS,
        _PRIVACY_ERROR,
        _PRIVACY_FINGERPRINT,
        _PRIVACY_CONTENT_SHA,
        _PRIVACY_ISSUE_MESSAGE,
        "pkg/",
        "b'",
        'b"',
    )


def test_public_data_model_reprs_use_only_the_redacted_allowlist():
    artifact, request, tag, frame, event, issue, record, inventory = (
        _privacy_models()
    )
    values = (artifact, request, tag, frame, event, issue, record, inventory)
    for value in values:
        assert "__repr__" in type(value).__dict__, type(value).__name__
    assert repr(tag) == "RuntimeTag()"
    assert repr(artifact) == (
        "RuntimeArtifact(format=text_log, declared_language=python, "
        f"size_bytes={len(_PRIVACY_CONTENT)})"
    )
    assert repr(request) == "RuntimeEvidenceRequest(artifact_count=1)"
    assert repr(frame) == (
        f"RuntimeStackFrame(frame_id={A}, event_id={A}, ordinal=0, "
        "language=unknown, line=44, column=7, "
        "path_match_basis=exact_relative_path, association=file, "
        "candidate_count=1)"
    )
    assert repr(event) == (
        f"RuntimeEvent(event_id={A}, artifact_id={A}, sequence=0, "
        "source_line=12, kind=error, frame_count=1, "
        "text_truncated=False, frames_truncated=False)"
    )
    assert repr(issue) == (
        f"RuntimeEvidenceIssue(code=invalid_utf8, artifact_id={A}, "
        "event_sequence=0, source_line=12)"
    )
    assert repr(record) == (
        f"RuntimeArtifactRecord(artifact_id={A}, format=text_log, "
        "declared_language=None, "
        f"size_bytes={len(_PRIVACY_CONTENT)}, status=complete, "
        "event_count=1, frame_count=1, duplicate_count=0, "
        "text_truncated=False, frames_truncated=False, issue_count=1)"
    )
    assert repr(inventory) == (
        "RuntimeEvidenceInventory(artifact_count=1, event_count=1, "
        "frame_count=1, issue_count=1, "
        f"total_input_bytes={len(_PRIVACY_CONTENT)}, "
        "submitted_artifacts=1, duplicate_artifacts=0, "
        "graph_binding=not_provided, event_text_retained=True, "
        "ingestion_complete=True, text_truncated=False, "
        "events_truncated=False, frames_truncated=False, "
        "issues_truncated=False, incomplete_reason_count=0)"
    )
    for value in values:
        text = repr(value)
        for forbidden in _privacy_forbidden_values():
            assert forbidden not in text, (type(value).__name__, forbidden)


def test_public_data_model_reprs_and_strs_never_expose_sensitive_values():
    for value in _privacy_models():
        representation = repr(value)
        string = str(value)
        assert string == representation
        for forbidden in _privacy_forbidden_values():
            assert forbidden not in representation, (
                type(value).__name__, "repr", forbidden)
            assert forbidden not in string, (
                type(value).__name__, "str", forbidden)


def test_redacted_reprs_do_not_recurse_into_nested_records_or_indexes():
    _, request, _, _, event, _, _, inventory = _privacy_models()
    request_text = repr(request)
    event_text = repr(event)
    inventory_text = repr(inventory)
    assert "RuntimeArtifact(" not in request_text
    assert "RuntimeTag(" not in event_text
    assert "RuntimeStackFrame(" not in event_text
    for marker in (
        "RuntimeArtifactRecord(",
        "RuntimeEvent(",
        "RuntimeStackFrame(",
        "RuntimeEvidenceIssue(",
        "events_by_",
        "frames_by_",
        "{",
        "}",
    ):
        assert marker not in inventory_text, marker


def test_runtime_artifact_large_binary_repr_omits_content_and_source_name():
    body = (b"CP1-PRIVACY-LARGE-CONTENT" + bytes(range(256))) * 5_000
    artifact = RuntimeArtifact(
        "CP1-PRIVACY-LARGE-SOURCE.log",
        RuntimeArtifactFormat.TEXT_LOG,
        body,
    )
    text = repr(artifact)
    expected = (
        "RuntimeArtifact(format=text_log, declared_language=None, "
        f"size_bytes={len(body)})"
    )
    assert len(text) == len(expected)
    assert text == expected
    for forbidden in (
        "CP1-PRIVACY-LARGE-CONTENT",
        "CP1-PRIVACY-LARGE-SOURCE",
        "b'",
        'b"',
    ):
        assert forbidden not in text


def test_redacted_reprs_contain_no_raw_or_escaped_hostile_text():
    hostile = "CP1-PRIVACY-HOSTILE-\x1b[31m\u202e"
    tag = RuntimeTag(hostile, hostile)
    frame = _file_frame(
        event_id=A,
        ordinal=0,
        function_text=hostile,
    )
    event = _event(
        message=hostile,
        logger=hostile,
        operation=hostile,
        trace_id=hostile,
        tags=(tag,),
        frames=(frame,),
    )
    for value in (tag, frame, event):
        text = repr(value)
        assert hostile not in text
        assert "CP1-PRIVACY-HOSTILE" not in text
        assert "\x1b" not in text
        assert "\u202e" not in text
        assert "\\x1b" not in text
        assert "\\u202e" not in text


_PIPELINE_STACK = (
    b'  File "pkg/a.py", line 10, in f\n'
    b'  File "pkg/b.py", line 20, in g\n'
    b"ValueError: boom\n"
)


def _pipeline_inventory():
    return RuntimeEvidenceIngestor().ingest(RuntimeEvidenceRequest(
        "repo",
        (
            RuntimeArtifact("a.log", RuntimeArtifactFormat.TEXT_LOG,
                            b"one\n"),
            RuntimeArtifact("a.log", RuntimeArtifactFormat.TEXT_LOG,
                            b"one\n"),
            RuntimeArtifact("b.json", RuntimeArtifactFormat.JSON_EVENTS,
                            b'{"kind":"log","message":"m",'
                            b'"trace_id":"trace-1"}'),
            RuntimeArtifact("c.stack",
                            RuntimeArtifactFormat.STACK_TRACE_TEXT,
                            _PIPELINE_STACK,
                            RuntimeFrameLanguage.PYTHON),
        ),
    ))


def test_pipeline_counters_equal_the_authoritative_tuples():
    inv = _pipeline_inventory()
    assert inv.submitted_artifacts == 4
    assert inv.duplicate_artifacts == 1
    assert inv.submitted_artifacts == (
        len(inv.artifacts) + inv.duplicate_artifacts)
    assert inv.total_input_bytes == sum(
        artifact.size_bytes for artifact in inv.artifacts)
    assert inv.total_events == len(inv.events) == sum(
        artifact.event_count for artifact in inv.artifacts)
    assert inv.total_frames == len(inv.frames) == sum(
        artifact.frame_count for artifact in inv.artifacts) == sum(
        len(event.frames) for event in inv.events)
    assert sum(artifact.issue_count for artifact in inv.artifacts) == len(
        inv.issues)
    assert inv.ingestion_complete is True
    assert inv.incomplete_reasons == ()
    validate_inventory(inv)


def test_pipeline_indexes_are_immutable_exact_filters():
    inv = _pipeline_inventory()
    assert len(inv.artifacts) == 3
    assert len(inv.events) == 3
    assert len(inv.frames) == 2
    expected_by_artifact = {
        artifact.artifact_id: tuple(
            event for event in inv.events
            if event.artifact_id == artifact.artifact_id)
        for artifact in inv.artifacts
    }
    expected_by_trace: dict[str, list] = {}
    expected_by_fingerprint: dict[str, list] = {}
    for event in inv.events:
        if event.trace_id is not None:
            expected_by_trace.setdefault(event.trace_id, []).append(event)
        expected_by_fingerprint.setdefault(
            event.event_fingerprint, []).append(event)
    expected_by_file: dict[str, list] = {}
    expected_by_node: dict[str, list] = {}
    for frame in inv.frames:
        if frame.relative_path is not None:
            expected_by_file.setdefault(
                frame.relative_path, []).append(frame)
        if (
            frame.association
            in (FrameAssociation.FILE, FrameAssociation.SYMBOL)
            and frame.primary_node_id is not None
        ):
            expected_by_node.setdefault(
                frame.primary_node_id, []).append(frame)

    assert dict(inv.events_by_artifact) == expected_by_artifact
    assert dict(inv.events_by_trace_id) == {
        key: tuple(values) for key, values in expected_by_trace.items()}
    assert dict(inv.events_by_fingerprint) == {
        key: tuple(values)
        for key, values in expected_by_fingerprint.items()}
    assert dict(inv.frames_by_file) == {
        key: tuple(values) for key, values in expected_by_file.items()}
    assert dict(inv.frames_by_node) == {
        key: tuple(values) for key, values in expected_by_node.items()}
    assert inv.events_by_trace_id["trace-1"] == (
        tuple(event for event in inv.events
              if event.trace_id == "trace-1"))
    assert set(inv.events_by_artifact) == {
        artifact.artifact_id for artifact in inv.artifacts}
    assert set(inv.frames_by_file) == {"pkg/a.py", "pkg/b.py"}

    for name in _INVENTORY_INDEX_FIELDS:
        mapping = getattr(inv, name)
        assert isinstance(mapping, MappingProxyType)
        assert list(mapping) == sorted(mapping)
        for value in mapping.values():
            assert type(value) is tuple
        with pytest.raises(TypeError):
            mapping["x"] = ()


# --- Task 18: public exports, convenience API, and configuration logging ---


EXPECTED_EXPORTS = {
    "CaptureCompleteness", "FrameAssociation", "GraphBinding", "PathMatchBasis",
    "RuntimeArtifact", "RuntimeArtifactFormat", "RuntimeArtifactRecord",
    "RuntimeArtifactStatus", "RuntimeEvent", "RuntimeEventKind",
    "RuntimeEvidenceConfig", "RuntimeEvidenceConfigurationError",
    "RuntimeEvidenceError", "RuntimeEvidenceGraphError",
    "RuntimeEvidenceIngestionError", "RuntimeEvidenceIngestor",
    "RuntimeEvidenceInventory", "RuntimeEvidenceInvariantError",
    "RuntimeEvidenceIssue", "RuntimeEvidenceLimitError",
    "RuntimeEvidenceRequest", "RuntimeEvidenceValidationError",
    "RuntimeFrameLanguage", "RuntimeIncompleteReason", "RuntimeIssueCode",
    "RuntimeStackFrame", "RuntimeTag", "ingest_runtime_evidence",
}


def test_public_exports_are_exact_and_hide_internals():
    assert set(runtime_evidence.__all__) == EXPECTED_EXPORTS
    assert len(runtime_evidence.__all__) == 28
    assert len(set(runtime_evidence.__all__)) == 28
    for name in EXPECTED_EXPORTS:
        assert getattr(runtime_evidence, name)
    for name in (
        "parse_text_log", "parse_json_events", "parse_stack_source",
        "normalize_source_reference", "build_inventory_indexes",
        "build_association_index", "AssociationIndex",
    ):
        assert not hasattr(runtime_evidence, name)


def test_convenience_api_forwards_defaults_and_explicit_inputs(monkeypatch):
    seen = []

    class FakeIngestor:
        def __init__(self, config=None):
            seen.append(("init", config))

        def ingest(self, request, graph=None):
            seen.append(("ingest", request, graph))
            return "RESULT"

    monkeypatch.setattr(
        runtime_evidence, "RuntimeEvidenceIngestor", FakeIngestor)
    request = _request()
    assert runtime_evidence.ingest_runtime_evidence(request) == "RESULT"
    assert seen == [("init", None), ("ingest", request, None)]

    seen.clear()
    config = RuntimeEvidenceConfig(max_events=1)
    graph = object()
    assert runtime_evidence.ingest_runtime_evidence(
        request, graph=graph, config=config) == "RESULT"
    assert seen == [("init", config), ("ingest", request, graph)]


def test_convenience_api_uses_a_fresh_instance_on_every_call(monkeypatch):
    created = []

    class FakeIngestor:
        def __init__(self, config=None):
            self.calls = 0
            created.append(self)

        def ingest(self, request, graph=None):
            self.calls += 1
            return ("RESULT", len(created), self.calls)

    monkeypatch.setattr(
        runtime_evidence, "RuntimeEvidenceIngestor", FakeIngestor)
    first = runtime_evidence.ingest_runtime_evidence(_request())
    second = runtime_evidence.ingest_runtime_evidence(_request())
    assert first == ("RESULT", 1, 1)
    assert second == ("RESULT", 2, 1)
    assert len(created) == 2
    assert created[0] is not created[1]


def test_convenience_api_propagates_the_original_failure(monkeypatch):
    failure = RuntimeEvidenceValidationError("SENTINEL-VALIDATION")

    class FakeIngestor:
        def __init__(self, config=None):
            pass

        def ingest(self, request, graph=None):
            raise failure

    monkeypatch.setattr(
        runtime_evidence, "RuntimeEvidenceIngestor", FakeIngestor)
    with pytest.raises(RuntimeEvidenceValidationError) as caught:
        runtime_evidence.ingest_runtime_evidence(_request())
    assert caught.value is failure


def test_constructor_rejects_non_exact_config_with_one_log(caplog):
    logger_name = "backend.runtime_evidence.ingestor"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        with pytest.raises(RuntimeEvidenceConfigurationError):
            runtime_evidence.RuntimeEvidenceIngestor(object())
    assert [(record.name, record.getMessage())
            for record in caplog.records] == [
        (logger_name, "runtime evidence configuration failed")]
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert record.args == ()
    assert record.exc_info is None
    assert record.stack_info is None


def test_convenience_config_rejection_logs_once_and_not_twice(caplog):
    logger_name = "backend.runtime_evidence.ingestor"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        with pytest.raises(RuntimeEvidenceConfigurationError):
            runtime_evidence.ingest_runtime_evidence(
                _request(), config=object())
    assert [(record.name, record.getMessage())
            for record in caplog.records] == [
        (logger_name, "runtime evidence configuration failed")]


def test_separate_instances_keep_distinct_effective_configs():
    request = _request(artifacts=(_artifact(content=b"one\ntwo\n"),))
    limited = runtime_evidence.RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(max_events=1)).ingest(request)
    full = runtime_evidence.RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(max_events=2)).ingest(request)
    assert limited.total_events == 1
    assert limited.events_truncated is True
    assert [event.message for event in limited.events] == ["one"]
    assert full.total_events == 2
    assert full.events_truncated is False
    assert [event.message for event in full.events] == ["one", "two"]


def test_convenience_results_keep_existing_redacted_representations():
    sentinel = "CP18-PRIVACY"
    inventory = runtime_evidence.ingest_runtime_evidence(_request(
        artifacts=(
            _artifact(
                source_name=f"{sentinel}-SOURCE.log",
                content=(sentinel + "-MESSAGE\n").encode(),
            ),
            _artifact(
                source_name=f"{sentinel}-STACK.log",
                format=RuntimeArtifactFormat.STACK_TRACE_TEXT,
                content=(
                    f'  File "pkg/{sentinel}-PATH.py", line 1, '
                    f"in {sentinel}-FUNCTION\n"
                ).encode(),
                declared_language=RuntimeFrameLanguage.PYTHON,
            ),
        ),
        repository_namespace=f"{sentinel}-NAMESPACE",
        declared_revision=f"{sentinel}-REVISION",
    ))
    assert inventory.frames
    values = (
        inventory,
        *inventory.artifacts,
        *inventory.events,
        *inventory.frames,
        *inventory.issues,
    )
    for value in values:
        text = repr(value) + str(value)
        assert sentinel not in text, type(value).__name__
