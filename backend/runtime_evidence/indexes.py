"""Internal immutable indexes and cross-model validation."""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType

from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceInvariantError,
)
from backend.runtime_evidence.models import (
    CaptureCompleteness,
    FrameAssociation,
    GraphBinding,
    RuntimeArtifactRecord,
    RuntimeArtifactStatus,
    RuntimeEvent,
    RuntimeEvidenceInventory,
    RuntimeEvidenceIssue,
    RuntimeIncompleteReason,
    RuntimeIssueCode,
    RuntimeStackFrame,
    RuntimeTag,
    _read_record_fields,
    _validate_stack_frame_record,
)
from backend.runtime_evidence.validation import (
    require_exact_bool,
    require_nonnegative_int,
    require_scalar_text,
)

_INVENTORY_INDEX_FIELDS = (
    "events_by_artifact",
    "events_by_trace_id",
    "events_by_fingerprint",
    "frames_by_file",
    "frames_by_node",
)

_INDEX_ELEMENT_TYPES = {
    "events_by_artifact": RuntimeEvent,
    "events_by_trace_id": RuntimeEvent,
    "events_by_fingerprint": RuntimeEvent,
    "frames_by_file": RuntimeStackFrame,
    "frames_by_node": RuntimeStackFrame,
}

_ARTIFACT_FIELD_NAMES = tuple(
    field.name for field in fields(RuntimeArtifactRecord)
)
_EVENT_FIELD_NAMES = tuple(field.name for field in fields(RuntimeEvent))
_FRAME_FIELD_NAMES = tuple(field.name for field in fields(RuntimeStackFrame))
_ISSUE_FIELD_NAMES = tuple(
    field.name for field in fields(RuntimeEvidenceIssue)
)
_TAG_FIELD_NAMES = tuple(field.name for field in fields(RuntimeTag))
_INVENTORY_FIELD_NAMES = tuple(
    field.name for field in fields(RuntimeEvidenceInventory)
)


def _require_exact_enum(
    value: object,
    enum_type: type,
    field_name: str,
) -> None:
    if type(value) is not enum_type:
        raise RuntimeEvidenceInvariantError(
            f"{field_name} must be an exact {enum_type.__name__}"
        )


def _require_exact_tuple(value: object, field_name: str) -> tuple:
    if type(value) is not tuple:
        raise RuntimeEvidenceInvariantError(
            f"{field_name} must be an exact tuple"
        )
    return value


def _snapshot_mapping(
    value: object,
    field_name: str,
    element_type: type,
) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise RuntimeEvidenceInvariantError(
            f"{field_name} must be a mapping"
        )
    fresh: dict[str, tuple] = {}
    previous_key: str | None = None
    for key, item in value.items():
        if type(key) is not str:
            raise RuntimeEvidenceInvariantError(
                f"{field_name} keys must be strings"
            )
        require_scalar_text(
            key, field_name, RuntimeEvidenceInvariantError
        )
        if previous_key is not None and previous_key >= key:
            raise RuntimeEvidenceInvariantError(
                f"{field_name} keys must be sorted and unique"
            )
        previous_key = key
        if type(item) is not tuple:
            raise RuntimeEvidenceInvariantError(
                f"{field_name} values must be exact tuples"
            )
        for record in item:
            if type(record) is not element_type:
                raise RuntimeEvidenceInvariantError(
                    f"{field_name} values must contain only exact "
                    f"{element_type.__name__} records"
                )
        fresh[key] = item
    return MappingProxyType(fresh)


@dataclass(frozen=True, slots=True)
class InventoryIndexes:
    events_by_artifact: Mapping[str, tuple[RuntimeEvent, ...]]
    events_by_trace_id: Mapping[str, tuple[RuntimeEvent, ...]]
    events_by_fingerprint: Mapping[str, tuple[RuntimeEvent, ...]]
    frames_by_file: Mapping[str, tuple[RuntimeStackFrame, ...]]
    frames_by_node: Mapping[str, tuple[RuntimeStackFrame, ...]]

    def __post_init__(self) -> None:
        for name in _INVENTORY_INDEX_FIELDS:
            object.__setattr__(
                self,
                name,
                _snapshot_mapping(
                    getattr(self, name),
                    name,
                    _INDEX_ELEMENT_TYPES[name],
                ),
            )


