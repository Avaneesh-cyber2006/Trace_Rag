"""Resolve ECMAScript import evidence against immutable graph indexes."""

from unicodedata import category

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
    classify_relevant_parse_loss,
    import_scope,
)
from backend.code_parser.models import (
    CallKind,
    ImportBinding,
    ImportInfo,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
    SymbolKind,
)


_ECMASCRIPT_LANGUAGES = (
    ParsedLanguage.JAVASCRIPT,
    ParsedLanguage.TYPESCRIPT,
    ParsedLanguage.TSX,
)
_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")
_CALLABLE_KINDS = (
    SymbolKind.FUNCTION,
    SymbolKind.METHOD,
    SymbolKind.CONSTRUCTOR,
)
_ECMASCRIPT_KEYWORDS = frozenset(
    "await break case catch class const continue debugger default delete do "
    "else enum export extends false finally for function if implements import "
    "in instanceof interface let new null package private protected public "
    "return static super switch this throw true try typeof var void while "
    "with yield".split()
)
_OTHER_ID_START = frozenset("\u1885\u1886\u2118\u212e\u309b\u309c")
_OTHER_ID_CONTINUE = frozenset(
    "\u00b7\u0387\u1369\u136a\u136b\u136c\u136d\u136e\u136f\u1370"
    "\u1371\u19da"
)


def _relative_candidates(
    index: GraphIndexes, file: ParsedFile, module: str
) -> tuple[tuple[str, ...], bool]:
    if (
        not module
        or "\\" in module
        or "\x00" in module
        or module.startswith("/")
        or not module.startswith(("./", "../"))
    ):
        return (), True

    components = module.split("/")
    if any(component == "" for component in components):
        return (), True

    stack = file.relative_path.split("/")[:-1]
    appended = False
    for component in components:
        if component == ".":
            continue
        if component == "..":
            if not stack:
                return (), False
            stack.pop()
            continue
        stack.append(component)
        appended = True

    if not appended:
        return (), True
    normalized = "/".join(stack)
    if normalized.endswith(_EXTENSIONS):
        paths = (normalized,)
    else:
        filename = normalized.rsplit("/", 1)[-1]
        if "." in filename:
            return (), True
        paths = tuple(
            path
            for extension in _EXTENSIONS
            for path in (normalized + extension, normalized + "/index" + extension)
        )

    candidate_ids = tuple(
        index.file_ids[path]
        for path in paths
        if path in index.file_ids
        and index.files_by_path[path].language in _ECMASCRIPT_LANGUAGES
    )
    return candidate_ids, False


def _module_resolution(
    index: GraphIndexes, file: ParsedFile, module: str
) -> Resolution:
    if file.language not in _ECMASCRIPT_LANGUAGES:
        return choose_resolution((), unsupported=True)
    if module.startswith("@/"):
        return choose_resolution((), unsupported=True)
    if module.startswith(("./", "../")):
        candidates, unsupported = _relative_candidates(index, file, module)
        return choose_resolution(candidates, unsupported=unsupported)
    if (
        not module
        or module.startswith((".", "/"))
        or "\\" in module
        or "\x00" in module
    ):
        return choose_resolution((), unsupported=True)
    return choose_resolution((), external=True)


def _module_file_ids(resolution: Resolution) -> tuple[str, ...]:
    if resolution.target_node_id is not None:
        return (resolution.target_node_id,)
    if resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        return resolution.candidate_node_ids
    return ()


def _exported_ids(
    index: GraphIndexes,
    file_id: str,
    imported_name: str,
) -> tuple[str, ...]:
    path = index.nodes_by_id[file_id].relative_path
    if imported_name == "default":
        candidates = index.children[file_id]
    else:
        candidates = index.by_file_name.get((path, imported_name), ())

    exported: list[str] = []
    for node_id in candidates:
        if index.parent_ids.get(node_id) is not None:
            continue
        symbol = index.symbols_by_id[node_id]
        modifiers = symbol.modifiers
        if "export" not in modifiers:
            continue
        if imported_name == "default":
            if "default" in modifiers:
                exported.append(node_id)
        elif "default" not in modifiers and symbol.name == imported_name:
            exported.append(node_id)
    return tuple(exported)


