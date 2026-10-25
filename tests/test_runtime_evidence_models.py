from dataclasses import FrozenInstanceError, fields

import pytest

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError, RuntimeEvidenceError,
    RuntimeEvidenceGraphError, RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError, RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
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