def _freeze_index(values: dict[str, list]) -> dict[str, tuple]:
    return {
        key: tuple(values[key])
        for key in sorted(values)
    }


def build_inventory_indexes(
    artifacts: tuple[RuntimeArtifactRecord, ...],
    events: tuple[RuntimeEvent, ...],
    frames: tuple[RuntimeStackFrame, ...],
) -> InventoryIndexes:
    events_by_artifact: dict[str, list[RuntimeEvent]] = {
        artifact.artifact_id: [] for artifact in artifacts
    }
    events_by_trace_id: dict[str, list[RuntimeEvent]] = {}
    events_by_fingerprint: dict[str, list[RuntimeEvent]] = {}
    for event in events:
        events_by_artifact.setdefault(event.artifact_id, []).append(event)
        if event.trace_id is not None:
            events_by_trace_id.setdefault(event.trace_id, []).append(event)
        events_by_fingerprint.setdefault(
            event.event_fingerprint, []
        ).append(event)

    frames_by_file: dict[str, list[RuntimeStackFrame]] = {}
    frames_by_node: dict[str, list[RuntimeStackFrame]] = {}
    for frame in frames:
        if frame.relative_path is not None:
            frames_by_file.setdefault(frame.relative_path, []).append(frame)
        if (
            frame.association
            in (FrameAssociation.FILE, FrameAssociation.SYMBOL)
            and frame.primary_node_id is not None
        ):
            frames_by_node.setdefault(
                frame.primary_node_id, []
            ).append(frame)

    return InventoryIndexes(
        events_by_artifact=_freeze_index(events_by_artifact),
        events_by_trace_id=_freeze_index(events_by_trace_id),
        events_by_fingerprint=_freeze_index(events_by_fingerprint),
        frames_by_file=_freeze_index(frames_by_file),
        frames_by_node=_freeze_index(frames_by_node),
    )


def _revalidate_record(
    record: object,
    expected_type: type,
    label: str,
    field_names: tuple[str, ...],
) -> None:
    if type(record) is not expected_type:
        raise RuntimeEvidenceInvariantError(
            f"{label} must be an exact {expected_type.__name__}"
        )
    _read_record_fields(record, field_names, label)
    try:
        record.__post_init__()
    except AttributeError:
        raise RuntimeEvidenceInvariantError(
            f"{label} is incomplete"
        ) from None


def _revalidate_event(event: RuntimeEvent) -> None:
    _revalidate_record(
        event, RuntimeEvent, "RuntimeEvent", _EVENT_FIELD_NAMES
    )
    tags = event.tags
    for tag in tags:
        _revalidate_record(tag, RuntimeTag, "RuntimeTag", _TAG_FIELD_NAMES)
    for frame in event.frames:
        if type(frame) is not RuntimeStackFrame:
            raise RuntimeEvidenceInvariantError(
                "RuntimeEvent frames must contain only exact "
                "RuntimeStackFrame instances"
            )
        _validate_stack_frame_record(frame)
    try:
        event.__post_init__()
    except AttributeError:
        raise RuntimeEvidenceInvariantError(
            "RuntimeEvent is incomplete"
        ) from None


def _revalidate_records(inventory: RuntimeEvidenceInventory) -> None:
    for artifact in inventory.artifacts:
        _revalidate_record(
            artifact,
            RuntimeArtifactRecord,
            "RuntimeArtifactRecord",
            _ARTIFACT_FIELD_NAMES,
        )
    for frame in inventory.frames:
        if type(frame) is not RuntimeStackFrame:
            raise RuntimeEvidenceInvariantError(
                "frames must contain only exact RuntimeStackFrame instances"
            )
        _validate_stack_frame_record(frame)
    for event in inventory.events:
        _revalidate_event(event)
    for issue in inventory.issues:
        _revalidate_record(
            issue,
            RuntimeEvidenceIssue,
            "RuntimeEvidenceIssue",
            _ISSUE_FIELD_NAMES,
        )


