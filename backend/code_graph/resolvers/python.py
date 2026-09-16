"""Resolve Python import evidence against immutable graph indexes."""

import keyword

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


_CALLABLE_KINDS = (
    SymbolKind.FUNCTION,
    SymbolKind.METHOD,
    SymbolKind.CONSTRUCTOR,
)
_SPECIAL_DYNAMIC_CALLS = frozenset(("getattr", "globals"))


def _is_identifier(value: str) -> bool:
    return value.isidentifier() and not keyword.iskeyword(value)


def _is_dotted_identifier(value: str) -> bool:
    return all(_is_identifier(part) for part in value.split("."))


def _module_candidates(
    index: GraphIndexes, file: ParsedFile, module: str
) -> tuple[tuple[str, ...], bool, bool]:
    level = len(module) - len(module.lstrip("."))
    tail_text = module[level:]
    if tail_text and not _is_dotted_identifier(tail_text):
        return (), False, True

    if not level:
        return index.python_modules.get(module, ()), True, False

    parts = file.relative_path.split("/")[:-1]
    if not parts or level - 1 > len(parts):
        return (), False, False
    anchor = parts[: len(parts) - (level - 1)]
    tail = tail_text.split(".") if tail_text else []
    key = ".".join(anchor + tail)
    return index.python_modules.get(key, ()), False, False


def _member_resolution(
    index: GraphIndexes,
    module_resolution: Resolution,
    imported_name: str,
) -> Resolution:
    if not _is_identifier(imported_name):
        return choose_resolution((), unsupported=True)
    if module_resolution.target_node_id is None:
        if module_resolution.reason is ResolutionFailureReason.AMBIGUOUS:
            return Resolution(None, ResolutionFailureReason.AMBIGUOUS)
        return module_resolution

    target_id = module_resolution.target_node_id
    target = index.nodes_by_id[target_id]
    candidate_ids = tuple(
        node_id
        for node_id in index.by_file_name.get(
            (target.relative_path, imported_name), ()
        )
        if index.parent_ids.get(node_id) is None
    )
    incomplete = (
        not candidate_ids
        and index.files_by_path[target.relative_path].status
        in (ParseStatus.PARTIAL, ParseStatus.FAILED)
    )
    return choose_resolution(candidate_ids, incomplete=incomplete)


def _is_indistinguishable_form(item: ImportInfo) -> bool:
    return (
        len(item.bindings) == 1
        and item.bindings[0].imported_name == item.module
        and "." not in item.module
    )


def _binding_local_name(imported_name: str, alias: str | None) -> str:
    return alias if alias is not None else imported_name.split(".", 1)[0]


