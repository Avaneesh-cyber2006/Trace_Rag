"""Runtime evidence ingestion pipeline.

The ingestor is the only component that assembles public records. It performs
request-envelope checks before hashing, deduplicates exact artifacts, dispatches
the declared format adapters, associates frames with an optional graph, and
returns one immutable inventory.
"""

import hashlib
import logging
from dataclasses import dataclass, replace

from backend.code_graph import CodeGraphInventory
from backend.runtime_evidence.association import (
    AssociationIndex,
    associate_frames,
    build_association_index,
)
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import (
    ArtifactParseResult,
    EventDraft,
    IssueDraft,
)
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError,
    RuntimeEvidenceGraphError,
    RuntimeEvidenceIngestionError,
    RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.identities import (
    artifact_id,
    event_fingerprint,
    event_id,
)
from backend.runtime_evidence.indexes import build_inventory_indexes
from backend.runtime_evidence.json_adapter import parse_json_events
from backend.runtime_evidence.models import (
    GraphBinding,
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeArtifactRecord,
    RuntimeArtifactStatus,
    RuntimeEvent,
    RuntimeEvidenceInventory,
    RuntimeEvidenceIssue,
    RuntimeEvidenceRequest,
    RuntimeFrameLanguage,
    RuntimeIncompleteReason,
    RuntimeIssueCode,
    issue_message,
)
from backend.runtime_evidence.stack_parser import parse_stack_trace_text
from backend.runtime_evidence.text_adapter import parse_text_log
from backend.runtime_evidence.validation import require_scalar_text

logger = logging.getLogger(__name__)

_DEDUPLICATION_KEY = tuple[
    RuntimeArtifactFormat, RuntimeFrameLanguage | None, str
]


@dataclass(frozen=True, slots=True)
class _PreparedArtifact:
    """Canonical deduplicated work item for one exact artifact."""

    artifact_id: str
    source_names: tuple[str, ...]
    artifact: RuntimeArtifact
    content_sha256: str
    duplicate_count: int


@dataclass(slots=True)
class _ArtifactGroup:
    """Mutable accumulator for one exact (format, language, digest) group."""

    artifact: RuntimeArtifact
    content_sha256: str
    source_names: set[str]
    submissions: int


@dataclass(slots=True)
class _BudgetState:
    """Invocation-local counters and flags for the three record budgets."""

    events_retained: int = 0
    frames_retained: int = 0
    ordinary_issues: int = 0
    events_truncated: bool = False
    frames_truncated: bool = False
    issues_truncated: bool = False
    event_limit_issue_emitted: bool = False


def _parse_artifact(
    artifact: RuntimeArtifact,
    config: RuntimeEvidenceConfig,
    max_new_events: int,
) -> ArtifactParseResult:
    """Dispatch one artifact to exactly one declared format adapter."""
    if artifact.format is RuntimeArtifactFormat.TEXT_LOG:
        return parse_text_log(
            artifact.content, config, max_new_events
        )
    if artifact.format is RuntimeArtifactFormat.JSON_EVENTS:
        return parse_json_events(
            artifact.content,
            config,
            artifact.declared_language,
            max_new_events,
        )
    if artifact.format is RuntimeArtifactFormat.STACK_TRACE_TEXT:
        return parse_stack_trace_text(
            artifact.content,
            config,
            artifact.declared_language,
            max_new_events,
        )
    raise RuntimeEvidenceValidationError("unsupported artifact format")


def _validate_request(
    request: object,
    config: RuntimeEvidenceConfig,
) -> RuntimeEvidenceRequest:
    """Revalidate caller objects without trusting their constructors."""
    if type(request) is not RuntimeEvidenceRequest:
        raise RuntimeEvidenceValidationError(
            "request must be an exact RuntimeEvidenceRequest"
        )
    request.__post_init__()
    require_scalar_text(
        request.repository_namespace,
        "repository_namespace",
        RuntimeEvidenceValidationError,
        allow_empty=False,
        max_bytes=config.max_namespace_bytes,
    )
    for artifact in request.artifacts:
        artifact.__post_init__()
    return request


def _sorted_source_names(source_names: set[str]) -> tuple[str, ...]:
    return tuple(
        sorted(source_names, key=lambda name: (name.casefold(), name))
    )