def _validate_top_level(inventory: RuntimeEvidenceInventory) -> None:
    require_scalar_text(
        inventory.repository_namespace,
        "repository_namespace",
        RuntimeEvidenceInvariantError,
        allow_empty=False,
    )
    for name in ("declared_revision", "declared_captured_at"):
        value = getattr(inventory, name)
        if value is not None:
            require_scalar_text(
                value, name, RuntimeEvidenceInvariantError
            )
    _require_exact_enum(
        inventory.declared_capture_completeness,
        CaptureCompleteness,
        "declared_capture_completeness",
    )
    _require_exact_enum(
        inventory.graph_binding, GraphBinding, "graph_binding"
    )
    for name in (
        "event_text_retained",
        "text_truncated",
        "events_truncated",
        "frames_truncated",
        "issues_truncated",
        "ingestion_complete",
    ):
        require_exact_bool(
            getattr(inventory, name),
            name,
            RuntimeEvidenceInvariantError,
        )
    for name in (
        "submitted_artifacts",
        "duplicate_artifacts",
        "total_input_bytes",
        "total_events",
        "total_frames",
    ):
        require_nonnegative_int(
            getattr(inventory, name),
            name,
            RuntimeEvidenceInvariantError,
        )
    for name in (
        "artifacts",
        "events",
        "frames",
        "issues",
        "incomplete_reasons",
    ):
        _require_exact_tuple(getattr(inventory, name), name)


def _validate_artifact_order(
    artifacts: tuple[RuntimeArtifactRecord, ...],
) -> dict[str, int]:
    positions: dict[str, int] = {}
    seen_source_names: set[str] = set()
    previous_key: tuple[str, str, str] | None = None
    for position, artifact in enumerate(artifacts):
        if artifact.artifact_id in positions:
            raise RuntimeEvidenceInvariantError(
                "artifact_id values must be unique"
            )
        positions[artifact.artifact_id] = position
        primary_source_name = artifact.source_names[0]
        key = (
            primary_source_name.casefold(),
            primary_source_name,
            artifact.artifact_id,
        )
        if previous_key is not None and key <= previous_key:
            raise RuntimeEvidenceInvariantError(
                "artifacts must be strictly ordered by primary source "
                "name and artifact_id"
            )
        previous_key = key
        for source_name in artifact.source_names:
            if source_name in seen_source_names:
                raise RuntimeEvidenceInvariantError(
                    "source names must be unique across artifacts"
                )
            seen_source_names.add(source_name)
    return positions


def _validate_event_order(
    artifacts: tuple[RuntimeArtifactRecord, ...],
    events: tuple[RuntimeEvent, ...],
    artifact_positions: dict[str, int],
) -> None:
    seen_event_ids: set[str] = set()
    next_sequence: dict[str, int] = {}
    previous_key: tuple[int, int] | None = None
    for event in events:
        if event.event_id in seen_event_ids:
            raise RuntimeEvidenceInvariantError(
                "event_id values must be unique"
            )
        seen_event_ids.add(event.event_id)
        artifact_position = artifact_positions.get(event.artifact_id)
        if artifact_position is None:
            raise RuntimeEvidenceInvariantError(
                "event artifact_id must reference an artifact"
            )
        key = (artifact_position, event.sequence)
        if previous_key is not None and key <= previous_key:
            raise RuntimeEvidenceInvariantError(
                "events must be ordered by artifact order and sequence"
            )
        previous_key = key
        expected_sequence = next_sequence.get(event.artifact_id, 0)
        if event.sequence != expected_sequence:
            raise RuntimeEvidenceInvariantError(
                "event sequences must be contiguous from zero within "
                "an artifact"
            )
        next_sequence[event.artifact_id] = expected_sequence + 1


