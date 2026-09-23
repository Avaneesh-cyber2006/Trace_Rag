"""Immutable outcomes and indexes shared by language resolvers."""

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, TypeVar, cast

from backend.code_graph.exceptions import CodeGraphResolutionError
from backend.code_graph.indexes import GraphIndexes, SymbolRangeNode
from backend.code_graph.models import ResolutionFailureReason
from backend.code_parser.models import (
    ImportInfo,
    ParsedFile,
    ParseStatus,
    SourceLocation,
)


_MemoValue = TypeVar("_MemoValue")


@dataclass(slots=True)
class _WildcardMemo:
    """Build-bound memo for deterministic wildcard member lookups."""

    _index: GraphIndexes | None = None
    _values: dict[tuple[object, ...], object] = field(default_factory=dict)

    def get_or_compute(
        self,
        index: GraphIndexes,
        key: tuple[object, ...],
        factory: Callable[[], _MemoValue],
    ) -> _MemoValue:
        if self._index is not index:
            self._index = index
            self._values.clear()
        if key not in self._values:
            self._values[key] = factory()
        return cast(_MemoValue, self._values[key])


@dataclass(frozen=True, slots=True)
class Resolution:
    target_node_id: str | None
    reason: ResolutionFailureReason | None
    candidate_node_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        target_present = self.target_node_id is not None
        reason_present = self.reason is not None
        if target_present == reason_present:
            raise CodeGraphResolutionError(
                "A resolution must contain exactly one target or failure reason."
            )
        if target_present and not (
            isinstance(self.target_node_id, str) and self.target_node_id
        ):
            raise CodeGraphResolutionError(
                "Resolution targets must be nonempty strings."
            )
        if reason_present and type(self.reason) is not ResolutionFailureReason:
            raise CodeGraphResolutionError("Invalid resolution failure reason.")
        if isinstance(self.candidate_node_ids, str):
            raise CodeGraphResolutionError(
                "Resolution candidates must be a sequence of IDs."
            )
        try:
            candidates = tuple(self.candidate_node_ids)
        except TypeError as exc:
            raise CodeGraphResolutionError(
                "Resolution candidates must be a sequence of IDs."
            ) from exc
        if not all(isinstance(candidate, str) and candidate for candidate in candidates):
            raise CodeGraphResolutionError(
                "Resolution candidates must be nonempty strings."
            )
        if candidates != tuple(sorted(set(candidates))):
            raise CodeGraphResolutionError(
                "Resolution candidates must be unique and sorted."
            )
        if candidates and self.reason is not ResolutionFailureReason.AMBIGUOUS:
            raise CodeGraphResolutionError(
                "Only ambiguous resolutions may contain candidates."
            )
        object.__setattr__(self, "candidate_node_ids", candidates)


@dataclass(frozen=True, slots=True)
class Binding:
    local_name: str
    scope_node_id: str
    mode: Literal["symbol", "module", "blocked"]
    resolution: Resolution

    def __post_init__(self) -> None:
        if not isinstance(self.local_name, str) or not self.local_name:
            raise CodeGraphResolutionError("Binding names must be nonempty strings.")
        if not isinstance(self.scope_node_id, str) or not self.scope_node_id:
            raise CodeGraphResolutionError("Binding scopes must be nonempty node IDs.")
        if self.mode not in ("symbol", "module", "blocked"):
            raise CodeGraphResolutionError("Invalid binding mode.")
        if type(self.resolution) is not Resolution:
            raise CodeGraphResolutionError("Binding resolution is invalid.")


@dataclass(frozen=True, slots=True)
class ImportResult:
    resolution: Resolution
    bindings: tuple[Binding, ...]

    def __post_init__(self) -> None:
        if type(self.resolution) is not Resolution:
            raise CodeGraphResolutionError("Import resolution is invalid.")
        if isinstance(self.bindings, (str, bytes)):
            raise CodeGraphResolutionError("Import bindings must be a sequence.")
        try:
            bindings = tuple(self.bindings)
        except TypeError as exc:
            raise CodeGraphResolutionError("Import bindings must be a sequence.") from exc
        if not all(type(binding) is Binding for binding in bindings):
            raise CodeGraphResolutionError("Import bindings contain an invalid value.")
        object.__setattr__(self, "bindings", bindings)


