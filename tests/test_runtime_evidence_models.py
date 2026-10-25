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
    CaptureCompleteness, FrameAssociation, GraphBinding, PathMatchBasis,
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeArtifactStatus,
    RuntimeEventKind, RuntimeEvidenceRequest, RuntimeFrameLanguage,
    RuntimeIncompleteReason, RuntimeIssueCode, RuntimeTag,
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