def _validate_frames(
    inventory: RuntimeEvidenceInventory,
    events: tuple[RuntimeEvent, ...],
) -> None:
    flattened: list[RuntimeStackFrame] = []
    seen_frame_ids: set[str] = set()
    for event in events:
        for position, frame in enumerate(event.frames):
            if frame.event_id != event.event_id:
                raise RuntimeEvidenceInvariantError(
                    "frame event_id must equal the containing event_id"
                )
            if frame.ordinal != position:
                raise RuntimeEvidenceInvariantError(
                    "frame ordinals must equal their positions"
                )
            if frame.frame_id in seen_frame_ids:
                raise RuntimeEvidenceInvariantError(
                    "frame_id values must be unique"
                )
            seen_frame_ids.add(frame.frame_id)
            flattened.append(frame)
    if len(inventory.frames) != len(flattened) or any(
        actual != expected
        for actual, expected in zip(inventory.frames, flattened)
    ):
        raise RuntimeEvidenceInvariantError(
            "frames must equal the event-order/ordinal flattening"
        )


def _validate_per_artifact_counts(
    artifacts: tuple[RuntimeArtifactRecord, ...],
    events: tuple[RuntimeEvent, ...],
    issues: tuple[RuntimeEvidenceIssue, ...],
) -> None:
    event_counts: dict[str, int] = {}
    frame_counts: dict[str, int] = {}
    for event in events:
        event_counts[event.artifact_id] = (
            event_counts.get(event.artifact_id, 0) + 1
        )
        frame_counts[event.artifact_id] = (
            frame_counts.get(event.artifact_id, 0) + len(event.frames)
        )
    issue_counts: dict[str, int] = {}
    for issue in issues:
        issue_counts[issue.artifact_id] = (
            issue_counts.get(issue.artifact_id, 0) + 1
        )
    for artifact in artifacts:
        if artifact.event_count != event_counts.get(
            artifact.artifact_id, 0
        ):
            raise RuntimeEvidenceInvariantError(
                "artifact event_count must match its events"
            )
        if artifact.frame_count != frame_counts.get(
            artifact.artifact_id, 0
        ):
            raise RuntimeEvidenceInvariantError(
                "artifact frame_count must match its frames"
            )
        if artifact.issue_count != issue_counts.get(
            artifact.artifact_id, 0
        ):
            raise RuntimeEvidenceInvariantError(
                "artifact issue_count must match its issues"
            )


def _issue_order_key(
    issue: RuntimeEvidenceIssue,
    artifact_positions: dict[str, int],
) -> tuple:
    return (
        artifact_positions[issue.artifact_id],
        0 if issue.event_sequence is not None else 1,
        issue.event_sequence if issue.event_sequence is not None else 0,
        0 if issue.source_line is not None else 1,
        issue.source_line if issue.source_line is not None else 0,
        issue.code.value,
        issue.message,
    )


def _validate_issues(
    inventory: RuntimeEvidenceInventory,
    artifact_positions: dict[str, int],
) -> None:
    seen_keys: set[tuple] = set()
    previous_key: tuple | None = None
    for issue in inventory.issues:
        if issue.artifact_id not in artifact_positions:
            raise RuntimeEvidenceInvariantError(
                "issue artifact_id must reference an artifact"
            )
        deduplication_key = (
            issue.code,
            issue.artifact_id,
            issue.event_sequence,
            issue.source_line,
        )
        if deduplication_key in seen_keys:
            raise RuntimeEvidenceInvariantError(
                "issues must be unique by code, artifact_id, "
                "event_sequence, and source_line"
            )
        seen_keys.add(deduplication_key)
        key = _issue_order_key(issue, artifact_positions)
        if previous_key is not None and key <= previous_key:
            raise RuntimeEvidenceInvariantError(
                "issues must follow canonical artifact, event sequence, "
                "source line, code, and message order"
            )
        previous_key = key
    sentinel_count = sum(
        1
        for issue in inventory.issues
        if issue.code is RuntimeIssueCode.ISSUE_LIMIT_REACHED
    )
    expected_sentinel_count = 1 if inventory.issues_truncated else 0
    if sentinel_count != expected_sentinel_count:
        raise RuntimeEvidenceInvariantError(
            "ISSUE_LIMIT_REACHED must appear exactly when "
            "issues_truncated is true"
        )