def _deduplicate_artifacts(
    request: RuntimeEvidenceRequest,
) -> tuple[_ArtifactGroup, ...]:
    """Hash submissions once, group them, and detect conflicting names."""
    groups: dict[_DEDUPLICATION_KEY, _ArtifactGroup] = {}
    source_keys: dict[str, _DEDUPLICATION_KEY] = {}
    for artifact in request.artifacts:
        content_sha256 = hashlib.sha256(artifact.content).hexdigest()
        key = (
            artifact.format,
            artifact.declared_language,
            content_sha256,
        )
        previous_key = source_keys.get(artifact.source_name)
        if previous_key is not None and previous_key != key:
            raise RuntimeEvidenceValidationError(
                "source_name is used by conflicting artifacts"
            )
        source_keys[artifact.source_name] = key
        group = groups.get(key)
        if group is None:
            groups[key] = _ArtifactGroup(
                artifact,
                content_sha256,
                {artifact.source_name},
                1,
            )
        else:
            group.source_names.add(artifact.source_name)
            group.submissions += 1
    return tuple(groups.values())


def _prepare_artifacts(
    namespace: str,
    groups: tuple[_ArtifactGroup, ...],
) -> tuple[_PreparedArtifact, ...]:
    """Compute canonical artifact IDs and sort the deduplicated groups."""
    prepared = [
        _PreparedArtifact(
            artifact_id=artifact_id(
                namespace,
                group.artifact.format,
                group.artifact.declared_language,
                group.content_sha256,
            ),
            source_names=_sorted_source_names(group.source_names),
            artifact=group.artifact,
            content_sha256=group.content_sha256,
            duplicate_count=group.submissions - 1,
        )
        for group in groups
    ]
    prepared.sort(
        key=lambda item: (
            item.source_names[0].casefold(),
            item.source_names[0],
            item.artifact_id,
        )
    )
    return tuple(prepared)


def _project_metadata_only(
    result: ArtifactParseResult,
    config: RuntimeEvidenceConfig,
) -> ArtifactParseResult:
    """Apply the public metadata-only projection to parsed drafts.

    The text and stack adapters intentionally retain free text themselves.
    Task 16 owns the public-record projection because it assembles the only
    records downstream consumers see. Structural stack loss is preserved
    because it represents real ingestion truncation, not policy omission.
    """
    if config.retain_event_text:
        return result
    structural_text_truncation = frozenset(
        issue.event_sequence
        for issue in result.issues
        if issue.code is RuntimeIssueCode.STACK_TOO_LARGE
        and issue.event_sequence is not None
    )
    events = tuple(
        replace(
            draft,
            message=None,
            logger=None,
            operation=None,
            error_type=None,
            error_message=None,
            tags=(),
            frames=tuple(
                replace(frame, function_text=None)
                for frame in draft.frames
            ),
            text_truncated=(
                draft.text_truncated
                and draft.sequence in structural_text_truncation
            ),
        )
        for draft in result.events
    )
    issues = tuple(
        issue
        for issue in result.issues
        if not (
            issue.code is RuntimeIssueCode.TEXT_TRUNCATED
            and issue.event_sequence is not None
            and issue.event_sequence not in structural_text_truncation
        )
    )
    return replace(
        result,
        events=events,
        issues=issues,
        text_truncated=any(event.text_truncated for event in events),
        frames_truncated=any(event.frames_truncated for event in events),
    )