def _member_resolution(
    index: GraphIndexes,
    module_resolution: Resolution,
    imported_name: str,
) -> Resolution:
    if (
        module_resolution.reason is not None
        and module_resolution.reason is not ResolutionFailureReason.AMBIGUOUS
    ):
        return module_resolution

    file_ids = _module_file_ids(module_resolution)
    candidate_ids = tuple(
        node_id
        for file_id in file_ids
        for node_id in _exported_ids(index, file_id, imported_name)
    )
    if module_resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        return Resolution(
            None,
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted(set(candidate_ids))),
        )
    if candidate_ids:
        return choose_resolution(candidate_ids)

    target_file = index.files_by_path[
        index.nodes_by_id[file_ids[0]].relative_path
    ]
    if target_file.status in (ParseStatus.PARTIAL, ParseStatus.FAILED):
        return Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE)
    return Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def _local_name(binding: ImportBinding) -> str:
    return binding.alias if binding.alias is not None else binding.imported_name


def _item_shape_supported(item: ImportInfo) -> bool:
    namespace_count = sum(
        binding.imported_name == "*" for binding in item.bindings
    )
    if item.is_wildcard:
        return namespace_count == 1 and all(
            binding.imported_name in ("default", "*")
            for binding in item.bindings
        )
    return namespace_count == 0


def _binding_shape_supported(binding: ImportBinding) -> bool:
    if binding.imported_name == "*":
        return binding.alias is not None and bool(binding.alias)
    if not binding.imported_name:
        return False
    if binding.alias is not None and not binding.alias:
        return False
    if binding.imported_name == "default" and binding.alias is None:
        return False
    return True


def _ambiguous_with_known_candidates(resolution: Resolution) -> Resolution:
    if resolution.target_node_id is not None:
        candidates = (resolution.target_node_id,)
    elif resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        candidates = resolution.candidate_node_ids
    else:
        candidates = ()
    return Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates)


def resolve_import(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> ImportResult:
    """Resolve one ECMAScript import record without reading project files."""

    scope_node_id, scope_reason = import_scope(index, file, item)
    module_resolution = _module_resolution(index, file, item.module)
    bindings: list[Binding] = []
    item_shape_supported = not item.modifiers and _item_shape_supported(item)

    for imported in item.bindings:
        local_name = _local_name(imported)
        if not local_name or (
            imported.imported_name == "*" and imported.alias is None
        ):
            continue
        if not item_shape_supported or not _binding_shape_supported(imported):
            bindings.append(
                Binding(
                    local_name,
                    scope_node_id,
                    "blocked",
                    Resolution(None, ResolutionFailureReason.UNSUPPORTED),
                )
            )
            continue

        if imported.imported_name == "*":
            mode = "module"
            binding_resolution = module_resolution
        else:
            mode = "symbol"
            binding_resolution = _member_resolution(
                index, module_resolution, imported.imported_name
            )

        if scope_reason is ResolutionFailureReason.AMBIGUOUS:
            mode = "blocked"
            binding_resolution = _ambiguous_with_known_candidates(
                binding_resolution
            )
        elif scope_reason is not None:
            mode = "blocked"
            binding_resolution = Resolution(None, scope_reason)
        elif binding_resolution.reason is ResolutionFailureReason.UNSUPPORTED:
            mode = "blocked"

        bindings.append(
            Binding(local_name, scope_node_id, mode, binding_resolution)
        )

    return ImportResult(module_resolution, tuple(bindings))


def _identifier_start(character: str) -> bool:
    return (
        character in ("$", "_")
        or character in _OTHER_ID_START
        or category(character) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Nl")
    )


def _identifier_part(character: str) -> bool:
    return (
        _identifier_start(character)
        or character in ("\u200c", "\u200d")
        or character in _OTHER_ID_CONTINUE
        or category(character) in ("Mn", "Mc", "Nd", "Pc")
    )


def _is_identifier_name(value: str) -> bool:
    return (
        bool(value)
        and _identifier_start(value[0])
        and all(_identifier_part(character) for character in value[1:])
    )


def _is_binding_identifier(value: str) -> bool:
    return _is_identifier_name(value) and value not in _ECMASCRIPT_KEYWORDS


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
            and node.language in _ECMASCRIPT_LANGUAGES
            and node.node_kind is GraphNodeKind.SYMBOL
            and node.symbol_kind in required_kinds
        )
    )