def _expected_incomplete_reasons(
    inventory: RuntimeEvidenceInventory,
) -> tuple[RuntimeIncompleteReason, ...]:
    reasons: set[RuntimeIncompleteReason] = set()
    for artifact in inventory.artifacts:
        if artifact.status is RuntimeArtifactStatus.FAILED:
            reasons.add(RuntimeIncompleteReason.ARTIFACT_FAILED)
        elif artifact.status is RuntimeArtifactStatus.PARTIAL:
            reasons.add(RuntimeIncompleteReason.ARTIFACT_PARTIAL)
        elif artifact.status is RuntimeArtifactStatus.SKIPPED:
            reasons.add(RuntimeIncompleteReason.ARTIFACT_SKIPPED)
    if inventory.events_truncated:
        reasons.add(RuntimeIncompleteReason.EVENT_LIMIT_REACHED)
    if inventory.frames_truncated:
        reasons.add(RuntimeIncompleteReason.FRAME_LIMIT_REACHED)
    if inventory.issues_truncated:
        reasons.add(RuntimeIncompleteReason.ISSUE_LIMIT_REACHED)
    if inventory.text_truncated:
        reasons.add(RuntimeIncompleteReason.TEXT_TRUNCATED)
    return tuple(sorted(reasons, key=lambda reason: reason.value))


def _validate_incomplete_reasons(
    inventory: RuntimeEvidenceInventory,
) -> None:
    previous_value: str | None = None
    for reason in inventory.incomplete_reasons:
        if type(reason) is not RuntimeIncompleteReason:
            raise RuntimeEvidenceInvariantError(
                "incomplete_reasons must contain only exact "
                "RuntimeIncompleteReason values"
            )
        if previous_value is not None and previous_value >= reason.value:
            raise RuntimeEvidenceInvariantError(
                "incomplete_reasons must be sorted and unique"
            )
        previous_value = reason.value
    expected = _expected_incomplete_reasons(inventory)
    if inventory.incomplete_reasons != expected:
        raise RuntimeEvidenceInvariantError(
            "incomplete_reasons must be derived exactly from artifact "
            "statuses and truncation flags"
        )
    if inventory.ingestion_complete != (expected == ()):
        raise RuntimeEvidenceInvariantError(
            "ingestion_complete must equal (incomplete_reasons == ())"
        )


def _validate_truncation_flags(
    inventory: RuntimeEvidenceInventory,
) -> None:
    events_by_artifact: dict[str, list[RuntimeEvent]] = {}
    for event in inventory.events:
        events_by_artifact.setdefault(
            event.artifact_id, []
        ).append(event)
    for artifact in inventory.artifacts:
        artifact_events = events_by_artifact.get(
            artifact.artifact_id, []
        )
        artifact_text_truncated = any(
            event.text_truncated for event in artifact_events
        )
        if artifact.text_truncated != artifact_text_truncated:
            raise RuntimeEvidenceInvariantError(
                "artifact text_truncated must match its events"
            )
        artifact_frames_truncated = any(
            event.frames_truncated for event in artifact_events
        )
        if artifact.frames_truncated != artifact_frames_truncated:
            raise RuntimeEvidenceInvariantError(
                "artifact frames_truncated must match its events"
            )
    any_event_text_truncated = any(
        event.text_truncated for event in inventory.events
    )
    any_artifact_text_truncated = any(
        artifact.text_truncated for artifact in inventory.artifacts
    )
    if inventory.text_truncated != (
        any_event_text_truncated or any_artifact_text_truncated
    ):
        raise RuntimeEvidenceInvariantError(
            "inventory text_truncated must match affected event and "
            "artifact flags"
        )
    any_event_frames_truncated = any(
        event.frames_truncated for event in inventory.events
    )
    any_artifact_frames_truncated = any(
        artifact.frames_truncated for artifact in inventory.artifacts
    )
    if inventory.frames_truncated != (
        any_event_frames_truncated or any_artifact_frames_truncated
    ):
        raise RuntimeEvidenceInvariantError(
            "inventory frames_truncated must match affected event and "
            "artifact flags"
        )


