"""Bounded, immutable, in-memory projection of *already retrieved* Module 9 evidence.

This is an internal, pre-redaction representation. Its ``content`` fields may
contain private code; it MUST NOT be logged, serialized to a model, or returned
through an API. A later increment will create a separately validated, redacted
provider-safe type. No storage access, network calls, provider calls, or source
file reads occur here. Trusted callers supply results from one Module 9 call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
import re

from backend.retrieval.hybrid import HybridHit
from backend.retrieval.graph import GraphContext
from backend.retrieval.history import HistoricalContext, HistoricalChange, PathHistory
from backend.retrieval.lexical import LexicalDocument
from backend.retrieval.runtime import RuntimeContext, RuntimeFrameClue, RuntimePathContext


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_INVALID = "Evidence assembly input is invalid or inconsistent."
_MAX_QUERY_CHARS = 4096
_MAX_HITS = 100
_MAX_CONTEXT = 100


class InvalidEvidenceBundle(ValueError):
    """A retrieval result, configuration, or identity violates the boundary."""


class EvidenceKind(str, Enum):
    INDEXED_SOURCE = "indexed_source"
    REVISION_BOUND_GRAPH = "revision_bound_graph"
    UNVERIFIED_GIT_HISTORY = "unverified_git_history"
    UNVERIFIED_RUNTIME = "unverified_runtime"


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Bounded deterministic selection limits; not a model token estimate."""

    max_primary_sources: int = 8
    max_graph_relationships: int = 8
    max_history_per_path: int = 5
    max_runtime_per_path: int = 5
    max_items: int = 64
    max_evidence_bytes: int = 65_536

    def __post_init__(self) -> None:
        bounds = (
            (self.max_primary_sources, 1, 100),
            (self.max_graph_relationships, 0, 100),
            (self.max_history_per_path, 0, 20),
            (self.max_runtime_per_path, 0, 20),
            (self.max_items, 1, 256),
            (self.max_evidence_bytes, 1, 1_048_576),
        )
        if any(type(value) is not int or not lo <= value <= hi for value, lo, hi in bounds):
            raise InvalidEvidenceBundle(_INVALID)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """Request-local identity. ``content`` is raw and intentionally not safe."""

    label: str
    kind: EvidenceKind
    repository_namespace: str = field(repr=False)
    relative_path: str = field(repr=False)
    chunk_id: str | None = field(default=None, repr=False)
    content_hash: str | None = field(default=None, repr=False)
    content: str = field(default="", repr=False)
    source_label: str | None = None
    details: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    source_revision_verified: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Immutable registry; evidence stays local and unredacted for Increment 1."""

    repository_namespace: str = field(repr=False)
    query_text: str = field(repr=False)
    items: tuple[EvidenceItem, ...] = field(repr=False)
    omissions: tuple[str, ...] = ()
    history_is_complete: bool | None = None
    runtime_ingestion_complete: bool | None = None
    # This bundle must never be fed to Gemini or serialized for a browser.
    @property
    def provider_safe(self) -> bool:
        """This raw bundle is *never* safe to send to a provider."""
        return False

    def resolve(self, label: str) -> EvidenceItem | None:
        """Lookup only a label this request actually emitted."""
        if type(label) is not str:
            return None
        for item in self.items:
            if item.label == label:
                return item
        return None


@dataclass(frozen=True, slots=True)
class _Source:
    chunk_id: str
    content_hash: str
    relative_path: str
    content: str


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and "\x00" not in value


def _path(value: object) -> bool:
    return (
        _text(value)
        and not value.startswith(("/", "\\"))
        and "\\" not in value
        and all(piece not in ("", ".", "..") for piece in value.split("/"))
        and ":" not in value.split("/", 1)[0]
    )


def _source(namespace: str, item: HybridHit | LexicalDocument) -> _Source:
    if (
        type(item) not in (HybridHit, LexicalDocument)
        or item.repository_namespace != namespace
        or type(item.chunk_id) is not str or _HASH.fullmatch(item.chunk_id) is None
        or type(item.content_hash) is not str or _HASH.fullmatch(item.content_hash) is None
        or not _path(item.relative_path)
        or not _text(item.content)
        or len(item.content) > 16_384
    ):
        raise InvalidEvidenceBundle(_INVALID)
    try:
        digest = sha256(item.content.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise InvalidEvidenceBundle(_INVALID) from None
    if digest != item.content_hash:
        raise InvalidEvidenceBundle(_INVALID)
    return _Source(item.chunk_id, item.content_hash, item.relative_path, item.content)


def _bytes(item: EvidenceItem) -> int:
    # Count all emitted strings, not only code content; no dynamic truncation.
    parts = (
        item.label, item.kind.value, item.repository_namespace,
        item.relative_path, item.chunk_id or "", item.content_hash or "",
        item.content, item.source_label or "",
        *(key + "=" + value for key, value in item.details),
    )
    return sum(len(part.encode("utf-8")) for part in parts)


def assemble_evidence(
    repository_namespace: str,
    query_text: str,
    hits: tuple[HybridHit, ...],
    *,
    graph_context: tuple[GraphContext, ...] = (),
    history: HistoricalContext | None = None,
    runtime: RuntimeContext | None = None,
    policy: EvidencePolicy | None = None,
) -> EvidenceBundle:
    """Project a **single** trusted Module 9 result into a citation registry.

    The service must supply ``hits``, ``graph_context``, ``history`` and
    ``runtime`` from the same Module 9 public return value; this function
    cannot authenticate the caller's origin. It never rereads a repository,
    opens a snapshot, or promotes Git/runtime observations to source proof.
    """
    policy = EvidencePolicy() if policy is None else policy
    if (
        type(policy) is not EvidencePolicy
        or not _text(repository_namespace)
        or not _text(query_text)
        or len(query_text) > _MAX_QUERY_CHARS
        or type(hits) is not tuple or len(hits) > _MAX_HITS
        or type(graph_context) is not tuple or len(graph_context) > _MAX_CONTEXT
        or (history is not None and type(history) is not HistoricalContext)
        or (runtime is not None and type(runtime) is not RuntimeContext)
    ):
        raise InvalidEvidenceBundle(_INVALID)
    try:
        query_text.encode("utf-8")
        namespace_bytes = repository_namespace.encode("utf-8")
        if len(namespace_bytes) > 256:
            raise InvalidEvidenceBundle(_INVALID)
    except UnicodeError:
        raise InvalidEvidenceBundle(_INVALID) from None

    original: dict[str, _Source] = {}
    all_paths: set[str] = set()
    for rank, hit in enumerate(hits, 1):
        source = _source(repository_namespace, hit)
        if (
            type(hit.rank) is not int or hit.rank != rank
            or type(hit.score) not in (float, int)
            or not math.isfinite(hit.score) or hit.score <= 0
            or source.chunk_id in original
        ):
            raise InvalidEvidenceBundle(_INVALID)
        original[source.chunk_id] = source
        all_paths.add(source.relative_path)

    # Reject inconsistent caller-supplied graph context rather than treating
    # a namespace/path match alone as proof of a bound graph revision.
    graph_seen: set[str] = set()
    validated_graph: list[tuple[GraphContext, _Source]] = []
    for rank, graph in enumerate(graph_context, 1):
        if (
            type(graph) is not GraphContext
            or type(graph.rank) is not int or graph.rank != rank
            or type(graph.document) is not LexicalDocument
            or type(graph.seed_chunk_id) is not str or graph.seed_chunk_id not in original
            or type(graph.edge_id) is not str or _HASH.fullmatch(graph.edge_id) is None
            or not isinstance(graph.edge_kind, Enum)
            or graph.edge_kind.value not in ("calls", "contains", "inherits", "implements")
            or not _text(graph.direction)
            or graph.direction not in ("incoming", "outgoing")
            or graph.document.chunk_id in graph_seen
            or graph.document.chunk_id in original
        ):
            raise InvalidEvidenceBundle(_INVALID)
        source = _source(repository_namespace, graph.document)
        graph_seen.add(source.chunk_id)
        validated_graph.append((graph, source))

    if history is not None:
        if (
            history.source_revision_verified is not False
            or type(history.paths) is not tuple
            or len(history.paths) > _MAX_HITS
            or type(history.history_is_complete) is not bool
        ):
            raise InvalidEvidenceBundle(_INVALID)
        seen_history: set[str] = set()
        for path in history.paths:
            if (
                type(path) is not PathHistory or not _path(path.relative_path)
                or path.relative_path not in all_paths
                or path.relative_path in seen_history
                or type(path.changes) is not tuple or len(path.changes) > 20
                or type(path.total_matching_changes) is not int
                or path.total_matching_changes < len(path.changes)
            ):
                raise InvalidEvidenceBundle(_INVALID)
            seen_history.add(path.relative_path)
            if any(type(change) is not HistoricalChange for change in path.changes):
                raise InvalidEvidenceBundle(_INVALID)
    if runtime is not None:
        if (
            runtime.source_revision_verified is not False
            or runtime.causal_relationship_verified is not False
            or type(runtime.paths) is not tuple
            or len(runtime.paths) > _MAX_HITS
            or type(runtime.ingestion_complete) is not bool
        ):
            raise InvalidEvidenceBundle(_INVALID)
        seen_runtime: set[str] = set()
        for path in runtime.paths:
            if (
                type(path) is not RuntimePathContext or not _path(path.relative_path)
                or path.relative_path not in all_paths
                or path.relative_path in seen_runtime
                or type(path.frames) is not tuple or len(path.frames) > 20
                or type(path.total_matching_frames) is not int
                or path.total_matching_frames < len(path.frames)
            ):
                raise InvalidEvidenceBundle(_INVALID)
            seen_runtime.add(path.relative_path)
            if any(type(frame) is not RuntimeFrameClue for frame in path.frames):
                raise InvalidEvidenceBundle(_INVALID)

    output: list[EvidenceItem] = []
    omitted: list[str] = []
    current_bytes = 0
    counters = {"S": 0, "G": 0, "H": 0, "R": 0}
    source_labels: dict[str, str] = {}
    path_labels: dict[str, str] = {}

    def note(reason: str) -> None:
        if reason not in omitted:
            omitted.append(reason)

    def put(prefix: str, kind: EvidenceKind, path: str, *,
            chunk_id: str | None = None, content_hash: str | None = None,
            content: str = "", source_label: str | None = None,
            details: tuple[tuple[str, str], ...] = (),
            verified: bool = False) -> str | None:
        nonlocal current_bytes
        label = f"{prefix}{counters[prefix] + 1}"
        item = EvidenceItem(label, kind, repository_namespace, path, chunk_id,
                            content_hash, content, source_label, details, verified)
        try:
            size = _bytes(item)
        except UnicodeError:
            raise InvalidEvidenceBundle(_INVALID) from None
        if len(output) >= policy.max_items:
            note("item_limit")
            return None
        if current_bytes + size > policy.max_evidence_bytes:
            note("byte_budget")
            return None
        output.append(item)
        counters[prefix] += 1
        current_bytes += size
        return label

    if len(hits) > policy.max_primary_sources:
        note("primary_source_limit")
    for hit in hits[:policy.max_primary_sources]:
        source = original[hit.chunk_id]
        label = put("S", EvidenceKind.INDEXED_SOURCE, source.relative_path,
                    chunk_id=source.chunk_id, content_hash=source.content_hash,
                    content=source.content, verified=True)
        if label is not None:
            source_labels[source.chunk_id] = label
            path_labels.setdefault(source.relative_path, label)

    if len(validated_graph) > policy.max_graph_relationships:
        note("graph_limit")
    for graph, source in validated_graph[:policy.max_graph_relationships]:
        seed_label = source_labels.get(graph.seed_chunk_id)
        if seed_label is None:
            note("unselected_graph_seed")
            continue
        related_label = source_labels.get(source.chunk_id)
        relation_details = (("seed", seed_label), ("edge_id", graph.edge_id),
                            ("edge_kind", graph.edge_kind.value),
                            ("direction", graph.direction))
        if related_label is None:
            # Reserve both records atomically: no orphan source if a graph edge
            # cannot fit the item or byte budget.
            next_source = f"S{counters['S'] + 1}"
            next_graph = f"G{counters['G'] + 1}"
            source_candidate = EvidenceItem(next_source, EvidenceKind.INDEXED_SOURCE,
                repository_namespace, source.relative_path, source.chunk_id,
                source.content_hash, source.content, None, (), True)
            graph_candidate = EvidenceItem(next_graph, EvidenceKind.REVISION_BOUND_GRAPH,
                repository_namespace, source.relative_path, None, None, "",
                next_source, relation_details, True)
            if len(output) + 2 > policy.max_items:
                note("item_limit")
                continue
            try:
                combined_size = _bytes(source_candidate) + _bytes(graph_candidate)
            except UnicodeError:
                raise InvalidEvidenceBundle(_INVALID) from None
            if current_bytes + combined_size > policy.max_evidence_bytes:
                note("byte_budget")
                continue
            related_label = put("S", EvidenceKind.INDEXED_SOURCE, source.relative_path,
                chunk_id=source.chunk_id, content_hash=source.content_hash,
                content=source.content, verified=True)
            if related_label is None:
                raise InvalidEvidenceBundle(_INVALID)
            source_labels[source.chunk_id] = related_label
        put("G", EvidenceKind.REVISION_BOUND_GRAPH, source.relative_path,
            source_label=related_label, details=relation_details, verified=True)

    if history is not None:
        if not history.history_is_complete or history.incomplete_reasons:
            note("history_incomplete")
        for path in history.paths:
            label = path_labels.get(path.relative_path)
            if label is None:
                continue
            if path.total_matching_changes > min(policy.max_history_per_path, len(path.changes)):
                note("history_truncated")
            for change in path.changes[:policy.max_history_per_path]:
                if (
                    type(change.commit_id) is not str
                    or len(change.commit_id) not in (40, 64)
                    or any(c not in "0123456789abcdef" for c in change.commit_id)
                    or type(change.change_id) is not str or _HASH.fullmatch(change.change_id) is None
                    or not _text(change.change_type)
                    or type(change.committer_timestamp) is not int
                ):
                    raise InvalidEvidenceBundle(_INVALID)
                put("H", EvidenceKind.UNVERIFIED_GIT_HISTORY, path.relative_path,
                    source_label=label, details=(("commit_id", change.commit_id),
                                                 ("change_id", change.change_id),
                                                 ("change_type", change.change_type),
                                                 ("timestamp", str(change.committer_timestamp))))
    if runtime is not None:
        if not runtime.ingestion_complete or runtime.incomplete_reasons or runtime.events_truncated or runtime.frames_truncated:
            note("runtime_incomplete")
        for path in runtime.paths:
            label = path_labels.get(path.relative_path)
            if label is None:
                continue
            if path.total_matching_frames > min(policy.max_runtime_per_path, len(path.frames)):
                note("runtime_truncated")
            for frame in path.frames[:policy.max_runtime_per_path]:
                if (
                    type(frame.artifact_id) is not str or _HASH.fullmatch(frame.artifact_id) is None
                    or type(frame.event_id) is not str or _HASH.fullmatch(frame.event_id) is None
                    or type(frame.frame_id) is not str or _HASH.fullmatch(frame.frame_id) is None
                    or not isinstance(frame.event_kind, Enum)
                    or frame.event_kind.value not in ("log", "error", "span")
                    or not isinstance(frame.path_match_basis, Enum)
                    or frame.path_match_basis.value not in (
                        "exact_relative_path", "unverified_relative_path",
                        "unique_path_suffix", "unique_basename",
                    )
                    or (frame.line is not None and (type(frame.line) is not int or frame.line < 1))
                    or (frame.column is not None and (type(frame.column) is not int or frame.column < 1))
                ):
                    raise InvalidEvidenceBundle(_INVALID)
                fields = (("artifact_id", frame.artifact_id), ("event_id", frame.event_id),
                          ("frame_id", frame.frame_id), ("event_kind", frame.event_kind.value),
                          ("path_match_basis", frame.path_match_basis.value))
                if frame.line is not None:
                    fields += (("reported_line", str(frame.line)),)
                put("R", EvidenceKind.UNVERIFIED_RUNTIME, path.relative_path,
                    source_label=label, details=fields)

    return EvidenceBundle(repository_namespace, query_text, tuple(output), tuple(omitted),
                          None if history is None else history.history_is_complete,
                          None if runtime is None else runtime.ingestion_complete)