def _assemble_event(
    draft: EventDraft,
    prepared: _PreparedArtifact,
    namespace: str,
    config: RuntimeEvidenceConfig,
    graph_index: AssociationIndex | None,
    frame_capacity: int,
) -> tuple[RuntimeEvent, tuple[IssueDraft, ...], bool]:
    """Build one immutable event from a draft and its associated frames.

    Association runs on every adapter-retained draft frame. The global frame
    budget then keeps only the capacity-fitting prefix. The returned boolean
    reports whether that global prefix actually dropped frames.
    """
    identifier = event_id(namespace, prepared.artifact_id, draft.sequence)
    association = associate_frames(
        draft.frames,
        event_id=identifier,
        event_sequence=draft.sequence,
        source_line=draft.source_line,
        repository_namespace=namespace,
        config=config,
        graph_index=graph_index,
    )
    frames = association.frames[:frame_capacity]
    frame_budget_cut = len(association.frames) > frame_capacity
    fingerprint = event_fingerprint(
        repository_namespace=namespace,
        kind=draft.kind,
        timestamp_text=draft.timestamp_text,
        level=draft.level,
        message=draft.message,
        logger=draft.logger,
        operation=draft.operation,
        trace_id=draft.trace_id,
        span_id=draft.span_id,
        parent_span_id=draft.parent_span_id,
        duration_ms_text=draft.duration_ms_text,
        status=draft.status,
        error_type=draft.error_type,
        error_message=draft.error_message,
        tags=draft.tags,
        frames=frames,
    )
    event = RuntimeEvent(
        event_id=identifier,
        event_fingerprint=fingerprint,
        artifact_id=prepared.artifact_id,
        sequence=draft.sequence,
        source_line=draft.source_line,
        kind=draft.kind,
        timestamp_text=draft.timestamp_text,
        level=draft.level,
        message=draft.message,
        logger=draft.logger,
        operation=draft.operation,
        trace_id=draft.trace_id,
        span_id=draft.span_id,
        parent_span_id=draft.parent_span_id,
        duration_ms_text=draft.duration_ms_text,
        status=draft.status,
        error_type=draft.error_type,
        error_message=draft.error_message,
        tags=draft.tags,
        frames=frames,
        text_truncated=draft.text_truncated,
        frames_truncated=draft.frames_truncated or frame_budget_cut,
    )
    return event, association.issues, frame_budget_cut


def _project_issues(
    artifact_identifier: str,
    drafts: tuple[IssueDraft, ...],
) -> tuple[RuntimeEvidenceIssue, ...]:
    """Project issue drafts, keeping the first occurrence of each key."""
    seen: set[tuple] = set()
    issues: list[RuntimeEvidenceIssue] = []
    for draft in drafts:
        key = (
            draft.code,
            artifact_identifier,
            draft.event_sequence,
            draft.source_line,
        )
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            RuntimeEvidenceIssue(
                code=draft.code,
                message=issue_message(draft.code),
                artifact_id=artifact_identifier,
                event_sequence=draft.event_sequence,
                source_line=draft.source_line,
            )
        )
    return tuple(issues)


def _artifact_status(
    events: tuple[RuntimeEvent, ...],
    issues: tuple[RuntimeEvidenceIssue, ...],
    text_truncated: bool,
    frames_truncated: bool,
) -> RuntimeArtifactStatus:
    if not events:
        return (
            RuntimeArtifactStatus.COMPLETE
            if not issues
            else RuntimeArtifactStatus.FAILED
        )
    if issues or text_truncated or frames_truncated:
        return RuntimeArtifactStatus.PARTIAL
    return RuntimeArtifactStatus.COMPLETE


def _incomplete_reasons(
    artifacts: tuple[RuntimeArtifactRecord, ...],
    text_truncated: bool,
    events_truncated: bool,
    frames_truncated: bool,
    issues_truncated: bool,
) -> tuple[RuntimeIncompleteReason, ...]:
    reasons: set[RuntimeIncompleteReason] = set()
    for artifact in artifacts:
        if artifact.status is RuntimeArtifactStatus.FAILED:
            reasons.add(RuntimeIncompleteReason.ARTIFACT_FAILED)
        elif artifact.status is RuntimeArtifactStatus.PARTIAL:
            reasons.add(RuntimeIncompleteReason.ARTIFACT_PARTIAL)
        elif artifact.status is RuntimeArtifactStatus.SKIPPED:
            reasons.add(RuntimeIncompleteReason.ARTIFACT_SKIPPED)
    if events_truncated:
        reasons.add(RuntimeIncompleteReason.EVENT_LIMIT_REACHED)
    if frames_truncated:
        reasons.add(RuntimeIncompleteReason.FRAME_LIMIT_REACHED)
    if issues_truncated:
        reasons.add(RuntimeIncompleteReason.ISSUE_LIMIT_REACHED)
    if text_truncated:
        reasons.add(RuntimeIncompleteReason.TEXT_TRUNCATED)
    return tuple(sorted(reasons, key=lambda reason: reason.value))


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


