"""Resolve Java import and declared-type evidence from parser metadata."""

from backend.code_graph.indexes import GraphIndexes
from backend.code_graph.models import (
    GraphNodeKind,
    ReferenceKind,
    ResolutionFailureReason,
)
from backend.code_graph.references import Reference
from backend.code_graph.resolvers.base import (
    Binding,
    ImportEnvironment,
    ImportResult,
    Resolution,
    choose_resolution,
    import_scope,
)
from backend.code_parser.models import (
    CallKind,
    ImportInfo,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
    SymbolKind,
)


_TYPE_KINDS = (SymbolKind.CLASS, SymbolKind.INTERFACE, SymbolKind.ENUM)
_CALLABLE_KINDS = (
    SymbolKind.FUNCTION,
    SymbolKind.METHOD,
    SymbolKind.CONSTRUCTOR,
)
_STATIC_MEMBER_KINDS = (
    SymbolKind.CLASS,
    SymbolKind.INTERFACE,
    SymbolKind.ENUM,
    SymbolKind.METHOD,
    SymbolKind.CONSTANT,
)
_JAVA_KEYWORDS = frozenset(
    "abstract assert boolean break byte case catch char class const continue "
    "default do double else enum extends final finally float for goto if "
    "implements import instanceof int interface long native new package private "
    "protected public return short static strictfp super switch synchronized "
    "this throw throws transient try void volatile while true false null _ "
    "record sealed permits non-sealed var yield".split()
)


def _is_identifier(value: str) -> bool:
    return (
        bool(value)
        and value not in _JAVA_KEYWORDS
        and value.replace("$", "_").isidentifier()
    )


def _is_dotted_identifier(value: str) -> bool:
    return bool(value) and all(_is_identifier(part) for part in value.split("."))


def _qualified_types(
    index: GraphIndexes,
    qualified_name: str,
    kinds: tuple[SymbolKind, ...] = _TYPE_KINDS,
) -> tuple[str, ...]:
    return tuple(
        node_id
        for node_id in index.by_qualified_name.get(qualified_name, ())
        if (
            (node := index.nodes_by_id[node_id]).language is ParsedLanguage.JAVA
            and node.node_kind is GraphNodeKind.SYMBOL
            and node.symbol_kind in kinds
        )
    )


def _file_ids_for_symbols(
    index: GraphIndexes, symbol_ids: tuple[str, ...]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                index.file_ids[index.nodes_by_id[node_id].relative_path]
                for node_id in symbol_ids
            }
        )
    )


def _static_members(
    index: GraphIndexes, owner_ids: tuple[str, ...], member_name: str
) -> tuple[str, ...]:
    return tuple(
        child_id
        for owner_id in owner_ids
        for child_id in index.children.get(owner_id, ())
        if (
            (child := index.nodes_by_id[child_id]).language is ParsedLanguage.JAVA
            and child.name == member_name
            and child.symbol_kind in _STATIC_MEMBER_KINDS
            and "static" in index.symbols_by_id[child_id].modifiers
        )
    )


def _incomplete_files(index: GraphIndexes, file_ids: tuple[str, ...]) -> bool:
    paths = {index.nodes_by_id[file_id].relative_path for file_id in file_ids}
    return any(
        index.files_by_path[path].status in (ParseStatus.PARTIAL, ParseStatus.FAILED)
        for path in paths
    )


def _blocked_binding(
    local_name: str,
    scope_node_id: str,
    reason: ResolutionFailureReason,
) -> Binding:
    return Binding(local_name, scope_node_id, "blocked", Resolution(None, reason))