def resolve_import(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> ImportResult:
    """Resolve one Python import record."""

    scope_node_id, scope_reason = import_scope(index, file, item)
    candidate_ids, is_absolute, unsupported_module = _module_candidates(
        index, file, item.module
    )
    module_resolution = choose_resolution(
        candidate_ids,
        unsupported=unsupported_module,
        external=is_absolute and not candidate_ids,
    )

    bindings: list[Binding] = []
    indistinguishable = _is_indistinguishable_form(item)
    for imported in item.bindings:
        local_name = _binding_local_name(imported.imported_name, imported.alias)
        may_be_direct = (
            len(item.bindings) == 1
            and imported.imported_name == item.module
            and not item.module.startswith(".")
        )
        imported_name_valid = (
            _is_dotted_identifier(imported.imported_name)
            if may_be_direct
            else _is_identifier(imported.imported_name)
        )
        alias_valid = imported.alias is None or _is_identifier(imported.alias)
        if not imported_name_valid or not alias_valid:
            bindings.append(
                Binding(
                    local_name,
                    scope_node_id,
                    "blocked",
                    Resolution(None, ResolutionFailureReason.UNSUPPORTED),
                )
            )
            continue

        if indistinguishable:
            symbol_interpretation = _member_resolution(
                index, module_resolution, imported.imported_name
            )
            common_resolution = (
                module_resolution
                if module_resolution == symbol_interpretation
                else Resolution(None, ResolutionFailureReason.UNSUPPORTED)
            )
            bindings.append(
                Binding(local_name, scope_node_id, "blocked", common_resolution)
            )
            continue

        is_direct_module = (
            may_be_direct
            and "." in imported.imported_name
        )
        if is_direct_module:
            mode = "module"
            binding_resolution = module_resolution
        else:
            mode = "symbol"
            binding_resolution = _member_resolution(
                index, module_resolution, imported.imported_name
            )

        if item.modifiers or scope_reason is not None:
            mode = "blocked"
            binding_resolution = Resolution(
                None,
                ResolutionFailureReason.UNSUPPORTED
                if item.modifiers
                else scope_reason,
            )
        bindings.append(
            Binding(local_name, scope_node_id, mode, binding_resolution)
        )

    return ImportResult(module_resolution, tuple(bindings))


def resolve_reference(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    """Resolve one Python call or declared-base reference."""

    if (
        reference.language is not ParsedLanguage.PYTHON
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
        or source.relative_path != reference.relative_path
        or source.language is not ParsedLanguage.PYTHON
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    if reference.reference_kind is ReferenceKind.CALL:
        return _resolve_call(index, environment, reference)
    if reference.reference_kind is ReferenceKind.INHERITS:
        return _resolve_base(index, environment, reference)
    return Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def _callable_scope_ids(index: GraphIndexes, source_id: str) -> tuple[str, ...]:
    scopes: list[str] = []
    current: str | None = source_id
    while current is not None:
        node = index.nodes_by_id[current]
        if node.symbol_kind in _CALLABLE_KINDS:
            scopes.append(current)
        current = index.parent_ids.get(current)
    return tuple(scopes)


def _parameter_conflict(
    index: GraphIndexes, scopes: tuple[str, ...], name: str
) -> bool:
    return any(
        any(
            parameter.name == name
            for parameter in index.symbols_by_id[scope].parameters
        )
        for scope in scopes
    )


def _eligible_ids(
    index: GraphIndexes,
    candidate_ids: tuple[str, ...],
    kinds: tuple[SymbolKind, ...],
) -> tuple[str, ...]:
    return tuple(
        candidate_id
        for candidate_id in candidate_ids
        if (
            (node := index.nodes_by_id[candidate_id]).node_kind
            is GraphNodeKind.SYMBOL
            and node.language is ParsedLanguage.PYTHON
            and node.symbol_kind in kinds
        )
    )


def _children_named(
    index: GraphIndexes,
    parent_id: str,
    name: str,
    kinds: tuple[SymbolKind, ...],
) -> tuple[str, ...]:
    return tuple(
        child_id
        for child_id in index.children.get(parent_id, ())
        if (
            (child := index.nodes_by_id[child_id]).name == name
            and child.language is ParsedLanguage.PYTHON
            and child.symbol_kind in kinds
        )
    )


def _top_level_named(
    index: GraphIndexes,
    path: str,
    name: str,
    kinds: tuple[SymbolKind, ...],
) -> tuple[str, ...]:
    return tuple(
        candidate_id
        for candidate_id in index.by_file_name.get((path, name), ())
        if (
            index.parent_ids.get(candidate_id) is None
            and index.nodes_by_id[candidate_id].language
            is ParsedLanguage.PYTHON
            and index.nodes_by_id[candidate_id].symbol_kind in kinds
        )
    )


def _missing_member_resolution(
    index: GraphIndexes, owner_ids: tuple[str, ...]
) -> Resolution:
    incomplete = any(
        index.files_by_path[index.nodes_by_id[owner_id].relative_path].status
        in (ParseStatus.PARTIAL, ParseStatus.FAILED)
        for owner_id in owner_ids
    )
    return choose_resolution((), incomplete=incomplete)


def _class_member_resolution(
    index: GraphIndexes,
    class_ids: tuple[str, ...],
    member_name: str,
    kinds: tuple[SymbolKind, ...],
) -> Resolution:
    members = tuple(
        member_id
        for class_id in class_ids
        for member_id in _children_named(index, class_id, member_name, kinds)
    )
    if len(class_ids) > 1 and len(set(members)) <= 1:
        return Resolution(None, ResolutionFailureReason.AMBIGUOUS)
    if members:
        return choose_resolution(members)
    return _missing_member_resolution(index, class_ids)


def _constructor_resolution(
    index: GraphIndexes, class_ids: tuple[str, ...]
) -> Resolution:
    return _class_member_resolution(
        index,
        class_ids,
        "__init__",
        (SymbolKind.CONSTRUCTOR,),
    )


def _strong_binding_failure(
    outcomes: tuple[Resolution, ...]
) -> Resolution | None:
    for reason in (
        ResolutionFailureReason.UNSUPPORTED,
        ResolutionFailureReason.INCOMPLETE_PARSE,
        ResolutionFailureReason.AMBIGUOUS,
    ):
        matching = tuple(item for item in outcomes if item.reason is reason)
        if not matching:
            continue
        candidates = tuple(
            sorted(
                {
                    candidate
                    for item in matching
                    for candidate in item.candidate_node_ids
                }
            )
        )
        if reason is ResolutionFailureReason.AMBIGUOUS and len(candidates) > 1:
            return Resolution(None, reason, candidates)
        return Resolution(None, reason)
    return None


def _binding_target_resolution(
    index: GraphIndexes,
    binding: Binding,
    *,
    call_kind: CallKind | None,
    member_name: str | None,
    required_kind: SymbolKind | None = None,
) -> Resolution:
    resolution = binding.resolution
    if binding.mode == "blocked":
        return resolution
    if (
        resolution.reason is ResolutionFailureReason.AMBIGUOUS
        and resolution.candidate_node_ids
    ):
        class_ids = _eligible_ids(
            index,
            resolution.candidate_node_ids,
            (SymbolKind.CLASS,),
        )
        if member_name is not None and len(class_ids) == len(
            resolution.candidate_node_ids
        ):
            kinds = (
                (SymbolKind.CLASS,)
                if required_kind is SymbolKind.CLASS
                else _CALLABLE_KINDS
            )
            return _class_member_resolution(
                index, class_ids, member_name, kinds
            )
        if call_kind is CallKind.CONSTRUCTOR and len(class_ids) == len(
            resolution.candidate_node_ids
        ):
            return _constructor_resolution(index, class_ids)
        if required_kind is SymbolKind.CLASS and len(class_ids) == len(
            resolution.candidate_node_ids
        ):
            return choose_resolution(class_ids)
        callable_ids = _eligible_ids(
            index, resolution.candidate_node_ids, _CALLABLE_KINDS
        )
        if call_kind is CallKind.CALL and len(callable_ids) == len(
            resolution.candidate_node_ids
        ):
            return choose_resolution(callable_ids)
        return Resolution(None, ResolutionFailureReason.AMBIGUOUS)
    if resolution.reason is not None:
        return resolution

    target_id = resolution.target_node_id
    if target_id is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    target = index.nodes_by_id[target_id]

    if binding.mode == "module":
        if member_name is None:
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        if call_kind is CallKind.CONSTRUCTOR:
            classes = _top_level_named(
                index,
                target.relative_path,
                member_name,
                (SymbolKind.CLASS,),
            )
            return (
                _constructor_resolution(index, classes)
                if classes
                else _missing_member_resolution(index, (target_id,))
            )
        kinds = (
            (SymbolKind.CLASS,)
            if required_kind is SymbolKind.CLASS
            else _CALLABLE_KINDS
        )
        candidates = _top_level_named(
            index, target.relative_path, member_name, kinds
        )
        if candidates:
            return choose_resolution(candidates)
        return _missing_member_resolution(index, (target_id,))

    if target.node_kind is not GraphNodeKind.SYMBOL:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if member_name is not None:
        if target.symbol_kind is not SymbolKind.CLASS:
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        kinds = (
            (SymbolKind.CLASS,)
            if required_kind is SymbolKind.CLASS
            else _CALLABLE_KINDS
        )
        return _class_member_resolution(index, (target_id,), member_name, kinds)
    if required_kind is not None:
        return (
            resolution
            if target.symbol_kind is required_kind
            else Resolution(None, ResolutionFailureReason.NOT_FOUND)
        )
    if call_kind is CallKind.CONSTRUCTOR:
        return (
            _constructor_resolution(index, (target_id,))
            if target.symbol_kind is SymbolKind.CLASS
            else Resolution(None, ResolutionFailureReason.NOT_FOUND)
        )
    return (
        resolution
        if target.symbol_kind in _CALLABLE_KINDS
        else Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    )


def _resolve_bindings(
    index: GraphIndexes,
    environment: ImportEnvironment,
    scopes: tuple[str, ...],
    name: str,
    *,
    call_kind: CallKind | None,
    member_name: str | None = None,
    required_kind: SymbolKind | None = None,
) -> Resolution | None:
    for scope in scopes:
        bindings = environment.bindings_by_scope_name.get((scope, name), ())
        if not bindings:
            continue
        outcomes = tuple(
            _binding_target_resolution(
                index,
                binding,
                call_kind=call_kind,
                member_name=member_name,
                required_kind=required_kind,
            )
            for binding in bindings
        )
        failure = _strong_binding_failure(outcomes)
        if failure is not None:
            return failure
        candidates = tuple(
            outcome.target_node_id
            for outcome in outcomes
            if outcome.target_node_id is not None
        )
        has_external = any(
            outcome.reason is ResolutionFailureReason.EXTERNAL
            for outcome in outcomes
        )
        if candidates and has_external:
            return Resolution(None, ResolutionFailureReason.AMBIGUOUS)
        if candidates:
            return choose_resolution(candidates)
        if has_external:
            return Resolution(None, ResolutionFailureReason.EXTERNAL)
        return Resolution(None, ResolutionFailureReason.NOT_FOUND)
    return None


def _lexical_declaration_resolution(
    index: GraphIndexes,
    scopes: tuple[str, ...],
    name: str,
    call_kind: CallKind,
) -> Resolution | None:
    for scope in scopes:
        scope_node = index.nodes_by_id[scope]
        candidates: list[str] = []
        if call_kind is CallKind.CALL and scope_node.name == name:
            candidates.append(scope)
        if call_kind is CallKind.CALL:
            candidates.extend(
                _children_named(
                    index, scope, name, (SymbolKind.FUNCTION,)
                )
            )
        else:
            classes = _children_named(
                index, scope, name, (SymbolKind.CLASS,)
            )
            if classes:
                return _constructor_resolution(index, classes)
        if candidates:
            return choose_resolution(tuple(candidates))
    return None


def _same_file_call_resolution(
    index: GraphIndexes, reference: Reference, name: str
) -> Resolution | None:
    if reference.call_kind is CallKind.CONSTRUCTOR:
        classes = _top_level_named(
            index,
            reference.relative_path,
            name,
            (SymbolKind.CLASS,),
        )
        return _constructor_resolution(index, classes) if classes else None
    candidates = _top_level_named(
        index,
        reference.relative_path,
        name,
        (SymbolKind.FUNCTION,),
    )
    return choose_resolution(candidates) if candidates else None


def _self_member_resolution(
    index: GraphIndexes, reference: Reference, member_name: str
) -> Resolution:
    source_id = reference.source_node_id
    source = index.nodes_by_id[source_id]
    symbol = index.symbols_by_id[source_id]
    parent_id = index.parent_ids.get(source_id)
    if (
        source.symbol_kind not in (SymbolKind.METHOD, SymbolKind.CONSTRUCTOR)
        or parent_id is None
        or index.nodes_by_id[parent_id].symbol_kind is not SymbolKind.CLASS
        or not symbol.parameters
        or symbol.parameters[0].name != "self"
        or sum(
            parameter.name == "self" for parameter in symbol.parameters
        )
        != 1
        or any(modifier not in ("async",) for modifier in symbol.modifiers)
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return _class_member_resolution(
        index,
        (parent_id,),
        member_name,
        _CALLABLE_KINDS,
    )


def _same_file_classes(
    index: GraphIndexes,
    reference: Reference,
    scopes: tuple[str, ...],
    class_name: str,
) -> tuple[str, ...]:
    for scope in scopes:
        classes = _children_named(
            index, scope, class_name, (SymbolKind.CLASS,)
        )
        if classes:
            return classes
    return _top_level_named(
        index,
        reference.relative_path,
        class_name,
        (SymbolKind.CLASS,),
    )


def _qualified_classes(
    index: GraphIndexes, qualified_name: str
) -> tuple[str, ...]:
    return _eligible_ids(
        index,
        index.by_qualified_name.get(qualified_name, ()),
        (SymbolKind.CLASS,),
    )


def _resolve_dotted_call(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
    parts: tuple[str, ...],
    callable_scopes: tuple[str, ...],
    visible_scopes: tuple[str, ...],
) -> Resolution:
    if len(parts) == 2 and parts[0] == "self":
        return _self_member_resolution(index, reference, parts[1])

    receiver = parts[0]
    if _parameter_conflict(index, callable_scopes, receiver):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    if len(parts) == 2:
        binding = _resolve_bindings(
            index,
            environment,
            visible_scopes,
            receiver,
            call_kind=reference.call_kind,
            member_name=parts[1],
        )
        if binding is not None:
            return binding

        classes = _same_file_classes(
            index, reference, callable_scopes, receiver
        )
        if classes and reference.call_kind is CallKind.CALL:
            return _class_member_resolution(
                index,
                classes,
                parts[1],
                _CALLABLE_KINDS,
            )

    exact = _eligible_ids(
        index,
        index.by_qualified_name.get(reference.raw_target, ()),
        _CALLABLE_KINDS,
    )
    if reference.call_kind is CallKind.CALL and exact:
        return choose_resolution(exact)

    if reference.call_kind is CallKind.CONSTRUCTOR:
        classes = _qualified_classes(index, reference.raw_target)
        if classes:
            return _constructor_resolution(index, classes)

    class_name = ".".join(parts[:-1])
    classes = _qualified_classes(index, class_name)
    if classes and reference.call_kind is CallKind.CALL:
        return _class_member_resolution(
            index,
            classes,
            parts[-1],
            _CALLABLE_KINDS,
        )
    return Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def _resolve_call(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    if reference.call_kind not in (CallKind.CALL, CallKind.CONSTRUCTOR):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if (
        not _is_dotted_identifier(reference.raw_target)
        or reference.raw_target in _SPECIAL_DYNAMIC_CALLS
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    parts = tuple(reference.raw_target.split("."))
    callable_scopes = _callable_scope_ids(
        index, reference.source_node_id
    )
    visible_scopes = (
        *callable_scopes,
        index.file_ids[reference.relative_path],
    )
    if len(parts) > 1:
        return _resolve_dotted_call(
            index,
            environment,
            reference,
            parts,
            callable_scopes,
            visible_scopes,
        )

    name = parts[0]
    if _parameter_conflict(index, callable_scopes, name):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    lexical = _lexical_declaration_resolution(
        index, callable_scopes, name, reference.call_kind
    )
    if lexical is not None:
        return lexical

    same_file = _same_file_call_resolution(index, reference, name)
    if same_file is not None:
        return same_file

    binding = _resolve_bindings(
        index,
        environment,
        visible_scopes,
        name,
        call_kind=reference.call_kind,
    )
    if binding is not None:
        return binding
    return Resolution(None, ResolutionFailureReason.NOT_FOUND)


def _resolve_base(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    source = index.nodes_by_id[reference.source_node_id]
    if (
        source.symbol_kind is not SymbolKind.CLASS
        or not _is_dotted_identifier(reference.raw_target)
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    parts = tuple(reference.raw_target.split("."))
    callable_scopes = _callable_scope_ids(
        index, reference.source_node_id
    )
    visible_scopes = (
        *callable_scopes,
        index.file_ids[reference.relative_path],
    )
    if len(parts) == 1:
        classes = tuple(
            candidate
            for candidate in _same_file_classes(
                index, reference, callable_scopes, parts[0]
            )
            if candidate != reference.source_node_id
        )
        if classes:
            return choose_resolution(classes)
        binding = _resolve_bindings(
            index,
            environment,
            visible_scopes,
            parts[0],
            call_kind=None,
            required_kind=SymbolKind.CLASS,
        )
        if binding is not None:
            return binding
        return Resolution(None, ResolutionFailureReason.NOT_FOUND)

    if _parameter_conflict(index, callable_scopes, parts[0]):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    if len(parts) == 2:
        binding = _resolve_bindings(
            index,
            environment,
            visible_scopes,
            parts[0],
            call_kind=None,
            member_name=parts[1],
            required_kind=SymbolKind.CLASS,
        )
        if binding is not None:
            return binding
    candidates = tuple(
        candidate
        for candidate in _qualified_classes(index, reference.raw_target)
        if candidate != reference.source_node_id
    )
    return choose_resolution(candidates)
