"""Offline, conservative *pre-model* projection of an internal EvidenceBundle.

Neither this heuristic redactor nor its output authorizes transmitting private
repository content. A server-side repository owner must explicitly authorize
external processing in a later service increment. Never log raw bundles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re

from .evidence import EvidenceBundle, EvidenceKind, EvidenceItem


_ERROR = "Evidence privacy boundary rejected the input."
_MARKER = "[REDACTED]"
_LABEL = re.compile(r"(?:S|G|H|R)[1-9][0-9]*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_OMISSIONS = frozenset((
    "item_limit", "byte_budget", "primary_source_limit", "graph_limit",
    "unselected_graph_seed", "history_incomplete", "history_truncated",
    "runtime_incomplete", "runtime_truncated",
))
_PREFIX_KINDS = {
    "S": EvidenceKind.INDEXED_SOURCE,
    "G": EvidenceKind.REVISION_BOUND_GRAPH,
    "H": EvidenceKind.UNVERIFIED_GIT_HISTORY,
    "R": EvidenceKind.UNVERIFIED_RUNTIME,
}
_BARE_SECRET = re.compile(
    r"(?i)(?:\b(?:gh[pousr]_[a-z0-9_]{20,}|github_pat_[a-z0-9_]{20,}|"
    r"sk-[a-z0-9_-]{20,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
    r"AIza[A-Za-z0-9_-]{35}|glpat-[A-Za-z0-9_-]{15,}|"
    r"xox[baprs]-[a-z0-9-]{12,})\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"\b(?:Bearer|Basic)\s+[a-z0-9+/._=-]{10,}\b)"
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)(?P<key>(?:\b[A-Z_]{0,32})?"
    r"(?:password|passwd|pwd|secret(?:_key)?|client[_-]?secret|"
    r"api[_-]?key|access[_-]?key|private[_-]?key|"
    r"auth[_-]?token|access[_-]?token|refresh[_-]?token|token|authorization)"
    r"\b[\\\"']?\s*(?:=|:)\s*[\\\"']?)"
    r"(?P<value>[^\s\\\"',;#}{]{1,})"
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ALLOWED_DETAIL = frozenset((
    "seed", "edge_id", "edge_kind", "direction", "commit_id", "change_id",
    "change_type", "timestamp", "artifact_id", "event_id", "frame_id",
    "event_kind", "path_match_basis", "reported_line",
))


class UnsafeEvidenceForModel(ValueError):
    """A source, query, or configuration cannot cross the privacy boundary."""


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    max_total_bytes: int = 65_536
    max_item_bytes: int = 16_384
    max_query_bytes: int = 4_096

    def __post_init__(self) -> None:
        if (
            type(self.max_total_bytes) is not int or not 1 <= self.max_total_bytes <= 1_048_576
            or type(self.max_item_bytes) is not int or not 1 <= self.max_item_bytes <= 65_536
            or type(self.max_query_bytes) is not int or not 1 <= self.max_query_bytes <= 16_384
        ):
            raise UnsafeEvidenceForModel(_ERROR)


@dataclass(frozen=True, slots=True)
class RedactedEvidenceItem:
    label: str
    kind: EvidenceKind
    content: str = field(repr=False)
    source_label: str | None = None
    details: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    redacted: bool = False


@dataclass(frozen=True, slots=True)
class RedactedEvidenceBundle:
    """Privacy-filtered, not transmission-authorized, request-local evidence.

    Fields are *not* full provenance: citations resolve against the raw local
    registry, which never leaves the backend. External send needs separate
    repository-owner consent, auth checks, and provider-specific policy.
    """

    query_text: str = field(repr=False)
    items: tuple[RedactedEvidenceItem, ...] = field(repr=False)
    omissions: tuple[str, ...] = ()
    history_is_complete: bool | None = None
    runtime_ingestion_complete: bool | None = None

    @property
    def provider_safe(self) -> bool:
        # Pattern-based redaction cannot establish absence of unknown secrets.
        return False

    def resolve(self, label: str) -> RedactedEvidenceItem | None:
        if type(label) is not str:
            return None
        for item in self.items:
            if item.label == label:
                return item
        return None


def _safe_text(value: str, *, is_query: bool = False) -> tuple[str, bool]:
    if type(value) is not str:
        raise UnsafeEvidenceForModel(_ERROR)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise UnsafeEvidenceForModel(_ERROR) from None
    if _CONTROL.search(value) or _PEM.search(value):
        # Entire private keys and control-bearing values cannot be safely cut.
        raise UnsafeEvidenceForModel(_ERROR)
    censored = _ASSIGNMENT_SECRET.sub(lambda m: m.group("key") + _MARKER, value)
    censored = _BARE_SECRET.sub(_MARKER, censored)
    censored = _EMAIL.sub(_MARKER, censored)
    changed = censored != value
    if is_query and changed:
        # A secret in the query requires a corrected user query, not a guess.
        raise UnsafeEvidenceForModel(_ERROR)
    return censored, changed


def _valid_metadata(item: EvidenceItem) -> bool:
    if (
        type(item.label) is not str or _LABEL.fullmatch(item.label) is None
        or type(item.kind) is not EvidenceKind
        or _PREFIX_KINDS[item.label[0]] is not item.kind
        or type(item.source_revision_verified) is not bool
        or item.source_revision_verified != (item.label[0] in "SG")
        or type(item.repository_namespace) is not str
        or type(item.relative_path) is not str
        or type(item.content) is not str or len(item.content) > 16_384
        or len(item.repository_namespace) > 256 or len(item.relative_path) > 2_048
        or type(item.source_label) not in (str, type(None))
        or (item.source_label is not None and _LABEL.fullmatch(item.source_label) is None)
        or type(item.details) is not tuple or len(item.details) > 32
    ):
        return False
    if item.kind is EvidenceKind.INDEXED_SOURCE:
        if (
            item.source_label is not None or type(item.chunk_id) is not str
            or type(item.content_hash) is not str
            or _HASH.fullmatch(item.chunk_id) is None
            or _HASH.fullmatch(item.content_hash) is None
        ):
            return False
        try:
            if sha256(item.content.encode("utf-8")).hexdigest() != item.content_hash:
                return False
        except UnicodeError:
            return False
    elif (
        item.content != "" or item.chunk_id is not None or item.content_hash is not None
        or type(item.source_label) is not str or not item.source_label.startswith("S")
    ):
        return False
    return all(
        type(pair) is tuple and len(pair) == 2
        and type(pair[0]) is str and pair[0] in _ALLOWED_DETAIL
        and type(pair[1]) is str and len(pair[1]) <= 256
        for pair in item.details
    )


def redact_evidence(
    bundle: EvidenceBundle, *, policy: RedactionPolicy | None = None
) -> RedactedEvidenceBundle:
    """Return an isolated redacted projection; never mutate the raw bundle.

    If a source cannot be safely processed, omit it and all dependent graph,
    history and runtime items instead of exposing a dangling citation.
    No path, namespace, original hash, source text, or raw metadata is echoed
    in diagnostics. Truncation is whole-record, not partial content.
    """
    policy = RedactionPolicy() if policy is None else policy
    if type(bundle) is not EvidenceBundle or type(policy) is not RedactionPolicy:
        raise UnsafeEvidenceForModel(_ERROR)
    query, _ = _safe_text(bundle.query_text, is_query=True)
    if len(query.encode("utf-8")) > policy.max_query_bytes:
        raise UnsafeEvidenceForModel(_ERROR)
    if (
        type(bundle.items) is not tuple or len(bundle.items) > 256
        or type(bundle.omissions) is not tuple
        or any(type(reason) is not str or reason not in _ALLOWED_OMISSIONS
               for reason in bundle.omissions)
        or type(bundle.history_is_complete) not in (bool, type(None))
        or type(bundle.runtime_ingestion_complete) not in (bool, type(None))
    ):
        raise UnsafeEvidenceForModel(_ERROR)

    output: list[RedactedEvidenceItem] = []
    omissions = list(bundle.omissions)
    admitted: set[str] = set()
    raw_seen: set[str] = set()
    used = len(query.encode("utf-8"))

    def note(reason: str) -> None:
        if reason not in omissions:
            omissions.append(reason)

    for item in bundle.items:
        if type(item) is not EvidenceItem or not _valid_metadata(item) or item.label in raw_seen:
            raise UnsafeEvidenceForModel(_ERROR)
        raw_seen.add(item.label)
        if item.source_label is not None and item.source_label not in admitted:
            note("dependent_evidence_omitted")
            continue
        # Graph records also reference their seed in trusted registry details.
        if item.kind is EvidenceKind.REVISION_BOUND_GRAPH:
            seed = dict(item.details).get("seed")
            if type(seed) is not str or seed not in admitted:
                note("dependent_evidence_omitted")
                continue
        try:
            safe, redacted = _safe_text(item.content)
            safe_details = []
            for key, value in item.details:
                sanitized, changed = _safe_text(value)
                safe_details.append((key, sanitized))
                redacted = redacted or changed
        except UnsafeEvidenceForModel:
            note("privacy_item_omitted")
            continue
        # Metadata may identify sensitive paths. Omit if sensitive rather than
        # exporting the path. Path itself is never part of the model view.
        try:
            _, path_redacted = _safe_text(item.relative_path)
        except UnsafeEvidenceForModel:
            path_redacted = True
        if path_redacted:
            note("privacy_item_omitted")
            continue
        projected = RedactedEvidenceItem(
            item.label, item.kind, safe, item.source_label, tuple(safe_details), redacted
        )
        size = (
            len(projected.label.encode("utf-8"))
            + len(projected.kind.value.encode("utf-8"))
            + len(projected.content.encode("utf-8"))
            + len((projected.source_label or "").encode("utf-8"))
            + sum(len(k.encode("utf-8")) + len(v.encode("utf-8")) for k, v in projected.details)
        )
        if size > policy.max_item_bytes or used + size > policy.max_total_bytes:
            note("redacted_byte_budget")
            continue
        used += size
        output.append(projected)
        admitted.add(item.label)
        if redacted:
            note("sensitive_text_redacted")
    return RedactedEvidenceBundle(
        query, tuple(output), tuple(omissions),
        bundle.history_is_complete, bundle.runtime_ingestion_complete,
    )