def resolve_import(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> ImportResult:
    """Resolve one Java import record without consulting directory layout."""

    scope_node_id, scope_reason = import_scope(index, file, item)
    if (
        file.language is not ParsedLanguage.JAVA
        or not _is_dotted_identifier(item.module)
        or item.bindings
        or item.modifiers not in ((), ("static",))
    ):
        return ImportResult(
            Resolution(None, ResolutionFailureReason.UNSUPPORTED), ()
        )

    is_static = item.modifiers == ("static",)
    if item.is_wildcard:
        if is_static:
            type_ids = _qualified_types(index, item.module)
            file_ids = _file_ids_for_symbols(index, type_ids)
            incomplete = not type_ids and _qualified_package_has_partial(
                index, item.module
            )
        else:
            file_ids = index.java_packages.get(item.module, ())
            incomplete = False
        return ImportResult(
            choose_resolution(
                file_ids, incomplete=incomplete, external=not file_ids
            ),
            (),
        )

    if is_static:
        owner_name, separator, member_name = item.module.rpartition(".")
        if not separator or not _is_identifier(member_name):
            return ImportResult(
                Resolution(None, ResolutionFailureReason.UNSUPPORTED), ()
            )
        owner_ids = _qualified_types(index, owner_name)
        owner_file_ids = _file_ids_for_symbols(index, owner_ids)
        member_ids = _static_members(index, owner_ids, member_name)
        incomplete = (
            bool(owner_ids)
            and not member_ids
            and _incomplete_files(index, owner_file_ids)
        ) or (
            not owner_ids
            and _qualified_package_has_partial(index, owner_name)
        )
        member_resolution = (
            Resolution(
                None,
                ResolutionFailureReason.AMBIGUOUS,
                tuple(sorted(set(member_ids))),
            )
            if len(owner_ids) > 1
            else choose_resolution(
                member_ids, incomplete=incomplete, external=not owner_ids
            )
        )
        file_resolution = (
            choose_resolution(owner_file_ids)
            if len(owner_ids) > 1
            else choose_resolution(
                _file_ids_for_symbols(index, member_ids),
                incomplete=incomplete,
                external=not owner_ids,
            )
        )
        binding = (
            _blocked_binding(member_name, scope_node_id, scope_reason)
            if scope_reason is not None
            else Binding(member_name, scope_node_id, "symbol", member_resolution)
        )
        return ImportResult(file_resolution, (binding,))

    type_ids = _qualified_types(index, item.module)
    file_ids = _file_ids_for_symbols(index, type_ids)
    incomplete = not type_ids and _qualified_package_has_partial(index, item.module)
    type_resolution = choose_resolution(
        type_ids, incomplete=incomplete, external=not type_ids
    )
    file_resolution = choose_resolution(
        file_ids, incomplete=incomplete, external=not file_ids
    )
    local_name = item.module.rsplit(".", 1)[-1]
    binding = (
        _blocked_binding(local_name, scope_node_id, scope_reason)
        if scope_reason is not None
        else Binding(local_name, scope_node_id, "symbol", type_resolution)
    )
    return ImportResult(file_resolution, (binding,))


def _package_has_partial_file(index: GraphIndexes, package: str) -> bool:
    return any(
        index.files_by_path[index.nodes_by_id[file_id].relative_path].status
        is ParseStatus.PARTIAL
        for file_id in index.java_packages.get(package, ())
    )


def _qualified_package_has_partial(
    index: GraphIndexes, qualified_name: str
) -> bool:
    package, separator, _ = qualified_name.rpartition(".")
    return bool(separator) and _package_has_partial_file(index, package)


def _source_package(index: GraphIndexes, source_id: str) -> str:
    top_level = source_id
    while (parent_id := index.parent_ids.get(top_level)) is not None:
        top_level = parent_id
    node = index.nodes_by_id[top_level]
    suffix = f".{node.name}"
    qualified = node.qualified_name or ""
    return qualified[: -len(suffix)] if qualified.endswith(suffix) else ""


def _same_file_type_tiers(
    index: GraphIndexes, path: str, source_id: str, name: str
) -> tuple[tuple[str, ...], ...]:
    candidates = tuple(
        node_id
        for node_id in index.by_file_name.get((path, name), ())
        if (
            (node := index.nodes_by_id[node_id]).language is ParsedLanguage.JAVA
            and node.symbol_kind in _TYPE_KINDS
        )
    )
    tiers: list[tuple[str, ...]] = []
    parent_id = index.parent_ids.get(source_id)
    while parent_id is not None:
        tier = tuple(
            node_id
            for node_id in candidates
            if index.parent_ids.get(node_id) == parent_id
        )
        if tier:
            tiers.append(tier)
        parent_id = index.parent_ids.get(parent_id)
    top_level = tuple(
        node_id
        for node_id in candidates
        if index.parent_ids.get(node_id) is None
    )
    if top_level:
        tiers.append(top_level)
    return tuple(tiers)


def _eligible_ids(
    index: GraphIndexes,
    candidates: tuple[str, ...],
    required_kinds: tuple[SymbolKind, ...],
) -> tuple[str, ...]:
    return tuple(
        node_id
        for node_id in candidates
        if (
            (node := index.nodes_by_id.get(node_id)) is not None
            and node.language is ParsedLanguage.JAVA
            and node.node_kind is GraphNodeKind.SYMBOL
            and node.symbol_kind in required_kinds
        )
    )


def _visible_type_outcome(
    index: GraphIndexes,
    candidates: tuple[str, ...],
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution | None:
    if not candidates:
        return None
    eligible = _eligible_ids(index, candidates, required_kinds)
    if len(eligible) != len(candidates):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return choose_resolution(eligible)


def _binding_outcome(
    index: GraphIndexes,
    binding: Binding,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution:
    resolution = binding.resolution
    if binding.mode == "blocked" or resolution.reason is not None:
        if (
            resolution.reason is ResolutionFailureReason.AMBIGUOUS
            and resolution.candidate_node_ids
        ):
            eligible = _eligible_ids(
                index, resolution.candidate_node_ids, required_kinds
            )
            if len(eligible) != len(resolution.candidate_node_ids):
                return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
            return Resolution(
                None, ResolutionFailureReason.AMBIGUOUS, eligible
            )
        return resolution
    target_id = resolution.target_node_id
    if target_id is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if not _eligible_ids(index, (target_id,), required_kinds):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return resolution


def _strong_outcome(outcomes: tuple[Resolution, ...]) -> Resolution:
    for reason in (
        ResolutionFailureReason.UNSUPPORTED,
        ResolutionFailureReason.INCOMPLETE_PARSE,
        ResolutionFailureReason.AMBIGUOUS,
    ):
        matching = tuple(outcome for outcome in outcomes if outcome.reason is reason)
        if matching:
            candidates = tuple(sorted({
                candidate
                for outcome in matching
                for candidate in outcome.candidate_node_ids
            }))
            return Resolution(None, reason, candidates) if candidates else Resolution(None, reason)
    targets = tuple(
        outcome.target_node_id
        for outcome in outcomes
        if outcome.target_node_id is not None
    )
    if targets:
        if any(outcome.reason is ResolutionFailureReason.EXTERNAL for outcome in outcomes):
            return Resolution(
                None, ResolutionFailureReason.AMBIGUOUS, tuple(sorted(set(targets)))
            )
        return choose_resolution(targets)
    if any(outcome.reason is ResolutionFailureReason.EXTERNAL for outcome in outcomes):
        return Resolution(None, ResolutionFailureReason.EXTERNAL)
    return Resolution(None, ResolutionFailureReason.NOT_FOUND)


def _explicit_binding_resolution(
    index: GraphIndexes,
    environment: ImportEnvironment,
    path: str,
    name: str,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution | None:
    file_id = index.file_ids[path]
    bindings = environment.bindings_by_scope_name.get((file_id, name), ())
    if not bindings:
        return None
    return _strong_outcome(tuple(
        _binding_outcome(index, binding, required_kinds) for binding in bindings
    ))


def _wildcard_type_candidates(
    index: GraphIndexes,
    items: tuple[ImportInfo, ...],
    name: str,
    required_kinds: tuple[SymbolKind, ...],
) -> tuple[tuple[str, ...], bool, bool, bool, bool]:
    candidates: list[str] = []
    incomplete = False
    external = False
    unsupported = False
    ambiguous = False
    for item in items:
        if not _is_dotted_identifier(item.module) or item.modifiers not in (
            (),
            ("static",),
        ):
            unsupported = True
            continue
        if item.modifiers == ("static",):
            owner_ids = _qualified_types(index, item.module)
            ambiguous = ambiguous or len(owner_ids) > 1
            owner_files = _file_ids_for_symbols(index, owner_ids)
            members = _static_members(index, owner_ids, name)
            type_members = tuple(
                member_id
                for member_id in members
                if index.nodes_by_id[member_id].symbol_kind in _TYPE_KINDS
            )
            typed_members = _eligible_ids(
                index, type_members, required_kinds
            )
            unsupported = unsupported or len(typed_members) != len(type_members)
            candidates.extend(typed_members)
            incomplete = incomplete or (
                (
                    bool(owner_ids)
                    and not type_members
                    and _incomplete_files(index, owner_files)
                )
                or (
                    not owner_ids
                    and _qualified_package_has_partial(index, item.module)
                )
            )
            external = external or not owner_ids
            continue

        qualified = f"{item.module}.{name}"
        all_types = _qualified_types(index, qualified)
        typed = _eligible_ids(index, all_types, required_kinds)
        unsupported = unsupported or len(typed) != len(all_types)
        candidates.extend(typed)
        package_files = index.java_packages.get(item.module, ())
        incomplete = incomplete or (
            not all_types and _package_has_partial_file(index, item.module)
        )
        external = external or not package_files
    return tuple(candidates), unsupported, incomplete, external, ambiguous


def _required_target_kind(
    reference: Reference, source_kind: SymbolKind
) -> SymbolKind | None:
    if reference.reference_kind is ReferenceKind.INHERITS:
        return (
            source_kind
            if source_kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
            else None
        )
    if reference.reference_kind is ReferenceKind.IMPLEMENTS:
        return (
            SymbolKind.INTERFACE
            if source_kind in (SymbolKind.CLASS, SymbolKind.ENUM)
            else None
        )
    return None


def _children_named(
    index: GraphIndexes,
    owner_ids: tuple[str, ...],
    name: str,
    kinds: tuple[SymbolKind, ...],
    *,
    require_static: bool = False,
) -> tuple[str, ...]:
    return tuple(
        child_id
        for owner_id in owner_ids
        for child_id in index.children.get(owner_id, ())
        if (
            (child := index.nodes_by_id[child_id]).language is ParsedLanguage.JAVA
            and child.node_kind is GraphNodeKind.SYMBOL
            and child.name == name
            and child.symbol_kind in kinds
            and (
                not require_static
                or "static" in index.symbols_by_id[child_id].modifiers
            )
        )
    )


def _missing_children_resolution(
    index: GraphIndexes, owner_ids: tuple[str, ...]
) -> Resolution:
    incomplete = any(
        index.files_by_path[index.nodes_by_id[owner_id].relative_path].status
        is ParseStatus.PARTIAL
        for owner_id in owner_ids
    )
    return choose_resolution((), incomplete=incomplete)


def _children_resolution(
    index: GraphIndexes,
    owner_resolution: Resolution,
    candidate_ids: tuple[str, ...],
) -> Resolution:
    if owner_resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        return Resolution(
            None,
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted(set(candidate_ids))),
        )
    if owner_resolution.reason is not None:
        return owner_resolution
    if candidate_ids:
        return choose_resolution(candidate_ids)
    target_id = owner_resolution.target_node_id
    if target_id is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return _missing_children_resolution(index, (target_id,))


def _member_resolution(
    index: GraphIndexes,
    owner_resolution: Resolution,
    member_name: str,
    *,
    require_static: bool,
) -> Resolution:
    owner_ids = (
        owner_resolution.candidate_node_ids
        if owner_resolution.reason is ResolutionFailureReason.AMBIGUOUS
        else (
            (owner_resolution.target_node_id,)
            if owner_resolution.target_node_id is not None
            else ()
        )
    )
    candidates = _children_named(
        index,
        owner_ids,
        member_name,
        _CALLABLE_KINDS,
        require_static=require_static,
    )
    return _children_resolution(index, owner_resolution, candidates)


def _constructor_resolution(
    index: GraphIndexes, owner_resolution: Resolution
) -> Resolution:
    owner_ids = (
        owner_resolution.candidate_node_ids
        if owner_resolution.reason is ResolutionFailureReason.AMBIGUOUS
        else (
            (owner_resolution.target_node_id,)
            if owner_resolution.target_node_id is not None
            else ()
        )
    )
    constructors = tuple(
        child_id
        for owner_id in owner_ids
        for child_id in index.children.get(owner_id, ())
        if (
            (child := index.nodes_by_id[child_id]).language is ParsedLanguage.JAVA
            and child.node_kind is GraphNodeKind.SYMBOL
            and child.symbol_kind is SymbolKind.CONSTRUCTOR
        )
    )
    return _children_resolution(index, owner_resolution, constructors)


def _current_type_id(index: GraphIndexes, source_id: str) -> str | None:
    current = index.parent_ids.get(source_id)
    while current is not None:
        if index.nodes_by_id[current].symbol_kind in _TYPE_KINDS:
            return current
        current = index.parent_ids.get(current)
    return None


def _parameter_conflict(index: GraphIndexes, source_id: str, name: str) -> bool:
    return any(
        parameter.name == name
        for parameter in index.symbols_by_id[source_id].parameters
    )


def _resolve_type_name(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
    name: str,
    required_kinds: tuple[SymbolKind, ...],
    *,
    excluded_id: str | None = None,
) -> Resolution:
    if "." in name:
        candidates = tuple(
            candidate
            for candidate in _qualified_types(index, name)
            if candidate != excluded_id
        )
        outcome = _visible_type_outcome(index, candidates, required_kinds)
        if outcome is not None:
            return outcome
        package, _, _ = name.rpartition(".")
        return choose_resolution(
            (),
            incomplete=bool(package)
            and _package_has_partial_file(index, package),
        )

    for tier in _same_file_type_tiers(
        index, reference.relative_path, reference.source_node_id, name
    ):
        candidates = tuple(
            candidate for candidate in tier if candidate != excluded_id
        )
        outcome = _visible_type_outcome(index, candidates, required_kinds)
        if outcome is not None:
            return outcome

    binding = _explicit_binding_resolution(
        index, environment, reference.relative_path, name, required_kinds
    )
    if binding is not None:
        return binding

    package = _source_package(index, reference.source_node_id)
    if package:
        candidates = tuple(
            candidate
            for candidate in _qualified_types(index, f"{package}.{name}")
            if candidate != excluded_id
        )
        outcome = _visible_type_outcome(index, candidates, required_kinds)
        if outcome is not None:
            return outcome
        if _package_has_partial_file(index, package):
            return Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE)

    wildcard_items = environment.wildcards_by_file.get(
        reference.relative_path, ()
    )
    if wildcard_items:
        key = (
            id(index),
            ParsedLanguage.JAVA,
            reference.relative_path,
            index.file_ids[reference.relative_path],
            wildcard_items,
            name,
            reference.reference_kind,
            required_kinds,
        )
        candidates, unsupported, incomplete, external, ambiguous = (
            environment._memoized_wildcard(
                index,
                key,
                lambda: _wildcard_type_candidates(
                    index, wildcard_items, name, required_kinds
                ),
            )
        )
        if ambiguous and not unsupported and not incomplete:
            return Resolution(
                None,
                ResolutionFailureReason.AMBIGUOUS,
                tuple(sorted(set(candidates))),
            )
        return choose_resolution(
            candidates,
            unsupported=unsupported,
            incomplete=incomplete,
            external=external and not candidates,
        )
    return Resolution(None, ResolutionFailureReason.NOT_FOUND)


def _explicit_call_resolution(
    index: GraphIndexes,
    environment: ImportEnvironment,
    path: str,
    name: str,
    call_kind: CallKind,
) -> Resolution | None:
    file_id = index.file_ids[path]
    bindings = environment.bindings_by_scope_name.get((file_id, name), ())
    if not bindings:
        return None
    if call_kind is CallKind.CONSTRUCTOR:
        outcomes = tuple(
            _constructor_resolution(
                index, _binding_outcome(index, binding, _TYPE_KINDS)
            )
            for binding in bindings
        )
    else:
        outcomes = tuple(
            _binding_outcome(index, binding, _CALLABLE_KINDS)
            for binding in bindings
        )
    return _strong_outcome(outcomes)


def _wildcard_static_call_candidates(
    index: GraphIndexes,
    items: tuple[ImportInfo, ...],
    name: str,
) -> tuple[tuple[str, ...], bool, bool, bool, bool]:
    candidates: list[str] = []
    incomplete = False
    external = False
    unsupported = False
    ambiguous = False
    for item in items:
        if item.modifiers != ("static",):
            continue
        if not _is_dotted_identifier(item.module):
            unsupported = True
            continue
        owner_ids = _qualified_types(index, item.module)
        ambiguous = ambiguous or len(owner_ids) > 1
        owner_files = _file_ids_for_symbols(index, owner_ids)
        members = _static_members(index, owner_ids, name)
        callable_members = _eligible_ids(index, members, _CALLABLE_KINDS)
        unsupported = unsupported or len(callable_members) != len(members)
        candidates.extend(callable_members)
        incomplete = incomplete or (
            (
                bool(owner_ids)
                and not members
                and _incomplete_files(index, owner_files)
            )
            or (
                not owner_ids
                and _qualified_package_has_partial(index, item.module)
            )
        )
        external = external or not owner_ids
    return tuple(candidates), unsupported, incomplete, external, ambiguous


def _wildcard_static_call_resolution(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
    name: str,
) -> Resolution | None:
    wildcard_items = environment.wildcards_by_file.get(
        reference.relative_path, ()
    )
    static_items = tuple(
        item for item in wildcard_items if item.modifiers == ("static",)
    )
    if not static_items:
        return None
    key = (
        id(index),
        ParsedLanguage.JAVA,
        reference.relative_path,
        index.file_ids[reference.relative_path],
        static_items,
        name,
        "static-call",
        _CALLABLE_KINDS,
    )
    candidates, unsupported, incomplete, external, ambiguous = (
        environment._memoized_wildcard(
            index,
            key,
            lambda: _wildcard_static_call_candidates(
                index, static_items, name
            ),
        )
    )
    if ambiguous and not unsupported and not incomplete:
        return Resolution(
            None,
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted(set(candidates))),
        )
    return choose_resolution(
        candidates,
        unsupported=unsupported,
        incomplete=incomplete,
        external=external and not candidates,
    )


def _resolve_call(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    if reference.call_kind not in (CallKind.CALL, CallKind.CONSTRUCTOR):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    raw_target = reference.raw_target
    if raw_target.startswith("this."):
        member_name = raw_target.removeprefix("this.")
        valid_target = _is_identifier(member_name) and "." not in member_name
    else:
        valid_target = _is_dotted_identifier(raw_target)
    if not valid_target:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    source_id = reference.source_node_id
    source = index.nodes_by_id[source_id]
    if source.symbol_kind not in _CALLABLE_KINDS:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    current_type = _current_type_id(index, source_id)
    if current_type is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    if reference.call_kind is CallKind.CONSTRUCTOR:
        if raw_target.startswith("this."):
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        return _constructor_resolution(
            index,
            _resolve_type_name(
                index,
                environment,
                reference,
                raw_target,
                _TYPE_KINDS,
            ),
        )

    if raw_target.startswith("this."):
        return _member_resolution(
            index,
            Resolution(current_type, None),
            raw_target.removeprefix("this."),
            require_static=False,
        )

    parts = tuple(raw_target.split("."))
    if len(parts) == 1:
        name = parts[0]
        if _parameter_conflict(index, source_id, name):
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        current = _member_resolution(
            index,
            Resolution(current_type, None),
            name,
            require_static=False,
        )
        if current.reason is not ResolutionFailureReason.NOT_FOUND:
            return current
        binding = _explicit_call_resolution(
            index, environment, reference.relative_path, name, CallKind.CALL
        )
        if binding is not None:
            return binding
        wildcard = _wildcard_static_call_resolution(
            index, environment, reference, name
        )
        return (
            wildcard
            if wildcard is not None
            else Resolution(None, ResolutionFailureReason.NOT_FOUND)
        )

    if _parameter_conflict(index, source_id, parts[0]):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    owner = _resolve_type_name(
        index,
        environment,
        reference,
        ".".join(parts[:-1]),
        _TYPE_KINDS,
    )
    if owner.reason is ResolutionFailureReason.NOT_FOUND:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return _member_resolution(
        index, owner, parts[-1], require_static=True
    )


def resolve_reference(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    """Resolve supported Java call and declared-type references."""

    if (
        reference.language is not ParsedLanguage.JAVA
        or reference.relative_path not in index.files_by_path
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if reference.blocked_reason is not None:
        return Resolution(None, reference.blocked_reason)
    if reference.source_node_id is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    source = index.nodes_by_id.get(reference.source_node_id)
    if (
        source is None
        or source.node_kind is not GraphNodeKind.SYMBOL
        or source.language is not ParsedLanguage.JAVA
        or source.relative_path != reference.relative_path
        or source.symbol_kind is None
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    if reference.reference_kind is ReferenceKind.CALL:
        return _resolve_call(index, environment, reference)
    if not _is_dotted_identifier(reference.raw_target):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    required_kind = _required_target_kind(reference, source.symbol_kind)
    if required_kind is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return _resolve_type_name(
        index,
        environment,
        reference,
        reference.raw_target,
        (required_kind,),
        excluded_id=reference.source_node_id,
    )
