"""Read-only Module 8 runtime clues for already source-verified Module 9 hits.

Stack paths and user-declared revisions are unverified *observations*.  Even a
Module 8 graph association is namespace-only, not a Module 5 source-revision
proof.  Thus these clues cannot change source ranking or prove causality.
Raw event messages, exceptions, tags, trace IDs and function text are never
copied into the result (the original Module 8 inventory may contain secrets).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.runtime_evidence.exceptions import RuntimeEvidenceInvariantError
from backend.runtime_evidence.indexes import validate_inventory
from backend.runtime_evidence.models import (
    CaptureCompleteness, FrameAssociation, GraphBinding, PathMatchBasis,
    RuntimeEventKind, RuntimeEvidenceInventory, RuntimeFrameLanguage,
    RuntimeIncompleteReason,
)

from .errors import InvalidHybridEvidence, InvalidRetrievalQuery
from .hybrid import HybridHit

_INVALID_RUNTIME = "Runtime evidence is invalid or inconsistent."
_INVALID_REQUEST = "Runtime retrieval request is invalid or exceeds its limit."
_MAX_HITS = 100
_MAX_FRAMES_PER_PATH = 20
_MAX_ARTIFACTS = 64
_MAX_EVENTS = 50_000
_MAX_FRAMES = 200_000
_MAX_ISSUES = 10_000
# Paths are accepted only if Module 8's normalized path is present and the
# association is not ambiguous/unmatched. These bases are *not* revision proof.
_PATH_BASES = frozenset((
    PathMatchBasis.EXACT_RELATIVE_PATH,
    PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
    PathMatchBasis.UNIQUE_PATH_SUFFIX,
    PathMatchBasis.UNIQUE_BASENAME,
))


@dataclass(frozen=True, slots=True)
class RuntimeFrameClue:
    """Sanitized reported frame; neither revision-bound nor causal proof."""

    artifact_id: str
    event_id: str
    frame_id: str
    event_kind: RuntimeEventKind
    language: RuntimeFrameLanguage
    line: int | None
    column: int | None
    path_match_basis: PathMatchBasis
    association: FrameAssociation


@dataclass(frozen=True, slots=True)
class RuntimePathContext:
    """Bounded frame clues for one exact, case-sensitive retrieved path."""

    relative_path: str
    frames: tuple[RuntimeFrameClue, ...] = field(repr=False)
    total_matching_frames: int = 0


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Module 8 capture metadata, limitations, and sanitized file clues."""

    paths: tuple[RuntimePathContext, ...] = field(repr=False)
    declared_revision: str | None = None
    declared_captured_at: str | None = None
    declared_capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN
    graph_binding: GraphBinding = GraphBinding.NOT_PROVIDED
    ingestion_complete: bool = False
    incomplete_reasons: tuple[RuntimeIncompleteReason, ...] = ()
    events_truncated: bool = False
    frames_truncated: bool = False
    source_revision_verified: bool = False
    causal_relationship_verified: bool = False


class RuntimeAnnotator:
    """Attach bounded, exact-path runtime hints without inspecting source files.

    The full Module 8 inventory is revalidated before a query embedding or
    publication lease is acquired.  It may be externally supplied/forged.
    Matching frame paths to index paths does not authenticate the runtime
    observation's revision, graph association, or claimed timestamp.
    """

    __slots__ = ("_namespace", "_inventory", "_per_path")

    def __init__(
        self,
        repository_namespace: str,
        inventory: RuntimeEvidenceInventory,
        per_path: int = 5,
    ) -> None:
        if (
            type(repository_namespace) is not str or not repository_namespace
            or type(inventory) is not RuntimeEvidenceInventory
            or type(per_path) is not int
            or not 1 <= per_path <= _MAX_FRAMES_PER_PATH
        ):
            raise InvalidRetrievalQuery(_INVALID_REQUEST)
        if inventory.repository_namespace != repository_namespace:
            raise InvalidHybridEvidence(_INVALID_RUNTIME)
        if (
            len(inventory.artifacts) > _MAX_ARTIFACTS
            or len(inventory.events) > _MAX_EVENTS
            or len(inventory.frames) > _MAX_FRAMES
            or len(inventory.issues) > _MAX_ISSUES
        ):
            raise InvalidHybridEvidence(_INVALID_RUNTIME)
        try:
            validate_inventory(inventory)
        except RuntimeEvidenceInvariantError:
            raise InvalidHybridEvidence(_INVALID_RUNTIME) from None
        self._namespace = repository_namespace
        self._inventory = inventory
        self._per_path = per_path

    def annotate(self, hits: tuple[HybridHit, ...]) -> RuntimeContext:
        if type(hits) is not tuple or len(hits) > _MAX_HITS:
            raise InvalidRetrievalQuery(_INVALID_REQUEST)
        paths: list[str] = []
        seen: set[str] = set()
        for rank, hit in enumerate(hits, 1):
            if (
                type(hit) is not HybridHit
                or hit.repository_namespace != self._namespace
                or type(hit.rank) is not int or hit.rank != rank
                or type(hit.relative_path) is not str or not hit.relative_path
            ):
                raise InvalidHybridEvidence(_INVALID_RUNTIME)
            if hit.relative_path not in seen:
                paths.append(hit.relative_path)
                seen.add(hit.relative_path)

        by_path: dict[str, list[RuntimeFrameClue]] = {path: [] for path in paths}
        totals = {path: 0 for path in paths}
        event_by_id = {event.event_id: event for event in self._inventory.events}
        for frame in self._inventory.frames:
            path = frame.relative_path
            if path not in by_path or frame.path_match_basis not in _PATH_BASES:
                continue
            totals[path] += 1
            if len(by_path[path]) < self._per_path:
                event = event_by_id[frame.event_id]
                by_path[path].append(RuntimeFrameClue(
                    artifact_id=event.artifact_id,
                    event_id=event.event_id,
                    frame_id=frame.frame_id,
                    event_kind=event.kind,
                    language=frame.language,
                    line=frame.line,
                    column=frame.column,
                    path_match_basis=frame.path_match_basis,
                    association=frame.association,
                ))

        inventory = self._inventory
        return RuntimeContext(
            paths=tuple(RuntimePathContext(path, tuple(by_path[path]), totals[path])
                        for path in paths),
            declared_revision=inventory.declared_revision,
            declared_captured_at=inventory.declared_captured_at,
            declared_capture_completeness=inventory.declared_capture_completeness,
            graph_binding=inventory.graph_binding,
            ingestion_complete=inventory.ingestion_complete,
            incomplete_reasons=inventory.incomplete_reasons,
            events_truncated=inventory.events_truncated,
            frames_truncated=inventory.frames_truncated,
            source_revision_verified=False,
            causal_relationship_verified=False,
        )