def _apply_issue_budget(
    issues: tuple[RuntimeEvidenceIssue, ...],
    config: RuntimeEvidenceConfig,
    artifact_positions: dict[str, int],
    budget: _BudgetState,
) -> tuple[RuntimeEvidenceIssue, ...]:
    """Deduplicate, order, cap, and sentinel the final issue tuple."""
    seen: set[tuple] = set()
    unique: list[RuntimeEvidenceIssue] = []
    for issue in issues:
        key = (
            issue.code,
            issue.artifact_id,
            issue.event_sequence,
            issue.source_line,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    ordered = sorted(
        unique,
        key=lambda issue: _issue_order_key(issue, artifact_positions),
    )
    if len(ordered) <= config.max_issues:
        budget.ordinary_issues = len(ordered)
        return tuple(ordered)

    budget.issues_truncated = True
    budget.ordinary_issues = config.max_issues
    first_dropped = ordered[config.max_issues]
    sentinel = RuntimeEvidenceIssue(
        code=RuntimeIssueCode.ISSUE_LIMIT_REACHED,
        message=issue_message(RuntimeIssueCode.ISSUE_LIMIT_REACHED),
        artifact_id=first_dropped.artifact_id,
        event_sequence=first_dropped.event_sequence,
        source_line=first_dropped.source_line,
    )
    final = [*ordered[:config.max_issues], sentinel]
    final.sort(
        key=lambda issue: _issue_order_key(issue, artifact_positions)
    )
    return tuple(final)


class RuntimeEvidenceIngestor:
    """Assemble runtime evidence records from exact artifact bytes."""

    __slots__ = ("_config",)

    def __init__(
        self,
        config: RuntimeEvidenceConfig | None = None,
    ) -> None:
        try:
            if config is None:
                config = RuntimeEvidenceConfig()
            elif type(config) is not RuntimeEvidenceConfig:
                raise RuntimeEvidenceConfigurationError(
                    "config must be an exact RuntimeEvidenceConfig"
                )
        except RuntimeEvidenceConfigurationError:
            logger.error("runtime evidence configuration failed")
            raise
        self._config = config

    def ingest(
        self,
        request: RuntimeEvidenceRequest,
        graph: CodeGraphInventory | None = None,
    ) -> RuntimeEvidenceInventory:
        """Ingest one request and return an immutable inventory."""
        try:
            logger.info("runtime evidence ingestion started")
            inventory = self._ingest(request, graph)
            self._log_artifact_details(inventory)
            logger.info(
                "runtime evidence ingestion completed "
                "artifacts=%d events=%d frames=%d issues=%d",
                len(inventory.artifacts),
                len(inventory.events),
                len(inventory.frames),
                len(inventory.issues),
            )
            return inventory
        except RuntimeEvidenceValidationError:
            logger.error("runtime evidence validation failed")
            raise
        except RuntimeEvidenceLimitError:
            logger.error("runtime evidence limit failed")
            raise
        except RuntimeEvidenceGraphError:
            logger.error("runtime evidence graph failed")
            raise
        except Exception:
            logger.error("runtime evidence ingestion failed")
            raise RuntimeEvidenceIngestionError(
                "runtime evidence ingestion failed"
            ) from None

    @staticmethod
    def _log_artifact_details(
        inventory: RuntimeEvidenceInventory,
    ) -> None:
        codes_by_artifact: dict[str, list[str]] = {}
        for issue in inventory.issues:
            codes_by_artifact.setdefault(
                issue.artifact_id, []
            ).append(issue.code.value)
        for artifact in inventory.artifacts:
            codes = sorted(
                codes_by_artifact.get(artifact.artifact_id, ())
            )
            rendered_codes = ",".join(codes)
            logger.debug(
                "runtime evidence artifact artifact_id=%s format=%s "
                "status=%s events=%d frames=%d issues=%s",
                artifact.artifact_id,
                artifact.format.value,
                artifact.status.value,
                artifact.event_count,
                artifact.frame_count,
                rendered_codes,
            )
            degraded = (
                artifact.status
                in (
                    RuntimeArtifactStatus.PARTIAL,
                    RuntimeArtifactStatus.FAILED,
                    RuntimeArtifactStatus.SKIPPED,
                )
                or artifact.text_truncated
                or artifact.frames_truncated
                or RuntimeIssueCode.ISSUE_LIMIT_REACHED.value in codes
            )
            if degraded:
                logger.warning(
                    "runtime evidence artifact warning artifact_id=%s "
                    "status=%s issues=%s",
                    artifact.artifact_id,
                    artifact.status.value,
                    rendered_codes,
                )

    def _ingest(
        self,
        request: RuntimeEvidenceRequest,
        graph: CodeGraphInventory | None = None,
    ) -> RuntimeEvidenceInventory:
        """Assemble one inventory without logging or failure translation."""
        config = self._config
        request = _validate_request(request, config)
        namespace = request.repository_namespace

        submitted_artifacts = len(request.artifacts)
        if submitted_artifacts > config.max_artifacts:
            raise RuntimeEvidenceLimitError(
                "submitted artifact count exceeds the configured limit"
            )
        submitted_bytes = sum(
            len(artifact.content) for artifact in request.artifacts
        )
        if submitted_bytes > config.max_submitted_input_bytes:
            raise RuntimeEvidenceLimitError(
                "submitted input bytes exceed the configured limit"
            )

        prepared = _deduplicate_artifacts(request)
        unique_input_bytes = sum(
            len(group.artifact.content) for group in prepared
        )
        if unique_input_bytes > config.max_total_input_bytes:
            raise RuntimeEvidenceLimitError(
                "unique input bytes exceed the configured limit"
            )

        graph_index = build_association_index(
            graph,
            repository_namespace=namespace,
            max_namespace_bytes=config.max_namespace_bytes,
        )
        work_items = _prepare_artifacts(namespace, prepared)

        events: list[RuntimeEvent] = []
        issues: list[RuntimeEvidenceIssue] = []
        artifacts: list[RuntimeArtifactRecord] = []
        artifact_positions: dict[str, int] = {}
        budget = _BudgetState()
        frame_limit_issue_emitted = False

        for position, item in enumerate(work_items):
            artifact_positions[item.artifact_id] = position
            size_bytes = len(item.artifact.content)
            artifact_events: list[RuntimeEvent] = []
            issue_drafts: list[IssueDraft] = []
            remaining_events = config.max_events - budget.events_retained

            if size_bytes > config.max_artifact_bytes:
                status = RuntimeArtifactStatus.SKIPPED
                issue_drafts.append(
                    IssueDraft(
                        code=RuntimeIssueCode.ARTIFACT_TOO_LARGE,
                        event_sequence=None,
                        source_line=None,
                    )
                )
            elif remaining_events <= 0:
                status = RuntimeArtifactStatus.SKIPPED
                budget.events_truncated = True
                if not budget.event_limit_issue_emitted:
                    issue_drafts.append(
                        IssueDraft(
                            code=RuntimeIssueCode.EVENT_LIMIT_REACHED,
                            event_sequence=None,
                            source_line=None,
                        )
                    )
                    budget.event_limit_issue_emitted = True
            else:
                parse_result = _parse_artifact(
                    item.artifact, config, remaining_events
                )
                parse_result = _project_metadata_only(parse_result, config)
                retained_drafts = parse_result.events[:remaining_events]
                if (
                    parse_result.event_limit_hit
                    or len(parse_result.events) > remaining_events
                ):
                    budget.events_truncated = True
                    issue_drafts.append(
                        IssueDraft(
                            code=RuntimeIssueCode.EVENT_LIMIT_REACHED,
                            event_sequence=None,
                            source_line=None,
                        )
                    )
                    budget.event_limit_issue_emitted = True
                budget.events_retained += len(retained_drafts)
                issue_drafts.extend(parse_result.issues)
                for draft in retained_drafts:
                    remaining_frames = max(
                        0, config.max_total_frames - budget.frames_retained
                    )
                    (
                        event,
                        association_issues,
                        frame_budget_cut,
                    ) = _assemble_event(
                        draft,
                        item,
                        namespace,
                        config,
                        graph_index,
                        remaining_frames,
                    )
                    if frame_budget_cut:
                        budget.frames_truncated = True
                        if not frame_limit_issue_emitted:
                            issue_drafts.append(
                                IssueDraft(
                                    code=(
                                        RuntimeIssueCode.FRAME_LIMIT_REACHED
                                    ),
                                    event_sequence=event.sequence,
                                    source_line=event.source_line,
                                )
                            )
                            frame_limit_issue_emitted = True
                    budget.frames_retained += len(event.frames)
                    artifact_events.append(event)
                    issue_drafts.extend(association_issues)
                status = None

            artifact_issues = _project_issues(
                item.artifact_id, tuple(issue_drafts)
            )
            artifact_text_truncated = any(
                event.text_truncated for event in artifact_events
            )
            artifact_frames_truncated = any(
                event.frames_truncated for event in artifact_events
            )
            if status is None:
                status = _artifact_status(
                    tuple(artifact_events),
                    artifact_issues,
                    artifact_text_truncated,
                    artifact_frames_truncated,
                )

            artifacts.append(
                RuntimeArtifactRecord(
                    artifact_id=item.artifact_id,
                    source_names=item.source_names,
                    format=item.artifact.format,
                    declared_language=item.artifact.declared_language,
                    content_sha256=item.content_sha256,
                    size_bytes=size_bytes,
                    status=status,
                    event_count=len(artifact_events),
                    frame_count=sum(
                        len(event.frames) for event in artifact_events
                    ),
                    duplicate_count=item.duplicate_count,
                    text_truncated=artifact_text_truncated,
                    frames_truncated=artifact_frames_truncated,
                    issue_count=len(artifact_issues),
                )
            )
            events.extend(artifact_events)
            issues.extend(artifact_issues)

        event_records = tuple(events)
        frame_records = tuple(
            frame for event in event_records for frame in event.frames
        )
        issue_records = _apply_issue_budget(
            tuple(issues), config, artifact_positions, budget
        )
        events_by_artifact: dict[str, list[RuntimeEvent]] = {}
        for event in event_records:
            events_by_artifact.setdefault(event.artifact_id, []).append(event)
        issue_counts: dict[str, int] = {}
        for issue in issue_records:
            issue_counts[issue.artifact_id] = (
                issue_counts.get(issue.artifact_id, 0) + 1
            )
        artifact_records = tuple(
            replace(
                artifact,
                event_count=len(
                    events_by_artifact.get(artifact.artifact_id, ())
                ),
                frame_count=sum(
                    len(event.frames)
                    for event in events_by_artifact.get(
                        artifact.artifact_id, ()
                    )
                ),
                text_truncated=any(
                    event.text_truncated
                    for event in events_by_artifact.get(
                        artifact.artifact_id, ()
                    )
                ),
                frames_truncated=any(
                    event.frames_truncated
                    for event in events_by_artifact.get(
                        artifact.artifact_id, ()
                    )
                ),
                issue_count=issue_counts.get(artifact.artifact_id, 0),
            )
            for artifact in artifacts
        )

        text_truncated = any(
            event.text_truncated for event in event_records
        )
        frames_truncated = any(
            event.frames_truncated for event in event_records
        )
        reasons = _incomplete_reasons(
            artifact_records,
            text_truncated,
            budget.events_truncated,
            frames_truncated,
            budget.issues_truncated,
        )
        indexes = build_inventory_indexes(
            artifact_records, event_records, frame_records
        )
        return RuntimeEvidenceInventory(
            repository_namespace=namespace,
            declared_revision=request.declared_revision,
            declared_captured_at=request.declared_captured_at,
            declared_capture_completeness=(
                request.declared_capture_completeness
            ),
            graph_binding=(
                GraphBinding.NOT_PROVIDED
                if graph is None
                else GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
            ),
            event_text_retained=config.retain_event_text,
            artifacts=artifact_records,
            events=event_records,
            frames=frame_records,
            issues=issue_records,
            submitted_artifacts=submitted_artifacts,
            duplicate_artifacts=(
                submitted_artifacts - len(artifact_records)
            ),
            total_input_bytes=unique_input_bytes,
            total_events=len(event_records),
            total_frames=len(frame_records),
            text_truncated=text_truncated,
            events_truncated=budget.events_truncated,
            frames_truncated=frames_truncated,
            issues_truncated=budget.issues_truncated,
            ingestion_complete=not reasons,
            incomplete_reasons=reasons,
            events_by_artifact=indexes.events_by_artifact,
            events_by_trace_id=indexes.events_by_trace_id,
            events_by_fingerprint=indexes.events_by_fingerprint,
            frames_by_file=indexes.frames_by_file,
            frames_by_node=indexes.frames_by_node,
        )
