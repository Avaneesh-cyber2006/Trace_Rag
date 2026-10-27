"""Validation and lookup indexes for an optional Module 6 code graph.

Only the graph's repository namespace and its public ``nodes`` tuple are
read. Edges, unresolved references, the graph's public index mappings, and
source files are never touched.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.drafts import FrameDraft, IssueDraft
from backend.runtime_evidence.exceptions import RuntimeEvidenceGraphError
from backend.runtime_evidence.identities import frame_id, source_reference_sha256
from backend.runtime_evidence.models import (
    FrameAssociation,
    PathMatchBasis,
    RuntimeIssueCode,
    RuntimeStackFrame,
)
from backend.runtime_evidence.source_references import (
    NormalizedSource,
    SourceReferenceKind,
    normalize_source_reference,
)
from backend.runtime_evidence.validation import (
    require_relative_posix_path,
    require_scalar_text,
)

_GRAPH_ERROR_MESSAGE = "runtime evidence graph is invalid"
_LOCATION_FIELDS = (
    "start_byte",
    "end_byte",
    "start_line",
    "start_column",
    "end_line",
    "end_column",
)


@dataclass(frozen=True, slots=True)
class GraphFileEntry:
    node_id: str
    relative_path: str
    components: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphSymbolEntry:
    node_id: str
    start_line: int
    last_covered_line: int


@dataclass(frozen=True, slots=True)
class AssociationIndex:
    files_by_path: Mapping[str, GraphFileEntry]
    files_by_basename: Mapping[str, tuple[GraphFileEntry, ...]]
    symbols_by_file: Mapping[str, tuple[GraphSymbolEntry, ...]]


def _invalid() -> RuntimeEvidenceGraphError:
    return RuntimeEvidenceGraphError(_GRAPH_ERROR_MESSAGE)


def _require_max_namespace_bytes(value: object) -> int:
    if type(value) is bool or type(value) is not int or value <= 0:
        raise _invalid()
    return value


def _require_namespace(value: object, max_bytes: int) -> str:
    try:
        return require_scalar_text(
            value,
            "graph",
            RuntimeEvidenceGraphError,
            allow_empty=False,
            max_bytes=max_bytes,
        )
    except RuntimeEvidenceGraphError:
        raise _invalid() from None


def _require_node_id(value: object) -> str:
    try:
        return require_scalar_text(
            value,
            "graph",
            RuntimeEvidenceGraphError,
            allow_empty=False,
        )
    except RuntimeEvidenceGraphError:
        raise _invalid() from None


def _require_relative_path(value: object) -> str:
    try:
        return require_relative_posix_path(
            value, "graph", RuntimeEvidenceGraphError
        )
    except RuntimeEvidenceGraphError:
        raise _invalid() from None


def _valid_location(location: object) -> tuple[int, int]:
    """Validate the six structural SourceLocation fields without importing it."""
    try:
        fields = tuple(getattr(location, name) for name in _LOCATION_FIELDS)
    except AttributeError:
        raise _invalid() from None
    except Exception:
        raise _invalid() from None
    if any(type(value) is not int for value in fields):
        raise _invalid()
    (
        start_byte,
        end_byte,
        start_line,
        start_column,
        end_line,
        end_column,
    ) = fields
    if start_byte < 0 or start_byte > end_byte:
        raise _invalid()
    if start_line < 1 or end_line < 1 or start_line > end_line:
        raise _invalid()
    if start_column < 0 or end_column < 0:
        raise _invalid()
    if start_line == end_line and start_column > end_column:
        raise _invalid()
    last_covered_line = end_line if end_column > 0 else end_line - 1
    return start_line, last_covered_line


def build_association_index(
    graph: object,
    *,
    repository_namespace: str,
    max_namespace_bytes: int,
) -> AssociationIndex | None:
    """Validate a Module 6 graph and build an immutable association index."""
    if graph is None:
        return None
    max_bytes = _require_max_namespace_bytes(max_namespace_bytes)
    request_namespace = _require_namespace(repository_namespace, max_bytes)
    if type(graph) is not CodeGraphInventory:
        raise _invalid()
    try:
        graph_namespace = graph.repository_namespace
        nodes = graph.nodes
    except AttributeError:
        raise _invalid() from None
    if _require_namespace(graph_namespace, max_bytes) != request_namespace:
        raise _invalid()
    if type(nodes) is not tuple:
        raise _invalid()

    files_by_path: dict[str, GraphFileEntry] = {}
    file_buckets: dict[str, list[GraphFileEntry]] = {}
    symbol_buckets: dict[str, list[GraphSymbolEntry]] = {}
    symbol_paths: list[str] = []
    seen_node_ids: set[str] = set()

    for node in nodes:
        if type(node) is not GraphNode:
            raise _invalid()
        try:
            node_id = node.node_id
            node_namespace = node.repository_namespace
            node_kind = node.node_kind
            relative_path = node.relative_path
            location = node.location
        except AttributeError:
            raise _invalid() from None
        validated_id = _require_node_id(node_id)
        if validated_id in seen_node_ids:
            raise _invalid()
        seen_node_ids.add(validated_id)
        if _require_namespace(node_namespace, max_bytes) != request_namespace:
            raise _invalid()
        if type(node_kind) is not GraphNodeKind:
            raise _invalid()
        path = _require_relative_path(relative_path)
        if node_kind is GraphNodeKind.FILE:
            if location is not None or path in files_by_path:
                raise _invalid()
            components = tuple(path.split("/"))
            entry = GraphFileEntry(validated_id, path, components)
            files_by_path[path] = entry
            file_buckets.setdefault(components[-1], []).append(entry)
        elif node_kind is GraphNodeKind.SYMBOL:
            start_line, last_covered_line = _valid_location(location)
            symbol_buckets.setdefault(path, []).append(
                GraphSymbolEntry(
                    validated_id, start_line, last_covered_line
                )
            )
            symbol_paths.append(path)
        else:
            raise _invalid()

    for path in symbol_paths:
        if path not in files_by_path:
            raise _invalid()

    files_by_path_snapshot = MappingProxyType(
        {path: files_by_path[path] for path in sorted(files_by_path)}
    )
    files_by_basename_snapshot = MappingProxyType(
        {
            basename: tuple(
                sorted(
                    bucket,
                    key=lambda entry: (entry.relative_path, entry.node_id),
                )
            )
            for basename, bucket in sorted(file_buckets.items())
        }
    )
    symbols_by_file_snapshot = MappingProxyType(
        {
            path: tuple(
                sorted(
                    bucket,
                    key=lambda symbol: (
                        symbol.start_line,
                        symbol.last_covered_line,
                        symbol.node_id,
                    ),
                )
            )
            for path, bucket in sorted(symbol_buckets.items())
        }
    )
    return AssociationIndex(
        files_by_path=files_by_path_snapshot,
        files_by_basename=files_by_basename_snapshot,
        symbols_by_file=symbols_by_file_snapshot,
    )


@dataclass(frozen=True, slots=True)
class AssociationResult:
    frames: tuple[RuntimeStackFrame, ...]
    issues: tuple[IssueDraft, ...]


def match_symbols_by_line(
    frame_lines: tuple[int, ...],
    symbols: tuple[GraphSymbolEntry, ...],
) -> tuple[tuple[GraphSymbolEntry, ...], ...]:
    """Return the containing symbols for each frame line.

    Lines are sorted once and swept in one pass over symbols ordered by start
    line. Repeated lines reuse the same match tuple.
    """
    if not frame_lines:
        return ()
    ordered_symbols = sorted(
        symbols,
        key=lambda symbol: (
            symbol.start_line,
            symbol.last_covered_line,
            symbol.node_id,
        ),
    )
    matches: list[tuple[GraphSymbolEntry, ...]] = [()] * len(frame_lines)
    active: list[GraphSymbolEntry] = []
    cached: tuple[GraphSymbolEntry, ...] = ()
    previous_line: int | None = None
    symbol_index = 0
    for frame_index in sorted(
        range(len(frame_lines)), key=lambda index: (frame_lines[index], index)
    ):
        line = frame_lines[frame_index]
        if previous_line is None or line != previous_line:
            active = [
                symbol
                for symbol in active
                if symbol.last_covered_line >= line
            ]
            while (
                symbol_index < len(ordered_symbols)
                and ordered_symbols[symbol_index].start_line <= line
            ):
                symbol = ordered_symbols[symbol_index]
                if symbol.last_covered_line >= line:
                    active.append(symbol)
                symbol_index += 1
            cached = tuple(active)
            previous_line = line
        matches[frame_index] = cached
    return tuple(matches)


@dataclass(frozen=True, slots=True)
class _FramePlan:
    draft: FrameDraft
    line: int | None
    column: int | None
    path_match_basis: PathMatchBasis
    relative_path: str | None
    file: GraphFileEntry | None
    invalid_location: bool


def _match_relative_suffix(
    normalized: NormalizedSource,
    graph_index: AssociationIndex,
) -> tuple[GraphFileEntry, ...]:
    components = normalized.components
    length = len(components)
    bucket = graph_index.files_by_basename.get(
        normalized.basename or "", ()
    )
    return tuple(
        entry
        for entry in bucket
        if len(entry.components) >= length
        and entry.components[-length:] == components
    )


def _match_absolute_suffix(
    normalized: NormalizedSource,
    graph_index: AssociationIndex,
) -> tuple[GraphFileEntry, ...]:
    components = normalized.components
    length = len(components)
    bucket = graph_index.files_by_basename.get(
        normalized.basename or "", ()
    )
    matches: list[GraphFileEntry] = []
    for entry in bucket:
        entry_length = len(entry.components)
        if (
            entry_length <= length
            and components[length - entry_length:] == entry.components
        ):
            matches.append(entry)
    return tuple(matches)


def _resolve_path(
    normalized: NormalizedSource,
    graph_index: AssociationIndex | None,
) -> tuple[PathMatchBasis, str | None, GraphFileEntry | None]:
    kind = normalized.kind
    if kind is SourceReferenceKind.EXTERNAL:
        return (PathMatchBasis.EXTERNAL_OR_NON_FILE, None, None)
    if kind in (SourceReferenceKind.NONE, SourceReferenceKind.INVALID):
        return (PathMatchBasis.UNMATCHED, None, None)
    if graph_index is None:
        if kind is SourceReferenceKind.RELATIVE:
            return (
                PathMatchBasis.UNVERIFIED_RELATIVE_PATH,
                normalized.relative_path,
                None,
            )
        return (PathMatchBasis.UNMATCHED, None, None)
    if kind is SourceReferenceKind.RELATIVE:
        exact = graph_index.files_by_path.get(normalized.relative_path or "")
        if exact is not None:
            return (PathMatchBasis.EXACT_RELATIVE_PATH, exact.relative_path,
                    exact)
        matches = _match_relative_suffix(normalized, graph_index)
        if len(matches) == 1:
            return (
                PathMatchBasis.UNIQUE_PATH_SUFFIX,
                matches[0].relative_path,
                matches[0],
            )
        if matches:
            return (PathMatchBasis.AMBIGUOUS, normalized.relative_path, None)
        return (PathMatchBasis.UNMATCHED, normalized.relative_path, None)
    if kind is SourceReferenceKind.BASENAME:
        matches = graph_index.files_by_basename.get(
            normalized.basename or "", ()
        )
        if len(matches) == 1:
            return (
                PathMatchBasis.UNIQUE_BASENAME,
                matches[0].relative_path,
                matches[0],
            )
        if matches:
            return (PathMatchBasis.AMBIGUOUS, None, None)
        return (PathMatchBasis.UNMATCHED, None, None)
    if kind is SourceReferenceKind.ABSOLUTE:
        matches = _match_absolute_suffix(normalized, graph_index)
        if len(matches) == 1:
            return (
                PathMatchBasis.UNIQUE_PATH_SUFFIX,
                matches[0].relative_path,
                matches[0],
            )
        if matches:
            return (PathMatchBasis.AMBIGUOUS, None, None)
        return (PathMatchBasis.UNMATCHED, None, None)
    return (PathMatchBasis.UNMATCHED, None, None)


def _plan_frame(
    draft: FrameDraft,
    graph_index: AssociationIndex | None,
    max_source_reference_bytes: int,
) -> _FramePlan:
    normalized = normalize_source_reference(
        draft.source_text, max_source_reference_bytes
    )
    line = draft.line
    column = draft.column
    invalid_location = False
    if line is not None and (type(line) is not int or line < 1):
        line = None
        invalid_location = True
    if column is not None and (type(column) is not int or column < 0):
        column = None
        invalid_location = True
    if normalized.kind is SourceReferenceKind.INVALID:
        invalid_location = True
    basis, relative_path, file = _resolve_path(normalized, graph_index)
    return _FramePlan(
        draft=draft,
        line=line,
        column=column,
        path_match_basis=basis,
        relative_path=relative_path,
        file=file,
        invalid_location=invalid_location,
    )


def _association_shape(
    plan: _FramePlan,
    containing: tuple[GraphSymbolEntry, ...],
    graph_supplied: bool,
) -> tuple[FrameAssociation, str | None, tuple[str, ...]]:
    if plan.file is None:
        association = (
            FrameAssociation.UNASSOCIATED
            if graph_supplied
            else FrameAssociation.NOT_EVALUATED
        )
        return (association, None, ())
    file_id = plan.file.node_id
    if plan.line is None:
        return (FrameAssociation.FILE, file_id, (file_id,))
    symbol_ids = sorted({symbol.node_id for symbol in containing})
    if not symbol_ids:
        return (FrameAssociation.FILE, file_id, (file_id,))
    if len(symbol_ids) == 1:
        return (
            FrameAssociation.SYMBOL,
            symbol_ids[0],
            tuple(sorted({file_id, symbol_ids[0]})),
        )
    return (
        FrameAssociation.AMBIGUOUS_SYMBOL,
        None,
        tuple(sorted({file_id, *symbol_ids})),
    )


def associate_frames(
    frames: tuple[FrameDraft, ...],
    *,
    event_id: str,
    event_sequence: int,
    source_line: int | None,
    repository_namespace: str,
    config: RuntimeEvidenceConfig,
    graph_index: AssociationIndex | None,
) -> AssociationResult:
    """Associate frame drafts with an optional graph by line containment."""
    plans = tuple(
        _plan_frame(
            draft, graph_index, config.max_source_reference_bytes
        )
        for draft in frames
    )

    lines_by_path: dict[str, list[tuple[int, int]]] = {}
    for ordinal, plan in enumerate(plans):
        if plan.file is not None and plan.line is not None:
            lines_by_path.setdefault(plan.file.relative_path, []).append(
                (ordinal, plan.line)
            )
    containing_by_ordinal: dict[int, tuple[GraphSymbolEntry, ...]] = {}
    for path, positioned_lines in lines_by_path.items():
        symbols = (
            graph_index.symbols_by_file.get(path, ())
            if graph_index is not None
            else ()
        )
        matched = match_symbols_by_line(
            tuple(line for _, line in positioned_lines), symbols
        )
        for (ordinal, _), containing in zip(positioned_lines, matched):
            containing_by_ordinal[ordinal] = containing

    associated: list[RuntimeStackFrame] = []
    issues: list[IssueDraft] = []
    graph_supplied = graph_index is not None
    for ordinal, plan in enumerate(plans):
        association, primary, candidates = _association_shape(
            plan, containing_by_ordinal.get(ordinal, ()), graph_supplied
        )
        relative_path = plan.relative_path
        associated.append(
            RuntimeStackFrame(
                frame_id=frame_id(repository_namespace, event_id, ordinal),
                event_id=event_id,
                ordinal=ordinal,
                language=plan.draft.language,
                function_text=plan.draft.function_text,
                relative_path=relative_path,
                line=plan.line,
                column=plan.column,
                source_reference_sha256=(
                    source_reference_sha256(relative_path)
                    if relative_path is not None
                    else None
                ),
                path_match_basis=plan.path_match_basis,
                association=association,
                primary_node_id=primary,
                candidate_node_ids=candidates,
            )
        )
        if plan.invalid_location:
            issues.append(
                IssueDraft(
                    RuntimeIssueCode.INVALID_SOURCE_LOCATION,
                    event_sequence,
                    source_line,
                )
            )
    return AssociationResult(
        frames=tuple(associated), issues=tuple(issues)
    )