def _visible_outcome(
    index: GraphIndexes,
    candidates: tuple[str, ...],
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution | None:
    if not candidates:
        return None
    eligible = _eligible_ids(index, candidates, required_kinds)
    return (
        choose_resolution(eligible)
        if eligible
        else Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    )


def _filtered_resolution(
    index: GraphIndexes,
    resolution: Resolution,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution:
    if resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        candidates = resolution.candidate_node_ids
        if not candidates:
            return resolution
        eligible = _eligible_ids(index, candidates, required_kinds)
        if not eligible:
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        return Resolution(
            None,
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted(set(eligible))),
        )
    if resolution.reason is not None:
        return resolution
    target_id = resolution.target_node_id
    if target_id is None or not _eligible_ids(
        index, (target_id,), required_kinds
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return resolution


def _strong_outcome(outcomes: tuple[Resolution, ...]) -> Resolution:
    for reason in (
        ResolutionFailureReason.UNSUPPORTED,
        ResolutionFailureReason.INCOMPLETE_PARSE,
        ResolutionFailureReason.AMBIGUOUS,
    ):
        matching = tuple(
            outcome for outcome in outcomes if outcome.reason is reason
        )
        if matching:
            candidate_ids = {
                candidate
                for outcome in matching
                for candidate in outcome.candidate_node_ids
            }
            if reason is ResolutionFailureReason.AMBIGUOUS:
                candidate_ids.update(
                    outcome.target_node_id
                    for outcome in outcomes
                    if outcome.target_node_id is not None
                )
            candidates = tuple(sorted(candidate_ids))
            return (
                Resolution(None, reason, candidates)
                if candidates
                else Resolution(None, reason)
            )

    targets = tuple(
        outcome.target_node_id
        for outcome in outcomes
        if outcome.target_node_id is not None
    )
    if targets:
        if any(
            outcome.reason is ResolutionFailureReason.EXTERNAL
            for outcome in outcomes
        ):
            return Resolution(
                None,
                ResolutionFailureReason.AMBIGUOUS,
                tuple(sorted(set(targets))),
            )
        return choose_resolution(targets)
    if any(
        outcome.reason is ResolutionFailureReason.EXTERNAL
        for outcome in outcomes
    ):
        return Resolution(None, ResolutionFailureReason.EXTERNAL)
    return Resolution(None, ResolutionFailureReason.NOT_FOUND)


def _scope_ids(
    index: GraphIndexes, source_id: str, *, callable_only: bool
) -> tuple[str, ...]:
    scopes: list[str] = []
    current: str | None = (
        source_id if callable_only else index.parent_ids.get(source_id)
    )
    while current is not None:
        node = index.nodes_by_id[current]
        if not callable_only or node.symbol_kind in _CALLABLE_KINDS:
            scopes.append(current)
        current = index.parent_ids.get(current)
    scopes.append(index.file_ids[index.nodes_by_id[source_id].relative_path])
    return tuple(scopes)


def _children_named(
    index: GraphIndexes,
    scope_id: str,
    name: str,
    *,
    excluded_id: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        child_id
        for child_id in index.children.get(scope_id, ())
        if child_id != excluded_id
        and (
            (child := index.nodes_by_id[child_id]).language
            in _ECMASCRIPT_LANGUAGES
        )
        and child.node_kind is GraphNodeKind.SYMBOL
        and child.name == name
    )


def _same_file_resolution(
    index: GraphIndexes,
    source_id: str,
    name: str,
    required_kinds: tuple[SymbolKind, ...],
    *,
    callable_scopes: bool,
    excluded_id: str | None = None,
) -> Resolution | None:
    file_id = index.file_ids[index.nodes_by_id[source_id].relative_path]
    for scope_id in _scope_ids(
        index, source_id, callable_only=callable_scopes
    ):
        if (
            callable_scopes
            and scope_id != file_id
            and _parameter_conflict(index, scope_id, name)
        ):
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        candidates = _children_named(
            index, scope_id, name, excluded_id=excluded_id
        )
        outcome = _visible_outcome(index, candidates, required_kinds)
        if outcome is not None:
            return outcome
        if scope_id != file_id:
            scope = index.nodes_by_id[scope_id]
            if (
                scope_id != excluded_id
                and scope.name == name
                and scope.symbol_kind is SymbolKind.FUNCTION
                and scope.symbol_kind in required_kinds
            ):
                return Resolution(scope_id, None)
    return None


def _binding_scopes(index: GraphIndexes, source_id: str) -> tuple[str, ...]:
    scopes: list[str] = []
    current: str | None = source_id
    while current is not None:
        scopes.append(current)
        current = index.parent_ids.get(current)
    scopes.append(index.file_ids[index.nodes_by_id[source_id].relative_path])
    return tuple(scopes)


def _bindings_for_name(
    index: GraphIndexes,
    environment: ImportEnvironment,
    source_id: str,
    name: str,
) -> tuple[Binding, ...]:
    for scope_id in _binding_scopes(index, source_id):
        bindings = environment.bindings_by_scope_name.get(
            (scope_id, name), ()
        )
        if bindings:
            return bindings
    return ()


def _symbol_binding_outcome(
    index: GraphIndexes,
    binding: Binding,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution:
    if binding.mode == "blocked":
        return _filtered_resolution(index, binding.resolution, required_kinds)
    if binding.mode != "symbol":
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return _filtered_resolution(index, binding.resolution, required_kinds)


def _explicit_resolution(
    index: GraphIndexes,
    environment: ImportEnvironment,
    source_id: str,
    name: str,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution | None:
    bindings = _bindings_for_name(index, environment, source_id, name)
    if not bindings:
        return None
    return _strong_outcome(
        tuple(
            _symbol_binding_outcome(index, binding, required_kinds)
            for binding in bindings
        )
    )


def _namespace_member_outcome(
    index: GraphIndexes,
    binding: Binding,
    member_name: str,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution:
    if binding.mode == "blocked":
        resolution = binding.resolution
        if (
            resolution.reason is ResolutionFailureReason.AMBIGUOUS
            and resolution.candidate_node_ids
            and all(
                index.nodes_by_id[candidate_id].node_kind
                is GraphNodeKind.FILE
                for candidate_id in resolution.candidate_node_ids
            )
        ):
            return _filtered_resolution(
                index,
                _member_resolution(index, resolution, member_name),
                required_kinds,
            )
        if (
            resolution.reason is ResolutionFailureReason.AMBIGUOUS
            and resolution.candidate_node_ids
        ):
            return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
        return resolution
    if binding.mode != "module":
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return _filtered_resolution(
        index,
        _member_resolution(index, binding.resolution, member_name),
        required_kinds,
    )


def _qualified_import_resolution(
    index: GraphIndexes,
    environment: ImportEnvironment,
    source_id: str,
    receiver: str,
    member_name: str,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution | None:
    bindings = _bindings_for_name(index, environment, source_id, receiver)
    if not bindings:
        return None
    return _strong_outcome(
        tuple(
            _namespace_member_outcome(
                index, binding, member_name, required_kinds
            )
            for binding in bindings
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
            (child := index.nodes_by_id[child_id]).language
            in _ECMASCRIPT_LANGUAGES
            and child.node_kind is GraphNodeKind.SYMBOL
            and child.symbol_kind is SymbolKind.CONSTRUCTOR
        )
    )
    if owner_resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        return Resolution(
            None,
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted(set(constructors))),
        )
    if owner_resolution.reason is not None:
        return owner_resolution
    if constructors:
        return choose_resolution(constructors)
    return _missing_children_resolution(index, owner_ids)


def _parameter_conflict(
    index: GraphIndexes, source_id: str, name: str
) -> bool:
    return any(
        parameter.name == name
        for parameter in index.symbols_by_id[source_id].parameters
    )


def _resolve_call(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    if reference.call_kind not in (CallKind.CALL, CallKind.CONSTRUCTOR):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    source_id = reference.source_node_id
    if source_id is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    source = index.nodes_by_id[source_id]
    if source.symbol_kind not in _CALLABLE_KINDS:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    raw_target = reference.raw_target
    if _is_binding_identifier(raw_target):
        required_kinds = (
            (SymbolKind.CLASS,)
            if reference.call_kind is CallKind.CONSTRUCTOR
            else _CALLABLE_KINDS
        )
        local = _same_file_resolution(
            index,
            source_id,
            raw_target,
            required_kinds,
            callable_scopes=True,
        )
        if local is not None:
            return (
                _constructor_resolution(index, local)
                if reference.call_kind is CallKind.CONSTRUCTOR
                else local
            )
        imported = _explicit_resolution(
            index,
            environment,
            source_id,
            raw_target,
            required_kinds,
        )
        if imported is not None:
            return (
                _constructor_resolution(index, imported)
                if reference.call_kind is CallKind.CONSTRUCTOR
                else imported
            )
        return Resolution(None, ResolutionFailureReason.NOT_FOUND)

    parts = raw_target.split(".")
    if (
        len(parts) != 2
        or not _is_binding_identifier(parts[0])
        or not _is_identifier_name(parts[1])
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    required_kinds = (
        (SymbolKind.CLASS,)
        if reference.call_kind is CallKind.CONSTRUCTOR
        else _CALLABLE_KINDS
    )
    imported = _qualified_import_resolution(
        index,
        environment,
        source_id,
        parts[0],
        parts[1],
        required_kinds,
    )
    if imported is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    return (
        _constructor_resolution(index, imported)
        if reference.call_kind is CallKind.CONSTRUCTOR
        else imported
    )


def _required_type_kinds(
    reference: Reference, source_kind: SymbolKind
) -> tuple[SymbolKind, ...] | None:
    if reference.reference_kind is ReferenceKind.INHERITS:
        return (
            (source_kind,)
            if source_kind in (SymbolKind.CLASS, SymbolKind.INTERFACE)
            else None
        )
    if reference.reference_kind is ReferenceKind.IMPLEMENTS:
        return (
            (SymbolKind.INTERFACE,)
            if source_kind in (SymbolKind.CLASS, SymbolKind.ENUM)
            else None
        )
    return None


def _resolve_type(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
    required_kinds: tuple[SymbolKind, ...],
) -> Resolution:
    source_id = reference.source_node_id
    if source_id is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    raw_target = reference.raw_target
    if _is_binding_identifier(raw_target):
        local = _same_file_resolution(
            index,
            source_id,
            raw_target,
            required_kinds,
            callable_scopes=False,
            excluded_id=source_id,
        )
        if local is not None:
            return local
        imported = _explicit_resolution(
            index,
            environment,
            source_id,
            raw_target,
            required_kinds,
        )
        return (
            imported
            if imported is not None
            else Resolution(None, ResolutionFailureReason.NOT_FOUND)
        )

    parts = raw_target.split(".")
    if (
        len(parts) != 2
        or not _is_binding_identifier(parts[0])
        or not _is_identifier_name(parts[1])
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    imported = _qualified_import_resolution(
        index,
        environment,
        source_id,
        parts[0],
        parts[1],
        required_kinds,
    )
    return (
        imported
        if imported is not None
        else Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    )


def resolve_reference(
    index: GraphIndexes,
    environment: ImportEnvironment,
    reference: Reference,
) -> Resolution:
    """Resolve supported ECMAScript call and declared-type references."""

    if (
        reference.language not in _ECMASCRIPT_LANGUAGES
        or reference.relative_path not in index.files_by_path
        or index.files_by_path[reference.relative_path].language
        is not reference.language
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
        or source.language not in _ECMASCRIPT_LANGUAGES
        or source.relative_path != reference.relative_path
        or source.symbol_kind is None
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)

    if reference.reference_kind is ReferenceKind.CALL:
        resolution = _resolve_call(index, environment, reference)
        return classify_relevant_parse_loss(
            index, reference.relative_path, reference.location, resolution
        )

    if (
        reference.reference_kind is ReferenceKind.IMPLEMENTS
        and reference.language is ParsedLanguage.JAVASCRIPT
    ):
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    required_kinds = _required_type_kinds(reference, source.symbol_kind)
    if required_kinds is None:
        return Resolution(None, ResolutionFailureReason.UNSUPPORTED)
    resolution = _resolve_type(index, environment, reference, required_kinds)
    return classify_relevant_parse_loss(
        index, reference.relative_path, reference.location, resolution
    )