@dataclass(frozen=True, slots=True)
class ImportEnvironment:
    by_file: Mapping[str, tuple[tuple[ImportInfo, ImportResult], ...]]
    bindings_by_scope_name: Mapping[
        tuple[str, str], tuple[Binding, ...]
    ] = field(init=False)
    wildcards_by_file: Mapping[str, tuple[ImportInfo, ...]] = field(init=False)
    _wildcard_memo: _WildcardMemo = field(
        init=False, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.by_file, Mapping):
            raise CodeGraphResolutionError("Import environment must be a mapping.")
        paths = tuple(self.by_file)
        if not all(isinstance(path, str) and path for path in paths):
            raise CodeGraphResolutionError(
                "Import environment paths must be nonempty strings."
            )

        by_file: dict[str, tuple[tuple[ImportInfo, ImportResult], ...]] = {}
        binding_buckets: defaultdict[tuple[str, str], list[Binding]] = defaultdict(list)
        wildcard_buckets: defaultdict[str, list[ImportInfo]] = defaultdict(list)
        for path in sorted(paths):
            raw_entries = self.by_file[path]
            if isinstance(raw_entries, (str, bytes)):
                raise CodeGraphResolutionError("Import entries must be a sequence.")
            try:
                entries = tuple(raw_entries)
            except TypeError as exc:
                raise CodeGraphResolutionError(
                    "Import entries must be a sequence."
                ) from exc

            checked: list[tuple[ImportInfo, ImportResult]] = []
            for entry in entries:
                if type(entry) not in (tuple, list) or len(entry) != 2:
                    raise CodeGraphResolutionError(
                        "Import entries must contain ImportInfo and ImportResult pairs."
                    )
                item, result = entry
                if type(item) is not ImportInfo or type(result) is not ImportResult:
                    raise CodeGraphResolutionError(
                        "Import entries contain an invalid value."
                    )
                checked.append((item, result))
                if item.is_wildcard:
                    wildcard_buckets[path].append(item)
                for binding in result.bindings:
                    key = (binding.scope_node_id, binding.local_name)
                    binding_buckets[key].append(binding)
            by_file[path] = tuple(checked)

        bindings_by_scope_name = {
            key: tuple(values) for key, values in sorted(binding_buckets.items())
        }
        wildcards_by_file = {
            path: tuple(values) for path, values in sorted(wildcard_buckets.items())
        }
        object.__setattr__(self, "by_file", MappingProxyType(dict(by_file)))
        object.__setattr__(
            self,
            "bindings_by_scope_name",
            MappingProxyType(bindings_by_scope_name),
        )
        object.__setattr__(
            self,
            "wildcards_by_file",
            MappingProxyType(wildcards_by_file),
        )
        object.__setattr__(self, "_wildcard_memo", _WildcardMemo())

    def _memoized_wildcard(
        self,
        index: GraphIndexes,
        key: tuple[object, ...],
        factory: Callable[[], _MemoValue],
    ) -> _MemoValue:
        return self._wildcard_memo.get_or_compute(index, key, factory)


def import_scope(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> tuple[str, ResolutionFailureReason | None]:
    """Return an import binding scope and an optional conservative barrier."""

    indexed_file = index.files_by_path.get(file.relative_path)
    if indexed_file != file or item not in file.imports:
        raise CodeGraphResolutionError("Import scope evidence is not indexed.")
    file_id = index.file_ids[file.relative_path]

    if _relevant_parse_loss(file, item.location):
        return file_id, ResolutionFailureReason.INCOMPLETE_PARSE

    containing = _containing_node_ids(
        index.symbol_ranges_by_file[file.relative_path].root,
        item.location,
    )

    if not containing:
        return file_id, None

    containing_set = set(containing)
    innermost: list[str] = []
    for candidate in containing:
        if not any(
            _is_ancestor(candidate, other, index.parent_ids)
            for other in containing_set
            if other != candidate
        ):
            innermost.append(candidate)
    if len(innermost) == 1:
        return innermost[0], None
    return file_id, ResolutionFailureReason.AMBIGUOUS


def _containing_node_ids(
    root: SymbolRangeNode | None, location: SourceLocation
) -> list[str]:
    containing: list[str] = []
    pending = [root] if root is not None else []
    while pending:
        node = pending.pop()
        if node.max_end < location.end_byte:
            continue
        if node.start > location.start_byte:
            if node.left is not None:
                pending.append(node.left)
            continue
        if node.end >= location.end_byte:
            containing.append(node.node_id)
        if node.left is not None and node.left.max_end >= location.end_byte:
            pending.append(node.left)
        if node.right is not None and node.right.max_end >= location.end_byte:
            pending.append(node.right)
    return containing


def _is_ancestor(
    possible_ancestor: str,
    node_id: str,
    parent_ids: Mapping[str, str | None],
) -> bool:
    parent_id = parent_ids.get(node_id)
    while parent_id is not None:
        if parent_id == possible_ancestor:
            return True
        parent_id = parent_ids.get(parent_id)
    return False


def _overlaps(first: SourceLocation, second: SourceLocation) -> bool:
    return (
        first.start_byte < second.end_byte
        and second.start_byte < first.end_byte
    )


def _relevant_parse_loss(file: ParsedFile, location: SourceLocation) -> bool:
    if file.status is ParseStatus.FAILED:
        return True
    if file.status is not ParseStatus.PARTIAL:
        return False
    return any(
        issue.location is None or _overlaps(issue.location, location)
        for issue in file.issues
    )


def classify_relevant_parse_loss(
    index: GraphIndexes,
    relative_path: str,
    location: SourceLocation,
    resolution: Resolution,
) -> Resolution:
    """Promote an otherwise definitive miss when relevant evidence was lost."""

    file = index.files_by_path[relative_path]
    if (
        resolution.reason is ResolutionFailureReason.NOT_FOUND
        and _relevant_parse_loss(file, location)
    ):
        return choose_resolution((), incomplete=True)
    return resolution


def choose_resolution(
    candidates: tuple[str, ...],
    *,
    unsupported: bool = False,
    incomplete: bool = False,
    external: bool = False,
) -> Resolution:
    """Select one target or the strongest applicable failure outcome."""

    if unsupported:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if incomplete:
        return Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE)
    if isinstance(candidates, (str, bytes)):
        raise CodeGraphResolutionError("Resolution candidates must be a sequence.")
    try:
        supplied_ids = tuple(candidates)
    except TypeError as exc:
        raise CodeGraphResolutionError(
            "Resolution candidates must be a sequence."
        ) from exc
    if not all(isinstance(candidate, str) and candidate for candidate in supplied_ids):
        raise CodeGraphResolutionError(
            "Resolution candidates must be nonempty strings."
        )
    ids = tuple(sorted(set(supplied_ids)))
    if len(ids) > 1:
        return Resolution(None, ResolutionFailureReason.AMBIGUOUS, ids)
    if len(ids) == 1:
        return Resolution(ids[0], None)
    reason = (
        ResolutionFailureReason.EXTERNAL
        if external
        else ResolutionFailureReason.NOT_FOUND
    )
    return Resolution(None, reason)