def _validate_counters(inventory: RuntimeEvidenceInventory) -> None:
    if inventory.submitted_artifacts != (
        len(inventory.artifacts) + inventory.duplicate_artifacts
    ):
        raise RuntimeEvidenceInvariantError(
            "submitted_artifacts must equal the artifact records plus "
            "duplicate_artifacts"
        )
    if inventory.total_input_bytes != sum(
        artifact.size_bytes for artifact in inventory.artifacts
    ):
        raise RuntimeEvidenceInvariantError(
            "total_input_bytes must equal the sum of artifact sizes"
        )
    expected_events = len(inventory.events)
    if inventory.total_events != expected_events or inventory.total_events != sum(
        artifact.event_count for artifact in inventory.artifacts
    ):
        raise RuntimeEvidenceInvariantError(
            "total_events must equal the event records and artifact counts"
        )
    expected_frames = len(inventory.frames)
    if (
        inventory.total_frames != expected_frames
        or inventory.total_frames
        != sum(artifact.frame_count for artifact in inventory.artifacts)
        or inventory.total_frames
        != sum(len(event.frames) for event in inventory.events)
    ):
        raise RuntimeEvidenceInvariantError(
            "total_frames must equal the frame records and counts"
        )
    if sum(artifact.issue_count for artifact in inventory.artifacts) != len(
        inventory.issues
    ):
        raise RuntimeEvidenceInvariantError(
            "artifact issue_count values must sum to the issue records"
        )


def _validate_indexes(inventory: RuntimeEvidenceInventory) -> None:
    canonical = build_inventory_indexes(
        inventory.artifacts, inventory.events, inventory.frames
    )
    for name in _INVENTORY_INDEX_FIELDS:
        snapshot = _snapshot_mapping(
            getattr(inventory, name),
            name,
            _INDEX_ELEMENT_TYPES[name],
        )
        for records in snapshot.values():
            for record in records:
                if type(record) is RuntimeEvent:
                    _revalidate_event(record)
                else:
                    _validate_stack_frame_record(record)
        if dict(snapshot) != dict(getattr(canonical, name)):
            raise RuntimeEvidenceInvariantError(
                f"{name} must exactly match a filter of authoritative "
                "tuples"
            )


def validate_inventory(inventory: object) -> None:
    if type(inventory) is not RuntimeEvidenceInventory:
        raise RuntimeEvidenceInvariantError(
            "inventory must be an exact RuntimeEvidenceInventory"
        )
    _read_record_fields(
        inventory, _INVENTORY_FIELD_NAMES, "RuntimeEvidenceInventory"
    )
    _validate_top_level(inventory)
    _revalidate_records(inventory)
    artifact_positions = _validate_artifact_order(inventory.artifacts)
    _validate_event_order(
        inventory.artifacts, inventory.events, artifact_positions
    )
    _validate_frames(inventory, inventory.events)
    _validate_per_artifact_counts(
        inventory.artifacts, inventory.events, inventory.issues
    )
    _validate_issues(inventory, artifact_positions)
    _validate_incomplete_reasons(inventory)
    _validate_truncation_flags(inventory)
    _validate_counters(inventory)
    _validate_indexes(inventory)
