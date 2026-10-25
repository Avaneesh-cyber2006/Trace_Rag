"""Configuration for runtime evidence ingestion."""

from dataclasses import dataclass

from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError,
)
from backend.runtime_evidence.validation import (
    require_exact_bool,
    require_positive_int,
)

_INTEGER_FIELD_NAMES = (
    "max_artifacts",
    "max_namespace_bytes",
    "max_artifact_bytes",
    "max_submitted_input_bytes",
    "max_total_input_bytes",
    "max_events",
    "max_json_nodes",
    "max_json_number_bytes",
    "max_frames_per_event",
    "max_total_frames",
    "max_issues",
    "max_line_bytes",
    "max_source_reference_bytes",
    "max_message_bytes",
    "max_error_message_bytes",
    "max_stack_bytes",
    "max_frame_function_bytes",
    "max_tags",
    "max_tag_key_bytes",
    "max_tag_value_bytes",
)


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceConfig:
    """Structural bounds and privacy controls for one ingestion request."""

    max_artifacts: int = 64
    max_namespace_bytes: int = 256
    max_artifact_bytes: int = 8_388_608
    max_submitted_input_bytes: int = 67_108_864
    max_total_input_bytes: int = 67_108_864
    max_events: int = 50_000
    max_json_nodes: int = 1_000_000
    max_json_number_bytes: int = 1_024
    max_frames_per_event: int = 128
    max_total_frames: int = 200_000
    max_issues: int = 10_000
    max_line_bytes: int = 65_536
    max_source_reference_bytes: int = 1_024
    max_message_bytes: int = 8_192
    max_error_message_bytes: int = 8_192
    max_stack_bytes: int = 65_536
    max_frame_function_bytes: int = 1_024
    max_tags: int = 32
    max_tag_key_bytes: int = 128
    max_tag_value_bytes: int = 512
    retain_event_text: bool = True

    def __post_init__(self) -> None:
        """Validate every limit and cross-field relation."""
        for field_name in _INTEGER_FIELD_NAMES:
            require_positive_int(
                getattr(self, field_name),
                field_name,
                RuntimeEvidenceConfigurationError,
            )
        require_exact_bool(
            self.retain_event_text,
            "retain_event_text",
            RuntimeEvidenceConfigurationError,
        )
        if self.max_artifact_bytes > self.max_total_input_bytes:
            raise RuntimeEvidenceConfigurationError(
                "max_artifact_bytes must not exceed max_total_input_bytes"
            )
        if self.max_total_input_bytes > self.max_submitted_input_bytes:
            raise RuntimeEvidenceConfigurationError(
                "max_total_input_bytes must not exceed max_submitted_input_bytes"
            )
        if self.max_frames_per_event > self.max_total_frames:
            raise RuntimeEvidenceConfigurationError(
                "max_frames_per_event must not exceed max_total_frames"
            )
